"""Тесты NER-детектора ФИО и адресов (T-008). Все ФИО и адреса выдуманные."""

import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection.ner_detector import (
    _ip_name_candidates,
    _load_models,
    _Span,
    _title_caps,
    detect_ner,
)
from app.services.masking.masker import mask_value

T = EntityType
BACKEND_DIR = Path(__file__).resolve().parents[1]


def make_doc(text: str) -> ExtractedDocument:
    return ExtractedDocument(document_id="doc-ner", filename="t.docx", text=text, segments=[])


def found(text: str, etype: EntityType) -> list[str]:
    return [text[e.start:e.end] for e in detect_ner(make_doc(text)) if e.type == etype]


# ---------- PERSON: найдено ----------
@pytest.mark.parametrize(
    ("text", "expected", "confidence"),
    [
        ("Договор подписал Иванов Иван Петрович от имени Заказчика.",
         "Иванов Иван Петрович", 0.85),
        ("Акт подписал Иванов И.И. в присутствии комиссии.", "Иванов И.И.", 0.9),
        ("Акт подписал И.И. Иванов в присутствии комиссии.", "И.И. Иванов", 0.9),
        ("Доверенность выдана Петровой А.С. сроком на год.", "Петровой А.С.", 0.9),
    ],
)
def test_person_found(text: str, expected: str, confidence: float) -> None:
    entities = [e for e in detect_ner(make_doc(text)) if e.type == T.PERSON]
    assert [text[e.start:e.end] for e in entities] == [expected]
    assert entities[0].confidence == confidence


def test_person_captures_initial_dot() -> None:
    text = "Доверенность выдана Петровой А.С. сроком на год."
    (person,) = found(text, T.PERSON)
    assert person.endswith("А.С.")


# ---------- PERSON: не найдено ----------
@pytest.mark.parametrize(
    "text",
    [
        "Договор согласован с Ивановым заранее.",
        "Склад находится на ул. Ленина.",
        "Поставщиком выступает ООО «Ромашка».",
        "площадь имени Пушкина открыта для посещения.",
    ],
)
def test_person_not_found(text: str) -> None:
    assert found(text, T.PERSON) == []


# ---------- PERSON: индивидуальный предприниматель ----------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ИП Смирнова Анна Сергеевна", "Смирнова Анна Сергеевна"),
        ("Индивидуальный предприниматель Петров П.П.", "Петров П.П."),
        ("Исполнитель: ИП Смирнова Анна Сергеевна, ИНН 701700000000.",
         "Смирнова Анна Сергеевна"),
        ("Поставщик И.П. Смирнова А.С. обязуется поставить товар.", "Смирнова А.С."),
        ("Поставщик — индивидуальный предприниматель Иванов Иван Иванович.",
         "Иванов Иван Иванович"),
    ],
)
def test_individual_entrepreneur_is_person(text: str, expected: str) -> None:
    entities = [e for e in detect_ner(make_doc(text)) if e.type == T.PERSON]
    assert [text[e.start:e.end] for e in entities] == [expected]
    assert entities[0].confidence == 0.9
    assert entities[0].category == EntityCategory.pd


def test_ip_remainder_of_org_span() -> None:
    # Natasha вернула всю конструкцию как ORG — берём остаток после префикса
    text = "ИП Смирнова Анна Сергеевна"
    spans = _ip_name_candidates(text, [_Span(0, len(text))])
    assert (3, len(text)) in [(s.start, s.end) for s in spans]
    assert _ip_name_candidates("ООО Смирнов и К", [_Span(0, 15)]) == []


@pytest.mark.parametrize(
    "text",
    ["ООО Смирнов и К", "Поставщик ООО Смирнов и К обязуется.", "ИП не зарегистрирован."],
)
def test_not_individual_entrepreneur(text: str) -> None:
    assert found(text, T.PERSON) == []


# ---------- PERSON: ФИО заглавными ----------
def test_person_in_capitals() -> None:
    text = "ФИО: ИВАНОВ ИВАН ИВАНОВИЧ"
    (entity,) = [e for e in detect_ner(make_doc(text)) if e.type == T.PERSON]
    assert text[entity.start:entity.end] == "ИВАНОВ ИВАН ИВАНОВИЧ"
    assert entity.value_masked == "И█████ И███ И███████"   # по исходному тексту


def test_person_in_capitals_with_initials() -> None:
    assert found("ПОДПИСАЛ ПЕТРОВ И.И. В ПРИСУТСТВИИ КОМИССИИ", T.PERSON) == ["ПЕТРОВ И.И."]


@pytest.mark.parametrize(
    "text",
    ["ООО «РОМАШКА»", "ДОГОВОР ПОСТАВКИ № 15. ОБЩИЕ ПОЛОЖЕНИЯ. ПРЕДМЕТ КОНТРАКТА"],
)
def test_capitals_without_person(text: str) -> None:
    assert found(text, T.PERSON) == []


def test_title_caps_keeps_length_and_abbreviations() -> None:
    text = "ООО «РОМАШКА», ИНН, КПП, ОГРН, НДС 20 %, РФ, 44-ФЗ; ИВАНОВ ИВАН, ИВАНОВА-ПЕТРОВА"
    result = _title_caps(text)
    assert len(result) == len(text)
    assert result == (
        "ООО «Ромашка», ИНН, КПП, ОГРН, НДС 20 %, РФ, 44-ФЗ; Иванов Иван, Иванова-Петрова"
    )
    assert _title_caps("Иванов Иван, ivan, Ё") == "Иванов Иван, ivan, Ё"


# ---------- ADDRESS: ПДн ----------
@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("Получатель: г. Томск, ул. Ленина, д. 30, кв. 5. Срок доставки — 3 дня.",
         "г. Томск, ул. Ленина, д. 30, кв. 5"),
        ("Заявитель, проживающий по адресу: г. Томск, ул. Советская, д. 4, подал жалобу.",
         "г. Томск, ул. Советская, д. 4"),
        ("Ответственный — Смирнова Анна Сергеевна, г. Томск, ул. Учебная, д. 8.",
         "г. Томск, ул. Учебная, д. 8"),
        ("Исполнитель Иванов Иван Петрович, проживающий по адресу: 634050, Томская обл., "
         "г. Томск, пр-кт Кирова, д. 12, кв. 7, обязуется выполнить работы.",
         "634050, Томская обл., г. Томск, пр-кт Кирова, д. 12, кв. 7"),
    ],
)
def test_personal_address_glued(text: str, address: str) -> None:
    entities = [e for e in detect_ner(make_doc(text)) if e.type == T.ADDRESS]
    assert len(entities) == 1, "адрес склеен в одну сущность"
    (entity,) = entities
    assert text[entity.start:entity.end] == address
    assert entity.confidence == 0.8
    assert entity.category == EntityCategory.pd


# ---------- ADDRESS: не ПДн ----------
@pytest.mark.parametrize(
    "text",
    [
        "Юридический адрес: 125009, г. Москва, ул. Тверская, д. 1. Телефон указан ниже.",
        "Поставщик ООО «Ромашка», г. Томск, ул. Ленина, д. 30, обязуется поставить товар.",
        "Место нахождения: г. Томск, пр-кт Ленина, д. 30.",
        "Доставка в г. Томск без указания улицы.",
    ],
)
def test_organization_address_not_returned(text: str) -> None:
    assert found(text, T.ADDRESS) == []


def test_initial_not_read_as_street() -> None:
    # AddrExtractor читает «Петров П.» как «переулок Петров»; ФИО не должно стать адресом
    text = "Контактное лицо Петров П.П., д. 5."
    entities = detect_ner(make_doc(text))
    (person,) = [e for e in entities if e.type == T.PERSON]
    assert text[person.start:person.end] == "Петров П.П."
    for e in entities:
        if e.type == T.ADDRESS:
            assert e.end <= person.start or e.start >= person.end


# ---------- общие свойства ----------
SAMPLE = (
    "Исполнитель Иванов Иван Петрович, проживающий по адресу: 634050, Томская обл., "
    "г. Томск, пр-кт Кирова, д. 12, кв. 7. Акт подписал Петров И.И."
)


def test_value_masked_and_detector() -> None:
    entities = detect_ner(make_doc(SAMPLE))
    assert {e.type for e in entities} == {T.PERSON, T.ADDRESS}
    for e in entities:
        assert isinstance(e, Entity)
        assert e.detector == "ner"
        assert len(e.value_masked) == e.end - e.start
        assert e.value_masked == mask_value(SAMPLE[e.start:e.end])


def test_sorted_and_offsets_in_original_text() -> None:
    entities = detect_ner(make_doc(SAMPLE))
    assert [e.start for e in entities] == sorted(e.start for e in entities)
    assert SAMPLE[entities[0].start:entities[0].end] == "Иванов Иван Петрович"


def test_empty_text() -> None:
    assert detect_ner(make_doc("")) == []
    assert detect_ner(make_doc("   \n ")) == []


def test_models_loaded_once() -> None:
    _load_models.cache_clear()
    detect_ner(make_doc(SAMPLE))
    detect_ner(make_doc("Акт подписал Петров И.И."))
    info = _load_models.cache_info()
    assert info.misses == 1
    assert info.hits >= 1


def test_models_not_loaded_on_import() -> None:
    code = (
        "import sys, app.services.detection.ner_detector\n"
        "assert 'natasha' not in sys.modules, 'natasha loaded on import'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, capture_output=True, text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_logs_have_no_values(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        detect_ner(make_doc(SAMPLE))
    assert "entities=" in caplog.text
    for value in ("Иванов", "Иван", "Петров", "Кирова", "634050", "Томск"):
        assert value not in caplog.text


# ---------- производительность ----------
_CLAUSES = (
    "{i}.1. Поставщик обязуется поставить товар в соответствии со спецификацией "
    "(Приложение № {i}) в срок не позднее {d} календарных дней с даты заключения контракта.",
    "{i}.2. Приёмка товара осуществляется по месту нахождения Заказчика в течение {d} "
    "рабочих дней, т.е. с оформлением акта и т.д. Претензии направляются письменно.",
    "{i}.3. Цена контракта составляет {d} 000 рублей, включая НДС 20 %, и является твёрдой.",
)
_PD_CLAUSES = (
    "Представитель Иванов Иван Петрович, проживающий по адресу: 634050, Томская обл., "
    "г. Томск, пр-кт Кирова, д. {d}, кв. {i}, действует на основании доверенности.",
    "Юридический адрес: 125009, г. Москва, ул. Тверская, д. {d}, стр. 2.",
    "Контактное лицо: Петрова А.С. Ответственный за исполнение — Сидоров И.И.",
)


def make_long_text(size: int, pd_every: int = 3) -> str:
    """Синтетический контракт: уникальные пункты, блок с ПДн в каждом pd_every-м разделе."""
    parts: list[str] = []
    i = length = 0
    while length < size:
        i += 1
        block = list(_CLAUSES) + (list(_PD_CLAUSES) if i % pd_every == 0 else [])
        for clause in block:
            parts.append(clause.format(i=i, d=(i * 7) % 97 + 1))
            length += len(parts[-1]) + 1
    return "\n".join(parts)[:size]


@pytest.mark.slow
def test_performance_200k_chars() -> None:
    text = make_long_text(200_000)
    _load_models()  # загрузка моделей не входит в замер
    started = time.perf_counter()
    entities = detect_ner(make_doc(text))
    elapsed = time.perf_counter() - started
    assert entities
    assert elapsed < 30, f"NER на 200 тыс. символов: {elapsed:.1f} с (цель ≤ 15 с)"
