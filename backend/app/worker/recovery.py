"""Завершение потерянных проверок без изменения контракта и таблиц БД."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from hashlib import sha256

from billiard.exceptions import WorkerLostError
from celery import Task
from celery.signals import task_failure, worker_process_init, worker_ready
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db.models import DocumentRow, TaskRow
from app.db.session import SessionLocal, engine
from app.services.storage import delete_file

logger = logging.getLogger(__name__)
INTERRUPTED = (
    "Проверка прервана из-за перезапуска или сбоя обработчика. Загрузите документ ещё раз."
)
PROCESS_DOCUMENT = "app.worker.tasks.process_document"


@worker_process_init.connect
def reset_database_pool(**_: object) -> None:
    """Дочерний процесс не использует соединения обработчика восстановления из родителя."""
    engine.dispose(close=False)


@contextmanager
def document_lock(session: Session, task_id: str, *, wait: bool = True) -> Iterator[bool]:
    """Блокировка живёт на отдельном соединении, переживая commit стадий проверки.

    После гибели процесса PostgreSQL сам снимает блокировку. При старте другого worker
    занятая задача пропускается. В SQLite (тесты) межпроцессных worker нет.
    """
    engine = session.get_bind()
    if engine.dialect.name != "postgresql":
        yield True
        return
    key = int.from_bytes(sha256(task_id.encode()).digest()[:8], "big", signed=True)
    function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
    with engine.connect() as connection:
        acquired = connection.execute(text(f"SELECT {function}(:key)"), {"key": key}).scalar()
        locked = acquired is not False
        try:
            yield locked
        finally:
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def fail_interrupted(task_id: str, session: Session) -> None:
    """Не меняет терминальные задачи и не удаляет исходник работающего процесса."""
    with document_lock(session, task_id, wait=False) as acquired:
        if not acquired:
            return
        task = session.get(TaskRow, task_id, populate_existing=True)
        if task is None or task.status not in {"queued", "processing"}:
            return
        document = session.get(DocumentRow, task.document_id)
        if document is not None and document.stored_path is not None:
            delete_file(document.stored_path)
            document.stored_path = None
        task.status, task.error = "failed", INTERRUPTED
        session.commit()
        logger.warning("task_id=%s interrupted_processing", task_id)


@worker_ready.connect
def recover_interrupted_tasks(**_: object) -> None:
    """При рестарте находит processing без живого владельца блокировки."""
    with SessionLocal() as session:
        if not inspect(session.get_bind()).has_table(TaskRow.__tablename__):
            return  # первый запуск: API ещё создаёт таблицы, потерянных задач нет
        task_ids = session.scalars(select(TaskRow.id).where(TaskRow.status == "processing")).all()
        for task_id in task_ids:
            fail_interrupted(task_id, session)


@task_failure.connect
def handle_worker_loss(
    sender: Task | None = None, exception: BaseException | None = None,
    args: Sequence[object] | None = None, **_: object,
) -> None:
    """WorkerLostError приходит в родительский процесс, где доступна БД после SIGKILL."""
    if sender is None or sender.name != PROCESS_DOCUMENT:
        return
    if not isinstance(exception, WorkerLostError):
        return
    if not args or not isinstance(args[0], str):
        return
    with SessionLocal() as session:
        fail_interrupted(args[0], session)
