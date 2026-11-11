"""Самопроверка обезличенной копии повторным извлечением текста. Задача T-026.

Система не отдаёт копию, в которой сама находит исходные значения ПДн.
"""

import html
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf
from PIL import Image, ImageSequence

from app.schemas import Entity, ExtractedDocument
from app.services.extraction.extract import extract
from app.services.redaction.ocr_image import IMAGE_FORMATS
from app.services.redaction.redact import RedactedFile
from app.services.redaction.values import (
    MIN_CHECK_LENGTH,
    PdValue,
    leftover_values,
    normalize_spaces,
    pd_values,
    replace_values,
)

_XML_SUFFIXES = (".xml", ".rels")
_TOKEN_PUNCTUATION = ".,;:()«»\"'"


def count_leftovers(redacted: RedactedFile, doc: ExtractedDocument, entities: list[Entity]) -> int:
    """Сколько разных исходных значений ПДн (длиной ≥ 4) осталось в копии; 0 — копия чистая.

    Текст копии читается тем же extract(), что и исходник (для .txt — как есть).
    Для .docx/.xlsx дополнительно просматривается XML всех частей архива (метаданные,
    сноски, ссылки), для .pdf — метаданные и адреса ссылок: то, чего extract() не видит.
    """
    values = pd_values(doc, entities)
    texts = copy_texts(redacted, doc.document_id)
    remaining = {value.text for text in texts for value in leftover_values(text, values)}
    if doc.ocr is not None:
        remaining |= {value.text for value in ocr_leaked_values(texts, doc, values)}
    return len(remaining)


def ocr_leaked_values(
    texts: list[str], doc: ExtractedDocument, values: list[PdValue],
) -> list[PdValue]:
    """Значения, часть которых (слово ≥ 4 символов) OCR копии видит чаще, чем должна (T-072).

    OCR может прочитать край слова из-под плашки, и тогда целое значение уже не найдётся.
    Поэтому для сканов проверяется каждое слово значения длиной ≥ 4: «Лебедев» из
    «Лебедев Артём Павлович». Такое слово может законно стоять в документе и вне ПДн
    («Москва» в шапке организации), поэтому сравниваем со «правильной» копией —
    исходным текстом, где все значения уже заменены метками: в копии слово не должно
    встречаться чаще. Обрывки короче 4 символов не проверяются: они ничего не раскрывают.
    """
    haystack = normalize_spaces(" ".join(texts))
    expected = normalize_spaces(replace_values(doc.text, values))
    leaked: list[PdValue] = []
    for value in values:
        if any(haystack.count(token) > expected.count(token) for token in value_tokens(value)):
            leaked.append(value)
    return leaked


def value_tokens(value: PdValue) -> list[str]:
    """Слова значения длиной ≥ MIN_CHECK_LENGTH без пунктуации по краям."""
    tokens = (part.strip(_TOKEN_PUNCTUATION) for part in value.text.split(" "))
    return [token for token in tokens if len(token) >= MIN_CHECK_LENGTH]


def copy_texts(redacted: RedactedFile, document_id: str) -> list[str]:
    """Все тексты копии, в которых не должно быть ПДн."""
    if redacted.ext == "txt":
        return [redacted.content.decode("utf-8")]
    with TemporaryDirectory(prefix="redacted-") as folder:
        path = Path(folder) / f"copy.{redacted.ext}"
        path.write_bytes(redacted.content)
        texts = [extract(path, path.name, document_id).text]
    if redacted.ext in ("docx", "xlsx"):
        texts.extend(_archive_texts(redacted.content))
    elif redacted.ext == "pdf":
        texts.extend(_pdf_hidden_texts(redacted.content))
    elif redacted.ext in IMAGE_FORMATS:
        texts.extend(image_hidden_texts(redacted.content))
    return texts


def _archive_texts(content: bytes) -> list[str]:
    """Текст всех XML-частей .docx/.xlsx без разметки сущностей (&amp; → &)."""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        return [
            html.unescape(archive.read(name).decode("utf-8", errors="ignore"))
            for name in archive.namelist() if name.endswith(_XML_SUFFIXES)
        ]


def _pdf_hidden_texts(content: bytes) -> list[str]:
    """Метаданные, XMP и адреса ссылок PDF."""
    with pymupdf.open(stream=content, filetype="pdf") as document:
        texts = [str(value) for value in (document.metadata or {}).values() if value]
        texts.append(document.get_xml_metadata() or "")
        for page in document:
            texts.extend(link.get("uri") or "" for link in page.get_links())
    return texts


def image_hidden_texts(content: bytes) -> list[str]:
    """Метаданные изображения (EXIF, текстовые чанки PNG, теги TIFF) всех кадров (T-072)."""
    texts: list[str] = []
    with Image.open(BytesIO(content)) as image:
        for frame in ImageSequence.Iterator(image):
            texts.extend(str(value) for value in frame.info.values())
            texts.extend(str(value) for value in frame.getexif().values())
    return texts
