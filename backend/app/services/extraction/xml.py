"""Парсер XML-выгрузок ЕИС (извещения 44-ФЗ и 223-ФЗ). Задача T-064a.

Разбор — только через defusedxml с forbid_dtd=True: DTD и сущности запрещены, это защита
от XXE и «billion laughs». Внутренний интерфейс модуля extraction — не контракт: контрактом
является extract() в extract.py. iter_xml_fields() публичная — по ней T-064b будет строить
обезличенную копию.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from defusedxml.ElementTree import parse

from app.schemas import TextSegment
from app.services.extraction.errors import ExtractionError

if TYPE_CHECKING:  # только аннотация типа; разбирает XML исключительно defusedxml
    from xml.etree.ElementTree import Element

FIO_PARTS: tuple[str, ...] = ("lastName", "firstName", "middleName")
MAX_SECTION = 120
# Ограничиваем текст до NER: допустимый по размеру XML может исчерпать память процесса.
MAX_XML_TEXT_CHARS = 1_000_000
XML_TEXT_TOO_LARGE = (
    "XML содержит слишком большой объём текста для проверки. "
    "Разделите выгрузку на несколько файлов."
)


@dataclass(frozen=True)
class XmlField:
    """Поле документа: один лист-элемент или ФИО из листьев одного родителя.

    elements — исходные элементы (для ФИО — в порядке lastName, firstName, middleName),
    text — текст поля, section — путь локальных имён от корня (без него) до родителя поля;
    "" — родитель поля сам корень.
    """

    elements: tuple[Element, ...]
    text: str
    section: str


def local_name(tag: str) -> str:
    """Имя без пространства имён: '{http://zakupki.gov.ru/oos/EPtypes/1}lastName' -> 'lastName'."""
    return tag.rsplit("}", 1)[-1]


def _section(path: str) -> str:
    """Путь не длиннее MAX_SECTION символов: у длинного остаётся хвост с целого имени."""
    if len(path) <= MAX_SECTION:
        return path
    tail = path[-MAX_SECTION:]
    return tail[tail.index("/") + 1:] if "/" in tail else tail


def _is_field(element: Element) -> bool:
    """Поле — элемент без дочерних элементов с непустым после strip() текстом."""
    return len(element) == 0 and bool((element.text or "").strip())


def _fio_group(children: list[Element]) -> tuple[Element, ...]:
    """Листья lastName/firstName/middleName одного родителя в порядке «Фамилия Имя Отчество».

    Берётся первое вхождение каждой части; меньше двух частей — группы нет, листья
    становятся обычными полями.
    """
    found: dict[str, Element] = {}
    for child in children:
        name = local_name(child.tag)
        if name in FIO_PARTS and name not in found and _is_field(child):
            found[name] = child
    parts = tuple(found[name] for name in FIO_PARTS if name in found)
    return parts if len(parts) >= 2 else ()


def _child_path(path: str, child: Element) -> str:
    name = local_name(child.tag)
    return f"{path}/{name}" if path else name


def _items(parent: Element, path: str) -> Iterator[XmlField | tuple[Element, str]]:
    """Поля и вложенные элементы родителя в порядке документа; path — путь самого родителя.

    Группа ФИО выдаётся один раз — на месте первого по документу из её элементов.
    """
    children = [child for child in parent if isinstance(child.tag, str)]
    fio = _fio_group(children)
    fio_ids = {id(element) for element in fio}
    fio_done = False
    section = _section(path)
    for child in children:
        if id(child) in fio_ids:
            if not fio_done:
                fio_done = True
                text = " ".join((element.text or "").strip() for element in fio)
                yield XmlField(elements=fio, text=text, section=section)
        elif _is_field(child):
            yield XmlField(elements=(child,), text=(child.text or "").strip(), section=section)
        elif len(child):
            yield child, _child_path(path, child)


def iter_xml_fields(root: Element) -> list[XmlField]:
    """Все поля XML-документа в порядке документа; без побочных эффектов.

    Поле — элемент без дочерних элементов с непустым после strip() текстом; если у родителя
    ≥ 2 таких листа из {lastName, firstName, middleName}, они дают одно поле «Фамилия Имя
    Отчество» (детектор режет ФИО по переводу строки, отдельными строками скрылась бы только
    фамилия). Пространства имён из имён убираются.

    Ограничения: атрибуты, комментарии, инструкции обработки и tail-текст не читаются;
    у элемента с дочерними элементами собственный текст (смешанное содержимое) не читается.
    Обход — явным стеком, без рекурсии: глубокая вложенность не роняет интерпретатор.
    """
    if _is_field(root):
        return [XmlField(elements=(root,), text=(root.text or "").strip(), section="")]
    fields: list[XmlField] = []
    stack: list[Iterator[XmlField | tuple[Element, str]]] = [_items(root, "")]
    while stack:
        item = next(stack[-1], None)
        if item is None:
            stack.pop()
        elif isinstance(item, XmlField):
            fields.append(item)
        else:
            stack.append(_items(*item))
    return fields


def extract_xml(path: Path) -> list[TextSegment]:
    """Сегмент на каждое поле: section — путь элементов (None, если родитель — корень).

    id и offset проставляет extract(). Ошибки разбора (ParseError, DTDForbidden,
    EntitiesForbidden…) не перехватываются — их переводит в ValueError extract().
    """
    root = parse(path, forbid_dtd=True).getroot()
    segments: list[TextSegment] = []
    text_size = 0
    for field in iter_xml_fields(root):
        text_size += len(field.text) + bool(segments)
        if text_size > MAX_XML_TEXT_CHARS:
            raise ExtractionError(XML_TEXT_TOO_LARGE, case="xml_text_too_large")
        segments.append(TextSegment(
            id=0, text=field.text, offset=0, page=None, section=field.section or None,
        ))
    return segments
