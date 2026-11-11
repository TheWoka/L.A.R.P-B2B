"""Распознавание изображений: исходные слова остаются только в памяти."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output, TesseractNotFoundError

from app.core.config import get_settings
from app.schemas import OcrInfo, OcrPage, TextSegment
from app.services.extraction.errors import ExtractionError

ProgressCallback = Callable[[int, int], None]
_progress: ContextVar[ProgressCallback | None] = ContextVar("ocr_progress", default=None)


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: list[OcrWord]
    block_num: int = 0
    par_num: int = 0


@dataclass(frozen=True)
class OcrParagraph:
    text: str
    words: list[OcrWord]


def ocr_image(image: Image.Image) -> list[OcrLine]:
    """Применяет EXIF-поворот и возвращает строки в порядке Tesseract."""
    with ImageOps.exif_transpose(image) as oriented, oriented.convert("L") as gray:
        data = pytesseract.image_to_data(
            gray, lang=get_settings().ocr_lang, config="--psm 3", output_type=Output.DICT,
        )
    groups: dict[tuple[int, int, int], list[OcrWord]] = {}
    for index, value in enumerate(data["text"]):
        confidence = float(data["conf"][index])
        text = value.strip()
        if confidence < 0 or not text:
            continue
        key = tuple(int(data[name][index]) for name in ("block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append(OcrWord(
            text=text, left=int(data["left"][index]), top=int(data["top"][index]),
            width=int(data["width"][index]), height=int(data["height"][index]),
            conf=min(confidence / 100, 1.0),
        ))
    return [OcrLine(" ".join(word.text for word in words), words, block, paragraph)
            for (block, paragraph, _line), words in groups.items()]


def ocr_paragraphs(lines: list[OcrLine]) -> list[OcrParagraph]:
    """Склеивает строки абзаца пробелами, сохраняя порядок и уверенность слов."""
    groups: dict[tuple[int, int], list[OcrWord]] = {}
    for line in lines:
        groups.setdefault((line.block_num, line.par_num), []).extend(line.words)
    return [OcrParagraph(" ".join(word.text for word in words), words)
            for words in groups.values()]


def tesseract_version() -> str:
    """Возвращает номер версии без диагностического вывода программы."""
    return str(pytesseract.get_tesseract_version()).splitlines()[0].split()[0]


def ocr_available() -> bool:
    """Отсутствие системной программы не ломает обработку текстовых документов."""
    try:
        tesseract_version()
    except TesseractNotFoundError:
        return False
    return True


@contextmanager
def ocr_progress(callback: ProgressCallback) -> Iterator[None]:
    """Передаёт прогресс воркеру, сохраняя публичный контракт extract()."""
    token = _progress.set(callback)
    try:
        yield
    finally:
        _progress.reset(token)


def check_page_limit(total: int) -> None:
    """Проверяет общий лимит полных страниц и отдельных областей до OCR."""
    limit = get_settings().ocr_max_pages
    if total > limit:
        raise ExtractionError(f"Скан больше {limit} страниц — разделите файл", case="ocr_limit")


@dataclass
class OcrResult:
    """Внутренние метаданные парсера, не меняющие Pydantic-контракт."""

    page_count: int = 0
    pages: dict[int, list[OcrWord]] = field(default_factory=dict)
    segments: dict[int, list[OcrWord]] = field(default_factory=dict)
    regions_processed: int = 0

    def add_page(self, page: int, paragraphs: list[OcrParagraph], total: int) -> None:
        self.pages.setdefault(page, []).extend(
            word for paragraph in paragraphs for word in paragraph.words
        )
        self.regions_processed += 1
        if callback := _progress.get():
            callback(self.regions_processed, total)

    def info(self, segments: list[TextSegment]) -> OcrInfo | None:
        """Считает средние по словам и смещения целых слов в полном тексте."""
        if not self.pages:
            return None
        settings = get_settings()
        pages = [OcrPage(page=page, confidence=_confidence(words))
                 for page, words in self.pages.items()]
        spans: list[tuple[int, int]] = []
        for segment in segments:
            offset = segment.offset
            for word in self.segments.get(segment.id, []):
                if word.conf < settings.ocr_low_word_conf:
                    spans.append((offset, offset + len(word.text)))
                offset += len(word.text) + 1
        return OcrInfo(
            engine=f"tesseract-{tesseract_version()} {settings.ocr_lang}", pages=pages,
            mean_confidence=_confidence([word for words in self.pages.values() for word in words]),
            low_confidence_pages=[
                p.page for p in pages if p.confidence < settings.ocr_low_page_conf
            ],
            low_confidence_spans=spans,
        )


def _confidence(words: list[OcrWord]) -> float:
    return sum(word.conf for word in words) / len(words) if words else 0.0
