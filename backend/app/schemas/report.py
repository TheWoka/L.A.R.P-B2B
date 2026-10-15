"""Отчёт и текст для подсветки (contracts.md, разделы 1 и 4.1)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.document import DocPurpose, TextSegment
from app.schemas.entity import Entity
from app.schemas.marker import Evidence
from app.schemas.rule import Violation


class ChecklistItem(BaseModel):
    category: Literal["approvals", "classification", "retention"]
    status: Literal["ok", "missing", "not_required"]
    comment: str
    evidence: Evidence | None = None


class Report(BaseModel):
    report_id: str
    document_id: str
    filename: str | None             # None для текстового запроса
    purpose: DocPurpose
    overall: Literal["red", "yellow", "green"]
    created_at: datetime
    processing_ms: int
    stats: dict[str, int]            # {"PERSON": 12, "SNILS": 3, ...}
    entities: list[Entity]
    checklist: list[ChecklistItem]   # ровно 3 элемента
    violations: list[Violation]
    llm_used: bool                   # False, если работали в режиме mock / без ключа


class ReportText(BaseModel):
    """Ответ GET /api/reports/{report_id}/text.

    text — маскирование с сохранением длины: каждый символ значения ПДн → "█", кроме первой
    буквы каждого слова ("Иванов Иван" → "И█████ И███"). Длина равна исходной, поэтому
    смещения Entity/Evidence из Report можно применять к text напрямую.
    """

    text: str
    segments: list[TextSegment]      # offset — в координатах исходного полного текста
