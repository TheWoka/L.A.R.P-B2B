"""Индексация текстов законов в pgvector. Задача T-014, исполнитель — Данила.

Внутренний интерфейс модуля rag — не контракт.
"""

from pathlib import Path


def build_index(legal_dir: Path) -> int:
    """Режет data/legal/*.md по статьям («## Статья N. Название»), считает эмбеддинги
    моделью EMBEDDING_MODEL и сохраняет в PostgreSQL. Возвращает число фрагментов.

    Задача T-014, исполнитель — Данила.
    """
    raise NotImplementedError("T-014 (Данила)")
