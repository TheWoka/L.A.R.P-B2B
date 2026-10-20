"""Подстановка значений и разметка после реального извлечения текста."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from random import Random
from string import Formatter

from app.evaluation.corpus import text_document
from app.evaluation.generator import values
from app.evaluation.generator.templates import MARKERS, Block, Template, paragraph, row
from app.evaluation.generator.writers import WRITERS
from app.evaluation.markup import CATEGORY_BY_TYPE
from app.schemas import EntityType, ExtractedDocument
from app.services.extraction.extract import extract

GENERATORS: dict[str, tuple[Callable[[Random], str], EntityType]] = {
    "person": (values.person, EntityType.PERSON),
    "passport": (values.passport, EntityType.PASSPORT),
    "snils": (values.snils, EntityType.SNILS),
    "inn_person": (values.inn_person, EntityType.INN),
    "phone": (values.phone, EntityType.PHONE),
    "email": (values.email, EntityType.EMAIL),
    "address": (values.address, EntityType.ADDRESS),
    "birth_date": (values.birth_date, EntityType.BIRTH_DATE),
    "card": (values.card, EntityType.CARD),
    "bank_account": (values.bank_account, EntityType.BANK_ACCOUNT),
    "special_category": (values.special_category, EntityType.SPECIAL_CATEGORY),
    "trade_secret_phrase": (values.trade_secret_phrase, EntityType.TRADE_SECRET),
}


@dataclass(frozen=True)
class Substitution:
    type: EntityType
    value: str


def read_for_markup(path: Path) -> ExtractedDocument:
    """Тот же выбор чтения, что у T-014a: TXT — один сегмент, прочие — extract()."""
    if path.suffix.lower() == ".txt":
        return text_document(path.read_text(encoding="utf-8"), path.name)
    return extract(path, path.name, path.stem)


def _fill_cell(
    text: str, rng: Random, cache: dict[str, str], uppercase: bool,
) -> tuple[str, list[Substitution]]:
    pieces: list[str] = []
    annotations: list[Substitution] = []
    for literal, field, _, _ in Formatter().parse(text):
        pieces.append(literal)
        if field is None:
            continue
        generator, entity_type = GENERATORS[field.rsplit("_", 1)[0]]
        if field not in cache:
            cache[field] = generator(rng)
        value = cache[field].upper() if uppercase else cache[field]
        pieces.append(value)
        # По T-014a у паспорта размечаются серия и номер, без вводного слова «серия».
        annotated = value[6:] if entity_type == EntityType.PASSPORT and (
            value.lower().startswith("серия ")
        ) else value
        annotations.append(Substitution(entity_type, annotated))
    filled = "".join(pieces)
    return (filled.upper() if uppercase else filled), annotations


def render_template(
    template: Template, rng: Random,
) -> tuple[tuple[Block, ...], list[Substitution]]:
    """Возвращает заполненные блоки и значения в порядке их появления, включая повторы."""
    blocks: list[Block] = []
    annotations: list[Substitution] = []
    cache: dict[str, str] = {}
    for block in template.blocks:
        rows: list[tuple[str, ...]] = []
        for source_row in block.rows:
            cells: list[str] = []
            for cell in source_row:
                filled, found = _fill_cell(cell, rng, cache, template.uppercase)
                cells.append(filled)
                annotations.extend(found)
            rows.append(tuple(cells))
        blocks.append(replace(block, rows=tuple(rows)))
    for marker in MARKERS:
        if rng.choice((False, True)):
            text = marker.upper() if template.uppercase else marker
            blocks.append(row(text) if template.format == "xlsx" else paragraph(text))
    return tuple(blocks), annotations


def _markup(text: str, annotations: list[Substitution], template: str) -> list[dict[str, object]]:
    entities: list[dict[str, object]] = []
    position = 0
    for item in annotations:
        start = text.find(item.value, position)
        if start < 0:
            raise ValueError(f"{template}: не найдено значение типа {item.type.value}")
        position = start + len(item.value)
        entities.append({
            "type": item.type.value, "category": CATEGORY_BY_TYPE[item.type].value,
            "start": start, "end": position,
        })
    return entities


def make_document(template: Template, rng: Random, out_dir: Path, name: str) -> Path:
    """Записывает файл и JSON; не перезаписывает существующие пары и не выводит значения."""
    if not name or Path(name).name != name or name in (".", "..") or "\\" in name:
        raise ValueError("Нужно простое имя документа без пути")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.{template.format}"
    annotation_path = out_dir / f"{name}.json"
    if annotation_path.exists() or any(out_dir.glob(f"{name}.*")):
        raise ValueError(f"{template.name}: имя документа уже используется")
    blocks, annotations = render_template(template, rng)
    try:
        WRITERS[template.format](path, blocks)
        entities = _markup(read_for_markup(path).text, annotations, template.name)
        payload = {"purpose": template.purpose.value, "entities": entities}
        annotation_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    except Exception:
        path.unlink(missing_ok=True)
        annotation_path.unlink(missing_ok=True)
        raise
    return path
