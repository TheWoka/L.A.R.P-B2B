"""Срок хранения, удаление связей и исходников на SQLite в памяти."""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import Connection

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

from app.core.config import get_settings
from app.db.models import Base, DocumentRow, ReportRow, TaskRow
from app.services import retention
from app.worker import tasks
from app.worker.celery_app import celery_app

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


@pytest.fixture
def retention_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("REPORT_TTL_DAYS", "7")
    get_settings.cache_clear()
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _: ConnectionPoolEntry) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()
        get_settings.cache_clear()


def add_report(session: Session, identifier: str, age: timedelta, path: Path | None = None) -> None:
    session.add(DocumentRow(
        id=identifier, purpose="internal", stored_path=str(path) if path else None,
    ))
    session.flush()
    session.add_all([
        ReportRow(id=identifier, document_id=identifier, data={}, created_at=NOW - age),
        TaskRow(id=identifier, document_id=identifier, report_id=identifier, status="done"),
    ])
    session.commit()


def test_purge_expired_reports(
    retention_db: Session, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    session = retention_db
    for days in (1, 6, 10):
        path = tmp_path / f"synthetic-{days}.txt"
        path.write_text("Синтетический документ", encoding="utf-8")
        add_report(session, str(days), timedelta(days=days), path)
    with caplog.at_level(logging.INFO, logger=retention.__name__):
        assert retention.purge_expired(session, NOW) == 1
        assert retention.purge_expired(session, NOW) == 0
    for model in (ReportRow, TaskRow, DocumentRow):
        assert set(session.scalars(select(model.id))) == {"1", "6"}
    assert not (tmp_path / "synthetic-10.txt").exists()
    assert all((tmp_path / f"synthetic-{days}.txt").exists() for days in (1, 6))
    assert [record.getMessage() for record in caplog.records] == [
        "purged_reports=1", "purged_reports=0",
    ]


def test_ttl_boundary(retention_db: Session) -> None:
    add_report(retention_db, "boundary", timedelta(days=7))
    add_report(retention_db, "expired", timedelta(days=7, microseconds=1))
    assert retention.purge_expired(retention_db, NOW) == 1
    assert retention_db.get(ReportRow, "boundary") is not None
    assert retention_db.get(ReportRow, "expired") is None


def test_missing_file_and_default_now(retention_db: Session, tmp_path: Path) -> None:
    add_report(retention_db, "missing", timedelta(days=10), tmp_path / "missing.txt")
    assert retention.purge_expired(retention_db) == 1
    assert retention.purge_expired(retention_db) == 0


def test_current_report_on_same_document_is_preserved(retention_db: Session) -> None:
    add_report(retention_db, "shared", timedelta(days=10))
    retention_db.add(ReportRow(
        id="current", document_id="shared", data={}, created_at=NOW - timedelta(days=1),
    ))
    retention_db.commit()
    assert retention.purge_expired(retention_db, NOW) == 1
    assert retention_db.get(ReportRow, "current") is not None
    assert retention_db.get(DocumentRow, "shared") is not None


def test_worker_task_and_hourly_schedule(
    retention_db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_report(retention_db, "expired", timedelta(days=10))
    monkeypatch.setattr(tasks, "SessionLocal", sessionmaker(bind=retention_db.get_bind()))
    assert tasks.purge_expired_reports.run() == 1
    assert tasks.purge_expired_reports.run() == 0
    schedule = celery_app.conf.beat_schedule["purge-expired-reports"]
    assert schedule["task"] == tasks.purge_expired_reports.name
    assert schedule["schedule"] == 3600.0
