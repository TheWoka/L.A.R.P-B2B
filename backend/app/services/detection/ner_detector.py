"""NER-детектор (Natasha / Presidio). Задача T-008, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.
"""

from app.schemas import Entity, ExtractedDocument


def detect_ner(doc: ExtractedDocument) -> list[Entity]:
    """Ищет ФИО и адреса моделью NER; detector="ner".

    Модель загружается один раз на процесс (лениво), не при импорте модуля.
    Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")
