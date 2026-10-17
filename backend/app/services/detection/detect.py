"""Детекция ПДн, коммерческой тайны и финансовых данных. T-007, T-008, исполнитель — Данила."""

import logging
from bisect import bisect_right
from collections import Counter

from app.schemas import Entity, ExtractedDocument
from app.services.detection.ner_detector import detect_ner
from app.services.detection.regex_detector import detect_regex

logger = logging.getLogger(__name__)

# Чем меньше число, тем точнее детектор: при пересечении он выигрывает.
DETECTOR_PRIORITY: dict[str, int] = {"regex": 0, "ner": 1, "dict": 2, "llm": 3, "model": 4}


def _better(a: Entity, b: Entity) -> bool:
    """True, если a предпочтительнее b: точнее детектор, при равенстве — длиннее."""
    key_a = (DETECTOR_PRIORITY[a.detector], a.start - a.end)
    key_b = (DETECTOR_PRIORITY[b.detector], b.start - b.end)
    return key_a < key_b


def merge_entities(*groups: list[Entity]) -> list[Entity]:
    """Сливает результаты нескольких детекторов в один список без пересечений.

    Проход слева направо по (start, приоритет детектора, -длина). Если сущность
    пересекается с последней оставленной, остаётся лучшая по (детектор, длина);
    при полном равенстве — та, что левее. Результат отсортирован по start.
    """
    ordered = sorted(
        (e for group in groups for e in group),
        key=lambda e: (e.start, DETECTOR_PRIORITY[e.detector], e.start - e.end),
    )
    kept: list[Entity] = []
    for entity in ordered:
        if kept and entity.start < kept[-1].end:
            if _better(entity, kept[-1]):
                kept[-1] = entity
            continue
        kept.append(entity)
    return kept


def assign_location(entities: list[Entity], doc: ExtractedDocument) -> list[Entity]:
    """Проставляет page и section по сегменту, в который попадает start сущности."""
    if not doc.segments:
        return list(entities)
    segments = sorted(doc.segments, key=lambda s: s.offset)
    offsets = [s.offset for s in segments]
    located: list[Entity] = []
    for entity in entities:
        i = bisect_right(offsets, entity.start) - 1
        if i < 0:
            located.append(entity)
            continue
        seg = segments[i]
        located.append(entity.model_copy(update={"page": seg.page, "section": seg.section}))
    return located


def detect(doc: ExtractedDocument) -> list[Entity]:
    """Находит сущности в документе: regex + NER + словари.

    Объединяет результаты regex_detector (T-007) и ner_detector (T-008), убирает
    пересечения (приоритет у более точного детектора), проставляет page/section
    по сегментам. Значения в Entity — только value_masked. Результат отсортирован по start.

    Контракт: docs/contracts.md, раздел 3. Задачи T-007, T-008, исполнитель — Данила.
    """
    entities = assign_location(merge_entities(detect_regex(doc), detect_ner(doc)), doc)
    # В лог — только количество по типам, никаких значений (AGENTS.md, правило 4).
    counts = Counter(e.type.value for e in entities)
    logger.info(
        "detect: document_id=%s entities=%d by_type=%s",
        doc.document_id, len(entities), dict(sorted(counts.items())),
    )
    return entities
