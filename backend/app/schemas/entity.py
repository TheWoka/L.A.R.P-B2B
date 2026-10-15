"""Найденные сущности и результат маскирования (contracts.md, разделы 1 и 3)."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    PERSON = "PERSON"                # ФИО
    PASSPORT = "PASSPORT"
    SNILS = "SNILS"
    INN = "INN"                      # ИНН физлица (12 цифр); ИНН юрлица — не ПДн
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    ADDRESS = "ADDRESS"
    BIRTH_DATE = "BIRTH_DATE"
    CARD = "CARD"                    # номер банковской карты
    BANK_ACCOUNT = "BANK_ACCOUNT"    # расчётный / лицевой счёт
    SPECIAL_CATEGORY = "SPECIAL_CATEGORY"  # здоровье, убеждения и т.п. (ст. 10 152-ФЗ)
    TRADE_SECRET = "TRADE_SECRET"    # себестоимость, маржа, ноу-хау, условия скидок
    FINANCIAL = "FINANCIAL"          # финансовые показатели, суммы, реквизиты


class EntityCategory(str, Enum):
    pd = "pd"
    pd_special = "pd_special"
    trade_secret = "trade_secret"
    financial = "financial"


class Entity(BaseModel):
    type: EntityType
    category: EntityCategory
    start: int
    end: int
    page: int | None = None
    section: str | None = None
    value_masked: str                # для показа: "Иванов И***", "***-***-*** 45"
    confidence: float = Field(ge=0, le=1)
    detector: Literal["regex", "ner", "dict", "llm", "model"]


class MaskSpan(BaseModel):
    """Одна замена: где плейсхолдер в masked_text и где значение в исходном тексте."""

    masked_start: int
    masked_end: int
    orig_start: int
    orig_end: int


class MaskResult(BaseModel):
    """Результат services/masking/masker.mask (contracts.md, раздел 3)."""

    masked_text: str                 # текст с плейсхолдерами [PERSON_1] — только он уходит в LLM
    # "[PERSON_1]" -> исходное значение (НЕ логировать, НЕ сохранять)
    mapping: dict[str, str]
    spans: list[MaskSpan]            # по одному на каждую замену, по возрастанию masked_start
