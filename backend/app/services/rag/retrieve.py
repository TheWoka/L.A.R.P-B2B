"""Поиск статей законов по смыслу. Задача T-014, исполнитель — Данила.

Внутренний интерфейс модуля rag — не контракт.
"""

from app.schemas import LawRef


def retrieve(query: str, k: int = 5) -> list[tuple[LawRef, str]]:
    """Возвращает k ближайших статей: ссылку на закон и текст статьи.

    Задача T-014, исполнитель — Данила.
    """
    raise NotImplementedError("T-014 (Данила)")
