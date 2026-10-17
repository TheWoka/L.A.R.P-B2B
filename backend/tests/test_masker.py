"""Тесты маскирования: mask, unmask, to_original_offset, mask_preserving_length (T-008).

Все значения выдуманные.
"""

import pytest

from app.schemas import Entity, EntityCategory, EntityType, MaskResult
from app.services.masking.masker import (
    expand_to_placeholders,
    mask,
    mask_preserving_length,
    mask_value,
    to_original_offset,
    unmask,
)

T = EntityType
C = EntityCategory


def ents(text: str, *items: tuple[str, EntityType] | tuple[str, EntityType, int]) -> list[Entity]:
    """Сущности по подстрокам: (значение, тип[, номер вхождения])."""
    result: list[Entity] = []
    for item in items:
        value, etype = item[0], item[1]
        occurrence = item[2] if len(item) == 3 else 0
        start = -1
        for _ in range(occurrence + 1):
            start = text.index(value, start + 1)
        category = {T.TRADE_SECRET: C.trade_secret, T.FINANCIAL: C.financial,
                    T.SPECIAL_CATEGORY: C.pd_special}.get(etype, C.pd)
        result.append(Entity(
            type=etype, category=category, start=start, end=start + len(value),
            value_masked=mask_value(value), confidence=0.9, detector="regex",
        ))
    return result


def check_invariants(text: str, result: MaskResult) -> None:
    """Инварианты spans из задачи T-008."""
    assert [s.masked_start for s in result.spans] == sorted(s.masked_start for s in result.spans)
    orig_pos = masked_pos = 0
    for s in result.spans:
        placeholder = result.masked_text[s.masked_start:s.masked_end]
        assert placeholder in result.mapping
        assert text[s.orig_start:s.orig_end]  # исходное значение
        # текст между заменами совпадает посимвольно
        assert text[orig_pos:s.orig_start] == result.masked_text[masked_pos:s.masked_start]
        orig_pos, masked_pos = s.orig_end, s.masked_end
    assert text[orig_pos:] == result.masked_text[masked_pos:]


TEXT = (
    "Исполнитель Иванов Иван Петрович (СНИЛС 112-233-445 95) и заказчик "
    "Смирнова Анна Сергеевна. Повторно: Иванов Иван Петрович, СНИЛС 11223344595."
)


@pytest.fixture
def text_entities() -> list[Entity]:
    return ents(
        TEXT,
        ("Иванов Иван Петрович", T.PERSON),
        ("112-233-445 95", T.SNILS),
        ("Смирнова Анна Сергеевна", T.PERSON),
        ("Иванов Иван Петрович", T.PERSON, 1),
        ("11223344595", T.SNILS),
    )


# ---------- mask ----------
def test_mask_placeholders(text_entities: list[Entity]) -> None:
    result = mask(TEXT, text_entities)
    assert result.masked_text == (
        "Исполнитель [PERSON_1] (СНИЛС [SNILS_1]) и заказчик "
        "[PERSON_2]. Повторно: [PERSON_1], СНИЛС [SNILS_1]."
    )
    assert result.mapping == {
        "[PERSON_1]": "Иванов Иван Петрович",
        "[PERSON_2]": "Смирнова Анна Сергеевна",
        "[SNILS_1]": "112-233-445 95",   # первое написание; повтор совпал по цифрам
    }
    assert len(result.spans) == 5
    check_invariants(TEXT, result)


def test_mask_spans_point_to_values(text_entities: list[Entity]) -> None:
    result = mask(TEXT, text_entities)
    for span, entity in zip(result.spans, sorted(text_entities, key=lambda e: e.start),
                            strict=True):
        assert (span.orig_start, span.orig_end) == (entity.start, entity.end)


def test_same_person_different_case_one_placeholder() -> None:
    text = "Подписал Иванов И.И., согласовал ИВАНОВ  И.И., утвердил Иванов И.И."
    entities = ents(text, ("Иванов И.И.", T.PERSON), ("ИВАНОВ  И.И.", T.PERSON),
                    ("Иванов И.И.", T.PERSON, 1))
    result = mask(text, entities)
    assert result.masked_text == (
        "Подписал [PERSON_1], согласовал [PERSON_1], утвердил [PERSON_1]"
    )
    # восстанавливается первое написание
    assert unmask(result.masked_text, result.mapping) == (
        "Подписал Иванов И.И., согласовал Иванов И.И., утвердил Иванов И.И."
    )


def test_yo_normalized() -> None:
    text = "Адрес: ул. Зелёная, д. 1; повторно ул. Зеленая, д. 1."
    result = mask(text, ents(text, ("ул. Зелёная, д. 1", T.ADDRESS),
                              ("ул. Зеленая, д. 1", T.ADDRESS)))
    assert result.masked_text == "Адрес: [ADDRESS_1]; повторно [ADDRESS_1]."


def test_numbering_per_type() -> None:
    text = "Тел. +7 (912) 345-67-89, почта anna@example.org, тел. 8 913 111-22-33"
    result = mask(text, ents(text, ("+7 (912) 345-67-89", T.PHONE),
                              ("anna@example.org", T.EMAIL), ("8 913 111-22-33", T.PHONE)))
    assert result.masked_text == "Тел. [PHONE_1], почта [EMAIL_1], тел. [PHONE_2]"


def test_trade_secret_and_financial_not_masked() -> None:
    text = "Себестоимость 38 400 руб., счёт 40702810400000000225, менеджер Иванов Иван."
    entities = ents(text, ("Себестоимость 38 400 руб.", T.TRADE_SECRET),
                    ("40702810400000000225", T.FINANCIAL), ("Иванов Иван", T.PERSON))
    result = mask(text, entities)
    assert result.masked_text == (
        "Себестоимость 38 400 руб., счёт 40702810400000000225, менеджер [PERSON_1]."
    )
    assert list(result.mapping) == ["[PERSON_1]"]
    assert len(result.spans) == 1


def test_pd_special_masked() -> None:
    text = "Справка: инвалидность II группы."
    result = mask(text, ents(text, ("инвалидность II группы", T.SPECIAL_CATEGORY)))
    assert result.masked_text == "Справка: [SPECIAL_CATEGORY_1]."


def test_overlap_raises() -> None:
    text = "Иванов Иван Петрович"
    a = ents(text, ("Иванов Иван", T.PERSON))[0]
    b = ents(text, ("Иван Петрович", T.PERSON))[0]
    with pytest.raises(ValueError):
        mask(text, [a, b])
    with pytest.raises(ValueError):
        mask_preserving_length(text, [b, a])


def test_out_of_range_raises() -> None:
    entity = ents("Иванов Иван", ("Иванов Иван", T.PERSON))[0]
    with pytest.raises(ValueError):
        mask("Иванов", [entity])


def test_unsorted_entities_accepted(text_entities: list[Entity]) -> None:
    assert mask(TEXT, list(reversed(text_entities))) == mask(TEXT, text_entities)


def test_empty_entities() -> None:
    result = mask("Текст без ПДн.", [])
    assert result.masked_text == "Текст без ПДн."
    assert result.spans == []
    assert result.mapping == {}


def test_existing_placeholder_in_text_is_skipped() -> None:
    text = "Шаблон: [PERSON_1]. Подписал Иванов Иван."
    result = mask(text, ents(text, ("Иванов Иван", T.PERSON)))
    assert result.masked_text == "Шаблон: [PERSON_1]. Подписал [PERSON_2]."
    assert unmask(result.masked_text, result.mapping) == text


# ---------- unmask ----------
def test_unmask_roundtrip(text_entities: list[Entity]) -> None:
    result = mask(TEXT, text_entities)
    # значения уникальны с точностью до написания СНИЛС — проверяем на тексте без повторов
    unique = ents(TEXT, ("Иванов Иван Петрович", T.PERSON), ("112-233-445 95", T.SNILS),
                  ("Смирнова Анна Сергеевна", T.PERSON))
    unique_result = mask(TEXT, unique)
    assert unmask(unique_result.masked_text, unique_result.mapping) == TEXT
    assert "Иванов" in unmask(result.masked_text, result.mapping)


def test_unmask_unknown_placeholder_kept() -> None:
    assert unmask("[PERSON_1] и [PERSON_9]", {"[PERSON_1]": "Иванов"}) == "Иванов и [PERSON_9]"


def test_unmask_no_recursive_expansion() -> None:
    assert unmask("[PERSON_1]", {"[PERSON_1]": "[PERSON_2]", "[PERSON_2]": "x"}) == "[PERSON_2]"


# ---------- to_original_offset ----------
OFFSET_TEXT = "Звонить Иванову Ивану по тел. +7 (912) 345-67-89 до 18:00."


@pytest.fixture
def offset_result() -> MaskResult:
    return mask(OFFSET_TEXT, ents(OFFSET_TEXT, ("Иванову Ивану", T.PERSON),
                                  ("+7 (912) 345-67-89", T.PHONE)))


def test_offset_before_first(offset_result: MaskResult) -> None:
    assert offset_result.masked_text == "Звонить [PERSON_1] по тел. [PHONE_1] до 18:00."
    for i in range(len("Звонить ")):
        assert to_original_offset(i, offset_result.spans) == i


def test_offset_between_and_after(offset_result: MaskResult) -> None:
    masked = offset_result.masked_text
    between = masked.index(" по тел.")
    assert to_original_offset(between, offset_result.spans) == OFFSET_TEXT.index(" по тел.")
    after = masked.index(" до 18:00")
    assert to_original_offset(after, offset_result.spans) == OFFSET_TEXT.index(" до 18:00")
    assert to_original_offset(len(masked), offset_result.spans) == len(OFFSET_TEXT)


def test_offset_inside_and_on_borders(offset_result: MaskResult) -> None:
    span = offset_result.spans[0]
    assert to_original_offset(span.masked_start, offset_result.spans) == span.orig_start
    assert to_original_offset(span.masked_start + 3, offset_result.spans) == span.orig_start
    assert to_original_offset(span.masked_end - 1, offset_result.spans) == span.orig_start
    assert to_original_offset(span.masked_end, offset_result.spans) == span.orig_end


def test_offset_without_spans() -> None:
    assert to_original_offset(0, []) == 0
    assert to_original_offset(42, []) == 42


def test_offset_negative_raises(offset_result: MaskResult) -> None:
    with pytest.raises(ValueError):
        to_original_offset(-1, offset_result.spans)


def test_offset_monotonic_and_text_matches(offset_result: MaskResult) -> None:
    masked = offset_result.masked_text
    values = [to_original_offset(i, offset_result.spans) for i in range(len(masked) + 1)]
    assert values == sorted(values), "значения не убывают"
    inside = {i for s in offset_result.spans for i in range(s.masked_start + 1, s.masked_end)}
    for i in range(len(masked)):
        if i not in inside and not any(s.masked_start == i for s in offset_result.spans):
            assert OFFSET_TEXT[values[i]] == masked[i]  # вне плейсхолдеров символы совпадают


def test_quote_roundtrip_via_offsets(offset_result: MaskResult) -> None:
    masked = offset_result.masked_text
    quote = "[PERSON_1] по тел. [PHONE_1]"
    start = masked.index(quote)
    orig_start = to_original_offset(start, offset_result.spans)
    orig_end = to_original_offset(start + len(quote), offset_result.spans)
    assert OFFSET_TEXT[orig_start:orig_end] == "Иванову Ивану по тел. +7 (912) 345-67-89"


# ---------- expand_to_placeholders ----------
def test_expand_to_placeholders(offset_result: MaskResult) -> None:
    person, phone = offset_result.spans
    spans = offset_result.spans
    # цитата начинается внутри [PERSON_1] и кончается внутри [PHONE_1]
    assert expand_to_placeholders(person.masked_start + 2, phone.masked_end - 2, spans) == (
        person.masked_start, phone.masked_end,
    )
    # границы на краях плейсхолдеров и вне их не меняются
    assert expand_to_placeholders(person.masked_start, phone.masked_end, spans) == (
        person.masked_start, phone.masked_end,
    )
    assert expand_to_placeholders(0, 5, spans) == (0, 5)
    assert expand_to_placeholders(3, 3, []) == (3, 3)
    with pytest.raises(ValueError):
        expand_to_placeholders(5, 2, spans)


# ---------- mask_preserving_length ----------
@pytest.mark.parametrize(
    ("value", "etype", "expected"),
    [
        ("Иванов Иван", T.PERSON, "И█████ И███"),
        ("123-456-789 00", T.SNILS, "███-███-███ ██"),
        ("+7 (912) 345-67-89", T.PHONE, "+█ (███) ███-██-██"),
        ("ivan.petrov@mail.ru", T.EMAIL, "i███.p█████@m███.r█"),
    ],
)
def test_preserving_length_contract_table(value: str, etype: EntityType, expected: str) -> None:
    text = f"Данные: {value}."
    result = mask_preserving_length(text, ents(text, (value, etype)))
    assert result == f"Данные: {expected}."


def test_preserving_length_slices(text_entities: list[Entity]) -> None:
    result = mask_preserving_length(TEXT, text_entities)
    assert len(result) == len(TEXT)
    for e in text_entities:
        assert result[e.start:e.end] == mask_value(TEXT[e.start:e.end])
        assert result[e.start:e.end] == e.value_masked
    # вне сущностей текст не меняется
    covered = {i for e in text_entities for i in range(e.start, e.end)}
    assert all(result[i] == TEXT[i] for i in range(len(TEXT)) if i not in covered)


def test_preserving_length_trade_secret_unchanged() -> None:
    text = "Себестоимость 38 400 руб., менеджер Иванов Иван."
    entities = ents(text, ("Себестоимость 38 400 руб.", T.TRADE_SECRET), ("Иванов Иван", T.PERSON))
    assert mask_preserving_length(text, entities) == (
        "Себестоимость 38 400 руб., менеджер И█████ И███."
    )


def test_preserving_length_empty() -> None:
    assert mask_preserving_length("", []) == ""
    assert mask_preserving_length("Без ПДн", []) == "Без ПДн"

