"""Парсер .pdf на PyMuPDF (import fitz / pymupdf). Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

import logging
import re
from pathlib import Path

import pymupdf

from app.schemas import TextSegment
from app.services.extraction.errors import PASSWORD_PROTECTED, ExtractionError, readable_file

logger = logging.getLogger(__name__)


class ScannedPdfError(ExtractionError):
    """PDF содержит страницы, но не содержит распознаваемого текста."""


def is_numbered_heading(line: str) -> bool:
    """Проверяет нумерованный заголовок по общим правилам PDF и DOCX."""
    if len(line) > 80 or len(line.split()) > 10 or line.endswith((".", ";", ":", ",")):
        return False
    match = re.match(r"^\d{1,2}(\.\d{1,2}){0,2}\.?\s+(?P<title>\S)", line)
    return match is not None and match.group("title").isupper()


def extract_pdf(path: Path) -> list[TextSegment]:
    """Читает .pdf и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page — номер страницы с 1, section — заголовок раздела, если удалось определить.
    Задача T-003.
    """
    segments: list[TextSegment] = []
    section: str | None = None
    offset = 0
    empty_pages: list[int] = []
    with readable_file(), pymupdf.open(path, filetype="pdf") as document:
        if document.needs_pass or document.is_encrypted:
            raise ExtractionError(PASSWORD_PROTECTED, case="password_protected")
        for page in document:
            count_before = len(segments)
            for block in page.get_text("blocks", sort=True):
                if block[6] != 0 or not block[4].strip():
                    continue
                text = block[4].strip()
                for line in text.splitlines():
                    line = line.strip()
                    if is_numbered_heading(line):
                        section = line
                segments.append(TextSegment(
                    id=len(segments), text=text, offset=offset,
                    page=page.number + 1, section=section,
                ))
                offset += len(text) + 1
            if len(segments) == count_before:
                empty_pages.append(page.number + 1)
        if empty_pages and len(empty_pages) == len(document):
            raise ScannedPdfError(
                "Документ похож на скан: текст не распознаётся. "
                "Загрузите документ с текстовым слоем.", case="scanned_pdf",
            )
    if empty_pages:
        logger.warning("PDF: страницы без текста: %s", empty_pages)
    return segments
