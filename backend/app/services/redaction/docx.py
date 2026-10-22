"""Обезличивание .docx на python-docx: замена значений ПДн метками. Задача T-026.

Внутренний модуль пакета redaction — не контракт. Обходит XML всех частей документа
(тело, таблицы, колонтитулы всех видов, надписи, сноски, примечания), а не только
то, что видит extraction/docx.py: ПДн не должны остаться ни в одной части файла.
"""

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.opc.part import Part, XmlPart
from docx.oxml.ns import qn
from lxml import etree

from app.services.redaction.values import PdValue, replace_values, value_pattern

_T, _P = qn("w:t"), qn("w:p")
# Элементы, которые python-docx показывает в тексте абзаца пробельным символом.
_BREAKS = frozenset({qn("w:tab"), qn("w:br"), qn("w:cr")})
# Текст вне видимых абзацев: удалённый в рецензировании и коды полей (HYPERLINK "mailto:…").
_LOOSE_TEXT = (qn("w:delText"), qn("w:instrText"))
_PROPERTY_FIELDS = ("title", "subject", "keywords", "comments", "category", "content_status")
_AUTHOR_FIELDS = ("author", "last_modified_by")

Span = tuple[int, int, str]


def redact_docx(source: Path, values: list[PdValue]) -> bytes:
    """Возвращает байты копии .docx, где значения ПДн заменены метками.

    Абзацы без ПДн не меняются, у изменённых сохраняется форматирование всех run,
    кроме тех, в которые попало значение. Метаданные: автор очищается, остальные
    поля — с той же заменой. Внешние ссылки (mailto:…) — с той же заменой.
    """
    document = Document(str(source))
    for part in document.part.package.iter_parts():
        _redact_part(part, values)
        for rel in part.rels.values():
            if rel.is_external:
                rel._target = replace_values(rel.target_ref, values)
    properties = document.core_properties
    for name in _AUTHOR_FIELDS:
        setattr(properties, name, "")
    for name in _PROPERTY_FIELDS:
        setattr(properties, name, replace_values(getattr(properties, name) or "", values))
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _redact_part(part: Part, values: list[PdValue]) -> None:
    """Части python-docx (тело, колонтитулы) правит на месте, сноски — через blob."""
    if isinstance(part, XmlPart):
        _redact_element(part.element, values)
    elif str(part.partname).startswith("/word/") and part.content_type.endswith("+xml"):
        root = etree.fromstring(part.blob)
        if _redact_element(root, values):
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                        standalone=True)


def _redact_element(root: etree._Element, values: list[PdValue]) -> bool:
    """Заменяет ПДн во всех абзацах дерева; True, если что-то изменилось."""
    changed = False
    for paragraph in root.iter(_P):
        changed |= _redact_paragraph(_paragraph_texts(paragraph), values)
    for element in root.iter(*_LOOSE_TEXT):
        if element.text and (new := replace_values(element.text, values)) != element.text:
            element.text, changed = new, True
    return changed


def _paragraph_texts(paragraph: etree._Element) -> list[etree._Element]:
    """w:t и разрывы абзаца в порядке документа, без абзацев вложенных надписей."""
    result: list[etree._Element] = []
    for element in paragraph.iter(_T, *_BREAKS):
        if next(element.iterancestors(_P), None) is paragraph:
            result.append(element)
    return result


def _redact_paragraph(elements: list[etree._Element], values: list[PdValue]) -> bool:
    """Значение может быть разбито на несколько run: ищем в склеенном тексте абзаца.

    Метка пишется в run, где значение начинается; из остальных затронутых run
    удаляется только их часть значения. Разрывы (w:tab, w:br) не удаляются.
    """
    pieces = [(element.text or "") if element.tag == _T else " " for element in elements]
    spans = match_spans("".join(pieces), values)
    if not spans:
        return False
    starts, position = [], 0
    for piece in pieces:
        starts.append(position)
        position += len(piece)
    for start, end, label in reversed(spans):
        for index, element in enumerate(elements):
            local_start = max(start, starts[index]) - starts[index]
            local_end = min(end, starts[index] + len(pieces[index])) - starts[index]
            if element.tag != _T or local_start >= local_end:
                continue
            text = element.text or ""
            first = starts[index] <= start
            element.text = text[:local_start] + (label if first else "") + text[local_end:]
            element.set(qn("xml:space"), "preserve")
    return True


def match_spans(text: str, values: list[PdValue]) -> list[Span]:
    """Непересекающиеся вхождения значений, по возрастанию; длинные значения — первыми."""
    spans: list[Span] = []
    for value in values:
        for match in value_pattern(value).finditer(text):
            start, end = match.span()
            if all(end <= other[0] or start >= other[1] for other in spans):
                spans.append((start, end, value.label))
    return sorted(spans)
