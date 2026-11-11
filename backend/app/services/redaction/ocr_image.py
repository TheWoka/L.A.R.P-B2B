"""Обезличивание сканов и изображений: закраска пикселей под ПДн. Задача T-072.

Внутренний модуль пакета redaction — не контракт (раздел 4.1: «пиксели под ПДн закрашиваются»).

Плашка поверх картинки в PDF не годится: исходное изображение под ней остаётся в файле
целиком. Поэтому меняются сами пиксели: страница перерисовывается, а в копию кладётся
только новая картинка. Где ПДн на картинке, узнаём тем же детерминированным OCR, что
и при извлечении: та же картинка и тот же dpi дают тот же текст, что видел детектор.
Значения ПДн и распознанный текст не логируются (AGENTS.md, правило 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

from app.services.extraction.ocr import OcrLine, OcrWord, ocr_image
from app.services.redaction.docx import match_spans
from app.services.redaction.values import PdValue

logger = logging.getLogger(__name__)

PADDING = 3                 # px вокруг объединения рамок слов
MIN_LABEL_SIZE = 8          # px; меньше — метку не пишем, остаётся чёрная плашка
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")  # fonts-dejavu-core
JPEG_QUALITY = 90
IMAGE_FORMATS: dict[str, str] = {
    "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF",
}
_KEEP_MODES = ("L", "RGB")

Box = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass(frozen=True)
class Hit:
    """Плашка: прямоугольник в пикселях и метка значения."""

    box: Box
    label: str


def find_hits(lines: list[OcrLine], values: list[PdValue]) -> list[Hit]:
    """Плашки для всех вхождений ПДн в строках OCR.

    Вхождение ищется в склеенном тексте строки (слова через пробел, как в ocr_image),
    закрашиваются все слова, которые оно задевает. Значение, которое OCR разбил на две
    строки, ищется ещё в склейке соседних строк — по плашке на каждую строку.
    """
    hits: list[Hit] = []
    for index, line in enumerate(lines):
        hits.extend(_line_hits([line], values, cross_only=False))
        if index + 1 < len(lines):
            hits.extend(_line_hits([line, lines[index + 1]], values, cross_only=True))
    return hits


def _line_hits(lines: list[OcrLine], values: list[PdValue], *, cross_only: bool) -> list[Hit]:
    """Вхождения в склейке строк; cross_only — только пересекающие границу строк."""
    words: list[tuple[int, int, int, OcrWord]] = []  # (номер строки, start, end, слово)
    position = 0
    for line_number, line in enumerate(lines):
        for word in line.words:
            words.append((line_number, position, position + len(word.text), word))
            position += len(word.text) + 1
    text = " ".join(word.text for _, _, _, word in words)
    boundary = sum(len(word.text) + 1 for word in lines[0].words) - 1
    hits: list[Hit] = []
    for start, end, label in match_spans(text, values):
        if cross_only and not (start < boundary < end):
            continue
        touched = [(number, word) for number, w_start, w_end, word in words
                   if w_start < end and w_end > start]
        for line_number in sorted({number for number, _ in touched}):
            boxes = [_word_box(word) for number, word in touched if number == line_number]
            hits.append(Hit(_union(boxes), label))
    return hits


def _word_box(word: OcrWord) -> Box:
    return word.left, word.top, word.left + word.width, word.top + word.height


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes) - PADDING, min(b[1] for b in boxes) - PADDING,
            max(b[2] for b in boxes) + PADDING, max(b[3] for b in boxes) + PADDING)


def redact_image(image: Image.Image, values: list[PdValue]) -> Image.Image:
    """Копия изображения, где пиксели под значениями ПДн закрашены чёрным с белой меткой.

    Ориентация из EXIF применяется заранее: координаты OCR — в повёрнутом изображении.
    Метаданные (EXIF, ICC, текстовые чанки) в копию не переносятся.
    """
    # OCR — по тому же изображению, что при извлечении: ocr_image сам повернёт его по EXIF.
    hits = find_hits(ocr_image(image), values) if values else []
    result = ImageOps.exif_transpose(image)
    result = result.convert("RGB") if result.mode not in _KEEP_MODES else result.copy()
    result.info = {}
    draw = ImageDraw.Draw(result)
    for hit in hits:
        box = _clip(hit.box, result.size)
        draw.rectangle(box, fill="black")
    for hit in _unique_labels(hits):
        _draw_label(draw, _clip(hit.box, result.size), hit.label)
    return result


def _clip(box: Box, size: tuple[int, int]) -> Box:
    return (max(box[0], 0), max(box[1], 0), min(box[2], size[0] - 1), min(box[3], size[1] - 1))


def _unique_labels(hits: list[Hit]) -> list[Hit]:
    """Одна метка на место: «Иванов» внутри уже закрашенного «Иванов Иван» не дублируем."""
    result: list[Hit] = []
    for hit in hits:
        if not any(_intersects(hit.box, other.box) for other in result):
            result.append(hit)
    return result


def _intersects(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _draw_label(draw: ImageDraw.ImageDraw, box: Box, label: str) -> None:
    """Белая метка внутри плашки; кегль подбирается по высоте и ширине плашки."""
    width, height = box[2] - box[0], box[3] - box[1]
    size = int(height * 0.7)
    font = _font(size) if size >= MIN_LABEL_SIZE else None
    while font is not None and font.getlength(label) > width - 4:
        size = int(size * 0.9)
        font = _font(size) if size >= MIN_LABEL_SIZE else None
    if font is None:
        return
    draw.text((box[0] + 2, box[1] + height / 2), label, fill="white", font=font, anchor="lm")


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | None:
    """DejaVuSans (кириллица); нет шрифта — плашка без метки, ПДн всё равно закрашены."""
    try:
        return ImageFont.truetype(str(FONT_PATH), size=size)
    except OSError:
        logger.warning("Шрифт для меток обезличенной копии не найден")
        return None


def redact_image_file(source: Path, values: list[PdValue]) -> bytes:
    """Байты копии файла-изображения в том же формате; многостраничный TIFF — все кадры."""
    image_format = IMAGE_FORMATS[source.suffix.lower().lstrip(".")]
    with Image.open(source) as image:
        frames = [redact_image(frame.copy(), values) for frame in ImageSequence.Iterator(image)]
    return encode_frames(frames, image_format)


def encode_frames(frames: list[Image.Image], image_format: str) -> bytes:
    """Сохраняет кадры без метаданных: без exif=, icc_profile=, pnginfo= и тегов TIFF."""
    buffer = BytesIO()
    first = frames[0]
    if image_format == "JPEG":
        first.save(buffer, "JPEG", quality=JPEG_QUALITY)
    elif image_format == "PNG":
        first.save(buffer, "PNG", optimize=True)
    else:
        first.save(buffer, "TIFF", save_all=True, append_images=frames[1:],
                   compression="tiff_deflate")
    return buffer.getvalue()


def render_page(page: pymupdf.Page, dpi: int) -> Image.Image:
    """Страница PDF как изображение — так же, как её видит OCR при извлечении."""
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def image_page_bytes(image: Image.Image) -> bytes:
    """Картинка для новой страницы PDF: JPEG — в разы меньше PNG на 300 dpi."""
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
