"""Детекция ПДн, коммерческой тайны и финансовых данных.

Задачи T-007, T-008, T-013a, исполнитель — Данила.
"""

import logging
import re
from bisect import bisect_right
from collections import Counter

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection.dict_detector import detect_dict
from app.services.detection.ner_detector import detect_ner
from app.services.detection.regex_detector import detect_regex
from app.services.masking.masker import mask_value

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


_WORD_RE = re.compile(r"\w+")


def trim_at_newline(entities: list[Entity], text: str) -> list[Entity]:
    """Спаны ПДн не переходят через перевод строки (T-019b).

    Строка — граница поля: «ФИО: Тихонов Арсений Валерьевич\nДата рождения: …» — два поля,
    NER иногда склеивает их в один PERSON. Из спана с «\n» остаётся строка с наибольшим числом
    слов (при равенстве — первая): «Тихонов Арсений Валерьевич» из «…\nДата», «В. Д.» из
    «В. Д.\nЛичный»; без букв и цифр — сущность отбрасывается. ADDRESS не режется:
    в блоке PDF адрес переносится по строкам абзаца. КТ, FINANCIAL и особые категории — фразы,
    их границы задают свои детекторы.
    """
    result: list[Entity] = []
    for entity in entities:
        value = text[entity.start:entity.end]
        if (entity.category != EntityCategory.pd or entity.type == EntityType.ADDRESS
                or "\n" not in value):
            result.append(entity)
            continue
        best: tuple[int, int] | None = None
        best_words = 0
        pos = entity.start
        for line in value.split("\n"):
            words = len(_WORD_RE.findall(line))
            if words > best_words:
                lead = len(line) - len(line.lstrip())
                best, best_words = (pos + lead, pos + lead + len(line.strip())), words
            pos += len(line) + 1
        if best is not None:
            result.append(entity.model_copy(update={
                "start": best[0], "end": best[1], "value_masked": mask_value(text[best[0]:best[1]]),
            }))
    return result


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

    Объединяет результаты regex_detector (T-007), ner_detector (T-008) и dict_detector
    (T-013a), убирает пересечения (приоритет у более точного детектора), проставляет
    page/section по сегментам. Значения в Entity — только value_masked. Результат
    отсортирован по start.

    Словарные спаны (TRADE_SECRET, FINANCIAL) до слияния обрезаются по найденным ПДн:
    «Наценка ИП Смирнова А.А. составляет 12%» даёт и PERSON, и TRADE_SECRET без пересечения,
    иначе merge_entities выкинул бы одну из сущностей.

    Спаны ПДн (кроме адреса) обрезаются по переводу строки — trim_at_newline (T-019b).

    Контракт: docs/contracts.md, раздел 3. Задачи T-007, T-008, T-013a, исполнитель — Данила.
    """
    regex = trim_at_newline(detect_regex(doc), doc.text)
    ner = trim_at_newline(detect_ner(doc), doc.text)
    dict_ = detect_dict(doc, pd_entities=regex + ner)
    entities = assign_location(merge_entities(regex, ner, dict_), doc)
    # В лог — только количество по типам, никаких значений (AGENTS.md, правило 4).
    counts = Counter(e.type.value for e in entities)
    logger.info(
        "detect: document_id=%s entities=%d by_type=%s",
        doc.document_id, len(entities), dict(sorted(counts.items())),
    )
    return entities
