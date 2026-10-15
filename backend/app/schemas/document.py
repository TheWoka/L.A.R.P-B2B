"""Назначение документа и результат извлечения текста (contracts.md, раздел 1)."""

from enum import Enum

from pydantic import BaseModel


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


class ExtractedDocument(BaseModel):
    document_id: str
    filename: str
    text: str                        # полный текст; сегменты склеены через "\n"
    segments: list[TextSegment]
    pages: int | None = None
