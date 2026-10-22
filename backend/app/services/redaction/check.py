"""Самопроверка обезличенной копии повторным извлечением текста. Задача T-026.

Система не отдаёт копию, в которой сама находит исходные значения ПДн.
"""

import html
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf

from app.schemas import Entity, ExtractedDocument
from app.services.extraction.extract import extract
from app.services.redaction.redact import RedactedFile
from app.services.redaction.values import leftover_values, pd_values

_XML_SUFFIXES = (".xml", ".rels")


def count_leftovers(redacted: RedactedFile, doc: ExtractedDocument, entities: list[Entity]) -> int:
    """Сколько разных исходных значений ПДн (длиной ≥ 4) осталось в копии; 0 — копия чистая.

    Текст копии читается тем же extract(), что и исходник (для .txt — как есть).
    Для .docx/.xlsx дополнительно просматривается XML всех частей архива (метаданные,
    сноски, ссылки), для .pdf — метаданные и адреса ссылок: то, чего extract() не видит.
    """
    values = pd_values(doc, entities)
    remaining = {
        value.text for text in copy_texts(redacted, doc.document_id)
        for value in leftover_values(text, values)
    }
    return len(remaining)


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
