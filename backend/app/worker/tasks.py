"""Первая стадия обработки документа в Celery (T-009a)."""

import logging
from pathlib import Path
from time import monotonic

from sqlalchemy.orm import Session

from app.db.models import DocumentRow, TaskRow
from app.db.session import SessionLocal
from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.extract import extract
from app.services.storage import delete_file
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
PROCESSING_ERROR = "Не удалось обработать документ. Попробуйте ещё раз"


@celery_app.task(name="app.worker.tasks.ping")
def ping() -> str:
    """Проверка доступности воркера."""
    logger.info("ping получен")
    return "pong"


def extract_document(document: DocumentRow) -> ExtractedDocument:
    """Для текстового запроса создаёт один сегмент, файлы передаёт сервису."""
    if document.stored_path is None:
        raise FileNotFoundError
    path = Path(document.stored_path)
    if path.suffix.lower() == ".txt":
        text = path.read_text(encoding="utf-8")
        return ExtractedDocument(
            document_id=document.id, filename="Текстовый запрос", text=text,
            segments=[TextSegment(id=0, text=text, offset=0)],
        )
    return extract(path, document.filename, document.id)


def run_pipeline(task_id: str, session: Session) -> None:
    """Обновляет статус, извлекает текст и удаляет исходник при любом результате."""
    task = session.get(TaskRow, task_id)
    if task is None:
        return
    document = session.get(DocumentRow, task.document_id)
    started = monotonic()
    try:
        task.status, task.stage, task.progress = "processing", "extracting", 10
        task.error = None
        session.commit()
        logger.info("task_id=%s document_id=%s stage=extracting", task_id, task.document_id)
        extract_document(document)
        task.status, task.progress = "done", 100
    except ValueError as exc:
        task.status, task.error = "failed", str(exc)
    except Exception as exc:
        task.status, task.error = "failed", PROCESSING_ERROR
        logger.error("task_id=%s exception_type=%s", task_id, type(exc).__name__)
    finally:
        try:
            if document is not None and document.stored_path is not None:
                delete_file(document.stored_path)
                document.stored_path = None
        except Exception as exc:
            task.status, task.error = "failed", PROCESSING_ERROR
            logger.error("task_id=%s exception_type=%s", task_id, type(exc).__name__)
        session.commit()
        logger.info(
            "task_id=%s document_id=%s stage=extracting elapsed_ms=%d",
            task_id, task.document_id, (monotonic() - started) * 1000,
        )


@celery_app.task(name="app.worker.tasks.process_document")
def process_document(task_id: str) -> None:
    """Запускает первую стадию в собственной сессии БД; продолжение — T-009b."""
    with SessionLocal() as session:
        run_pipeline(task_id, session)
