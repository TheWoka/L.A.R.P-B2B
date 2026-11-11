"""Корпус «сканов» для оценки полноты после OCR. Задача T-077b.

Два независимых шага, оба без участия детектора:

1. `render_scan_pages` / `scan_pdf_bytes` — чистый текст документа рисуется на страницах А4 и
   портится как скан (поворот, шум, JPEG); PDF собирается только из картинок.
2. `transfer_gold` — эталон из координат чистого текста переносится в координаты текста,
   который вернул OCR. Выравнивание посимвольное (difflib), зависит только от двух текстов.

Сущность, которую OCR потерял целиком, остаётся в эталоне с нулевой длиной и `lost=True`:
её нельзя найти, и это честный пропуск, а не повод выкинуть её из знаменателя recall.
"""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from random import Random

from app.evaluation.markup import GoldEntity

DEFAULT_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
A4_MM = (210.0, 297.0)
MARGIN_MM = 20.0
FONT_PT = 11.0
LINE_SPACING = 1.35
NOISE_SIGMA = 6.0
MAX_ANGLE = 1.5
JPEG_QUALITY = 85


# ---------- Выравнивание и перенос эталона ----------


def char_map(clean: str, ocr: str) -> list[int | None]:
    """Для каждого символа чистого текста — позиция в OCR-тексте или None (символ выпал).

    `equal` — один к одному; `replace` (OCR прочитал иначе: «О» → «0», «ш» → «щ») —
    пропорционально внутри блока, символ считается сопоставленным; `delete` — None;
    `insert` (мусор OCR) чистому тексту не соответствует. Отображение не убывает.
    """
    mapping: list[int | None] = [None] * len(clean)
    matcher = SequenceMatcher(None, clean, ocr, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
        elif tag == "replace":
            n, m = i2 - i1, j2 - j1
            for k in range(n):
                mapping[i1 + k] = j1 + k * m // n
    return mapping


def _anchor(mapping: Sequence[int | None], pos: int, ocr_len: int) -> int:
    """Позиция в OCR для первого сопоставленного символа начиная с pos (иначе конец текста)."""
    for k in range(pos, len(mapping)):
        mapped = mapping[k]
        if mapped is not None:
            return mapped
    return ocr_len


def transfer_gold(gold: Sequence[GoldEntity], clean: str, ocr: str) -> list[GoldEntity]:
    """Переносит эталон из чистого текста в OCR-текст.

    start — позиция первого сопоставленного символа сущности, end — последнего + 1.
    Нет ни одного сопоставленного символа → нулевой отрезок там, где сущность была бы
    (перед следующим сопоставленным символом), и lost=True. Порядок сущностей сохраняется.
    """
    mapping = char_map(clean, ocr)
    result: list[GoldEntity] = []
    for g in gold:
        mapped = [p for p in mapping[g.start:g.end] if p is not None]
        if mapped:
            start, end = mapped[0], mapped[-1] + 1
            result.append(GoldEntity(g.type, g.category, start, end))
        else:
            at = _anchor(mapping, g.end, len(ocr))
            result.append(GoldEntity(g.type, g.category, at, at, lost=True))
    return result


def ocr_similarity(clean: str, ocr: str) -> float:
    """Доля совпавших символов (SequenceMatcher.ratio) — грубая оценка качества OCR."""
    return SequenceMatcher(None, clean, ocr, autojunk=False).ratio()


# ---------- Отрисовка «скана» ----------


def _px(mm: float, dpi: int) -> int:
    return round(mm / 25.4 * dpi)


def _wrap(text: str, font: object, width: int) -> list[str]:
    """Перенос по словам; слово шире строки режется по символам. Пустая строка сохраняется."""
    length = font.getlength  # type: ignore[attr-defined]
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if length(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = ""
            for ch in word:
                if length(current + ch) > width and current:
                    lines.append(current)
                    current = ""
                current += ch
        lines.append(current)
    return lines


def render_scan_pages(
    text: str, *, seed: int | str, dpi: int = 200, font_path: Path = DEFAULT_FONT,
) -> list[bytes]:
    """Текст → JPEG-страницы А4 «как со сканера». Одинаковый seed — одинаковые байты.

    DejaVuSans 11 pt, поля 2 см, перенос по словам; затем поворот ±1,5° (Random(seed)),
    гауссов шум sigma 6 и JPEG 85.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    if not font_path.is_file():
        raise FileNotFoundError(f"Нет шрифта {font_path} (пакет fonts-dejavu-core)")
    font = ImageFont.truetype(str(font_path), size=round(FONT_PT * dpi / 72))
    width, height = _px(A4_MM[0], dpi), _px(A4_MM[1], dpi)
    margin = _px(MARGIN_MM, dpi)
    line_height = round(font.size * LINE_SPACING)
    per_page = max(1, (height - 2 * margin) // line_height)
    lines = _wrap(text, font, width - 2 * margin)
    rng = Random(f"scan:{seed}")
    pages: list[bytes] = []
    for first in range(0, len(lines), per_page):
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        for n, line in enumerate(lines[first:first + per_page]):
            draw.text((margin, margin + n * line_height), line, font=font, fill=20)
        angle = rng.uniform(-MAX_ANGLE, MAX_ANGLE)
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
        noise = np.random.default_rng(rng.getrandbits(64)).normal(0.0, NOISE_SIGMA, (height, width))
        pixels = np.clip(np.asarray(image, dtype=np.float64) + noise, 0, 255).astype(np.uint8)
        out = BytesIO()
        Image.fromarray(pixels, mode="L").save(out, format="JPEG", quality=JPEG_QUALITY)
        pages.append(out.getvalue())
    return pages


def scan_pdf_bytes(pages: Sequence[bytes]) -> bytes:
    """JPEG-страницы → PDF только из картинок (без текстового слоя), страницы А4."""
    import pymupdf

    width, height = (mm / 25.4 * 72 for mm in A4_MM)
    with pymupdf.open() as document:
        for jpeg in pages:
            page = document.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=jpeg)
        document.set_metadata({})
        return document.tobytes(garbage=3, deflate=True, no_new_id=True)
