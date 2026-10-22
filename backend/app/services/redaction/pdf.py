"""Обезличивание .pdf на PyMuPDF: настоящая редакция, а не закраска. Задача T-026.

Внутренний модуль пакета redaction — не контракт.

Закрасить ПДн прямоугольником поверх нельзя: текст под ним остаётся в потоке
содержимого и копируется из PDF. Поэтому — redaction-аннотация + apply_redactions():
символы под ней физически удаляются со страницы, затем garbage=4 выбрасывает
из файла старые объекты.
"""

from pathlib import Path

import pymupdf

from app.services.redaction.values import PdValue, normalize_spaces, replace_values

FILL = (0, 0, 0)          # чёрная плашка — явно видно, что здесь были ПДн
LABEL_COLOR = (1, 1, 1)   # метка белым по чёрному
LABEL_FONT = "F_redact"
MAX_FONT_SIZE = 9.0
_AUTHOR_FIELDS = ("author", "creator", "producer")
_PROPERTY_FIELDS = ("title", "subject", "keywords")


def redact_pdf(source: Path, values: list[PdValue]) -> bytes:
    """Возвращает байты копии .pdf, где значения ПДн удалены и подписаны метками.

    Значение ищется на всех страницах: то же ФИО на другой странице детектор мог
    не отметить, а в копии его быть не должно. Метаданные (автор, XMP) очищаются,
    ссылки с ПДн в адресе (mailto:…) удаляются.
    """
    font = pymupdf.Font("helv")  # встроенный Nimbus Sans: есть кириллица для меток
    with pymupdf.open(source, filetype="pdf") as document:
        for page in document:
            hits = _redact_page(page, values)
            if hits:
                page.insert_font(fontname=LABEL_FONT, fontbuffer=font.buffer)
                for rect, label in hits:
                    _draw_label(page, rect, label, font)
            _remove_links(page, values)
        _clean_metadata(document, values)
        return document.tobytes(garbage=4, deflate=True)


def _redact_page(page: pymupdf.Page, values: list[PdValue]) -> list[tuple[pymupdf.Rect, str]]:
    """Ставит redaction-аннотации на все вхождения и применяет их; возвращает места меток."""
    hits: list[tuple[pymupdf.Rect, str]] = []
    for value in values:
        for rect in _find(page, value):
            page.add_redact_annot(rect, fill=FILL, cross_out=False)
            # «Иванов» внутри уже найденного «Иванов Иван»: текст удаляем, метку не дублируем.
            if not any(rect.intersects(other) for other, _ in hits):
                hits.append((rect, value.label))
    if hits:
        # Удаляем текст под аннотациями; картинки и графику, которые лишь касаются
        # плашки, не трогаем (рамки таблиц и логотипы остаются).
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )
    return hits


def _find(page: pymupdf.Page, value: PdValue) -> list[pymupdf.Rect]:
    """Прямоугольники вхождений; короткое значение — только как отдельное слово."""
    if value.checkable:
        return list(page.search_for(value.text))
    words = value.text.split(" ")
    if len(words) != 1:
        return list(page.search_for(value.text))
    return [pymupdf.Rect(word[:4]) for word in page.get_text("words")
            if word[4].strip(".,;:()«»\"'") == value.text]


def _draw_label(page: pymupdf.Page, rect: pymupdf.Rect, label: str, font: pymupdf.Font) -> None:
    """Пишет метку внутри плашки, уменьшая кегль, чтобы она поместилась по ширине."""
    width = font.text_length(label, fontsize=1)
    size = min(MAX_FONT_SIZE, rect.height * 0.8, rect.width / width if width else MAX_FONT_SIZE)
    size = max(size, 3.0)
    baseline = pymupdf.Point(rect.x0 + 0.5, rect.y1 - (rect.height - size) / 2 - size * 0.2)
    page.insert_text(baseline, label, fontname=LABEL_FONT, fontsize=size, color=LABEL_COLOR)


def _remove_links(page: pymupdf.Page, values: list[PdValue]) -> None:
    """Ссылка mailto:/tel: с ПДн в адресе видна в свойствах PDF — удаляем её целиком."""
    for link in page.get_links():
        uri = link.get("uri") or ""
        if uri and replace_values(normalize_spaces(uri), values) != normalize_spaces(uri):
            page.delete_link(link)


def _clean_metadata(document: pymupdf.Document, values: list[PdValue]) -> None:
    """Автор и программа-создатель очищаются, остальные поля — с заменой ПДн; XMP удаляется."""
    metadata = dict(document.metadata or {})
    for name in _AUTHOR_FIELDS:
        metadata[name] = ""
    for name in _PROPERTY_FIELDS:
        metadata[name] = replace_values(metadata.get(name) or "", values)
    document.set_metadata({key: value for key, value in metadata.items()
                           if key in _AUTHOR_FIELDS + _PROPERTY_FIELDS})
    document.del_xml_metadata()
