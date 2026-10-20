"""Загрузка эталонного корпуса. Задача T-014a.

Поддерживаются два формата в одной папке:
1. `name.txt` с разметкой в тексте `[[TYPE:значение]]` (стартовый набор eval/seed, markup.py);
2. `name.docx|pdf|xlsx|txt` + `name.json` (генератор T-011):
   `{"purpose": "...", "entities": [{"type", "category", "start", "end"}]}`, смещения —
   в `ExtractedDocument.text`, который вернёт `extract()`.
Если рядом с `name.txt` лежит `name.json` — это формат 2, разметка в тексте не ищется.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from app.evaluation.markup import CATEGORY_BY_TYPE, GoldEntity, parse_markup, parse_type
from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.extract import extract

DOCUMENT_SUFFIXES = (".docx", ".pdf", ".xlsx", ".txt")


@dataclass(frozen=True)
class CorpusDocument:
    """Документ корпуса: имя файла, извлечённый текст и эталонная разметка."""

    name: str
    doc: ExtractedDocument
    gold: list[GoldEntity]


def text_document(text: str, name: str) -> ExtractedDocument:
    """Текст → ExtractedDocument из одного сегмента, как для .txt в worker (T-009a)."""
    return ExtractedDocument(
        document_id=Path(name).stem, filename=name, text=text,
        segments=[TextSegment(id=0, text=text, offset=0)],
    )


def _read_document(path: Path) -> ExtractedDocument:
    if path.suffix.lower() == ".txt":
        return text_document(path.read_text(encoding="utf-8"), path.name)
    return extract(path, path.name, path.stem)


def validate_gold(gold: list[GoldEntity], text_len: int, source: str) -> None:
    """Границы в пределах текста, непустые отрезки, без пересечений → иначе ValueError."""
    prev_end = 0
    for e in sorted(gold, key=lambda g: (g.start, g.end)):
        if not 0 <= e.start < e.end <= text_len:
            raise ValueError(f"{source}: {e.type.value} [{e.start}, {e.end}) вне текста")
        if e.start < prev_end:
            raise ValueError(f"{source}: эталонные сущности пересекаются в позиции {e.start}")
        prev_end = e.end


def _parse_json_entities(path: Path) -> list[GoldEntity]:
    data = json.loads(path.read_text(encoding="utf-8"))
    gold: list[GoldEntity] = []
    for n, item in enumerate(data.get("entities", []), start=1):
        where = f"{path.name}: сущность {n}"
        try:
            raw_type, start, end = str(item["type"]), int(item["start"]), int(item["end"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{where}: нужны поля type, start, end") from exc
        entity_type = parse_type(raw_type, where)
        category = CATEGORY_BY_TYPE[entity_type]
        if "category" in item and item["category"] != category.value:
            raise ValueError(f"{where}: категория {item['category']!r} не подходит к типу")
        gold.append(GoldEntity(entity_type, category, start, end))
    return gold


def _load_json_pair(json_path: Path) -> CorpusDocument:
    candidates = [json_path.with_suffix(s) for s in DOCUMENT_SUFFIXES]
    found = [p for p in candidates if p.is_file()]
    if len(found) != 1:
        raise ValueError(f"{json_path.name}: нужен ровно один документ с тем же именем")
    doc = _read_document(found[0])
    gold = _parse_json_entities(json_path)
    validate_gold(gold, len(doc.text), json_path.name)
    return CorpusDocument(found[0].name, doc, sorted(gold, key=lambda g: g.start))


def _load_markup(path: Path) -> CorpusDocument:
    clean, gold = parse_markup(path.read_text(encoding="utf-8"), path.name)
    return CorpusDocument(path.name, text_document(clean, path.name), gold)


def load_corpus(folder: Path) -> list[CorpusDocument]:
    """Загружает все документы папки (без подпапок), отсортированные по имени файла.

    `*.json` → формат «файл + JSON»; `*.txt` без пары `.json` → разметка в тексте;
    `.docx/.pdf/.xlsx` без `.json` → ValueError. Остальные файлы (README.md и т.п.)
    пропускаются. Пустой корпус или несуществующая папка → ValueError.
    """
    if not folder.is_dir():
        raise ValueError(f"папка корпуса не найдена: {folder}")
    docs: list[CorpusDocument] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        suffix = path.suffix.lower()
        if suffix == ".json":
            docs.append(_load_json_pair(path))
        elif suffix == ".txt" and not path.with_suffix(".json").is_file():
            docs.append(_load_markup(path))
        elif suffix in DOCUMENT_SUFFIXES and not path.with_suffix(".json").is_file():
            raise ValueError(f"{path.name}: нет файла разметки {path.stem}.json")
    if not docs:
        raise ValueError(f"в папке {folder} нет документов корпуса")
    return docs
