"""Извлечение и проверка документа в Celery с удалением исходника."""

import logging
from collections import Counter
from pathlib import Path
from time import monotonic

from sqlalchemy.orm import Session

from app.db.models import DocumentRow, ReportRow, TaskRow
from app.db.session import SessionLocal
from app.schemas import DocPurpose, ExtractedDocument, Stage, TextSegment
from app.services.extraction.extract import extract
from app.services.pipeline import run_audit
from app.services.storage import delete_file
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
PROCESSING_ERROR = "Не удалось обработать документ. Попробуйте ещё раз"
MODULE_UNAVAILABLE = "Проверка временно недоступна: часть модулей в разработке"


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


def _audit_document(document: DocumentRow, task: TaskRow, session: Session, started: float) -> None:
    """Запускает проверку, фиксирует стадии и сохраняет только безопасный результат."""
    def on_stage(stage: Stage, progress: int) -> None:
        task.stage, task.progress = stage.value, progress
        session.commit()
        logger.info(
            "task_id=%s document_id=%s stage=%s elapsed_ms=%d",
            task.id, task.document_id, stage.value, (monotonic() - started) * 1000,
        )

    if document.stored_path is None:
        raise FileNotFoundError
    source_bytes = Path(document.stored_path).read_bytes()
    doc = extract_document(document)
    if document.filename is None:
        source_bytes = doc.text.encode("utf-8")
    result = run_audit(doc, DocPurpose(document.purpose), source_bytes, on_stage)
    report, report_text = result.report, result.report_text
    session.add(ReportRow(
        id=report.report_id, document_id=document.id,
        data=report.model_dump(mode="json"), text=report_text.model_dump(mode="json"),
    ))
    task.status, task.progress, task.report_id = "done", 100, report.report_id
    counts = Counter(entity.type.value for entity in report.entities)
    logger.info(
        "task_id=%s document_id=%s stage=reporting by_type=%s",
        task.id, document.id, dict(sorted(counts.items())),
    )


def run_pipeline(task_id: str, session: Session) -> None:
    """Обновляет статус, проверяет документ и удаляет исходник при любом результате."""
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
        if document is None:
            raise FileNotFoundError
        _audit_document(document, task, session, started)
    except ValueError as exc:
        task.status, task.error = "failed", str(exc)
    except NotImplementedError:
        task.status, task.error = "failed", MODULE_UNAVAILABLE
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
            "task_id=%s document_id=%s stage=%s elapsed_ms=%d",
            task_id, task.document_id, task.stage, (monotonic() - started) * 1000,
        )


@celery_app.task(name="app.worker.tasks.process_document")
def process_document(task_id: str) -> None:
    """Запускает конвейер в собственной сессии БД."""
    with SessionLocal() as session:
        run_pipeline(task_id, session)
