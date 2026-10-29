"""XML-выгрузки ЕИС (T-064a): поля, ФИО одним сегментом, защита от XXE. Данные выдуманы."""

import logging
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

from app.schemas import EntityType, ExtractedDocument
from app.services.detection.detect import detect
from app.services.extraction.errors import EMPTY_DOCUMENT, ExtractionError
from app.services.extraction.extract import extract
from app.services.extraction.xml import MAX_SECTION, extract_xml, iter_xml_fields

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic_notice_44fz.xml"
CONTACT_SECTION = "purchaseResponsibleInfo/responsibleInfo/contactPersonInfo"
NAME = "Петров Пётр Петрович"
EMAIL = "petrov@example.ru"
PHONE = "+7 (900) 000-00-01"
UNREADABLE = "Не удалось прочитать файл"

NOTICE = """<?xml version="1.0" encoding="UTF-8"?>
<ns2:notice xmlns:ns2="http://zakupki.gov.ru/oos/EPtypes/1"
            xmlns:ns3="http://zakupki.gov.ru/oos/common/1" schemeVersion="13.2">
  <ns2:commonInfo>
    <ns2:purchaseNumber attr="не читается">0000000000000000002</ns2:purchaseNumber>
    <!-- комментарий не читается -->
    <ns2:empty></ns2:empty>
    <ns2:blank>   </ns2:blank>
    <ns2:purchaseObjectInfo>Поставка бумаги</ns2:purchaseObjectInfo>
  </ns2:commonInfo>
  <ns2:contact>
    <ns3:firstName>Анна</ns3:firstName>
    <ns3:lastName>Сидорова</ns3:lastName>
    <ns3:contactEMail>sidorova@example.ru</ns3:contactEMail>
  </ns2:contact>
  <ns2:signer>
    <ns3:lastName>Иванов</ns3:lastName>
    <ns3:position>Директор</ns3:position>
  </ns2:signer>
  <ns2:tail>Последнее поле</ns2:tail>
</ns2:notice>
"""


def write(tmp_path: Path, content: bytes, name: str = "notice.xml") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def assert_offsets(document: ExtractedDocument) -> None:
    assert document.text == "\n".join(segment.text for segment in document.segments)
    expected = 0
    for index, segment in enumerate(document.segments):
        assert segment.id == index and segment.offset == expected
        assert document.text[segment.offset:segment.offset + len(segment.text)] == segment.text
        expected += len(segment.text) + 1


def test_fixture_fio_is_one_segment_and_detected() -> None:
    document = extract(FIXTURE, FIXTURE.name, "synthetic-xml")
    names = [segment for segment in document.segments if segment.text == NAME]
    assert len(names) == 1 and names[0].section == CONTACT_SECTION
    assert document.pages is None and all(s.page is None for s in document.segments)
    assert_offsets(document)
    entities = detect(document)
    persons = [e for e in entities if e.type == EntityType.PERSON]
    assert [document.text[e.start:e.end] for e in persons] == [NAME]
    assert persons[0].section == CONTACT_SECTION
    found = {e.type: document.text[e.start:e.end] for e in entities}
    assert found[EntityType.PHONE] == PHONE and found[EntityType.EMAIL] == EMAIL


def test_fields_order_namespaces_and_skips() -> None:
    fields = iter_xml_fields(fromstring(NOTICE.encode("utf-8"), forbid_dtd=True))
    assert [(field.text, field.section) for field in fields] == [
        ("0000000000000000002", "commonInfo"),
        ("Поставка бумаги", "commonInfo"),
        ("Сидорова Анна", "contact"),           # на месте первого элемента, порядок «Ф И»
        ("sidorova@example.ru", "contact"),
        ("Иванов", "signer"),                   # одна часть ФИО — обычное поле
        ("Директор", "signer"),
        ("Последнее поле", ""),                 # родитель — корень
    ]
    fio = fields[2]
    assert [element.tag.rsplit("}", 1)[-1] for element in fio.elements] == [
        "lastName", "firstName",
    ]
    assert all(len(field.elements) == 1 for field in fields if field is not fio)


def test_three_part_fio_and_extract_segments(tmp_path: Path) -> None:
    xml = (
        '<r xmlns="urn:example"><a><b><middleName>Петрович</middleName>'
        "<firstName>Пётр</firstName><lastName>Петров</lastName></b></a><x>1</x></r>"
    )
    segments = extract_xml(write(tmp_path, xml.encode("utf-8")))
    assert [(s.text, s.section) for s in segments] == [(NAME, "a/b"), ("1", None)]


def test_offsets_with_empty_elements(tmp_path: Path) -> None:
    document = extract(write(tmp_path, NOTICE.encode("utf-8")), "notice.xml", "synthetic-id")
    assert len(document.segments) == 7
    assert "Последнее поле" == document.segments[-1].text and document.segments[-1].section is None
    assert_offsets(document)


def test_long_section_keeps_tail() -> None:
    names = [f"level{index:03d}" for index in range(30)]
    xml = "<root>" + "".join(f"<{n}>" for n in names) + "<v>x</v>"
    xml += "".join(f"</{n}>" for n in reversed(names)) + "</root>"
    section = iter_xml_fields(fromstring(xml))[0].section
    assert len(section) <= MAX_SECTION
    assert section.endswith("level029") and "/".join(names).endswith(section)
    assert not section.startswith("/")


def test_root_leaf_and_deep_nesting(tmp_path: Path) -> None:
    assert [s.text for s in extract_xml(write(tmp_path, "<a> текст </a>".encode()))] == ["текст"]
    depth = 5000
    deep = "<e>" * depth + "<v>глубоко</v>" + "</e>" * depth
    segments = extract_xml(write(tmp_path, deep.encode("utf-8"), "deep.xml"))
    assert [s.text for s in segments] == ["глубоко"]


def test_windows_1251(tmp_path: Path) -> None:
    xml = '<?xml version="1.0" encoding="windows-1251"?><r><p><lastName>Петров</lastName>'
    xml += "<firstName>Пётр</firstName><middleName>Петрович</middleName></p></r>"
    path = write(tmp_path, xml.encode("cp1251"))
    document = extract(path, "notice.xml", "synthetic-id")
    assert document.text == NAME


@pytest.mark.parametrize("content", [
    # «billion laughs»: сущности, раскрывающиеся экспоненциально
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><r>&lol2;</r>',
    # XXE: внешняя сущность читает файл сервера
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    b"<r>&xxe;</r>",
    # DTD без сущностей тоже запрещён
    b'<?xml version="1.0"?><!DOCTYPE r><r><v>1</v></r>',
    # обрезанный файл
    b"<r><commonInfo><purchaseNumber>0000",
    b"",
])
def test_unsafe_or_broken_xml(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(ValueError, match=UNREADABLE):
        extract(write(tmp_path, content), "notice.xml", "synthetic-id")


def test_xml_without_fields(tmp_path: Path) -> None:
    path = write(tmp_path, b"<r><a/><b>  </b><c><d></d></c></r>")
    with pytest.raises(ExtractionError, match=EMPTY_DOCUMENT):
        extract(path, "notice.xml", "synthetic-id")


def test_logs_without_values(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        detect(extract(FIXTURE, FIXTURE.name, "synthetic-xml"))
    for value in ("Петров", "Петрович", EMAIL, PHONE, "000-00-01"):
        assert value not in caplog.text
