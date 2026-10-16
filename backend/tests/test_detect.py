"""Тесты detect(), merge_entities(), assign_location() (T-007)."""

import logging

import pytest

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, TextSegment
from app.services.detection.detect import assign_location, detect, merge_entities
from tests.test_validators import make_inn12, make_snils

T = EntityType


def build_doc(parts: list[tuple[str, int | None, str | None]]) -> ExtractedDocument:
    """Документ из сегментов (текст, страница, раздел), склеенных через "\\n"."""
    segments: list[TextSegment] = []
    offset = 0
    for i, (text, page, section) in enumerate(parts):
        segments.append(TextSegment(id=i, text=text, offset=offset, page=page, section=section))
        offset += len(text) + 1
    full = "\n".join(p[0] for p in parts)
    pages = max((p[1] or 0) for p in parts) or None
    return ExtractedDocument(
        document_id="doc-1", filename="t.pdf", text=full, segments=segments, pages=pages
    )


def ent(start: int, end: int, detector: str = "regex", etype: EntityType = T.PHONE) -> Entity:
    return Entity(
        type=etype, category=EntityCategory.pd, start=start, end=end,
        value_masked="█" * (end - start), confidence=0.9, detector=detector,
    )


SNILS = make_snils(21)
INN = make_inn12(22)
PHONE = "+7 (912) 345-67-89"


@pytest.fixture
def doc() -> ExtractedDocument:
    return build_doc([
        (f"1. Общие положения. СНИЛС {SNILS}.", 1, "1. Общие положения"),
        (f"Исполнитель: ИНН {INN}.", 1, "2. Стороны"),
        (f"Контакты: {PHONE}, ivan.petrov@mail.ru", 2, "3. Реквизиты"),
    ])


def test_page_and_section_from_segment(doc: ExtractedDocument) -> None:
    entities = detect(doc)
    located = {e.type: (e.page, e.section) for e in entities}
    assert located == {
        T.SNILS: (1, "1. Общие положения"),
        T.INN: (1, "2. Стороны"),
        T.PHONE: (2, "3. Реквизиты"),
        T.EMAIL: (2, "3. Реквизиты"),
    }
    for e in entities:
        assert len(e.value_masked) == e.end - e.start


def test_offsets_in_full_text(doc: ExtractedDocument) -> None:
    by_type = {e.type: doc.text[e.start:e.end] for e in detect(doc)}
    assert by_type[T.SNILS] == SNILS
    assert by_type[T.INN] == INN
    assert by_type[T.PHONE] == PHONE


def test_sorted_by_start(doc: ExtractedDocument) -> None:
    starts = [e.start for e in detect(doc)]
    assert starts == sorted(starts)


def test_empty_document() -> None:
    empty = ExtractedDocument(document_id="e", filename="e.pdf", text="", segments=[])
    assert detect(empty) == []


def test_document_without_pd() -> None:
    clean = build_doc([("ИНН 7707083893, р/с 40702810400000000225, БИК 044525225", 1, None)])
    assert detect(clean) == []


def test_logs_have_no_values(doc: ExtractedDocument, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        detect(doc)
    log_text = caplog.text
    assert "entities=4" in log_text
    for value in (SNILS, INN, PHONE, "ivan.petrov", "345-67-89", INN[:6]):
        assert value not in log_text


# ---------- merge_entities ----------
def test_merge_prefers_regex_over_ner() -> None:
    ner = ent(0, 30, "ner", T.ADDRESS)
    regex = ent(10, 20, "regex")
    assert merge_entities([ner], [regex]) == [regex]
    assert merge_entities([regex], [ner]) == [regex]


def test_merge_same_detector_prefers_longer() -> None:
    short, long_ = ent(0, 5, "ner"), ent(2, 12, "ner")
    assert merge_entities([short, long_]) == [long_]


def test_merge_keeps_disjoint_and_sorts() -> None:
    a, b, c = ent(20, 25), ent(0, 5), ent(5, 10, "dict")
    assert merge_entities([a], [b, c]) == [b, c, a]  # [0,5) и [5,10) не пересекаются


def test_merge_detector_priority_order() -> None:
    llm, model, dict_ = ent(0, 10, "llm"), ent(0, 10, "model"), ent(0, 10, "dict")
    assert merge_entities([model], [llm], [dict_]) == [dict_]


def test_merge_empty() -> None:
    assert merge_entities() == []
    assert merge_entities([], []) == []


# ---------- assign_location ----------
def test_assign_location_boundaries(doc: ExtractedDocument) -> None:
    second = doc.segments[1]
    first_char = ent(second.offset, second.offset + 3)
    last_char_of_first = ent(second.offset - 2, second.offset - 1)
    located = assign_location([first_char, last_char_of_first], doc)
    assert located[0].section == "2. Стороны"
    assert located[1].section == "1. Общие положения"


def test_assign_location_without_segments() -> None:
    bare = ExtractedDocument(document_id="b", filename="b.txt", text="x" * 20, segments=[])
    e = ent(0, 5)
    assert assign_location([e], bare) == [e]
