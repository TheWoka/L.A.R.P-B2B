"""Маркеры чек-листа и цитаты-доказательства (contracts.md, раздел 1)."""

from enum import Enum

from pydantic import BaseModel


class MarkerType(str, Enum):
    approval_sheet = "approval_sheet"          # лист согласования / подписи / визы
    classification_mark = "classification_mark"  # гриф "Конфиденциально", "Коммерческая тайна"
    retention_period = "retention_period"      # указан срок хранения / уничтожения
    consent_clause = "consent_clause"          # согласие на обработку ПДн
    processing_assignment = "processing_assignment"  # условия поручения обработки ПДн (ч.3 ст.6)
    kii_mention = "kii_mention"                # сведения о значимых объектах КИИ


class Evidence(BaseModel):
    # цитата исходного текста, ПДн замаскированы с сохранением длины (contracts.md, 4.1):
    # quote == ReportText.text[start:end], len(quote) == end - start
    quote: str
    start: int
    end: int
    page: int | None = None
    section: str | None = None


class Marker(BaseModel):
    type: MarkerType
    present: bool
    evidence: Evidence | None = None
