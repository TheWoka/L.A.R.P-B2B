"""OCR на сгенерированных изображениях: все имена и номера выдуманы."""

import logging
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont
from pytesseract import TesseractNotFoundError

from app.core.config import Settings
from app.schemas import EntityType
from app.services.detection.detect import detect
from app.services.extraction import image as image_parser
from app.services.extraction import ocr, pdf
from app.services.extraction.extract import extract
from app.services.extraction.ocr import OcrLine, OcrWord, ocr_paragraphs, ocr_progress
from tests.test_extraction import assert_offsets

CONTACT = "Контактное лицо: Тихонов Арсений Валерьевич, тел. +7 (900) 000-00-01"


@pytest.fixture
def contact_image() -> Image.Image:
    font = ImageFont.truetype("DejaVuSans.ttf", 28)
    image = Image.new("RGB", (1300, 180), "white")
    ImageDraw.Draw(image).text((30, 50), CONTACT, font=font, fill="black")
    return image


def image_pdf(path: Path, image: Image.Image, count: int, *, mixed: bool = False) -> None:
    stream = BytesIO()
    image.save(stream, format="PNG")
    with pymupdf.open() as document:
        if mixed:
            document.new_page().insert_text((30, 50), "1. General terms")
        for _ in range(count):
            page = document.new_page(width=650, height=90)
            page.insert_image(page.rect, stream=stream.getvalue())
        document.save(path)


def mixed_image_pdf(path: Path, image: Image.Image, *, hidden_text: str | None = None) -> None:
    """Текстовый колонтитул и скан на одной странице; данные только синтетические."""
    stream = BytesIO()
    image.save(stream, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=650, height=300)
        font = ImageFont.truetype("DejaVuSans.ttf", 28)
        page.insert_font(fontname="dejavu", fontfile=font.path)
        page.insert_text((20, 30), "Стр. 1", fontname="dejavu")
        page.insert_image((0, 80, 650, 170), stream=stream.getvalue())
        if hidden_text is not None:
            page.insert_text((15, 110), hidden_text, fontname="dejavu", fontsize=10, render_mode=3)
        document.save(path)


def test_mixed_page_detects_pd_in_image(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mixed-page.pdf"
    mixed_image_pdf(path, contact_image)
    recognize = Mock(wraps=pdf.ocr_image)
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.text.startswith("Стр. 1\n")
    assert doc.ocr is not None and [page.page for page in doc.ocr.pages] == [1]
    assert {entity.type for entity in detect(doc)} >= {EntityType.PERSON, EntityType.PHONE}
    assert {segment.section for segment in doc.segments[1:]} == {"Распознанное изображение 1"}
    recognize.assert_called_once()
    assert_offsets(doc)


def test_hidden_text_layer_skips_ocr(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "hidden-text.pdf"
    mixed_image_pdf(path, contact_image, hidden_text=CONTACT)
    recognize = Mock(side_effect=AssertionError("Text layer already exists"))
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is None
    assert doc.text.count("Тихонов") == 1 and doc.text.count("000-00-01") == 1
    assert {entity.type for entity in detect(doc)} >= {EntityType.PERSON, EntityType.PHONE}
    recognize.assert_not_called()


@pytest.mark.parametrize(("rect", "dpi", "expected"), [
    ((20, 50, 70, 100), 300, False),  # Маленький логотип.
    ((0, 50, 200, 350), 300, False),  # 14.9% страницы.
    ((0, 50, 210, 350), 300, True),   # Ровно 15%.
    ((0, 50, 210, 350), 72, False),   # Площадь достаточна, но ширина < 300 px.
    ((0, 50, 300, 260), 72, False),   # Высота < 300 px.
    ((0, 50, 300, 350), 72, True),    # Ровно 300 px по каждой стороне.
])
def test_image_region_size_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rect: tuple[int, int, int, int],
    dpi: int, expected: bool,
) -> None:
    path = tmp_path / "size.pdf"
    with pymupdf.open() as source:
        page = source.new_page(width=700, height=600)
        page.insert_text((20, 30), "Page 1")
        stream = BytesIO()
        Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
        page.insert_image(rect, stream=stream.getvalue(), keep_proportion=False)
        source.save(path)
    monkeypatch.setattr(pdf, "get_settings", lambda: Settings(_env_file=None, ocr_dpi=dpi))
    recognize = Mock(return_value=[make_line(("Синтетический", 0.9))])
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    doc = extract(path, path.name, "synthetic-ocr")
    assert (doc.ocr is not None) is expected
    assert recognize.call_count == int(expected)


@pytest.mark.parametrize("characters", [49, 50])
def test_image_text_layer_threshold(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch, characters: int,
) -> None:
    path = tmp_path / "threshold.pdf"
    mixed_image_pdf(path, contact_image, hidden_text="A" * characters)
    recognize = Mock(return_value=[make_line(("Синтетический", 0.9))])
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    doc = extract(path, path.name, "synthetic-ocr")
    assert recognize.call_count == int(characters < 50)
    assert (doc.ocr is None) is (characters >= 50)


def multi_region_pdf(path: Path, *, full_scan: bool = False) -> None:
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    with pymupdf.open() as source:
        page = source.new_page(width=600, height=600)
        page.insert_text((10, 20), "Page 1")
        for index in range(6):
            left, top = index % 2 * 300, 30 + index // 2 * 180
            page.insert_image(
                (left, top, left + 300, top + 180), stream=stream.getvalue(), keep_proportion=False,
            )
        if full_scan:
            source.new_page(width=100, height=100)
        source.save(path)


def test_four_regions_per_page_confidence_and_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "regions.pdf"
    multi_region_pdf(path)
    recognize = Mock(side_effect=[
        [make_line(("Первое", 0.2), ("слово", 0.4))],
        [make_line(("Второе", 1.0))],
        [make_line(("Третье", 0.8))],
        [make_line(("Четвёртое", 0.6))],
    ])
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    states: list[tuple[int, int]] = []
    with ocr_progress(lambda done, total: states.append((done, total))):
        doc = extract(path, path.name, "synthetic-ocr")
    assert recognize.call_count == 4
    assert states == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert doc.ocr is not None and len(doc.ocr.pages) == 1
    assert doc.ocr.pages[0].confidence == pytest.approx(0.6)
    assert doc.ocr.mean_confidence == pytest.approx(0.6)
    assert [segment.section for segment in doc.segments[1:]] == [
        f"Распознанное изображение {index}" for index in range(1, 5)
    ]
    assert [doc.text[start:end] for start, end in doc.ocr.low_confidence_spans] == [
        "Первое", "слово",
    ]
    assert_offsets(doc)


@pytest.mark.parametrize("full_scan", [False, True])
def test_regions_count_in_global_limit_before_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full_scan: bool,
) -> None:
    path = tmp_path / "region-limit.pdf"
    multi_region_pdf(path, full_scan=full_scan)
    settings = Settings(_env_file=None, ocr_max_pages=4 if full_scan else 3)
    monkeypatch.setattr(ocr, "get_settings", lambda: settings)
    recognize = Mock()
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    with pytest.raises(ValueError, match="разделите файл"):
        extract(path, path.name, "synthetic-ocr")
    recognize.assert_not_called()


@pytest.mark.parametrize("extension", ["pdf", "png"])
def test_paragraphs_join_lines_without_losing_word_offsets(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch, extension: str,
) -> None:
    path = tmp_path / f"paragraph.{extension}"
    if extension == "pdf":
        image_pdf(path, contact_image, 1)
        module = pdf
    else:
        contact_image.save(path)
        module = image_parser
    first = OcrLine("Себестоимость изделия", make_line(
        ("Себестоимость", 0.9), ("изделия", 0.8),
    ).words, 1, 1)
    second = OcrLine("38 400 руб.", make_line(
        ("38", 0.2), ("400", 0.9), ("руб.", 0.9),
    ).words, 1, 1)
    third = OcrLine("Контакты", make_line(("Контакты", 0.7)).words, 1, 2)
    monkeypatch.setattr(module, "ocr_image", lambda image: [first, second, third])
    doc = extract(path, path.name, "synthetic-ocr")
    assert [segment.text for segment in doc.segments] == [
        "Себестоимость изделия 38 400 руб.", "Контакты",
    ]
    assert EntityType.TRADE_SECRET in {entity.type for entity in detect(doc)}
    assert doc.ocr is not None
    assert [doc.text[start:end] for start, end in doc.ocr.low_confidence_spans] == ["38"]
    assert_offsets(doc)


def test_empty_paragraphs() -> None:
    assert ocr_paragraphs([]) == []


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_regions_use_rendered_clip_with_rotation_and_mask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rotation: int,
) -> None:
    path = tmp_path / "rendered.pdf"
    image = Image.new("RGBA", (1300, 180), (255, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (30, 50), CONTACT, font=ImageFont.truetype("DejaVuSans.ttf", 28), fill="black",
    )
    mixed_image_pdf(path, image)
    with pymupdf.open(path) as source:
        source[0].set_rotation(rotation)
        source.saveIncr()
    received: list[tuple[tuple[int, int], bytes]] = []

    def recognize(rendered: Image.Image) -> list[OcrLine]:
        received.append((rendered.size, rendered.tobytes()))
        return [make_line(("Синтетический", 0.9))]

    monkeypatch.setattr(pdf, "ocr_image", recognize)
    with pymupdf.open(path) as source:
        page = source[0]
        info = page.get_image_info(xrefs=True)[0]
        bbox = pymupdf.Rect(info["bbox"]) * page.rotation_matrix
        pixmap = page.get_pixmap(clip=bbox, dpi=300, colorspace=pymupdf.csRGB)
        expected = ((pixmap.width, pixmap.height), pixmap.samples)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is not None
    assert received == [expected]


def test_png_extract_detect_and_private_logs(
    tmp_path: Path, contact_image: Image.Image, caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "synthetic.png"
    contact_image.save(path)
    with caplog.at_level(logging.INFO):
        doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is not None and doc.pages == 1
    assert doc.ocr.engine.startswith("tesseract-") and doc.ocr.engine.endswith(" rus+eng")
    assert "Тихонов" in doc.text and "000-00-01" in doc.text
    assert {entity.type for entity in detect(doc)} >= {EntityType.PERSON, EntityType.PHONE}
    assert "Тихонов" not in caplog.text and "000-00-01" not in caplog.text
    assert_offsets(doc)


@pytest.mark.parametrize("mixed", [False, True])
def test_scan_and_mixed_pdf(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch, mixed: bool,
) -> None:
    path = tmp_path / "synthetic.pdf"
    image_pdf(path, contact_image, 1 if mixed else 2, mixed=mixed)
    recognize = Mock(wraps=pdf.ocr_image)
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    states: list[tuple[int, int]] = []
    with ocr_progress(lambda done, total: states.append((done, total))):
        doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is not None
    assert [page.page for page in doc.ocr.pages] == ([2] if mixed else [1, 2])
    assert recognize.call_count == (1 if mixed else 2)
    assert states == ([(1, 1)] if mixed else [(1, 2), (2, 2)])
    assert "Тихонов" in doc.text
    if mixed:
        assert doc.text.startswith("1. General terms\n")
        assert doc.segments[-1].section == "1. General terms"
    assert_offsets(doc)


def test_text_pdf_never_uses_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "text.pdf"
    with pymupdf.open() as source:
        source.new_page().insert_text((30, 50), "Original text.")
        source.save(path)
    recognize = Mock(side_effect=AssertionError("OCR called for text PDF"))
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    monkeypatch.setattr(pdf, "ocr_available", lambda: False)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.text == "Original text." and doc.ocr is None
    recognize.assert_not_called()


def test_jpeg_exif_rotation(tmp_path: Path, contact_image: Image.Image) -> None:
    path = tmp_path / "rotated.jpg"
    rotated = contact_image.transpose(Image.Transpose.ROTATE_90)
    exif = rotated.getexif()
    exif[274] = 6
    rotated.save(path, quality=95, exif=exif)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is not None and "Тихонов" in doc.text and "000-00-01" in doc.text


def test_multipage_tiff(tmp_path: Path, contact_image: Image.Image) -> None:
    path = tmp_path / "pages.tiff"
    contact_image.save(path, save_all=True, append_images=[contact_image])
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.pages == 2 and doc.ocr is not None
    assert [page.page for page in doc.ocr.pages] == [1, 2]
    assert {segment.page for segment in doc.segments} == {1, 2}


@pytest.mark.parametrize("content", [b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
def test_corrupt_image(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="^Не удалось открыть изображение$"):
        extract(path, path.name, "synthetic-ocr")


@pytest.mark.parametrize("extension", ["pdf", "tiff"])
def test_page_limit_before_ocr(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch, extension: str,
) -> None:
    path = tmp_path / f"limit.{extension}"
    settings = Settings(_env_file=None, ocr_max_pages=1)
    monkeypatch.setattr(ocr, "get_settings", lambda: settings)
    if extension == "pdf":
        image_pdf(path, contact_image, 2)
        module = pdf
    else:
        contact_image.save(path, save_all=True, append_images=[contact_image])
        module = image_parser
    recognize = Mock()
    monkeypatch.setattr(module, "ocr_image", recognize)
    with pytest.raises(ValueError, match="Скан больше 1 страниц — разделите файл"):
        extract(path, path.name, "synthetic-ocr")
    recognize.assert_not_called()


def make_line(*pairs: tuple[str, float]) -> OcrLine:
    words = [OcrWord(text, 0, 0, 10, 10, conf) for text, conf in pairs]
    return OcrLine(" ".join(word.text for word in words), words)


def test_confidence_and_full_text_offsets(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mixed.pdf"
    image_pdf(path, contact_image, 2, mixed=True)
    recognize = Mock(side_effect=[
        [make_line(("1. Заголовок", 0.9)), make_line(("Слово", 0.2), ("Слово", 0.6))],
        [make_line(("Последнее", 0.75))],
    ])
    monkeypatch.setattr(pdf, "ocr_image", recognize)
    doc = extract(path, path.name, "synthetic-ocr")
    assert doc.ocr is not None
    assert [page.confidence for page in doc.ocr.pages] == pytest.approx([1.7 / 3, 0.75])
    assert doc.ocr.mean_confidence == pytest.approx(2.45 / 4)
    assert doc.ocr.low_confidence_pages == [2]
    assert len(doc.ocr.low_confidence_spans) == 1
    start, end = doc.ocr.low_confidence_spans[0]
    assert doc.text[start:end] == "Слово"
    assert start == doc.text.index("Слово")
    assert_offsets(doc)


def test_ocr_grouping_and_preprocessing(
    monkeypatch: pytest.MonkeyPatch, contact_image: Image.Image,
) -> None:
    recognize = Mock(return_value={
        "text": ["", "Первое", "слово", "Второе", " ", "пропуск"],
        "conf": [-1, 90, 55, 0, 80, -1],
        "block_num": [1, 1, 1, 2, 2, 2], "par_num": [1] * 6,
        "line_num": [0, 1, 2, 1, 1, 1],
        "left": [0] * 6, "top": [0] * 6, "width": [10] * 6, "height": [10] * 6,
    })
    monkeypatch.setattr(ocr.pytesseract, "image_to_data", recognize)
    lines = ocr.ocr_image(contact_image)
    assert [line.text for line in lines] == ["Первое", "слово", "Второе"]
    assert [paragraph.text for paragraph in ocr_paragraphs(lines)] == ["Первое слово", "Второе"]
    assert [word.conf for line in lines for word in line.words] == [0.9, 0.55, 0.0]
    assert recognize.call_args.args[0].mode == "L"
    assert recognize.call_args.kwargs == {
        "lang": "rus+eng", "config": "--psm 3", "output_type": ocr.Output.DICT,
    }


@pytest.mark.parametrize("available", [False, True])
def test_ocr_availability(monkeypatch: pytest.MonkeyPatch, available: bool) -> None:
    version = Mock(
        return_value="5.3.0", side_effect=None if available else TesseractNotFoundError(),
    )
    monkeypatch.setattr(ocr.pytesseract, "get_tesseract_version", version)
    assert ocr.ocr_available() is available
    if available:
        assert ocr.tesseract_version() == "5.3.0"


@pytest.mark.parametrize("extension", ["pdf", "png"])
def test_unavailable_ocr(
    tmp_path: Path, contact_image: Image.Image, monkeypatch: pytest.MonkeyPatch, extension: str,
) -> None:
    path = tmp_path / f"unavailable.{extension}"
    if extension == "pdf":
        image_pdf(path, contact_image, 1)
        module = pdf
    else:
        contact_image.save(path)
        module = image_parser
    monkeypatch.setattr(module, "ocr_available", lambda: False)
    with pytest.raises(pdf.ScannedPdfError, match="недоступно"):
        extract(path, path.name, "synthetic-ocr")


def test_blank_image(tmp_path: Path) -> None:
    path = tmp_path / "blank.png"
    Image.new("RGB", (300, 100), "white").save(path)
    with pytest.raises(pdf.ScannedPdfError, match="Не удалось распознать"):
        extract(path, path.name, "synthetic-ocr")


def test_progress_context_resets_after_error() -> None:
    callback = Mock()
    with pytest.raises(RuntimeError), ocr_progress(callback):
        raise RuntimeError
    ocr.OcrResult().add_page(1, [], 1)
    callback.assert_not_called()
