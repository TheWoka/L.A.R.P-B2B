"""Детекция ПДн, коммерческой тайны и финансовых данных. T-007, T-008, исполнитель — Данила."""

from app.schemas import Entity, ExtractedDocument


def detect(doc: ExtractedDocument) -> list[Entity]:
    """Находит сущности в документе: regex + NER + словари.

    Объединяет результаты regex_detector (T-007) и ner_detector (T-008), убирает
    пересечения (приоритет у более точного детектора), проставляет page/section
    по сегментам. Значения в Entity — только value_masked.

    Контракт: docs/contracts.md, раздел 3. Задачи T-007, T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-007, T-008 (Данила)")
