"""Парсер .docx на python-docx (import docx). Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

import re
from collections.abc import Iterator
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tc
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.schemas import TextSegment


def extract_docx(path: Path) -> list[TextSegment]:
    """Читает .docx и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page = None, section — ближайший заголовок ("3.2. Обязанности сторон");
    таблицы тоже извлекаются.
    Задача T-003.
    """
    document = Document(str(path))
    segments: list[TextSegment] = []
    section: str | None = None
    offset = 0
    for element in document.element.body:
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            style = paragraph.style.name if paragraph.style is not None else ""
            if text and (style.startswith(("Heading", "Заголовок")) or (
                len(text) < 120 and re.match(r"^\d+(\.\d+)*\.?\s+\S", text)
            )):
                section = text
            texts = [text]
        elif element.tag == qn("w:tbl"):
            texts = list(_table_rows(Table(element, document)))
        else:
            continue
        for text in texts:
            if not text:
                continue
            segments.append(TextSegment(
                id=len(segments), text=text, offset=offset, section=section,
            ))
            offset += len(text) + 1
    return segments


def _table_rows(table: Table) -> Iterator[str]:
    """Возвращает строки, пропуская повторные ссылки на объединённые ячейки."""
    seen: set[CT_Tc] = set()
    for row in table.rows:
        values: list[str] = []
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            if text := cell.text.strip():
                values.append(text)
        if values:
            yield " | ".join(values)
