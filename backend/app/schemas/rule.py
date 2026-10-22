"""Правила, нарушения и контекст движка правил (contracts.md, разделы 1 и 3)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from app.schemas.document import DocPurpose, ExtractedDocument
from app.schemas.entity import Entity, MaskSpan
from app.schemas.marker import Evidence, Marker


class LawRef(BaseModel):
    law: str                         # "152-ФЗ", "187-ФЗ", "GDPR", "98-ФЗ", "Политика ИБ компании"
    article: str                     # "ст. 6 ч. 3", "Art. 28"
    title: str | None = None
    url: str | None = None


class Severity(str, Enum):
    red = "red"
    yellow = "yellow"


class Rule(BaseModel):
    id: str                          # "PD_IN_PUBLICATION"
    title: str
    description: str
    severity: Severity
    law_refs: list[LawRef]
    applies_to: list[DocPurpose] = []  # пусто = ко всем документам
    condition: dict                  # см. contracts.md, раздел 2
    recommendation: str
    enabled: bool = True


class Violation(BaseModel):
    rule_id: str
    title: str
    severity: Severity
    law_refs: list[LawRef]
    evidence: list[Evidence]
    recommendation: str
    source: Literal["rule", "llm"]


class RuleContext(BaseModel):
    """Вход движка правил services/rules/engine.evaluate (contracts.md, раздел 3)."""

    doc: ExtractedDocument
    masked_text: str
    entities: list[Entity]
    markers: list[Marker]
    purpose: DocPurpose
    mask_spans: list[MaskSpan] = []  # MaskResult.spans: перевод цитат LLM в исходные координаты
