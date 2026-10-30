"""Прерванная проверка не остаётся processing; данные исключительно синтетические."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from billiard.exceptions import WorkerLostError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, DocumentRow, TaskRow
from app.worker import recovery, tasks


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(recovery, "SessionLocal", factory)
    with factory() as session:
        yield session
    engine.dispose()


def add_task(session: Session, tmp_path: Path, status: str) -> tuple[TaskRow, Path]:
    source = tmp_path / f"{status}.txt"
    source.write_text("Учебный документ", encoding="utf-8")
    session.add(DocumentRow(
        id=status, filename=None, purpose="publication", stored_path=str(source),
    ))
    session.flush()
    task = TaskRow(id=status, document_id=status, status=status, stage="detecting")
    session.add(task)
    session.commit()
    return task, source


@pytest.mark.parametrize("status", ["done", "failed"])
def test_terminal_task_is_not_reprocessed(
    database: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    task, source = add_task(database, tmp_path, status)
    task.report_id = "existing-report"
    database.commit()
    audit = Mock()
    monkeypatch.setattr(tasks, "_audit_document", audit)
    tasks.run_pipeline(task.id, database)
    assert (task.status, task.report_id, task.error) == (status, "existing-report", None)
    audit.assert_not_called()
    assert source.exists()


@pytest.mark.parametrize("status", ["queued", "processing", "done", "failed"])
def test_startup_recovers_only_interrupted_tasks(
    database: Session, tmp_path: Path, status: str,
) -> None:
    task, source = add_task(database, tmp_path, status)
    recovery.recover_interrupted_tasks()
    database.expire_all()
    task = database.get(TaskRow, task.id)
    if status == "processing":
        assert task.status == "failed" and task.error == recovery.INTERRUPTED
        assert not source.exists()
        assert database.get(DocumentRow, task.document_id).stored_path is None
    else:
        assert task.status == status and source.exists()


@pytest.mark.parametrize("status", ["queued", "processing", "done", "failed"])
def test_worker_loss_records_error(database: Session, tmp_path: Path, status: str) -> None:
    task, source = add_task(database, tmp_path, status)
    recovery.handle_worker_loss(
        sender=tasks.process_document, exception=WorkerLostError("synthetic"), args=[task.id],
    )
    database.expire_all()
    task = database.get(TaskRow, task.id)
    assert task.status == ("done" if status == "done" else "failed")
    assert source.exists() == (status in {"done", "failed"})


@pytest.mark.parametrize("sender,error,args", [
    (None, WorkerLostError(), ["processing"]),
    (Mock(name="other"), ValueError("synthetic"), ["processing"]),
    (tasks.process_document, ValueError("synthetic"), ["processing"]),
    (tasks.process_document, WorkerLostError(), []),
    (tasks.process_document, WorkerLostError(), [123]),
])
def test_unrelated_failure_is_ignored(
    database: Session, tmp_path: Path, sender: object, error: BaseException, args: list[object],
) -> None:
    task, source = add_task(database, tmp_path, "processing")
    recovery.handle_worker_loss(sender=sender, exception=error, args=args)
    database.refresh(task)
    assert task.status == "processing" and source.exists()


@pytest.mark.parametrize("acquired", [True, False])
def test_postgres_lock_is_released_only_when_acquired(acquired: bool) -> None:
    session = Mock()
    engine = session.get_bind.return_value
    engine.dialect.name = "postgresql"
    connection = MagicMock()
    engine.connect.return_value.__enter__ = Mock(return_value=connection)
    engine.connect.return_value.__exit__ = Mock(return_value=False)
    connection.execute.return_value.scalar.return_value = acquired
    with pytest.raises(RuntimeError), recovery.document_lock(
        session, "synthetic", wait=False,
    ) as locked:
        assert locked == acquired
        raise RuntimeError("synthetic")
    assert connection.execute.call_count == (2 if acquired else 1)


def test_missing_task_is_ignored(database: Session) -> None:
    recovery.fail_interrupted("missing", database)
    assert database.query(TaskRow).count() == 0


def test_locked_task_is_not_recovered(
    database: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    @contextmanager
    def busy_lock(*_: object, **__: object) -> Iterator[bool]:
        yield False

    task, source = add_task(database, tmp_path, "processing")
    monkeypatch.setattr(recovery, "document_lock", busy_lock)
    recovery.recover_interrupted_tasks()
    database.refresh(task)
    assert task.status == "processing" and source.exists()


def test_child_discards_inherited_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Mock()
    monkeypatch.setattr(recovery, "engine", engine)
    recovery.reset_database_pool()
    engine.dispose.assert_called_once_with(close=False)


def test_startup_before_schema_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    monkeypatch.setattr(recovery, "SessionLocal", sessionmaker(engine))
    recovery.recover_interrupted_tasks()
    engine.dispose()
