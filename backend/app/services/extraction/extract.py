"""Извлечение текста из файла закупки. Задача T-003, исполнитель — Владос."""

from pathlib import Path

from app.schemas import ExtractedDocument


def extract(path: Path, filename: str, document_id: str) -> ExtractedDocument:
    """Извлекает текст из .pdf / .docx / .xlsx с привязкой к страницам и разделам.

    Выбирает парсер по расширению (pdf.py, docx.py, xlsx.py), склеивает сегменты
    через "\\n" в ExtractedDocument.text и проставляет каждому сегменту offset
    в этом полном тексте. Неподдерживаемый формат — ValueError с понятным текстом.

    Контракт: docs/contracts.md, раздел 3. Задача T-003, исполнитель — Владос.
    """
    raise NotImplementedError("T-003 (Владос)")
