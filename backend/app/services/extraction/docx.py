"""Парсер .docx на python-docx (import docx). Задача T-003, исполнитель — Владос.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

from pathlib import Path

from app.schemas import TextSegment


def extract_docx(path: Path) -> list[TextSegment]:
    """Читает .docx и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page = None, section — ближайший заголовок ("3.2. Обязанности сторон");
    таблицы тоже извлекаются.
    Задача T-003, исполнитель — Владос.
    """
    raise NotImplementedError("T-003 (Владос)")
