"""Значения ПДн для обезличенной копии и замена их метками. Задача T-026.

Значения берутся из исходного текста по координатам сущностей и живут только в памяти:
НЕ логировать и НЕ сохранять (AGENTS.md, правило 4).
"""

import re
from dataclasses import dataclass

from app.schemas import Entity, EntityType, ExtractedDocument
from app.services.masking.masker import MASKED_CATEGORIES

# Метка, которой заменяется значение в копии. Только ПДн: КТ и финансы не трогаются (4.1).
LABELS: dict[EntityType, str] = {
    EntityType.PERSON: "[ФИО]",
    EntityType.PASSPORT: "[ПАСПОРТ]",
    EntityType.SNILS: "[СНИЛС]",
    EntityType.INN: "[ИНН]",
    EntityType.PHONE: "[ТЕЛЕФОН]",
    EntityType.EMAIL: "[EMAIL]",
    EntityType.ADDRESS: "[АДРЕС]",
    EntityType.BIRTH_DATE: "[ДАТА РОЖДЕНИЯ]",
    EntityType.CARD: "[КАРТА]",
    EntityType.BANK_ACCOUNT: "[СЧЁТ]",
    EntityType.SPECIAL_CATEGORY: "[ОСОБЫЕ ДАННЫЕ]",
}
DEFAULT_LABEL = "[ПДн]"
# Короче — значение заменяется только целым словом и не участвует в самопроверке:
# подстрока «Ли» встречается в «Лист», а совпадение коротких строк ничего не доказывает.
MIN_CHECK_LENGTH = 4
# Разделители, которыми extraction склеивает текст: строки PDF и ячейки таблиц.
_PIECE_SEPARATORS_RE = re.compile(r"\n| \| ")
_SPACES_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PdValue:
    """Одно значение ПДн (или его часть в пределах строки/ячейки) и его метка."""

    text: str
    label: str
    page: int | None = None

    @property
    def checkable(self) -> bool:
        return len(self.text) >= MIN_CHECK_LENGTH


def normalize_spaces(text: str) -> str:
    """Схлопывает пробельные символы: извлечение по-разному переносит строки."""
    return _SPACES_RE.sub(" ", text).strip()


def pd_values(doc: ExtractedDocument, entities: list[Entity]) -> list[PdValue]:
    """Значения ПДн из doc.text[start:end], разбитые по строкам и ячейкам.

    Значение, которое extraction склеил из нескольких строк PDF или ячеек таблицы,
    в файле лежит частями — поэтому заменяется и проверяется каждая часть отдельно.
    Без повторов; длинные — первыми, чтобы «Иванов» не испортил «Иванов Иван».
    """
    found: dict[str, PdValue] = {}
    for entity in entities:
        if entity.category not in MASKED_CATEGORIES:
            continue
        label = LABELS.get(entity.type, DEFAULT_LABEL)
        for piece in _PIECE_SEPARATORS_RE.split(doc.text[entity.start:entity.end]):
            piece = normalize_spaces(piece)
            if piece and piece not in found:
                found[piece] = PdValue(text=piece, label=label, page=entity.page)
    return sorted(found.values(), key=lambda value: (-len(value.text), value.text))


def value_pattern(value: PdValue) -> re.Pattern[str]:
    """Шаблон значения: пробелы — любые пробельные; короткое — только целым словом."""
    body = r"\s+".join(re.escape(part) for part in value.text.split(" "))
    if value.checkable:
        return re.compile(body)
    return re.compile(rf"(?<!\w){body}(?!\w)")


def replace_values(text: str, values: list[PdValue]) -> str:
    """Заменяет все вхождения значений метками; значения уже отсортированы по длине."""
    for value in values:
        text = value_pattern(value).sub(value.label, text)  # в метках нет «\\» и групп
    return text


def leftover_values(text: str, values: list[PdValue]) -> list[PdValue]:
    """Значения длиной ≥ MIN_CHECK_LENGTH, которые остались в тексте копии."""
    haystack = normalize_spaces(text)
    return [value for value in values if value.checkable and value.text in haystack]
