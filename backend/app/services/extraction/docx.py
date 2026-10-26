"""Парсер .docx на python-docx (import docx). Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import Literal

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tc
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.schemas import TextSegment
from app.services.extraction.errors import readable_file
from app.services.extraction.pdf import is_numbered_heading


def extract_docx(path: Path) -> list[TextSegment]:
    """Переводит ошибки чтения DOCX в безопасное сообщение."""
    with readable_file():
        return _read_docx(path)


def _read_docx(path: Path) -> list[TextSegment]:
    """Читает .docx и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page = None, section — ближайший заголовок ("3.2. Обязанности сторон");
    таблицы тоже извлекаются.
    Задача T-003.
    """
    document = Document(str(path))
    segments: list[TextSegment] = []
    offset = 0
    blocks = chain(
        _margin_texts(document, "header"),
        _body_texts(document),
        _margin_texts(document, "footer"),
    )
    for text, section in blocks:
        segments.append(TextSegment(
            id=len(segments), text=text, offset=offset, section=section,
        ))
        offset += len(text) + 1
    return segments


def _margin_texts(
    document: DocxDocument, kind: Literal["header", "footer"],
) -> Iterator[tuple[str, str]]:
    """Извлекает активные колонтитулы, включая наследуемые, без повторов."""
    label = "Верхний колонтитул" if kind == "header" else "Нижний колонтитул"
    seen: set[tuple[str, ...]] = set()
    for section in document.sections:
        names = [kind]
        if section.different_first_page_header_footer:
            names.append(f"first_page_{kind}")
        if document.settings.odd_and_even_pages_header_footer:
            names.append(f"even_page_{kind}")
        for name in names:
            margin = getattr(section, name)
            texts: list[str] = []
            for block in margin.iter_inner_content():
                if isinstance(block, Paragraph):
                    if text := block.text.strip():
                        texts.append(text)
                else:
                    texts.extend(_table_rows(block))
            key = tuple(texts)
            if key in seen:
                continue
            seen.add(key)
            for text in texts:
                yield text, label


def _body_texts(document: DocxDocument) -> Iterator[tuple[str, str | None]]:
    """Сохраняет порядок абзацев и таблиц и ближайший заголовок тела документа."""
    section: str | None = None
    for element in document.element.body:
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            style = paragraph.style.name if paragraph.style is not None else ""
            if text and (
                style.startswith(("Heading", "Заголовок")) or is_numbered_heading(text)
            ):
                section = text
            texts = [text]
        elif element.tag == qn("w:tbl"):
            texts = list(_table_rows(Table(element, document)))
        else:
            continue
        for text in texts:
            if text:
                yield text, section


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
