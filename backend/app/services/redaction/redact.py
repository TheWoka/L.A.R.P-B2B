"""Обезличенная копия документа в исходном формате. Задача T-026.

Контракт: docs/contracts.md, раздел 4.1 «Обезличенная копия». ПДн (pd, pd_special)
удаляются физически и заменяются метками «[ФИО]», «[ТЕЛЕФОН]»…; коммерческая тайна
и финансовые данные не трогаются (решение 17.10).
"""

from dataclasses import dataclass
from pathlib import Path

from app.core.config import OCR_REDACT_MAX_PAGES, get_settings
from app.schemas import Entity, ExtractedDocument
from app.services.masking.masker import MASKED_CATEGORIES
from app.services.redaction.docx import redact_docx
from app.services.redaction.ocr_image import IMAGE_FORMATS, redact_image_file
from app.services.redaction.pdf import redact_pdf
from app.services.redaction.values import DEFAULT_LABEL, LABELS, PdValue, pd_values, replace_values
from app.services.redaction.xlsx import redact_xlsx
from app.services.redaction.xml import redact_xml

MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain; charset=utf-8",
    "xml": "application/xml",
    # Копии сканов-изображений (T-072, контракт v3, раздел 4.1).
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}


@dataclass(frozen=True)
class RedactedFile:
    content: bytes
    ext: str          # без точки: "docx", "xlsx", "pdf", "txt", "xml", "png", "jpg", "tiff"…


def ocr_page_count(doc: ExtractedDocument) -> int:
    """Сколько страниц документа распознано OCR; 0 — весь текст из текстового слоя."""
    return len(doc.ocr.pages) if doc.ocr is not None else 0


def scan_copy_allowed(doc: ExtractedDocument) -> bool:
    """Копию скана делаем, только если OCR-страниц не больше OCR_REDACT_MAX_PAGES (T-072)."""
    return ocr_page_count(doc) <= OCR_REDACT_MAX_PAGES


def redact(source: Path, doc: ExtractedDocument, entities: list[Entity]) -> RedactedFile:
    """Создаёт копию документа того же формата без ПДн.

    source — исходный файл (для текстового запроса — .txt с его текстом), doc — результат
    извлечения этого файла, entities — найденные сущности (координаты в doc.text).
    Формат определяется по расширению source. Неподдерживаемый формат — ValueError.
    Изображение и страницы PDF из doc.ocr — закраска пикселей (T-072).
    """
    ext = source.suffix.lower().lstrip(".")
    values = pd_values(doc, entities)
    if ext == "txt":
        content = redact_text(doc.text, entities, values).encode("utf-8")
    elif ext == "docx":
        content = redact_docx(source, values)
    elif ext == "xlsx":
        content = redact_xlsx(source, values)
    elif ext == "pdf":
        ocr_pages = frozenset(page.page for page in doc.ocr.pages) if doc.ocr else frozenset()
        content = redact_pdf(source, values, ocr_pages, get_settings().ocr_dpi)
    elif ext in IMAGE_FORMATS:
        content = redact_image_file(source, values)
    elif ext == "xml":
        content = redact_xml(source, values)
    else:
        raise ValueError(f"Обезличивание формата .{ext} не поддерживается")
    return RedactedFile(content=content, ext=ext)


def redact_text(text: str, entities: list[Entity], values: list[PdValue]) -> str:
    """Замена по координатам с конца текста к началу, затем — оставшихся повторов.

    Второй проход ловит то же значение в другом месте текста, которое детектор
    не отметил (например, ФИО упомянуто дважды): в копии его быть не должно.
    """
    pd = sorted(
        (entity for entity in entities if entity.category in MASKED_CATEGORIES),
        key=lambda entity: entity.start, reverse=True,
    )
    for entity in pd:
        label = LABELS.get(entity.type, DEFAULT_LABEL)
        text = text[:entity.start] + label + text[entity.end:]
    return replace_values(text, values)
