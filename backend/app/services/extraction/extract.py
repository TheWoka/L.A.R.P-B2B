"""Извлечение текста из файла закупки. Задача T-003."""

import logging
from pathlib import Path
from time import monotonic

from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.docx import extract_docx
from app.services.extraction.errors import EMPTY_DOCUMENT, ExtractionError
from app.services.extraction.image import extract_image
from app.services.extraction.ocr import OcrResult
from app.services.extraction.pdf import extract_pdf
from app.services.extraction.xlsx import extract_xlsx
from app.services.extraction.xml import extract_xml

logger = logging.getLogger(__name__)


def extract(path: Path, filename: str, document_id: str) -> ExtractedDocument:
    """Извлекает текст из документов и изображений с привязкой к страницам и разделам.

    Выбирает парсер по расширению, склеивает сегменты
    через "\\n" в ExtractedDocument.text и проставляет каждому сегменту offset
    в этом полном тексте. Неподдерживаемый формат — ValueError с понятным текстом.

    Контракт: docs/contracts.md, раздел 3. Задача T-003.
    """
    extension = Path(filename).suffix.lower()
    started = monotonic()
    ocr = OcrResult()
    parsers = {
        ".pdf": extract_pdf, ".docx": extract_docx, ".xlsx": extract_xlsx, ".xml": extract_xml,
    }
    images = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    if extension not in parsers and extension not in images:
        raise ValueError(
            "Формат файла не поддерживается. "
            "Загрузите .pdf, .docx, .xlsx, .xml, .png, .jpg, .jpeg, .tif или .tiff"
        )
    pages: int | None = None
    try:
        if extension == ".pdf" or extension in images:
            parser = extract_pdf if extension == ".pdf" else extract_image
            segments = parser(path, ocr=ocr)
            pages = ocr.page_count
        else:
            segments = parsers[extension](path)
    except ExtractionError as exc:
        logger.info("case=%s document_id=%s", exc.case, document_id)
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
    if not normalized:
        logger.info("case=empty_document document_id=%s", document_id)
        raise ExtractionError(EMPTY_DOCUMENT, case="empty_document")
    info = ocr.info(normalized)
    if info is not None:
        logger.info(
            "OCR: страниц=%d время_мс=%d средняя_уверенность=%.3f",
            len(info.pages), (monotonic() - started) * 1000, info.mean_confidence,
        )
    logger.info(
        "Извлечён документ %s: страниц=%s, сегментов=%s", document_id, pages, len(normalized),
    )
    return ExtractedDocument(
        document_id=document_id, filename=filename, pages=pages,
        text="\n".join(segment.text for segment in normalized), segments=normalized, ocr=info,
    )
