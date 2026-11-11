"""Назначение документа и результат извлечения текста (contracts.md, раздел 1)."""

from enum import Enum

from pydantic import BaseModel, Field


class DocPurpose(str, Enum):
    internal = "internal"            # внутренний документ компании
    publication = "publication"      # будет опубликован (ЕИС, сайт) — открытый доступ
    contract = "contract"            # проект контракта / договор с контрагентом
    international = "international"  # международный контракт (проверяем ещё и GDPR)


class TextSegment(BaseModel):
    id: int
    text: str
    offset: int                      # начало сегмента в полном тексте
    page: int | None = None          # номер страницы (pdf), иначе None
    section: str | None = None       # "3.2. Обязанности сторон" или "Лист1!B2"


class OcrPage(BaseModel):
    """Одна распознанная страница (контракт v3)."""

    page: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)  # средняя уверенность слов страницы


class OcrInfo(BaseModel):
    """Сведения о распознавании (OCR) страниц без текстового слоя (контракт v3)."""

    engine: str                      # "tesseract-5.3.0 rus+eng"
    pages: list[OcrPage]             # только распознанные страницы
    mean_confidence: float = Field(ge=0, le=1)  # по всем распознанным словам
    low_confidence_pages: list[int] = []        # confidence < 0.75 (OCR_LOW_PAGE_CONF)
    # [start, end) слов с conf < 0.6 (OCR_LOW_WORD_CONF), координаты полного текста
    low_confidence_spans: list[tuple[int, int]] = []


class ExtractedDocument(BaseModel):
    document_id: str
    filename: str
    text: str                        # полный текст; сегменты склеены через "\n"
    segments: list[TextSegment]
    pages: int | None = None
    ocr: OcrInfo | None = None       # None — весь текст из текстового слоя (контракт v3)
