"""Парсер .pdf на PyMuPDF (import fitz / pymupdf). Задача T-003, исполнитель — Владос.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

from pathlib import Path

from app.schemas import TextSegment


def extract_pdf(path: Path) -> list[TextSegment]:
    """Читает .pdf и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page — номер страницы с 1, section — заголовок раздела, если удалось определить.
    Задача T-003, исполнитель — Владос.
    """
    raise NotImplementedError("T-003 (Владос)")
