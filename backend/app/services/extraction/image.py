"""Изображения и многостраничные TIFF: один абзац OCR — один сегмент."""

from pathlib import Path

from PIL import Image, ImageSequence

from app.schemas import TextSegment
from app.services.extraction.errors import ExtractionError
from app.services.extraction.ocr import (
    OcrResult,
    check_page_limit,
    ocr_available,
    ocr_image,
    ocr_paragraphs,
)
from app.services.extraction.pdf import ScannedPdfError, is_numbered_heading


def extract_image(path: Path, *, ocr: OcrResult | None = None) -> list[TextSegment]:
    """Скрывает ошибки декодирования и не включает содержимое в сообщения."""
    try:
        with Image.open(path) as image:
            return _read_image(image, ocr if ocr is not None else OcrResult())
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Не удалось открыть изображение", case="corrupt_image") from None


def _read_image(image: Image.Image, ocr: OcrResult) -> list[TextSegment]:
    ocr.page_count = getattr(image, "n_frames", 1)
    check_page_limit(ocr.page_count)
    if not ocr_available():
        raise ScannedPdfError("Распознавание изображений недоступно.", case="ocr_unavailable")
    segments: list[TextSegment] = []
    offset = 0
    section: str | None = None
    for page, frame in enumerate(ImageSequence.Iterator(image), start=1):
        paragraphs = ocr_paragraphs(ocr_image(frame))
        ocr.add_page(page, paragraphs, ocr.page_count)
        for paragraph in paragraphs:
            if is_numbered_heading(paragraph.text):
                section = paragraph.text
            ocr.segments[len(segments)] = paragraph.words
            segments.append(TextSegment(
                id=len(segments), text=paragraph.text, offset=offset, page=page, section=section,
            ))
            offset += len(paragraph.text) + 1
    if not segments:
        raise ScannedPdfError(
            "Не удалось распознать текст изображения. Загрузите более чёткий документ.",
            case="empty_image",
        )
    return segments
