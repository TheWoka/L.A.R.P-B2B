"""Детектор на регулярных выражениях. Задача T-007, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.
"""

from app.schemas import Entity, ExtractedDocument


def detect_regex(doc: ExtractedDocument) -> list[Entity]:
    """Ищет паспорт, СНИЛС, ИНН, телефон, email, карту, счёт регулярками.

    Кандидаты с контрольной суммой проверяются через validators.py; detector="regex".
    Задача T-007, исполнитель — Данила.
    """
    raise NotImplementedError("T-007 (Данила)")
