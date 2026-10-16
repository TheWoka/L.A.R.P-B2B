"""Тесты regex-детектора ПДн (T-007).

Только синтетика: номера с контрольными суммами генерируются помощниками из
test_validators; реквизиты организаций — открытые данные юрлиц (не ПДн).
"""

import time
from collections import Counter

import pytest

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, TextSegment
from app.services.detection.regex_detector import (
    CONF_ACCOUNT_NO_BIK,
    CONF_CHECKSUM,
    CONF_FORMAT,
    CONF_PHONE_LOCAL,
    CONF_ROLE_EMAIL,
    CONF_TOLL_FREE,
    detect_regex,
)
from tests.test_validators import (
    break_digit,
    make_account,
    make_bik,
    make_card,
    make_inn10,
    make_inn12,
    make_snils,
)

T = EntityType


def make_doc(text: str) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="doc-test",
        filename="test.docx",
        text=text,
        segments=[TextSegment(id=0, text=text, offset=0, page=1)],
    )


def run(text: str) -> list[Entity]:
    return detect_regex(make_doc(text))


def assert_found(text: str, etype: EntityType, value: str) -> Entity:
    """Ровно одна сущность нужного типа с точными координатами значения."""
    entities = run(text)
    start = text.index(value)
    matches = [e for e in entities if e.type == etype]
    assert len(matches) == 1, f"{etype}: {[(e.type, e.start, e.end) for e in entities]}"
    ent = matches[0]
    assert (ent.start, ent.end) == (start, start + len(value))
    assert text[ent.start:ent.end] == value
    assert len(ent.value_masked) == ent.end - ent.start
    assert ent.category == EntityCategory.pd
    assert ent.detector == "regex"
    assert 0 <= ent.confidence <= 1
    return ent


# ---------- Позитивы: (тип, шаблон предложения, значение) ----------
_BIK = make_bik(11)
_SNILS = make_snils(1)

POSITIVES: list[tuple[EntityType, str, str]] = [
    # СНИЛС
    (T.SNILS, "Работник предъявил СНИЛС {} для оформления.", make_snils(1)),
    (T.SNILS, "Страховой номер: {}.", make_snils(2).replace("-", " ")),
    (T.SNILS, "СНИЛС {} указан в анкете.", make_snils(3, sep=False)),
    (T.SNILS, "Номер индивидуального лицевого счёта {}", make_snils(4)),
    (T.SNILS, "Страховое свидетельство № {}", make_snils(5, sep=False)),
    # ИНН физлица
    (T.INN, "ИНН исполнителя {}, самозанятый.", make_inn12(1)),
    (T.INN, "Индивидуальный предприниматель (ИНН: {}) обязуется", make_inn12(2)),
    (T.INN, "Реквизиты физлица {} указаны в приложении.", make_inn12(3)),
    # Паспорт
    (T.PASSPORT, "Паспорт гражданина РФ {} выдан ОВД района.", "45 06 123456"),
    (T.PASSPORT, "паспорт: {}", "4506 654321"),
    (T.PASSPORT, "Документ: серия {}, выдан 12.03.2010.", "4512987654"),
    (T.PASSPORT, "Паспорт серия {} выдан отделом УФМС.", "4510 № 112233"),
    (T.PASSPORT, "паспорт серии {} выдан ОВД", "45 06 № 123456"),
    # Карта
    (T.CARD, "Оплата картой {} принята.", make_card("4276", 1)),
    (T.CARD, "Номер {} для возврата средств.", make_card("5469", 2, sep="-")),
    (T.CARD, "Номер карты: {}.", make_card("2200", 3, sep="")),
    (T.CARD, "Card {} (19 digits)", make_card("6", 4, length=19, sep="")),
    # Счёт физлица
    (T.BANK_ACCOUNT, "Счёт получателя {}, БИК " + _BIK + ".", make_account("40817810", _BIK, 1)),
    (T.BANK_ACCOUNT, "Перечислить на счёт {} (вклад).", make_account("42307810", _BIK, 2)),
    (T.BANK_ACCOUNT, "Лицевой счёт {} открыт в банке.", make_account("40820810", _BIK, 3)),
    # Телефон
    (T.PHONE, "Контактный телефон {} (будни).", "+7 (912) 345-67-89"),
    (T.PHONE, "Звонить по номеру {}.", "8 912 345 67 89"),
    (T.PHONE, "Тел.: {}", "+79123456789"),
    (T.PHONE, "Приёмная: {}, доб. 12.", "8(3822)12-34-56"),
    (T.PHONE, "WhatsApp {} — менеджер", "8-923-111-22-33"),
    (T.PHONE, "Тел.: {}, с 9 до 18", "(912) 345-67-89"),
    (T.PHONE, "моб. {} (Иван)", "912 345-67-89"),
    (T.PHONE, "Телефон/факс {}", "(3822) 12-34-56"),
    # Email
    (T.EMAIL, "Пишите на {} до пятницы.", "ivan.petrov@mail.ru"),
    (T.EMAIL, "Почта: {}.", "petrov_a@почта.рф"),
    (T.EMAIL, "Ответственный: {}", "a.sidorova+tender@example.com"),
    # Дата рождения
    (T.BIRTH_DATE, "Дата рождения: {}.", "01.02.1990"),
    (T.BIRTH_DATE, "Родился {} в г. Томске.", "1.2.1990"),
    (T.BIRTH_DATE, "дата рожд. {}", "01/02/1990"),
    (T.BIRTH_DATE, "Сведения о рождении: {}", "1990-02-01"),
    (T.BIRTH_DATE, "Родилась {} г. в Новосибирске.", "1 февраля 1990"),
    (T.BIRTH_DATE, "Петров П.П., {} г.р., проживает", "15.07.1985"),
    (T.BIRTH_DATE, "Д.р. {}", "30.12.2001"),
]

# Ещё позитивы для мини-метрики (форматы с NBSP, тире и т.п.)
EXTRA_POSITIVES: list[tuple[EntityType, str, str]] = [
    (T.SNILS, "СНИЛС {}", make_snils(6).replace("-", "—").replace(" ", " ")),
    (T.CARD, "Карта {}", make_card("4", 5, sep=" ")),
    (T.PHONE, "Тел. {}", "+7 912 345–67–89"),
    (T.BANK_ACCOUNT, "Р/с физлица {}", "40817 810 0 1234 5678901"),
    (T.EMAIL, "e-mail: {}", "sidorov@mail.ru"),
]


@pytest.mark.parametrize(("etype", "template", "value"), POSITIVES)
def test_positive(etype: EntityType, template: str, value: str) -> None:
    assert_found(template.format(value), etype, value)


def test_masked_values() -> None:
    text = "Тел. +7 (912) 345-67-89, email ivan.petrov@mail.ru"
    by_type = {e.type: e.value_masked for e in run(text)}
    assert by_type[T.PHONE] == "+█ (███) ███-██-██"
    assert by_type[T.EMAIL] == "i███.p█████@m███.r█"


def test_nbsp_and_em_dash_in_numbers() -> None:
    snils = make_snils(7).replace("-", "—").replace(" ", " ")
    ent = assert_found(f"СНИЛС {snils} работника", T.SNILS, snils)
    assert ent.value_masked == "███—███—███ ██"
    card = make_card("4276", 8, sep="–")
    assert_found(f"Карта {card}", T.CARD, card)
    phone = "8 912 345—67—89"
    assert_found(f"Тел. {phone}", T.PHONE, phone)


def test_confidences() -> None:
    acc_no_bik = make_account("40817810", _BIK, 5)
    assert assert_found(f"Счёт {acc_no_bik}", T.BANK_ACCOUNT, acc_no_bik).confidence == (
        CONF_ACCOUNT_NO_BIK
    )
    acc = make_account("40817810", _BIK, 6)
    text = f"Счёт {acc}\nБИК {_BIK}"
    entities = run(text)
    assert [e.confidence for e in entities] == [CONF_CHECKSUM]
    assert assert_found("Пишите: zakupki@org.ru", T.EMAIL, "zakupki@org.ru").confidence == (
        CONF_ROLE_EMAIL
    )
    assert assert_found("info2@org.ru", T.EMAIL, "info2@org.ru").confidence == CONF_ROLE_EMAIL


def test_account_wrong_key_with_bik_rejected() -> None:
    acc = break_digit(make_account("40817810", _BIK, 7), 12)
    assert run(f"Счёт {acc}, БИК {_BIK}") == []


def test_card_with_trailing_group_of_three() -> None:
    card = make_card("4276", 9)
    assert_found(f"Карта {card} 123", T.CARD, card)


# ---------- Негативы ----------
NEGATIVES: list[str] = [
    f"ИНН {make_inn10(1)} КПП 773601001",
    "ИНН 7707083893, ОГРН 1027700132195",
    "ОГРНИП 304770000123456 выдан 01.02.2004",
    "БИК 044525225, к/с 30101810400000000225",
    "ОКПО 00032537, ОКТМО 45380000",
    "ОКТМО 79701000001",
    "р/с 40702810400000000225 в ПАО Сбербанк, БИК 044525225",
    "Расчётный счёт 40702 810 4 0000 0000225",
    "Счёт 4060 2810 1234 5678 9012 организации",
    "ИКЗ 263770708389377360100100010010000244",
    "Номер контракта 0365100012326000123",
    "Цена контракта 1 234 567,89 руб., НДС 205 761,32 руб.",
    "Договор от 15.10.2026 № 12/2026",
    "Срок поставки до 2026-12-31 включительно",
    "Извещение опубликовано 1 февраля 2026 г.",
    "Код 4506123456 указан в спецификации",
    "Код товара 45 06 123456",
    "Артикул 12345678901 на складе",
    f"Номер заказа {make_snils(9, sep=False)}",
    f"Номер {break_digit(make_card('4276', 10, sep=''))} в реестре",
    f"Номер {break_digit(make_card('4276', 11))} в реестре",
    f"СНИЛС {break_digit(make_snils(12))}",
    f"ИНН {break_digit(make_inn12(13))}",
    "Паспорт 0006 123456 выдан",
    "Паспорт серия 4550 № 123456",
    "Дата рождения 31.02.1990",
    "Дата рождения: 01.01.1850",
    "Телефон 8 (112) 345-67-89",
    "Телефон +1 202 555 0143",
    "Версия 1.2.1990 протокола",
    # Ревью T-007: «серия» без паспорта, телефон без кода страны и без метки
    "Серия поставки 2023 номер 123456",
    "Серия лекарственного препарата 4521 номер партии 210987",
    "серии 63 01 445566 — данные представителя",
    "Код позиции (912) 345-67-89 в каталоге",
    "Номер 912 345-67-89 по реестру",
    "Артикул (3822) 12-34-56",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative(text: str) -> None:
    found = run(text)
    assert found == [], [(e.type, text[e.start:e.end]) for e in found]


# ---------- Ревью T-007: паспорт и телефон ----------
def test_passport_context_word_counts_once() -> None:
    text = "Паспорт 4506 123456, тел. 4012 345678"
    passports = [text[e.start:e.end] for e in run(text) if e.type == T.PASSPORT]
    assert passports == ["4506 123456"]


def test_passport_series_word_alone_is_not_context() -> None:
    assert run("Серия поставки 2023 номер 123456") == []
    assert run("Серия лекарственного препарата 4521 номер партии 210987") == []
    assert run("серия 4506 № 123456") == []          # нет «выдан» / «паспорт»
    text = "паспорт серии 45 06 № 123456 выдан ОВД"
    assert [e.type for e in run(text)] == [T.PASSPORT]


def test_passport_series_needs_adjacency() -> None:
    # «серия» далеко от числа — даже с «выдан» не контекст
    assert run("Серия бланков обновлена, номер 4506 123456 выдан") == []
    assert [e.type for e in run("Серия: 4506 № 123456, выдан ОВД")] == [T.PASSPORT]


def test_issued_word_does_not_jump_over_other_number() -> None:
    text = "серия 4506 123456; код 4512 987654 выдан"
    found = [text[e.start:e.end] for e in run(text) if e.type == T.PASSPORT]
    assert found == []  # первое число: «выдан» за другим числом; второе: «серия» не рядом


@pytest.mark.parametrize("value", ["(912) 345-67-89", "912 345-67-89", "(3822) 12-34-56"])
def test_local_phone_requires_label(value: str) -> None:
    ent = assert_found(f"тел. {value}", T.PHONE, value)
    assert ent.confidence == CONF_PHONE_LOCAL
    assert run(f"Номер {value} в реестре") == []


def test_local_phone_label_window() -> None:
    far = "Телефон указан в приложении к договору поставки. " + "(912) 345-67-89"
    assert run(far) == []  # метка дальше 30 символов
    assert [e.type for e in run("Phone: (912) 345-67-89")] == [T.PHONE]


def test_local_phone_inside_full_phone_is_merged() -> None:
    text = "Тел. 8 (912) 345-67-89"
    entities = run(text)
    assert [(text[e.start:e.end], e.confidence) for e in entities] == [
        ("8 (912) 345-67-89", CONF_FORMAT)
    ]


@pytest.mark.parametrize(
    "text, value",
    [
        ("Горячая линия {}", "8 800 123-45-67"),
        ("Звонок бесплатный: {}", "+7 (804) 123-45-67"),
        ("тел. {}", "(800) 123-45-67"),
    ],
)
def test_toll_free_low_confidence(text: str, value: str) -> None:
    assert assert_found(text.format(value), T.PHONE, value).confidence == CONF_TOLL_FREE


# ---------- Пересечения ----------
def test_phone_not_inside_snils() -> None:
    # 11 цифр с "8 9…" — похоже на телефон, но контекст СНИЛС и контрольная сумма важнее
    snils = next(
        s for s in (make_snils(i, sep=False) for i in range(5000)) if s.startswith("89")
    )
    entities = run(f"СНИЛС {snils}")
    assert [e.type for e in entities] == [T.SNILS]
    grouped = f"{snils[:3]}-{snils[3:6]}-{snils[6:9]} {snils[9:]}"
    assert [e.type for e in run(f"Номер {grouped}")] == [T.SNILS]


def test_card_not_split_into_phone() -> None:
    card = next(
        c for c in (make_card("4", i) for i in range(5000)) if "8 9" in c or " 89" in c
    )
    assert [e.type for e in run(f"Оплата {card}")] == [T.CARD]


def test_phone_without_snils_context_stays_phone() -> None:
    assert [e.type for e in run("Звоните 89123456789")] == [T.PHONE]


def test_phone_not_inside_org_account() -> None:
    assert run("Счёт 40702 810 8 9123 4567890 организации") == []


def test_birth_context_does_not_leak_to_next_date() -> None:
    text = "Дата рождения 01.02.1990, выдан 12.03.2010"
    assert [text[e.start:e.end] for e in run(text) if e.type == T.BIRTH_DATE] == ["01.02.1990"]


def test_context_limited_to_line() -> None:
    assert run("Паспорт\nКод 4506123456") == []
    assert run("СНИЛС\n12345678964") == []


def test_results_sorted_and_disjoint() -> None:
    text = " ".join(t.format(v) for _, t, v in POSITIVES)
    entities = run(text)
    starts = [e.start for e in entities]
    assert starts == sorted(starts)
    assert all(a.end <= b.start for a, b in zip(entities, entities[1:], strict=False))


def test_empty_text() -> None:
    assert run("") == []


# ---------- Мини-метрика для слайда ----------
def test_mini_metric() -> None:
    positives = POSITIVES + EXTRA_POSITIVES
    total: Counter[str] = Counter()
    found: Counter[str] = Counter()
    for etype, template, value in positives:
        text = template.format(value)
        start = text.index(value)
        total[etype.value] += 1
        if any(
            e.type == etype and (e.start, e.end) == (start, start + len(value))
            for e in run(text)
        ):
            found[etype.value] += 1
    false_positives = sum(len(run(text)) for text in NEGATIVES)

    recall = sum(found.values()) / sum(total.values())
    lines = [f"{'тип':<14}{'найдено / всего':>16}"]
    lines += [f"{t:<14}{found[t]:>8} / {total[t]:<6}" for t in sorted(total)]
    lines.append(f"{'ИТОГО':<14}{sum(found.values()):>8} / {sum(total.values()):<6}")
    lines.append(f"recall = {recall:.3f}; негативов: {len(NEGATIVES)}, ложных срабатываний: "
                 f"{false_positives}")
    print("\n" + "\n".join(lines))  # таблица для слайда, видна в `pytest -s`

    assert len(positives) >= 40 and len(NEGATIVES) >= 30
    assert recall >= 0.95
    assert false_positives == 0


# ---------- Производительность ----------
def test_performance_200k_chars() -> None:
    chunk = (
        f"Исполнитель: ИНН {make_inn12(1)}, СНИЛС {_SNILS}, тел. +7 (912) 345-67-89, "
        f"ivan.petrov@mail.ru. Цена контракта 1 234 567,89 руб., ИНН заказчика 7707083893, "
        f"р/с 40702810400000000225, БИК 044525225, ИКЗ 263770708389377360100100010010000244, "
        f"договор от 15.10.2026. Дата рождения 01.02.1990.\n"
    )
    text = chunk * (200_000 // len(chunk) + 1)
    started = time.perf_counter()
    entities = run(text)
    elapsed = time.perf_counter() - started
    print(f"\n{len(text)} символов, {len(entities)} сущностей, {elapsed:.2f} с")
    assert len(entities) >= 5 * (len(text) // len(chunk))
    assert elapsed < 5.0  # цель < 1 с; запас для медленного CI
