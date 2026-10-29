"""Обезличенная копия XML-выгрузок ЕИС (T-064b). Все данные выдуманы."""

import logging
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from app.schemas import EntityType
from app.services.detection.detect import detect
from app.services.extraction.extract import extract
from app.services.redaction.check import count_leftovers
from app.services.redaction.redact import redact
from app.services.redaction.values import PdValue
from app.services.redaction.xml import redact_xml
from app.worker.tasks import make_redacted_copy

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic_notice_44fz.xml"
NOTICE_VALUES = (
    "Петров", "Пётр", "Петрович", "petrov@example.ru", "+7 (900) 000-00-01", "000-00-01",
)
PASSPORT = "45 12 654321"
APPLICATION = f"""<?xml version="1.0" encoding="UTF-8"?>
<ns2:application xmlns:ns2="http://zakupki.gov.ru/oos/EPtypes/1"
                 xmlns:ns3="http://zakupki.gov.ru/oos/common/1">
  <ns2:participantInfo>
    <ns3:individualPersonInfo>
      <ns3:lastName>Сидорова</ns3:lastName>
      <ns3:firstName>Анна</ns3:firstName>
      <ns3:middleName>Игоревна</ns3:middleName>
    </ns3:individualPersonInfo>
    <ns3:identityDocument>Паспорт: {PASSPORT}</ns3:identityDocument>
    <ns3:contactPhone>+7 (900) 000-00-02</ns3:contactPhone>
  </ns2:participantInfo>
  <ns2:maxPrice>99000.00</ns2:maxPrice>
</ns2:application>
"""
APPLICATION_VALUES = ("Сидорова", "Анна", "Игоревна", PASSPORT, "+7 (900) 000-00-02")


def audit_copy(path: Path) -> tuple[bytes, int]:
    """Конвейер без БД: извлечение → детекция → копия → самопроверка."""
    doc = extract(path, path.name, "synthetic-xml")
    entities = detect(doc)
    copy = redact(path, doc, entities)
    assert copy.ext == "xml"
    return copy.content, count_leftovers(copy, doc, entities)


def tags(content: bytes) -> list[str]:
    """Полные имена элементов в порядке документа: структура и пространства имён."""
    return [element.tag for element in fromstring(content, forbid_dtd=True).iter()]


def by_local_name(content: bytes, name: str) -> list[str]:
    root = fromstring(content, forbid_dtd=True)
    return [e.text or "" for e in root.iter() if e.tag.rsplit("}", 1)[-1] == name]


def prefixes(content: bytes) -> set[str]:
    return set(re.findall(rb"</?([A-Za-z_][\w.-]*):", content))


def test_notice_copy_is_clean_valid_and_keeps_prefixes() -> None:
    source = FIXTURE.read_bytes()
    content, leftovers = audit_copy(FIXTURE)
    assert leftovers == 0
    assert content.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
    assert tags(content) == tags(source)                       # разбирается, структура та же
    assert prefixes(content) == prefixes(source) == {b"ns2", b"ns3"}
    assert b'xmlns:ns2="http://zakupki.gov.ru/oos/EPtypes/1"' in content
    assert b"ns0:" not in content
    for value in NOTICE_VALUES:
        assert value.encode() not in content
    for name in ("lastName", "firstName", "middleName"):
        assert by_local_name(content, name) == ["[ФИО]"]
    assert by_local_name(content, "contactEMail") == ["[EMAIL]"]
    assert by_local_name(content, "contactPhone") == ["[ТЕЛЕФОН]"]
    # Поля без ПДн не меняются.
    assert by_local_name(content, "maxPrice") == ["150000.00"]
    assert by_local_name(content, "regNum") == ["00000000001"]
    assert by_local_name(content, "purchaseNumber") == ["0000000000000000001"]
    assert by_local_name(content, "fullName") == ["Акционерное общество «Банк Пример»"]


def test_application_with_passport(tmp_path: Path) -> None:
    path = tmp_path / "application.xml"
    path.write_text(APPLICATION, encoding="utf-8")
    doc = extract(path, path.name, "synthetic-xml")
    assert EntityType.PASSPORT in {entity.type for entity in detect(doc)}
    content, leftovers = audit_copy(path)
    assert leftovers == 0
    for value in APPLICATION_VALUES:
        assert value.encode() not in content
    assert by_local_name(content, "identityDocument") == ["Паспорт: [ПАСПОРТ]"]
    assert by_local_name(content, "maxPrice") == ["99000.00"]


def write(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / "doc.xml"
    path.write_bytes(text.encode(encoding))
    return path


def test_partial_fio_hides_whole_group(tmp_path: Path) -> None:
    """Детектор отметил только фамилию — имя и отчество всё равно скрываются."""
    path = write(tmp_path, (
        "<r><p><lastName>Петров</lastName><firstName>Пётр</firstName>"
        "<middleName>Петрович</middleName></p><q><lastName>Кузнецов</lastName></q></r>"
    ))
    content = redact_xml(path, [PdValue(text="Петров", label="[ФИО]")])
    root = fromstring(content)
    assert [e.text for e in root.find("p")] == ["[ФИО]"] * 3
    assert root.find("q/lastName").text == "Кузнецов"        # без ПДн — не трогается


def test_fio_without_values_unchanged(tmp_path: Path) -> None:
    path = write(tmp_path, "<r><p><lastName>Петров</lastName><firstName>Пётр</firstName></p></r>")
    root = fromstring(redact_xml(path, [PdValue(text="+7 (900) 000-00-01", label="[ТЕЛЕФОН]")]))
    assert [e.text for e in root.find("p")] == ["Петров", "Пётр"]


def test_attributes_and_tail_are_swept(tmp_path: Path) -> None:
    path = write(tmp_path, (
        '<r><p note="звонить +7 (900) 000-00-01">'
        "<v>+7 (900) 000-00-01</v>хвост +7 (900) 000-00-01</p></r>"
    ))
    content = redact_xml(path, [PdValue(text="+7 (900) 000-00-01", label="[ТЕЛЕФОН]")])
    assert "000-00-01".encode() not in content
    root = fromstring(content)
    assert root.find("p").get("note") == "звонить [ТЕЛЕФОН]"
    assert root.find("p/v").tail == "хвост [ТЕЛЕФОН]"


@pytest.mark.parametrize("text", [
    # один префикс для разных uri в разных местах
    '<a:r xmlns:a="urn:one"><a:x xmlns:a="urn:two">1</a:x><a:y>2</a:y></a:r>',
    # пространство по умолчанию и элемент, выведенный из него (xmlns="")
    '<r xmlns="urn:default"><x>1</x><y xmlns=""><z>2</z></y></r>',
    # пространство по умолчанию и префиксы ЕИС
    '<r xmlns="urn:d" xmlns:ns2="urn:e"><ns2:x>1</ns2:x><x>2</x></r>',
])
def test_namespaces_round_trip(tmp_path: Path, text: str) -> None:
    content = redact_xml(write(tmp_path, text), [])
    assert tags(content) == tags(text.encode())


def test_windows_1251_source(tmp_path: Path) -> None:
    text = '<?xml version="1.0" encoding="windows-1251"?><r><v>Поставка</v><p>Петров</p></r>'
    content = redact_xml(write(tmp_path, text, "cp1251"), [PdValue("Петров", "[ФИО]")])
    assert content.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
    assert [e.text for e in fromstring(content)] == ["Поставка", "[ФИО]"]


def test_global_namespace_table_restored(tmp_path: Path) -> None:
    before = dict(ElementTree._namespace_map)
    redact_xml(FIXTURE, [])
    assert ElementTree._namespace_map == before


def test_redacted_copy_logs_without_values(caplog: pytest.LogCaptureFixture) -> None:
    doc = extract(FIXTURE, FIXTURE.name, "synthetic-xml")
    entities = detect(doc)
    with caplog.at_level(logging.DEBUG):
        copy = make_redacted_copy(FIXTURE, doc, entities, "synthetic-task")
    assert copy is not None and copy.ext == "xml"
    assert "redaction=ok ext=xml" in caplog.text
    for value in NOTICE_VALUES:
        assert value not in caplog.text
