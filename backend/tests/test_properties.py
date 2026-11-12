"""Property-based тесты главных инвариантов (T-085): Hypothesis, сотни случайных входов на свойство.

Обычные тесты проверяют десяток придуманных примеров. Здесь вход придумывает Hypothesis:
случайный текст и случайные сущности. Проверяются не конкретные ответы, а законы, которые
обязаны выполняться для ЛЮБОГО входа: «длина не меняется», «значение ПДн не остаётся в тексте
для LLM», «координаты цитаты переводятся обратно без сдвига».

Все данные синтетические (AGENTS.md, правило 2): случайные символы и выдуманные вставки.
"""

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from app.schemas import Entity, EntityCategory, EntityType, MaskSpan
from app.services.detection.detect import DETECTOR_PRIORITY, merge_entities, trim_at_newline
from app.services.detection.ocr_normalize import CONFUSABLE_DIGITS, digits_view
from app.services.masking.masker import (
    MASKED_CATEGORIES,
    expand_to_placeholders,
    mask,
    mask_preserving_length,
    mask_value,
    to_original_offset,
    unmask,
)

T = EntityType
C = EntityCategory

PROPERTY = settings(max_examples=300, deadline=None)

CATEGORY_BY_TYPE: dict[EntityType, EntityCategory] = {
    T.PERSON: C.pd, T.PHONE: C.pd, T.SNILS: C.pd, T.EMAIL: C.pd, T.ADDRESS: C.pd,
    T.SPECIAL_CATEGORY: C.pd_special,
    T.TRADE_SECRET: C.trade_secret, T.FINANCIAL: C.financial,   # маска их НЕ трогает
}
PD_TYPES = [T.PERSON, T.PHONE, T.SNILS, T.EMAIL, T.ADDRESS, T.SPECIAL_CATEGORY]
OTHER_TYPES = [T.TRADE_SECRET, T.FINANCIAL]

ALPHABET = (
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " \n\t "
    ".,;:!?-–()+/_@«»\"'№|"
    "█[]"
)
# Выдуманные вставки: чтобы в случайном тексте встречались повторы значений, разные написания
# одного значения, готовые плейсхолдеры и OCR-опечатки. Вероятность собрать их из символов мала.
SNIPPETS = [
    "Иванов Иван", "ИВАНОВ ИВАН", "иванов  иван", "Семёнов", "Семенов",
    "112-233-445 95", "11223344595", "1l2-233-445 9S", "+7 (912) 345-67-89", "89123456789",
    "test@example.ru", "TEST@example.ru", "г. Томск,\nул. Мира, 1",
    "[PERSON_1]", "[PERSON_2]", "[SNILS_1]", "[PHONE_1]", "[EMAIL_", "_1]", "PERSON", "\n",
]

_chunk = st.one_of(st.text(ALPHABET, max_size=20), st.sampled_from(SNIPPETS))
texts = st.one_of(
    st.text(ALPHABET, max_size=400),
    st.lists(_chunk, max_size=40).map(lambda parts: "".join(parts)[:400]),
)
_types = st.one_of(st.sampled_from(PD_TYPES), st.sampled_from(PD_TYPES + OTHER_TYPES))
_detectors = st.sampled_from(sorted(DETECTOR_PRIORITY))
_confidences = st.floats(min_value=0, max_value=1, allow_nan=False)


def make_entity(
    text: str, start: int, end: int, etype: EntityType, detector: str, confidence: float,
) -> Entity:
    return Entity(
        type=etype, category=CATEGORY_BY_TYPE[etype], start=start, end=end,
        value_masked=mask_value(text[start:end]), confidence=confidence, detector=detector,
    )


def _snippet_bounds(text: str) -> list[int]:
    """Границы вставок в тексте — чтобы сущности часто совпадали со вставками целиком."""
    bounds: set[int] = set()
    for snippet in SNIPPETS:
        pos = text.find(snippet)
        while pos != -1:
            bounds.update((pos, pos + len(snippet)))
            pos = text.find(snippet, pos + 1)
    return sorted(bounds)


@st.composite
def text_and_entities(draw: st.DrawFn) -> tuple[str, list[Entity]]:
    """Текст и НЕпересекающиеся сущности (start < end, внутри текста), как после detect().

    Текст режется на отрезки случайными точками; каждый отрезок с вероятностью 1/2 становится
    сущностью. Соседние отрезки могут оба быть сущностями — это стык без зазора.
    """
    text = draw(texts)
    if not text:
        return text, []
    point = st.integers(0, len(text))
    bounds = _snippet_bounds(text)
    if bounds:
        point = st.one_of(point, st.sampled_from(bounds))
    cuts = sorted(draw(st.sets(point, max_size=14)))
    entities: list[Entity] = []
    for start, end in zip(cuts, cuts[1:], strict=False):
        if draw(st.booleans()):
            entities.append(make_entity(
                text, start, end, draw(_types), draw(_detectors), draw(_confidences),
            ))
    return text, entities


@st.composite
def text_and_raw_entities(draw: st.DrawFn) -> tuple[str, list[Entity]]:
    """Текст и сущности, которые МОГУТ пересекаться — сырой выход детекторов до merge_entities."""
    text = draw(texts.filter(bool))
    entities: list[Entity] = []
    for _ in range(draw(st.integers(0, 12))):
        start = draw(st.integers(0, len(text) - 1))
        end = draw(st.integers(start + 1, min(len(text), start + 40)))
        entities.append(make_entity(
            text, start, end, draw(_types), draw(_detectors), draw(_confidences),
        ))
    return text, entities


def pd_only(entities: list[Entity]) -> list[Entity]:
    return [e for e in entities if e.category in MASKED_CATEGORIES]


def gaps(text: str, entities: list[Entity]) -> list[str]:
    """Куски текста между сущностями ПДн — то, что маска обязана оставить как есть."""
    pieces: list[str] = []
    pos = 0
    for e in sorted(pd_only(entities), key=lambda e: e.start):
        pieces.append(text[pos:e.start])
        pos = e.end
    pieces.append(text[pos:])
    return pieces


def overlaps_span(start: int, end: int, spans: list[MaskSpan]) -> bool:
    return any(s.masked_start < end and start < s.masked_end for s in spans)


def cuts_span(offset: int, spans: list[MaskSpan]) -> bool:
    """Смещение попадает строго внутрь плейсхолдера (режет его)."""
    return any(s.masked_start < offset < s.masked_end for s in spans)


# --- a) маскирование с сохранением длины ------------------------------------------------------


@PROPERTY
@given(text_and_entities())
def test_mask_preserving_length_keeps_length_and_outside_text(
    case: tuple[str, list[Entity]],
) -> None:
    text, entities = case
    masked = mask_preserving_length(text, entities)
    assert len(masked) == len(text)   # иначе координаты отчёта (ReportText) поедут
    inside = [False] * len(text)
    for e in pd_only(entities):
        inside[e.start:e.end] = [True] * (e.end - e.start)
        # внутри ПДн не остаётся ни одной цифры (номера закрываются целиком)
        assert not any(ch.isdigit() for ch in masked[e.start:e.end])
    for i, (before, after) in enumerate(zip(text, masked, strict=True)):
        if not inside[i]:
            assert before == after, f"символ вне ПДн изменился на позиции {i}"


# --- b) значение ПДн не доходит до LLM --------------------------------------------------------


@st.composite
def text_with_secret(draw: st.DrawFn) -> tuple[str, list[Entity]]:
    """Как text_and_entities, но с гарантированной сущностью ПДн длиной ≥ 3 («секрет»).

    Секрет вставляется на стык: в начало, в конец или сразу после другой сущности; сущности
    правее сдвигаются. Без этого большинство случайных входов отсеивал бы assume.
    """
    text, entities = draw(text_and_entities())
    secret = draw(st.text(ALPHABET, min_size=3, max_size=25))
    at = draw(st.sampled_from(sorted({0, len(text)} | {e.end for e in entities})))
    shift = len(secret)
    new_text = text[:at] + secret + text[at:]
    moved = [
        e if e.end <= at else e.model_copy(update={"start": e.start + shift, "end": e.end + shift})
        for e in entities
    ]
    moved.append(make_entity(
        new_text, at, at + shift, draw(st.sampled_from(PD_TYPES)), draw(_detectors), 0.9,
    ))
    return new_text, moved


@PROPERTY
@given(text_with_secret())
def test_mask_removes_pd_values_from_text_for_llm(case: tuple[str, list[Entity]]) -> None:
    text, entities = case
    free_text = gaps(text, entities)
    # Проверяемые значения: длиной ≥ 3 и не встречаются в тексте вне сущностей ПДн
    # (иначе значение законно остаётся в тексте — оно там было не как ПДн).
    values = {
        text[e.start:e.end] for e in pd_only(entities)
        if e.end - e.start >= 3 and not any(text[e.start:e.end] in gap for gap in free_text)
    }
    assume(values)
    result = mask(text, entities)
    for value in values:
        pos = result.masked_text.find(value)
        while pos != -1:
            # Единственное допустимое совпадение — случайное, с участием символов самого
            # плейсхолдера: значение «PERSON» внутри «[PERSON_1]». Исходных данных там нет.
            assert overlaps_span(pos, pos + len(value), result.spans), "значение ПДн в masked_text"
            pos = result.masked_text.find(value, pos + 1)


# --- c) unmask возвращает исходный текст ------------------------------------------------------


@PROPERTY
@given(text_and_entities())
def test_unmask_restores_original_text(case: tuple[str, list[Entity]]) -> None:
    text, entities = case
    result = mask(text, entities)
    restored = unmask(result.masked_text, result.mapping)
    # Общий закон: каждая замена раскрывается в значение из mapping, остальное — посимвольно.
    expected: list[str] = []
    pos = 0
    same_spelling = True
    for s in result.spans:
        value = result.mapping[result.masked_text[s.masked_start:s.masked_end]]
        expected += [text[pos:s.orig_start], value]
        same_spelling = same_spelling and value == text[s.orig_start:s.orig_end]
        pos = s.orig_end
    expected.append(text[pos:])
    assert restored == "".join(expected)
    # Точное равенство — когда повторы одного значения написаны одинаково. «Иванов Иван» и
    # «ИВАНОВ ИВАН» по контракту получают один плейсхолдер, и unmask вернёт первое написание.
    if same_spelling:
        assert restored == text


# --- d) перевод координат masked_text → исходный текст ----------------------------------------


@PROPERTY
@given(text_and_entities(), st.data())
def test_to_original_offset_maps_ranges_back(
    case: tuple[str, list[Entity]], data: st.DataObject,
) -> None:
    text, entities = case
    result = mask(text, entities)
    a = data.draw(st.integers(0, len(result.masked_text)), label="a")
    b = data.draw(st.integers(a, len(result.masked_text)), label="b")
    orig_a = to_original_offset(a, result.spans)
    orig_b = to_original_offset(b, result.spans)
    assert 0 <= orig_a <= orig_b <= len(text)
    if not overlaps_span(a, b, result.spans) and not cuts_span(a, result.spans):
        # отрезок целиком вне плейсхолдеров: цитата LLM = тот же кусок исходного текста
        assert text[orig_a:orig_b] == result.masked_text[a:b]


# --- e) расширение цитаты до целых плейсхолдеров ----------------------------------------------


@PROPERTY
@given(text_and_entities(), st.data())
def test_expand_to_placeholders_never_cuts_placeholder(
    case: tuple[str, list[Entity]], data: st.DataObject,
) -> None:
    text, entities = case
    result = mask(text, entities)
    a = data.draw(st.integers(0, len(result.masked_text)), label="a")
    b = data.draw(st.integers(a, len(result.masked_text)), label="b")
    start, end = expand_to_placeholders(a, b, result.spans)
    assert start <= a <= b <= end                       # содержит исходный отрезок
    assert 0 <= start and end <= len(result.masked_text)
    assert not cuts_span(start, result.spans)           # не режет плейсхолдер
    assert not cuts_span(end, result.spans)
    if not cuts_span(a, result.spans):
        assert start == a                               # и не расширяет без нужды
    if not cuts_span(b, result.spans):
        assert end == b


# --- f) цифровой вид OCR-текста ---------------------------------------------------------------


@PROPERTY
@given(st.one_of(st.text(max_size=400), texts))
def test_digits_view_keeps_length_and_non_numeric_text(s: str) -> None:
    view = digits_view(s)
    assert len(view) == len(s)        # координаты находки в копии = координаты в исходном тексте
    if not any(ch.isdigit() for ch in s):
        assert view == s              # нет цифр — нет «числовых токенов», менять нечего
    for before, after in zip(s, view, strict=True):
        # символ либо не меняется, либо это путаемая буква → её цифра
        assert after == before or CONFUSABLE_DIGITS.get(before) == after


# --- g) слияние детекторов и обрезка по переводу строки ---------------------------------------


@PROPERTY
@given(text_and_raw_entities(), st.integers(1, 4))
def test_merge_entities_returns_sorted_non_overlapping(
    case: tuple[str, list[Entity]], groups_count: int,
) -> None:
    _, entities = case
    groups = [entities[i::groups_count] for i in range(groups_count)]
    merged = merge_entities(*groups)
    for prev, cur in zip(merged, merged[1:], strict=False):
        assert prev.start <= cur.start       # отсортированы по start
        assert prev.end <= cur.start         # не пересекаются — иначе mask() бросит ValueError
    assert all(any(m is e for e in entities) for m in merged)   # ничего не выдумано


def _merge_keeps_or_covers(entities: list[Entity]) -> None:
    merged = merge_entities(entities)
    for e in entities:
        # сущность либо осталась, либо уступила пересекающейся с ней оставленной
        assert any(m.start < e.end and e.start < m.end for m in merged), (
            f"{e.type.value} [{e.start}, {e.end}) потеряна без конкурента"
        )


def _plain(start: int, end: int, etype: EntityType, detector: str) -> Entity:
    return make_entity("x" * 20, start, end, etype, detector, 0.9)


@pytest.mark.xfail(strict=True, reason=(
    "найдено T-085: merge_entities теряет сущность без конкурента — B уступила A, затем A "
    "вытеснена C, а B не возвращается и остаётся немаскированной"
))
@PROPERTY
@example([
    _plain(0, 10, T.ADDRESS, "ner"),    # A
    _plain(1, 3, T.PERSON, "ner"),      # B: уступает A (A длиннее)
    _plain(5, 12, T.PHONE, "regex"),    # C: вытесняет A (regex точнее), с B не пересекается
])
@given(text_and_raw_entities().map(lambda case: case[1]))
def test_merge_entities_drops_only_overlapped(entities: list[Entity]) -> None:
    _merge_keeps_or_covers(entities)


@PROPERTY
@given(text_and_raw_entities())
def test_trim_at_newline_leaves_no_newline_in_pd_spans(case: tuple[str, list[Entity]]) -> None:
    text, entities = case
    trimmed = trim_at_newline(entities, text)
    assert len(trimmed) <= len(entities)
    for e in trimmed:
        assert 0 <= e.start < e.end <= len(text)
        if e.category == C.pd and e.type != T.ADDRESS:
            assert "\n" not in text[e.start:e.end]
        assert e.value_masked == mask_value(text[e.start:e.end])
    # ADDRESS, КТ, FINANCIAL и особые категории не режутся (как в коде) — проходят как были
    untouched = [e for e in entities if e.category != C.pd or e.type == T.ADDRESS]
    assert [e for e in trimmed if e.category != C.pd or e.type == T.ADDRESS] == untouched
