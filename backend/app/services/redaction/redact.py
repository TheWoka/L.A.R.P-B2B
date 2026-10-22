"""Обезличенная копия документа в исходном формате. Задача T-026.

Контракт: docs/contracts.md, раздел 4.1 «Обезличенная копия». ПДн (pd, pd_special)
удаляются физически и заменяются метками «[ФИО]», «[ТЕЛЕФОН]»…; коммерческая тайна
и финансовые данные не трогаются (решение 17.10).
"""

from dataclasses import dataclass
from pathlib import Path

from app.schemas import Entity, ExtractedDocument
from app.services.masking.masker import MASKED_CATEGORIES
from app.services.redaction.docx import redact_docx
from app.services.redaction.pdf import redact_pdf
from app.services.redaction.values import DEFAULT_LABEL, LABELS, PdValue, pd_values, replace_values
from app.services.redaction.xlsx import redact_xlsx

MEDIA_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain; charset=utf-8",
}


@dataclass(frozen=True)
class RedactedFile:
    content: bytes
    ext: str          # без точки: "docx", "xlsx", "pdf", "txt"


def redact(source: Path, doc: ExtractedDocument, entities: list[Entity]) -> RedactedFile:
    """Создаёт копию документа того же формата без ПДн.

    source — исходный файл (для текстового запроса — .txt с его текстом), doc — результат
    извлечения этого файла, entities — найденные сущности (координаты в doc.text).
    Формат определяется по расширению source. Неподдерживаемый формат — ValueError.
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
        content = redact_pdf(source, values)
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
