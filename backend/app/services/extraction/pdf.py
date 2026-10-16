"""Парсер .pdf на PyMuPDF (import fitz / pymupdf). Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

import re
from pathlib import Path

import pymupdf

from app.schemas import TextSegment


def extract_pdf(path: Path) -> list[TextSegment]:
    """Читает .pdf и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page — номер страницы с 1, section — заголовок раздела, если удалось определить.
    Задача T-003.
    """
    segments: list[TextSegment] = []
    section: str | None = None
    offset = 0
    with pymupdf.open(path, filetype="pdf") as document:
        for page in document:
            for block in page.get_text("blocks", sort=True):
                if block[6] != 0 or not block[4].strip():
                    continue
                text = block[4].strip()
                for line in text.splitlines():
                    line = line.strip()
                    if len(line) < 120 and re.match(r"^\d+(\.\d+)*\.?\s+\S", line):
                        section = line
                segments.append(TextSegment(
                    id=len(segments), text=text, offset=offset,
                    page=page.number + 1, section=section,
                ))
                offset += len(text) + 1
    return segments
