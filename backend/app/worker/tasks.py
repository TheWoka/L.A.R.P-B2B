"""Задачи Celery."""

import logging

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.ping")
def ping() -> str:
    """Проверка, что воркер жив: `celery -A app.worker.celery_app call app.worker.tasks.ping`."""
    logger.info("ping получен")
    return "pong"


@celery_app.task(name="app.worker.tasks.process_document")
def process_document(task_id: str) -> None:
    """Полный конвейер проверки документа. Реализация — T-009 (оркестрация).

    Стадии (app.schemas.Stage), после каждой обновляем TaskRow.stage и progress:
      1. extracting — services.extraction.extract → ExtractedDocument
      2. detecting  — services.detection.detect → list[Entity]
                      + services.markers.find_markers → list[Marker]
      3. masking    — services.masking.mask → MaskResult (mapping НЕ сохранять и НЕ логировать)
      4. checking   — services.rules.engine.evaluate (+ LLM-проверки по masked_text)
      5. reporting  — services.report.builder.build_report → Report в таблицу reports
    После обработки исходный файл удаляется (contracts.md, раздел 4).
    """
    raise NotImplementedError("T-009: конвейер обработки документа")
