"""Парсер .pdf на PyMuPDF (import fitz / pymupdf). Задача T-003.

Внутренний интерфейс модуля extraction — не контракт: исполнитель может его менять,
контрактом является только extract() в extract.py.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from app.core.config import get_settings
from app.schemas import TextSegment
from app.services.extraction.errors import PASSWORD_PROTECTED, ExtractionError, readable_file
from app.services.extraction.ocr import (
    OcrResult,
    OcrWord,
    check_page_limit,
    ocr_available,
    ocr_image,
    ocr_paragraphs,
)

logger = logging.getLogger(__name__)
MIN_IMAGE_AREA = 0.15
MIN_IMAGE_PIXELS = 300
MAX_IMAGE_REGIONS = 4
TEXT_LAYER_CHARS = 50


@dataclass(frozen=True)
class OcrRegion:
    bbox: pymupdf.Rect
    section: str | None = None


class ScannedPdfError(ExtractionError):
    """PDF содержит страницы, но не содержит распознаваемого текста."""


def is_numbered_heading(line: str) -> bool:
    """Проверяет нумерованный заголовок по общим правилам PDF и DOCX."""
    if len(line) > 80 or len(line.split()) > 10 or line.endswith((".", ";", ":", ",")):
        return False
    match = re.match(r"^\d{1,2}(\.\d{1,2}){0,2}\.?\s+(?P<title>\S)", line)
    return match is not None and match.group("title").isupper()


def extract_pdf(path: Path, *, ocr: OcrResult | None = None) -> list[TextSegment]:
    """Читает .pdf и возвращает сегменты текста по порядку.

    offset считается от начала текста этого файла при склейке сегментов через "\\n";
    page — номер страницы с 1, section — заголовок раздела, если удалось определить.
    Задача T-003.
    """
    segments: list[TextSegment] = []
    section: str | None = None
    offset = 0
    empty_pages: list[int] = []
    ocr = ocr if ocr is not None else OcrResult()
    with readable_file(), pymupdf.open(path, filetype="pdf") as document:
        if document.needs_pass or document.is_encrypted:
            raise ExtractionError(PASSWORD_PROTECTED, case="password_protected")
        ocr.page_count = len(document)
        regions = _plan_ocr(document)
        total = sum(len(areas) for areas in regions.values())
        check_page_limit(total)
        if total and not ocr_available():
            raise ScannedPdfError(
                "Распознавание сканов недоступно. Загрузите документ с текстовым слоем.",
                case="ocr_unavailable",
            )
        for page in document:
            count_before = len(segments)
            for text, words, source in _page_text(page, ocr, regions[page.number], total):
                if source is None:
                    for line in text.splitlines():
                        line = line.strip()
                        if is_numbered_heading(line):
                            section = line
                if words:
                    ocr.segments[len(segments)] = words
                segments.append(TextSegment(
                    id=len(segments), text=text, offset=offset,
                    page=page.number + 1, section=source if source is not None else section,
                ))
                offset += len(text) + 1
            if len(segments) == count_before:
                empty_pages.append(page.number + 1)
        if empty_pages and len(empty_pages) == len(document):
            raise ScannedPdfError(
                "Не удалось распознать текст скана. Загрузите более чёткий документ.",
                case="scanned_pdf",
            )
    if empty_pages:
        logger.warning("PDF: страницы без текста: %s", empty_pages)
    return segments


def _page_text(
    page: pymupdf.Page, ocr: OcrResult, regions: list[OcrRegion], total: int,
) -> list[tuple[str, list[OcrWord], str | None]]:
    """Сначала текстовые блоки страницы, затем абзацы её OCR-областей."""
    texts: list[tuple[str, list[OcrWord], str | None]] = [
        (block[4].strip(), [], None) for block in page.get_text("blocks", sort=True)
        if block[6] == 0 and block[4].strip()
    ]
    for region in regions:
        pixmap = page.get_pixmap(
            clip=region.bbox, dpi=get_settings().ocr_dpi, colorspace=pymupdf.csRGB,
        )
        with Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) as image:
            paragraphs = ocr_paragraphs(ocr_image(image))
        ocr.add_page(page.number + 1, paragraphs, total)
        texts.extend((paragraph.text, paragraph.words, region.section) for paragraph in paragraphs)
    return texts


def _plan_ocr(document: pymupdf.Document) -> dict[int, list[OcrRegion]]:
    """Планирует все области заранее: лимит проверяется до первого запуска OCR."""
    return {
        page.number: _image_regions(page) if page.get_text().strip() else [OcrRegion(page.rect)]
        for page in document
    }


def _image_regions(page: pymupdf.Page) -> list[OcrRegion]:
    """Отбирает до четырёх крупных изображений без достаточного текстового слоя."""
    bounds = page.rect * page.derotation_matrix
    scale = pymupdf.Matrix(get_settings().ocr_dpi / 72, get_settings().ocr_dpi / 72)
    candidates: dict[tuple[float, ...], pymupdf.Rect] = {}
    for image in page.get_image_info(xrefs=True):
        bbox = pymupdf.Rect(image["bbox"]) & bounds
        if bbox.is_empty or bbox.get_area() < MIN_IMAGE_AREA * bounds.get_area():
            continue
        rendered = bbox * page.rotation_matrix
        pixels = (rendered * scale).irect
        if min(pixels.width, pixels.height) < MIN_IMAGE_PIXELS:
            continue
        if len(page.get_text(clip=bbox).strip()) >= TEXT_LAYER_CHARS:
            continue
        candidates[tuple(rendered)] = rendered
    # Если кандидатов больше четырёх, приоритет у самых крупных областей.
    largest = sorted(candidates.values(), key=lambda rect: rect.get_area(), reverse=True)
    selected = sorted(largest[:MAX_IMAGE_REGIONS], key=lambda rect: (rect.y0, rect.x0))
    return [OcrRegion(bbox, f"Распознанное изображение {index}")
            for index, bbox in enumerate(selected, start=1)]
