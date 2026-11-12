"""Обезличенная копия сканов и изображений: закраска пикселей (T-072).

Только выдуманные данные; LLM_PROVIDER=mock. Тесты с OCR требуют tesseract (rus) и шрифт
DejaVuSans — в образе api и в CI они есть; без них такие тесты пропускаются.
"""

import logging
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence
from PIL.PngImagePlugin import PngInfo

from app.schemas import (
    Entity,
    EntityCategory,
    EntityType,
    ExtractedDocument,
    OcrInfo,
    OcrPage,
    TextSegment,
)
from app.services.detection.detect import detect
from app.services.extraction.extract import extract
from app.services.extraction.ocr import OcrLine, OcrWord, ocr_available
from app.services.redaction import redact as redact_module
from app.services.redaction.check import (
    copy_texts,
    count_leftovers,
    image_hidden_texts,
    ocr_leaked_values,
    value_tokens,
)
from app.services.redaction.ocr_image import PADDING, find_hits, redact_image
from app.services.redaction.redact import MEDIA_TYPES, redact, scan_copy_allowed
from app.services.redaction.values import PdValue
from app.worker import tasks

FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
NAME = "Тихонов Арсений Валерьевич"
PHONE = "+7 (900) 000-00-01"
OTHER_NAME = "Кузнецова Дарья Олеговна"
OTHER_PHONE = "+7 (900) 000-00-02"
CLEAN_LINES = ("Техническое задание на поставку", "Срок поставки: 30 дней.")
DEMO_TYPES = {EntityType.PERSON, EntityType.PHONE, EntityType.SNILS}

needs_ocr = pytest.mark.skipif(
    not (ocr_available() and FONT_PATH.is_file()),
    reason="нужны tesseract-ocr-rus и fonts-dejavu-core (есть в образе api и CI)",
)


# --- вспомогательное -----------------------------------------------------------------------

def text_image(lines: tuple[str, ...], size: tuple[int, int] = (1700, 900)) -> Image.Image:
    """Белая «страница» с крупным текстом — хорошо распознаётся OCR."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT_PATH), size=40)
    for index, line in enumerate(lines):
        draw.text((80, 80 + index * 90), line, fill="black", font=font)
    return image


def entities_for(doc: ExtractedDocument, values: dict[str, EntityType]) -> list[Entity]:
    """Сущности по всем вхождениям значений в тексте — без зависимости от детектора."""
    entities: list[Entity] = []
    for value, entity_type in values.items():
        start = doc.text.find(value)
        while start != -1:
            entities.append(Entity(
                type=entity_type, category=EntityCategory.pd, start=start,
                end=start + len(value), value_masked="***", confidence=1.0, detector="regex",
            ))
            start = doc.text.find(value, start + 1)
    return entities


def save(image: Image.Image, path: Path, **params: object) -> Path:
    image.save(path, **params)
    return path


def first_page(content: bytes) -> Image.Image:
    """Первая страница PDF (72 dpi) или первый кадр изображения."""
    if content.startswith(b"%PDF"):
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            pixmap = pdf[0].get_pixmap(dpi=72)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    with Image.open(BytesIO(content)) as image:
        return image.convert("RGB")


def black_ratio(image: Image.Image) -> float:
    gray = image.convert("L")
    return sum(gray.histogram()[:20]) / (gray.width * gray.height)


# --- поиск плашек: без tesseract -----------------------------------------------------------

def line(words: list[tuple[str, int]], top: int = 100) -> OcrLine:
    """Строка OCR: слова с left, ширина = 20 px на символ, высота 30 px."""
    items = [OcrWord(text, left, top, 20 * len(text), 30, 0.95) for text, left in words]
    return OcrLine(" ".join(word.text for word in items), items)


def test_find_hits_covers_touched_words_only() -> None:
    ocr_line = line([("Контакт:", 0), ("Тихонов", 200), ("Арсений", 400), ("Валерьевич,", 600),
                     ("отдел", 900)])
    hits = find_hits([ocr_line], [PdValue(NAME, "[ФИО]")])
    assert len(hits) == 1
    left, top, right, bottom = hits[0].box
    assert (left, top) == (200 - PADDING, 100 - PADDING)
    # «Валерьевич,» задет — закрашен целиком, «отдел» — нет.
    assert right == 600 + 20 * len("Валерьевич,") + PADDING < 900
    assert bottom == 130 + PADDING and hits[0].label == "[ФИО]"


def test_find_hits_value_split_between_lines() -> None:
    """OCR разбил ФИО на две строки: по плашке на каждую строку, середина не закрашена."""
    first = line([("Работник:", 0), ("Тихонов", 300)], top=100)
    second = line([("Арсений", 0), ("Валерьевич", 200), ("принят.", 500)], top=200)
    hits = find_hits([first, second], [PdValue(NAME, "[ФИО]")])
    assert sorted(hit.box[1] for hit in hits) == [100 - PADDING, 200 - PADDING]
    assert all(hit.box[2] < 500 for hit in hits if hit.box[1] == 200 - PADDING)
    assert all(hit.box[0] >= 300 - PADDING for hit in hits if hit.box[1] == 100 - PADDING)


def test_find_hits_short_value_whole_word_only() -> None:
    """Короткое значение («Ли») — только целым словом, не внутри «Лист»."""
    ocr_line = line([("Лист", 0), ("Ли", 200)])
    hits = find_hits([ocr_line], [PdValue("Ли", "[ФИО]")])
    assert [hit.box[0] for hit in hits] == [200 - PADDING]


def test_find_hits_nothing_without_values() -> None:
    assert find_hits([line([("Тихонов", 0)])], []) == []


# --- самопроверка: без tesseract -----------------------------------------------------------

def ocr_doc(text: str) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="doc_t072", filename="scan.png", text=text,
        segments=[TextSegment(id=0, text=text, offset=0, page=1)], pages=1,
        ocr=OcrInfo(engine="tesseract test", pages=[OcrPage(page=1, confidence=0.9)],
                    mean_confidence=0.9),
    )


def test_value_tokens_skip_short_and_punctuation() -> None:
    assert value_tokens(PdValue("г. Москва, ул. Лесная, д. 5", "[АДРЕС]")) == ["Москва", "Лесная"]
    assert value_tokens(PdValue(PHONE, "[ТЕЛЕФОН]")) == ["000-00-01"]


def test_ocr_leak_part_of_value_is_caught() -> None:
    """OCR прочитал фамилию из-под плашки — целого значения нет, но копию не отдаём."""
    doc = ocr_doc(f"Работник: {NAME}.")
    values = [PdValue(NAME, "[ФИО]")]
    assert ocr_leaked_values(["Работник: [ФИО] Тихонов"], doc, values) == values
    assert ocr_leaked_values(["Работник: [ФИО] Тих"], doc, values) == []  # < 4 символов


def test_ocr_leak_ignores_legit_repeats_outside_pd() -> None:
    """«Москва» есть и в шапке вне ПДн: столько же раз в копии — не утечка, больше — утечка."""
    doc = ocr_doc("ООО «Ромашка», г. Москва. Адрес работника: г. Москва, ул. Лесная, д. 5.")
    values = [PdValue("г. Москва, ул. Лесная, д. 5", "[АДРЕС]")]
    assert ocr_leaked_values(["ООО «Ромашка», г. Москва. Адрес работника: [АДРЕС]."],
                             doc, values) == []
    assert ocr_leaked_values(["г. Москва. Адрес работника: [АДРЕС] Москва"], doc, values) == values


def test_scan_copy_allowed_by_page_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redact_module, "OCR_REDACT_MAX_PAGES", 2)
    doc = ocr_doc("текст")
    assert scan_copy_allowed(doc)
    big = doc.model_copy(update={"ocr": doc.ocr.model_copy(update={  # type: ignore[union-attr]
        "pages": [OcrPage(page=number, confidence=0.9) for number in range(1, 4)]})})
    assert not scan_copy_allowed(big)
    assert scan_copy_allowed(doc.model_copy(update={"ocr": None}))


def test_scan_over_limit_has_no_copy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    """Больше лимита страниц OCR — копию даже не начинаем делать, в логе только число страниц."""
    monkeypatch.setattr(redact_module, "OCR_REDACT_MAX_PAGES", 20)
    doc = ocr_doc(f"Работник: {NAME}.")
    doc = doc.model_copy(update={"ocr": doc.ocr.model_copy(update={  # type: ignore[union-attr]
        "pages": [OcrPage(page=number, confidence=0.9) for number in range(1, 22)]})})

    def must_not_run(*_args: object) -> None:
        raise AssertionError("redact() не должен вызываться")

    monkeypatch.setattr(tasks, "redact", must_not_run)
    with caplog.at_level(logging.INFO):
        result = tasks.make_redacted_copy(tmp_path / "scan.pdf", doc, [], "task_t072")
    assert result is None
    assert "копия скана пропущена: 21 страниц" in caplog.text
    assert NAME not in caplog.text


def test_media_types_for_images() -> None:
    assert MEDIA_TYPES["jpg"] == MEDIA_TYPES["jpeg"] == "image/jpeg"
    assert MEDIA_TYPES["png"] == "image/png" and MEDIA_TYPES["tiff"] == "image/tiff"


# --- с OCR ---------------------------------------------------------------------------------

@needs_ocr
@pytest.mark.parametrize("filename", ["11_scan_application.pdf", "12_scan_anketa.jpg"])
def test_demo_scans_copy_has_no_pd(repo_root: Path, filename: str) -> None:
    """Демо 11 и 12: копия есть, самопроверка чистая, OCR копии не видит ФИО, телефон, СНИЛС."""
    source = repo_root / "data" / "demo" / filename
    if not source.is_file():
        pytest.skip("data/demo недоступен (в образе api нет демо-файлов)")
    doc = extract(source, filename, "doc_demo")
    entities = detect(doc)
    redacted = redact(source, doc, entities)
    assert redacted.ext == source.suffix.lstrip(".")
    assert count_leftovers(redacted, doc, entities) == 0
    copy_text = " ".join(copy_texts(redacted, "doc_copy"))
    checked = [entity for entity in entities if entity.type in DEMO_TYPES]
    assert {entity.type for entity in checked} >= {EntityType.PERSON, EntityType.PHONE}
    for entity in checked:
        assert doc.text[entity.start:entity.end] not in copy_text
    # Плашки закрашены пикселями: чёрного на первой странице копии больше, чем в исходнике.
    assert black_ratio(first_page(redacted.content)) > black_ratio(first_page(source.read_bytes()))


@needs_ocr
def test_image_without_pd_is_unchanged(tmp_path: Path) -> None:
    """Картинка без ПДн: тот же размер, ни одной закраски (PNG — без потерь, пиксель в пиксель)."""
    source = save(text_image(CLEAN_LINES), tmp_path / "clean.png")
    doc = extract(source, source.name, "doc_clean")
    redacted = redact(source, doc, entities_for(doc, {}))
    with Image.open(source) as original, Image.open(BytesIO(redacted.content)) as copy:
        assert copy.size == original.size and copy.format == "PNG"
        assert ImageChops.difference(original.convert("RGB"), copy.convert("RGB")).getbbox() is None


@needs_ocr
def test_image_metadata_removed(tmp_path: Path) -> None:
    """EXIF JPEG и текстовые чанки PNG с ФИО в копию не попадают."""
    image = text_image((f"Работник: {NAME}", CLEAN_LINES[1]))
    exif = Image.Exif()
    exif[0x013B] = NAME            # Artist
    exif[0x010E] = f"Анкета {NAME}"  # ImageDescription
    jpeg = save(image, tmp_path / "anketa.jpg", exif=exif.tobytes(), quality=95)
    info = PngInfo()
    info.add_text("Author", NAME)
    png = save(image, tmp_path / "anketa.png", pnginfo=info)
    for source in (jpeg, png):
        doc = extract(source, source.name, "doc_meta")
        entities = entities_for(doc, {NAME: EntityType.PERSON})
        redacted = redact(source, doc, entities)
        with Image.open(BytesIO(redacted.content)) as copy:
            assert len(copy.getexif()) == 0
            assert "Author" not in copy.info and "exif" not in copy.info
        assert all(NAME not in text for text in image_hidden_texts(redacted.content))
        assert count_leftovers(redacted, doc, entities) == 0


@needs_ocr
def test_exif_rotated_jpeg_boxes_match_rotation(tmp_path: Path) -> None:
    """JPEG, повёрнутый тегом EXIF: плашки на месте ФИО в повёрнутой картинке, тега в копии нет."""
    upright = text_image((f"Работник: {NAME}", CLEAN_LINES[1]))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: повернуть на 90° по часовой при показе
    source = save(upright.transpose(Image.Transpose.ROTATE_90), tmp_path / "rot.jpg",
                  exif=exif.tobytes(), quality=95)
    doc = extract(source, source.name, "doc_rot")
    entities = entities_for(doc, {NAME: EntityType.PERSON})
    assert entities, "OCR должен прочитать ФИО после поворота по EXIF"
    redacted = redact(source, doc, entities)
    with Image.open(BytesIO(redacted.content)) as copy:
        assert copy.size == upright.size and len(copy.getexif()) == 0
    assert count_leftovers(redacted, doc, entities) == 0


@needs_ocr
def test_multipage_tiff_all_frames_redacted(tmp_path: Path) -> None:
    frames = [text_image((f"Работник: {NAME}", CLEAN_LINES[0])),
              text_image((f"Телефон: {OTHER_PHONE}", f"Ответственный: {OTHER_NAME}"))]
    source = tmp_path / "scan.tiff"
    frames[0].save(source, save_all=True, append_images=frames[1:], compression="tiff_deflate")
    doc = extract(source, source.name, "doc_tiff")
    entities = entities_for(doc, {NAME: EntityType.PERSON, OTHER_NAME: EntityType.PERSON,
                                  OTHER_PHONE: EntityType.PHONE})
    assert len(entities) == 3
    redacted = redact(source, doc, entities)
    assert redacted.ext == "tiff"
    with Image.open(BytesIO(redacted.content)) as copy:
        copies = [frame.copy() for frame in ImageSequence.Iterator(copy)]
    assert len(copies) == 2
    assert all(black_ratio(copy) > black_ratio(frame) for copy, frame in zip(copies, frames,
                                                                             strict=True))
    assert count_leftovers(redacted, doc, entities) == 0


@needs_ocr
def test_mixed_pdf_text_page_redacted_scan_page_rasterized(tmp_path: Path) -> None:
    """Смешанный PDF: страница с текстовым слоем — прежней редакцией, скан-страница — пикселями."""
    pdf = pymupdf.open()
    text_page = pdf.new_page()
    text_page.insert_text((72, 100), f"Контактное лицо: {OTHER_NAME}", fontname="dejavu",
                          fontfile=str(FONT_PATH), fontsize=12)
    text_page.insert_text((72, 130), "Раздел с текстовым слоем.", fontname="dejavu",
                          fontfile=str(FONT_PATH), fontsize=12)
    scan = text_image((f"Работник: {NAME}", f"Телефон: {PHONE}"), size=(1240, 1754))
    buffer = BytesIO()
    scan.save(buffer, "PNG")
    scan_page = pdf.new_page()
    scan_page.insert_image(scan_page.rect, stream=buffer.getvalue())
    pdf.set_metadata({"author": NAME, "title": f"Анкета {NAME}"})
    source = tmp_path / "mixed.pdf"
    pdf.save(source)

    doc = extract(source, source.name, "doc_mixed")
    assert doc.ocr is not None and [page.page for page in doc.ocr.pages] == [2]
    entities = entities_for(doc, {NAME: EntityType.PERSON, OTHER_NAME: EntityType.PERSON,
                                  PHONE: EntityType.PHONE})
    redacted = redact(source, doc, entities)
    assert count_leftovers(redacted, doc, entities) == 0
    with pymupdf.open(stream=redacted.content, filetype="pdf") as copy:
        first, second = copy[0], copy[1]
        assert "Раздел с текстовым слоем." in first.get_text()   # текст остался текстом
        assert "[ФИО]" in first.get_text() and OTHER_NAME not in first.get_text()
        assert first.get_images() == []
        assert second.get_text().strip() == "" and len(second.get_images()) == 1
        assert NAME not in str(copy.metadata)


@needs_ocr
def test_failed_painting_rejects_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Если закраска не сработала, самопроверка находит ФИО — копию не отдаём."""
    from app.services.redaction import ocr_image as ocr_image_module

    source = save(text_image((f"Работник: {NAME}",)), tmp_path / "anketa.png")
    doc = extract(source, source.name, "doc_fail")
    entities = entities_for(doc, {NAME: EntityType.PERSON})
    monkeypatch.setattr(ocr_image_module, "redact_image",
                        lambda image, _values: image.copy())
    redacted = redact(source, doc, entities)
    assert count_leftovers(redacted, doc, entities) > 0
    assert tasks.make_redacted_copy(source, doc, entities, "task_fail") is None


@needs_ocr
def test_redact_image_paints_pixels_not_overlay() -> None:
    """Под плашкой исходных пикселей не остаётся: внутри рамки — только чёрный и белая метка."""
    image = text_image((f"Работник: {NAME}",))
    result = redact_image(image, [PdValue(NAME, "[ФИО]")])
    assert result.size == image.size and result is not image
    assert black_ratio(result) > black_ratio(image) * 3
