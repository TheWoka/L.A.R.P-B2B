"""Тесты detect(), merge_entities(), assign_location() (T-007, T-008, T-013a)."""

import logging
from pathlib import Path

import pytest

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, TextSegment
from app.services.detection.detect import (
    assign_location,
    detect,
    merge_entities,
    trim_at_newline,
)
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
    entities = detect(clean)
    # реквизиты организации — не ПДн; с T-013a расчётный счёт — FINANCIAL (detector="dict")
    assert [e for e in entities if e.category == EntityCategory.pd] == []
    assert [(e.type, e.detector) for e in entities] == [(T.FINANCIAL, "dict")]


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


# ---------- сквозной тест T-008: detect → mask (AGENTS.md, правило 5) ----------
def test_detect_then_mask_hides_all_pd() -> None:
    from app.services.masking.masker import mask, mask_preserving_length, unmask

    snils = make_snils(31)
    fio, address = "Иванов Иван Петрович", "г. Томск, ул. Ленина, д. 30, кв. 5"
    e2e = build_doc([
        (f"Исполнитель {fio}, СНИЛС {snils}, тел. {PHONE}.", 1, "1. Стороны"),
        (f"Проживает по адресу: {address}. Цена контракта 120 000 руб.", 1, "1. Стороны"),
    ])
    entities = detect(e2e)
    assert {e.type for e in entities} >= {T.PERSON, T.SNILS, T.PHONE, T.ADDRESS}

    result = mask(e2e.text, entities)
    for value in (fio, "Иванов", snils, PHONE, "345-67-89", address, "ул. Ленина"):
        assert value not in result.masked_text, "в LLM уходит только замаскированный текст"
    for placeholder in ("[PERSON_1]", "[SNILS_1]", "[PHONE_1]", "[ADDRESS_1]"):
        assert placeholder in result.masked_text
    assert "120 000 руб." in result.masked_text   # не ПДн — остаётся
    assert unmask(result.masked_text, result.mapping) == e2e.text

    shown = mask_preserving_length(e2e.text, entities)
    assert len(shown) == len(e2e.text)
    for value in (fio, snils, PHONE, address):
        assert value not in shown


def test_detect_then_mask_capitals() -> None:
    from app.services.masking.masker import mask

    caps = build_doc([("ФИО: ИВАНОВ ИВАН ИВАНОВИЧ, ИП СМИРНОВА АННА СЕРГЕЕВНА", 1, None)])
    result = mask(caps.text, detect(caps))
    assert result.masked_text == "ФИО: [PERSON_1], ИП [PERSON_2]"
    for value in ("ИВАНОВ", "ИВАН", "СМИРНОВА"):
        assert value not in result.masked_text


# ---------- сквозной тест T-013a: ПДн + коммерческая тайна → mask → правила ----------
def test_detect_trade_secret_mask_and_rule(repo_root: Path) -> None:
    from app.schemas import DocPurpose, Marker, MarkerType, RuleContext
    from app.services.masking.masker import mask
    from app.services.rules.engine import evaluate
    from app.services.rules.loader import load_rules

    fio, secret = "Петров Пётр Сергеевич", "себестоимость единицы составляет 38 400 руб."
    e2e = build_doc([
        (f"Менеджер {fio}, тел. {PHONE}.", 1, "1. Контакты"),
        (f"Расчёт цены: {secret}", 2, "2. Цена"),
    ])
    entities = detect(e2e)
    types = {e.type for e in entities}
    assert {T.PERSON, T.PHONE, T.TRADE_SECRET} <= types
    [ts] = [e for e in entities if e.type == T.TRADE_SECRET]
    assert (ts.detector, ts.category, ts.page) == ("dict", EntityCategory.trade_secret, 2)
    assert e2e.text[ts.start:ts.end] == ts.value_masked == secret

    result = mask(e2e.text, entities)
    assert fio not in result.masked_text and PHONE not in result.masked_text
    assert secret in result.masked_text            # маскируются только ПДн (решение 17.10)

    ctx = RuleContext(
        doc=e2e, masked_text=result.masked_text, entities=entities, purpose=DocPurpose.internal,
        markers=[Marker(type=MarkerType.classification_mark, present=False)],
    )
    violations = evaluate(load_rules(repo_root / "data/rules/rules.yaml"), ctx)
    [violation] = [v for v in violations if v.rule_id == "TRADE_SECRET_WITHOUT_MARK"]
    assert [(ev.start, ev.end) for ev in violation.evidence] == [(ts.start, ts.end)]


def test_trade_secret_trimmed_around_ip_person() -> None:
    from app.services.masking.masker import mask

    text = "Наценка ИП Смирнова А.А. составляет 12%"
    entities = detect(build_doc([(text, 1, None)]))
    by_type = {e.type: e for e in entities}
    assert {T.PERSON, T.TRADE_SECRET} <= set(by_type)
    person, ts = by_type[T.PERSON], by_type[T.TRADE_SECRET]
    assert ts.end <= person.start or ts.start >= person.end, "спаны не пересекаются"
    assert ts.value_masked.endswith("12%") and "Смирнов" not in ts.value_masked
    result = mask(text, entities)                  # не падает на пересечении
    assert "Смирнова" not in result.masked_text and "12%" in result.masked_text


# ---------- сквозные тесты T-019a ----------
def _rule_ids(repo_root: Path, doc: ExtractedDocument, purpose: str, consent: bool) -> set[str]:
    from app.schemas import DocPurpose, Marker, MarkerType, RuleContext
    from app.services.masking.masker import mask
    from app.services.rules.engine import evaluate
    from app.services.rules.loader import load_rules

    entities = detect(doc)
    ctx = RuleContext(
        doc=doc, masked_text=mask(doc.text, entities).masked_text, entities=entities,
        purpose=DocPurpose(purpose),
        markers=[Marker(type=MarkerType.consent_clause, present=consent)],
    )
    return {v.rule_id for v in evaluate(load_rules(repo_root / "data/rules/rules.yaml"), ctx)}


def test_org_contacts_do_not_trigger_pd_in_publication(repo_root: Path) -> None:
    tz = build_doc([
        ("ТЕХНИЧЕСКОЕ ЗАДАНИЕ на поставку ИБП. Гарантия 36 месяцев.", 1, "1. Общие"),
        ("Телефон службы поддержки: 8-800-000-00-00, e-mail: support@example.ru.", 1, "2. Связь"),
        ("Приёмная заказчика: 8 (3822) 70-11-22, zakupki@example.ru", 1, "2. Связь"),
    ])
    pd_categories = (EntityCategory.pd, EntityCategory.pd_special)
    assert [e for e in detect(tz) if e.category in pd_categories] == []
    assert "PD_IN_PUBLICATION" not in _rule_ids(repo_root, tz, "publication", consent=False)


def test_special_category_masked_and_rule(repo_root: Path) -> None:
    from app.services.masking.masker import mask

    text = "Работник Сафронов Леонид Игоревич: инвалидность III группы, работы только в помещении."
    doc = build_doc([(text, 1, "Персонал")])
    entities = detect(doc)
    [special] = [e for e in entities if e.type == T.SPECIAL_CATEGORY]
    assert special.category == EntityCategory.pd_special and special.page == 1
    assert "инвалидность" not in special.value_masked
    masked = mask(doc.text, entities).masked_text
    assert "инвалидность" not in masked and "[SPECIAL_CATEGORY_1]" in masked
    assert "SPECIAL_PD_NO_CONSENT" in _rule_ids(repo_root, doc, "internal", consent=False)
    assert "SPECIAL_PD_NO_CONSENT" not in _rule_ids(repo_root, doc, "internal", consent=True)


@pytest.mark.parametrize(
    "text",
    [
        "Поставка лекарственных препаратов для лечения онкологических заболеваний",
        "Расходные материалы для отделения беременных и родильниц",
        "Средства реабилитации для инвалидов",
        "Медицинская помощь при заболеваниях системы кровообращения",
        "Закупка инсулина для пациентов с диабетом",
    ],
)
def test_medical_procurement_is_not_special_category(text: str) -> None:
    entities = detect(build_doc([(text, 1, "Предмет закупки")]))
    assert [e for e in entities if e.type == T.SPECIAL_CATEGORY] == []


def test_disability_next_to_person_is_special_category() -> None:
    text = "Иванов Иван Иванович, инвалид II группы, работает в отделе снабжения."
    entities = detect(build_doc([(text, 1, None)]))
    by_type = {e.type: e for e in entities}
    assert {T.PERSON, T.SPECIAL_CATEGORY} <= set(by_type)
    special = by_type[T.SPECIAL_CATEGORY]
    assert text[special.start:special.end].startswith("инвалид II группы")
    assert special.category == EntityCategory.pd_special


# ---------- T-019b: спаны ПДн не переходят строку ----------
def _span(text: str, value: str, etype: EntityType, category: EntityCategory) -> Entity:
    start = text.index(value)
    return Entity(type=etype, category=category, start=start, end=start + len(value),
                  value_masked=value, confidence=0.9, detector="ner")


def test_trim_person_at_newline_keeps_line_with_most_words() -> None:
    text = "ФИО: Тихонов Арсений Валерьевич\nДата заполнения анкеты: 12.10.2026"
    person = _span(text, "Тихонов Арсений Валерьевич\nДата", EntityType.PERSON, EntityCategory.pd)
    [trimmed] = trim_at_newline([person], text)
    assert text[trimmed.start:trimmed.end] == "Тихонов Арсений Валерьевич"
    assert trimmed.value_masked == "Т██████ А██████ В█████████"
    assert trimmed.type == EntityType.PERSON and trimmed.detector == "ner"


def test_trim_keeps_address_phrases_and_one_line_spans() -> None:
    text = "г. Томск, ул. Ленина,\nд. 5, кв. 3\nНаценка 14%\nдля\nклиента"
    address = _span(text, "г. Томск, ул. Ленина,\nд. 5, кв. 3", EntityType.ADDRESS,
                    EntityCategory.pd)
    secret = _span(text, "Наценка 14%\nдля\nклиента", EntityType.TRADE_SECRET,
                   EntityCategory.trade_secret)
    plain = _span(text, "Наценка 14%", EntityType.PERSON, EntityCategory.pd)
    assert trim_at_newline([address, secret, plain], text) == [address, secret, plain]


def test_trim_initials_tail_before_next_cell() -> None:
    """xlsx: «Павлов В. Д.\nЛичный телефон» — NER дал второй спан «В. Д.\nЛичный»."""
    text = "Контактное лицо | Павлов В. Д.\nЛичный телефон | 8 (999) 000-00-00"
    tail = _span(text, "В. Д.\nЛичный", EntityType.PERSON, EntityCategory.pd)
    [trimmed] = trim_at_newline([tail], text)
    assert text[trimmed.start:trimmed.end] == "В. Д."


def test_trim_drops_span_without_letters() -> None:
    text = "Иванов\n—\n"
    edge = Entity(type=EntityType.PERSON, category=EntityCategory.pd, start=6, end=9,
                  value_masked="\n—\n", confidence=0.9, detector="ner")
    assert trim_at_newline([edge], text) == []
    assert trim_at_newline([], text) == []


def test_detect_person_does_not_swallow_next_line() -> None:
    text = "Анкета члена комиссии\nТихонов Арсений Валерьевич\nДата рождения: 03.04.1988"
    doc = ExtractedDocument(document_id="nl", filename="a.txt", text=text,
                            segments=[TextSegment(id=0, text=text, offset=0)])
    for entity in detect(doc):
        assert "\n" not in text[entity.start:entity.end]
    persons = [text[e.start:e.end] for e in detect(doc) if e.type == EntityType.PERSON]
    assert persons == ["Тихонов Арсений Валерьевич"]
