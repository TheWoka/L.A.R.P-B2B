"""Обезличивание XML-выгрузок ЕИС: замена значений ПДн в полях. Задача T-064b.

Внутренний модуль пакета redaction — не контракт. Разбор — только defusedxml (forbid_dtd=True),
поля — те же, что видел детектор: iter_xml_fields из extraction/xml.py. Значения ПДн
не логируются (AGENTS.md, правило 4).
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ElementTree  # только сериализация; разбор — defusedxml
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from defusedxml.ElementTree import iterparse, parse

from app.schemas import EntityType
from app.services.extraction.xml import XmlField, iter_xml_fields
from app.services.redaction.values import LABELS, PdValue, replace_values

FIO_LABEL = LABELS[EntityType.PERSON]
_NAMESPACE_LOCK = threading.Lock()


def redact_xml(source: Path, values: list[PdValue]) -> bytes:
    """Байты копии XML в UTF-8 с декларацией, где значения ПДн заменены метками.

    Одиночное поле — замена подстрок. Поле ФИО (lastName/firstName/middleName одного
    родителя): если в нём есть хоть одно значение ПДн, каждый элемент группы получает «[ФИО]» —
    имя и отчество тоже ПДн, даже если детектор отметил только часть. Затем те же значения
    заменяются в атрибутах и «хвостах» (tail, текст смешанного содержимого): детектор их не
    читает, но повтор найденного телефона в атрибуте в копии оставаться не должен.
    Комментарии и инструкции обработки разборщик не сохраняет — в копию они не попадают.
    """
    root = parse(source, forbid_dtd=True).getroot()
    for field in iter_xml_fields(root):
        _redact_field(field, values)
    _redact_rest(root, values)
    mapping = _declared_namespaces(source)
    if "" in mapping.values() and any("}" not in element.tag for element in root.iter()):
        default_uri = next(uri for uri, prefix in mapping.items() if prefix == "")
        mapping[default_uri] = _free_prefix("default", mapping)
    with _document_prefixes(mapping):
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _redact_field(field: XmlField, values: list[PdValue]) -> None:
    if len(field.elements) == 1:
        element = field.elements[0]
        element.text = replace_values(element.text or "", values)
    elif replace_values(field.text, values) != field.text:
        for element in field.elements:
            element.text = FIO_LABEL


def _redact_rest(root: ElementTree.Element, values: list[PdValue]) -> None:
    """Атрибуты, tail и текст элементов с дочерними элементами."""
    for element in root.iter():
        for name, value in element.attrib.items():
            element.set(name, replace_values(value, values))
        if element.tail and element.tail.strip():
            element.tail = replace_values(element.tail, values)
        if len(element) and element.text and element.text.strip():
            element.text = replace_values(element.text, values)


def _declared_namespaces(source: Path) -> dict[str, str]:
    """uri -> префикс в порядке объявления; у одного uri — первый префикс, префиксы уникальны.

    Если один префикс в разных местах объявлен для разных uri, второму даётся «<префикс>_N»:
    в копии все объявления оказываются на корне, и одинаковые префиксы сломали бы XML.
    """
    mapping: dict[str, str] = {}
    for _event, (prefix, uri) in iterparse(str(source), events=("start-ns",), forbid_dtd=True):
        if uri in mapping:
            continue
        taken = prefix in mapping.values()
        mapping[uri] = _free_prefix(prefix or "ns", mapping) if taken else prefix
    return mapping


def _free_prefix(base: str, mapping: dict[str, str]) -> str:
    """«<base>_N» с наименьшим N, которого ещё нет среди префиксов."""
    index = 1
    while f"{base}_{index}" in mapping.values():
        index += 1
    return f"{base}_{index}"


@contextmanager
def _document_prefixes(mapping: dict[str, str]) -> Iterator[None]:
    """Префиксы исходника на время сериализации, затем прежняя таблица ElementTree.

    register_namespace() отвергает префиксы вида «ns2» («зарезервированы»), а в выгрузках ЕИС
    именно они, поэтому таблица меняется напрямую и под блокировкой. Пустой префикс —
    пространство по умолчанию; если в документе есть элементы без пространства имён, его
    объявлять нельзя (они бы в него попали) — тогда у него непустой префикс.
    """
    with _NAMESPACE_LOCK:
        table = ElementTree._namespace_map  # noqa: SLF001 — см. docstring
        saved = dict(table)
        try:
            for uri in list(table):
                if table[uri] in mapping.values():
                    del table[uri]
            table.update(mapping)
            yield
        finally:
            table.clear()
            table.update(saved)
