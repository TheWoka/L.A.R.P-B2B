"""Извлечение текста из файла закупки. Задача T-003."""

import logging
from pathlib import Path

import pymupdf

from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.docx import extract_docx
from app.services.extraction.pdf import ScannedPdfError, extract_pdf
from app.services.extraction.xlsx import extract_xlsx

logger = logging.getLogger(__name__)


def extract(path: Path, filename: str, document_id: str) -> ExtractedDocument:
    """Извлекает текст из .pdf / .docx / .xlsx с привязкой к страницам и разделам.

    Выбирает парсер по расширению (pdf.py, docx.py, xlsx.py), склеивает сегменты
    через "\\n" в ExtractedDocument.text и проставляет каждому сегменту offset
    в этом полном тексте. Неподдерживаемый формат — ValueError с понятным текстом.

    Контракт: docs/contracts.md, раздел 3. Задача T-003.
    """
    extension = Path(filename).suffix.lower()
    parsers = {".pdf": extract_pdf, ".docx": extract_docx, ".xlsx": extract_xlsx}
    if extension not in parsers:
        raise ValueError("Формат файла не поддерживается. Загрузите .pdf, .docx или .xlsx")
    pages: int | None = None
    try:
        segments = parsers[extension](path)
        if extension == ".pdf":
            with pymupdf.open(path, filetype="pdf") as pdf:
                pages = len(pdf)
    except ScannedPdfError:
        raise
    except Exception as exc:
        raise ValueError(
            "Не удалось прочитать файл. Он повреждён, недоступен или защищён паролем."
        ) from exc
    normalized: list[TextSegment] = []
    offset = 0
    for segment in segments:
        if not segment.text.strip():
            continue
        normalized.append(segment.model_copy(update={"id": len(normalized), "offset": offset}))
        offset += len(segment.text) + 1
    logger.info(
        "Извлечён документ %s: страниц=%s, сегментов=%s", document_id, pages, len(normalized),
    )
    return ExtractedDocument(
        document_id=document_id, filename=filename, pages=pages,
        text="\n".join(segment.text for segment in normalized), segments=normalized,
    )
