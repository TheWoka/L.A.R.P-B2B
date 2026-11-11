"""Извлечение и проверка документа в Celery с удалением исходника."""

import logging
from collections import Counter
from pathlib import Path
from time import monotonic

from sqlalchemy.orm import Session

from app.db.models import DocumentRow, ReportRow, TaskRow
from app.db.session import SessionLocal
from app.schemas import DocPurpose, Entity, ExtractedDocument, LlmMode, Stage, TextSegment
from app.services.extraction.extract import extract
from app.services.extraction.ocr import ocr_progress
from app.services.pipeline import run_audit
from app.services.redaction.check import count_leftovers
from app.services.redaction.redact import (
    RedactedFile,
    ocr_page_count,
    redact,
    scan_copy_allowed,
)
from app.services.retention import purge_expired
from app.services.rules import repository
from app.services.storage import delete_file
from app.worker.celery_app import celery_app
from app.worker.recovery import document_lock

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


def make_redacted_copy(
    source: Path, doc: ExtractedDocument, entities: list[Entity], task_id: str,
) -> RedactedFile | None:
    """Обезличенная копия до удаления исходника; None — копию не предлагаем.

    Любая ошибка не ломает проверку. Копия, в которой самопроверка повторным
    извлечением находит исходное значение ПДн, не отдаётся. В лог — только тип
    исключения, формат и число оставшихся значений (AGENTS.md, правило 4).
    Скан больше OCR_REDACT_MAX_PAGES страниц OCR — копию не делаем (T-072): ещё два
    прохода OCR (копия и её самопроверка) надолго заняли бы воркер.
    """
    if not scan_copy_allowed(doc):
        logger.info("task_id=%s копия скана пропущена: %d страниц", task_id, ocr_page_count(doc))
        return None
    if doc.ocr is not None:
        return None  # Обезличивание пикселей — T-072.
    try:
        redacted = redact(source, doc, entities)
        remaining = count_leftovers(redacted, doc, entities)
    except Exception as exc:
        logger.error("task_id=%s redaction failed exception_type=%s", task_id, type(exc).__name__)
        return None
    if remaining:
        logger.error(
            "task_id=%s redaction self-check failed: ext=%s remaining_values=%d",
            task_id, redacted.ext, remaining,
        )
        return None
    logger.info(
        "task_id=%s redaction=ok ext=%s bytes=%d", task_id, redacted.ext, len(redacted.content),
    )
    return redacted


def document_llm_mode(document: DocumentRow) -> LlmMode:
    """Режим из документа; у документов до T-073 (None) или с мусором — режим по умолчанию."""
    if document.llm_mode in modes.ALL_MODES:
        return document.llm_mode  # type: ignore[return-value]
    return modes.default_mode()


def llm_mode_available(mode: LlmMode, task_id: str) -> bool:
    """Доступен ли режим в момент обработки. Ошибка проверки — «недоступен», не падение."""
    try:
        available = modes.mode_info(mode).available
    except Exception as exc:
        logger.warning("task_id=%s llm mode check failed: %s", task_id, type(exc).__name__)
        available = False
    if not available:
        # Только имя режима: причина может содержать адрес сервера (AGENTS.md, правила 3–4).
        logger.warning("task_id=%s режим %s недоступен", task_id, mode)
    return available


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
    with ocr_progress(lambda done, total: on_stage(Stage.extracting, 10 + 19 * done // total)):
        doc = extract_document(document)
    if document.filename is None:
        source_bytes = doc.text.encode("utf-8")
    rules = repository.list_rules(session)
    mode = document_llm_mode(document)
    # T-073: вся проверка — в режиме документа; недоступный режим → LLM-шаги дают unknown.
    with use_llm_mode(mode, available=llm_mode_available(mode, task.id)):
        result = run_audit(doc, DocPurpose(document.purpose), source_bytes, on_stage, rules=rules)
    report, report_text = result.report, result.report_text
    redacted = make_redacted_copy(Path(document.stored_path), doc, report.entities, task.id)
    if redacted is not None:
        report = report.model_copy(update={"redacted_available": True})
    session.add(ReportRow(
        id=report.report_id, document_id=document.id,
        data=report.model_dump(mode="json"), text=report_text.model_dump(mode="json"),
        llm_log=[call.model_dump(mode="json") for call in result.llm_log],
        redacted=redacted.content if redacted else None,
        redacted_ext=redacted.ext if redacted else None,
    ))
    task.status, task.progress, task.report_id = "done", 100, report.report_id
    counts = Counter(entity.type.value for entity in report.entities)
    logger.info(
        "task_id=%s document_id=%s stage=reporting by_type=%s",
        task.id, document.id, dict(sorted(counts.items())),
    )


def run_pipeline(task_id: str, session: Session) -> None:
    """Не допускает одновременную или повторную обработку доставленной задачи."""
    with document_lock(session, task_id):
        _run_pipeline(task_id, session)


def _run_pipeline(task_id: str, session: Session) -> None:
    """Обновляет статус, проверяет документ и удаляет исходник при любом результате."""
    task = session.get(TaskRow, task_id)
    if task is None or task.status in {"done", "failed"}:
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


@celery_app.task(name="app.worker.tasks.purge_expired_reports")
def purge_expired_reports() -> int:
    """Удаляет просроченные отчёты в собственной сессии БД."""
    with SessionLocal() as session:
        return purge_expired(session)
