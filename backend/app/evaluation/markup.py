"""Разметка эталонных сущностей прямо в тексте: `[[TYPE:значение]]`. Задача T-014a.

Пример строки: `Представитель: [[PERSON:Иванов Иван Петрович]], тел. [[PHONE:+7 912 345-67-89]]`.
Парсер убирает разметку и возвращает чистый текст и сущности в его координатах.
"""

import re
from dataclasses import dataclass

from app.schemas import EntityCategory, EntityType

# Категория по типу — таблица из docs/contracts.md (раздел 1) и задачи T-014a.
CATEGORY_BY_TYPE: dict[EntityType, EntityCategory] = {
    EntityType.PERSON: EntityCategory.pd,
    EntityType.PASSPORT: EntityCategory.pd,
    EntityType.SNILS: EntityCategory.pd,
    EntityType.INN: EntityCategory.pd,
    EntityType.PHONE: EntityCategory.pd,
    EntityType.EMAIL: EntityCategory.pd,
    EntityType.ADDRESS: EntityCategory.pd,
    EntityType.BIRTH_DATE: EntityCategory.pd,
    EntityType.CARD: EntityCategory.pd,
    EntityType.BANK_ACCOUNT: EntityCategory.pd,
    EntityType.SPECIAL_CATEGORY: EntityCategory.pd_special,
    EntityType.TRADE_SECRET: EntityCategory.trade_secret,
    EntityType.FINANCIAL: EntityCategory.financial,
}

OPEN, CLOSE = "[[", "]]"
_OPEN_RE = re.compile(r"\[\[([A-Za-z_]*):")


@dataclass(frozen=True)
class GoldEntity:
    """Эталонная сущность: тип, категория и отрезок [start, end) в чистом тексте."""

    type: EntityType
    category: EntityCategory
    start: int
    end: int


def parse_type(name: str, where: str) -> EntityType:
    """Строка → EntityType; неизвестный тип → ValueError с указанием места."""
    try:
        return EntityType(name)
    except ValueError:
        raise ValueError(f"{where}: неизвестный тип сущности {name!r}") from None


def _parse_line(line: str, base: int, where: str) -> tuple[str, list[GoldEntity]]:
    """Одна строка: чистый текст строки и сущности со смещением base."""
    out: list[str] = []
    entities: list[GoldEntity] = []
    pos, clean_len = 0, 0
    while True:
        i = line.find(OPEN, pos)
        stray = line.find(CLOSE, pos, i if i >= 0 else len(line))
        if stray >= 0:
            raise ValueError(f"{where}: лишнее {CLOSE!r} без открывающей разметки")
        if i < 0:
            break
        head = _OPEN_RE.match(line, i)
        if head is None:
            raise ValueError(f"{where}: ожидается [[TYPE:значение]]")
        entity_type = parse_type(head.group(1), where)
        j = line.find(CLOSE, head.end())
        if j < 0:
            raise ValueError(f"{where}: разметка не закрыта {CLOSE!r}")
        value = line[head.end():j]
        if OPEN in value:
            raise ValueError(f"{where}: вложенная разметка не поддерживается")
        if not value.strip():
            raise ValueError(f"{where}: пустое значение у {entity_type.value}")
        out.append(line[pos:i])
        clean_len += i - pos
        start = base + clean_len
        entities.append(GoldEntity(
            entity_type, CATEGORY_BY_TYPE[entity_type], start, start + len(value),
        ))
        out.append(value)
        clean_len += len(value)
        pos = j + len(CLOSE)
    out.append(line[pos:])
    return "".join(out), entities


def parse_markup(raw: str, source: str = "<text>") -> tuple[str, list[GoldEntity]]:
    """Убирает разметку `[[TYPE:значение]]` и возвращает (чистый текст, сущности).

    Смещения сущностей — в координатах чистого текста: `clean[start:end] == значение`.
    Категория берётся из типа по CATEGORY_BY_TYPE. Разметка не может переходить на другую
    строку. Неизвестный тип, вложенная, незакрытая или пустая разметка, лишнее «]]» →
    ValueError с именем источника и номером строки (с 1).
    """
    lines = raw.split("\n")
    clean_lines: list[str] = []
    entities: list[GoldEntity] = []
    offset = 0
    for lineno, line in enumerate(lines, start=1):
        clean, found = _parse_line(line, offset, f"{source}:{lineno}")
        clean_lines.append(clean)
        entities.extend(found)
        offset += len(clean) + 1
    return "\n".join(clean_lines), entities
