"""Тесты словарного детектора коммерческой тайны и финансовых данных (T-013a).

Только выдуманные данные: суммы, ФИО и реквизиты сгенерированы или придуманы.
"""

import logging
import time
from collections import Counter
from pathlib import Path
from typing import IO, Any

import pytest
import yaml

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection import dict_detector as dd
from app.services.detection.dict_detector import detect_dict, find_candidates, load_dictionaries
from tests.test_validators import make_account, make_bik

T = EntityType
TS, FIN = T.TRADE_SECRET, T.FINANCIAL

BIK = make_bik(41)
ORG_ACCOUNT = make_account("40702810", BIK, 41)
IP_ACCOUNT = make_account("40802810", BIK, 42)
PERSONAL_ACCOUNT = make_account("40817810", BIK, 43)


def make_corr_account(bik: str) -> str:
    """Корсчёт 30101810K00000000XXX с верным ключом K: для 301… ключ — по "0" + 5–6-я цифры БИК."""
    body = "30101810" + "0" + "0" * 8 + bik[-3:]
    total = sum(int(d) * (7, 1, 3)[i % 3] for i, d in enumerate("0" + bik[4:6] + body))
    return body[:8] + str(total % 10 * 3 % 10) + body[9:]


CORR_ACCOUNT = make_corr_account(BIK)


def make_doc(text: str) -> ExtractedDocument:
    return ExtractedDocument(document_id="d-1", filename="t.docx", text=text, segments=[])


def run(text: str) -> list[Entity]:
    return detect_dict(make_doc(text))


def groups_found(text: str) -> list[str]:
    return [c.group_id for c in find_candidates(make_doc(text))]


# (группа, тип, текст, фрагмент, который должен попасть в сущность)
POSITIVES: list[tuple[str, EntityType, str, str]] = [
    ("cost_price", TS, "Себестоимость единицы товара составляет 38 400 руб.", "38 400 руб."),
    ("cost_price", TS, "Плановая себестоимость продукции — 1 234 567,89 ₽.", "1 234 567,89 ₽"),
    ("cost_price", TS, "Расчёт себестоимости: 15 000 руб./ед. без учёта логистики", "руб./ед."),
    ("cost_price", TS, "СЕБЕСТОИМОСТЬ партии 2,5 млн рублей", "2,5 млн рублей"),
    ("margin", TS, "маржинальность сделки — 18 процентов", "18 процентов"),
    ("margin", TS, "Маржа по договору составит 22%.", "22%"),
    ("margin", TS, "при марже не ниже 9,5 % поставщик готов снизить цену", "9,5 %"),
    ("markup", TS, "торговая наценка 12%", "12%"),
    ("markup", TS, "12% — наценка поставщика", "12%"),
    ("markup", TS, "Торговая надбавка составляет 7 процентов от отпускной цены", "7 процентов"),
    ("markup", TS, "С наценкой в 450 руб. на каждую позицию", "450 руб."),
    ("profitability", TS, "Рентабельность продаж за квартал — 14,2%", "14,2%"),
    ("purchase_price", TS, "Закупочная цена у производителя 3 100 руб. за штуку", "3 100 руб."),
    ("purchase_price", TS, "входная цена комплекта $1200", "$1200"),
    ("purchase_price", TS, "Цена закупки снижена до 980 руб./шт.", "980 руб./шт."),
    ("discounts", TS, "индивидуальная скидка для Заказчика 7%", "7%"),
    ("discounts", TS, "Дилерская скидка — 15 %", "15 %"),
    ("discounts", TS, "ретро-бонус 3% от оборота", "3%"),
    ("discounts", TS, "Ретробонус за год составил 120 тыс. руб.", "120 тыс. руб."),
    ("discounts", TS, "бонусы поставщика: 2% при объёме свыше плана", "2%"),
    ("discounts", TS, "Условия скидок: 5% при оплате в течение 10 дней", "5%"),
    # needs_value: false — отрезок до конца фразы, только с содержанием (T-019a)
    ("know_how", TS, "Рецептура смеси: цемент 70%, песок 25%, пластификатор 5%.",
     "Рецептура смеси: цемент 70%, песок 25%, пластификатор 5%"),
    ("know_how", TS, "ноу-хау — режим отжига (650 °C, 40 мин)", "ноу-хау — режим отжига (650 °C"),
    ("know_how", TS, "Технология производства: прессование при 180 атм, сушка 2 ч",
     "Технология производства: прессование при 180 атм, сушка 2 ч"),
    ("know_how", TS, "секреты производства покрытия (три слоя, грунт ГФ-021)",
     "секреты производства покрытия (три слоя"),
    ("client_base", TS, "Перечень клиентов: ООО «Альфа», ООО «Бета»", "ООО «Бета»"),
    ("client_base", TS, "Клиентская база — 1 240 дилеров в 12 регионах", "1 240 дилеров"),
    ("min_price", TS, "Минимальная цена, ниже которой не опускаемся: 1 290 000 руб.",
     "1 290 000 руб."),
    ("min_price", TS, "Нижняя граница цены — 19 900 руб. за шкаф", "19 900 руб."),
    ("cost_items", TS, "Себестоимость — 1 120 000 руб. (материалы 640 000 руб.)", "материалы 640"),
    ("cost_items", TS, "Калькуляция: накладные расходы 1 150 руб., транспорт 640 руб.",
     "транспорт 640 руб."),
    ("revenue_profit", FIN, "выручка за 2025 год — 2,5 млн рублей", "2,5 млн рублей"),
    ("revenue_profit", FIN, "Чистая прибыль Общества составила 310 000 руб.", "310 000 руб."),
    ("revenue_profit", FIN, "убыток по итогам полугодия 1,2 млн руб.", "1,2 млн руб."),
    ("revenue_profit", FIN, "Оборот компании в 2025 году превысил 80 млн рублей", "80 млн рублей"),
    ("revenue_profit", FIN, "EBITDA за 9 месяцев — 45 млн руб.", "45 млн руб."),
    ("receivables_payables", FIN, "дебиторская задолженность 1 234 567,89 ₽", "1 234 567,89 ₽"),
    ("receivables_payables", FIN, "Кредиторская задолженность банку 4,7 млн руб.", "4,7 млн"),
    ("cash_balance", FIN, "Остаток денежных средств на 01.10.2026 — 870 000 руб.", "870 000"),
    ("cash_balance", FIN, "остатки на счетах 2 300 000,00 руб.", "2 300 000,00 руб."),
    ("payroll", FIN, "ФОТ отдела — 450 000 руб./мес.", "450 000 руб./мес."),
    ("payroll", FIN, "Фонд оплаты труда на 2027 год — 12 млн руб.", "12 млн руб."),
    ("payroll", FIN, "Заработная плата инженера 95 000 рублей", "95 000 рублей"),
    ("payroll", FIN, "должностной оклад 70 000 руб.", "70 000 руб."),
    ("org_account", FIN, f"р/с {ORG_ACCOUNT}, БИК {BIK}", ORG_ACCOUNT),
    ("org_account", FIN, f"к/с {CORR_ACCOUNT}, БИК {BIK}", CORR_ACCOUNT),
    ("org_account", FIN, f"Счёт получателя {ORG_ACCOUNT}", ORG_ACCOUNT),
]

NEGATIVES: list[str] = [
    "Начальная (максимальная) цена контракта 1 200 000 руб.",
    "НМЦК: 5 000 000,00 руб.",
    "Цена договора включает НДС 20%",
    "Размер обеспечения заявки 1% от НМЦК",
    "Размер аванса — 30%",
    "Обеспечение исполнения контракта — 5% от цены контракта",
    "Стоимость контракта составляет 2 400 000 руб.",
    "Федеральный закон от 29.07.2004 № 98-ФЗ «О коммерческой тайне»",
    "Сведения о цене не являются коммерческой тайной",
    "Себестоимость 38 400 руб. не является коммерческой тайной",
    "Цена контракта сформирована с учётом рентабельности 15%",
    "НМЦК определена с учётом средней наценки 10%",
    "маржа",
    "Маржинальность сделки будет определена позднее",
    "скидка",
    "Скидка на товар не предусмотрена",
    "НДС 20%",
    "Сумма 150 000 руб. перечисляется в течение 10 дней",
    "Наценка не установлена; срок поставки — 30 дней.",
    "Специалист прибыл на объект 12.10.2026",
    "Документооборот ведётся в электронной форме",
    "Оборотная сторона бланка не заполняется",
    "Фото товара прилагается",
    "Рентабельность проекта оценивается экспертно.",
    f"Счёт физлица {PERSONAL_ACCOUNT}, БИК {BIK}",
    "Срок поставки 30 дней, гарантия 12 месяцев",
    "ст. 1465 ГК РФ «Секрет производства (ноу-хау)»",
    "Выручка от реализации учитывается по методу начисления.",
    # T-019a: слово КТ без содержания, ставки налогов, счёт ИП, статьи без калькуляции
    "Сведения о технологии производства и рецептуре изделия",
    "Передача ноу-хау не допускается",
    "Рецептура смеси является собственностью Поставщика",
    "Пилотный перечень клиентов, на объектах которых применялась технология, предоставляется "
    "по запросу.",
    "Налог на прибыль 20% уплачен в полном объёме.",
    "налог на имущество 2,2%, УСН 6%",
    "Заработная плата выплачивается дважды в месяц, НДФЛ 13% удерживается",
    f"Расчётный счёт ИП {IP_ACCOUNT} в банке, БИК {BIK}",
    "Материалы 640 000 руб. оплачены по счёту",
    "Победителем признаётся участник, предложивший минимальную цену контракта 1 000 руб.",
]


@pytest.mark.parametrize(("group", "etype", "text", "fragment"), POSITIVES)
def test_positive(group: str, etype: EntityType, text: str, fragment: str) -> None:
    cands = find_candidates(make_doc(text))
    start = text.index(fragment)
    hit = [
        c for c in cands
        if c.group_id == group and c.type == etype and c.start <= start
        and start + len(fragment) <= c.end
    ]
    assert hit, f"не найдено: {group}"


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative(text: str) -> None:
    assert run(text) == []


def test_entity_fields_and_offsets() -> None:
    text = " ".join(t for _, _, t, _ in POSITIVES)
    entities = run(text)
    assert entities
    for e in entities:
        assert e.detector == "dict"
        assert text[e.start:e.end] == e.value_masked     # не ПДн — без маскирования
        assert e.end - e.start <= dd.MAX_ENTITY_LEN
        expected = EntityCategory.trade_secret if e.type == TS else EntityCategory.financial
        assert e.category == expected
    starts = [e.start for e in entities]
    assert starts == sorted(starts)
    assert all(a.end <= b.start for a, b in zip(entities, entities[1:], strict=False))


def test_span_from_keyword_to_value() -> None:
    [e] = run("Себестоимость единицы товара составляет 38 400 руб.")
    assert e.value_masked == "Себестоимость единицы товара составляет 38 400 руб."
    [e] = run("Итого: 12% — наценка поставщика")
    assert e.value_masked == "12% — наценка"


def test_value_too_far_or_other_phrase() -> None:
    far = "Наценка " + "согласована сторонами " * 5 + "и составляет 12%"
    assert run(far) == []
    assert run("Наценка согласована. Срок 12% годовых не применяется") == []


def test_confidence() -> None:
    [with_value] = run("торговая наценка 12%")
    [keyword_only] = run("Передача ноу-хау (схема 3, приложение 2) не допускается")
    [with_bik] = run(f"р/с {ORG_ACCOUNT}, БИК {BIK}")
    [no_bik] = run(f"Счёт получателя {ORG_ACCOUNT}")
    assert with_value.confidence == dd.CONF_WITH_VALUE == 0.9
    assert keyword_only.confidence == dd.CONF_KEYWORD_ONLY == 0.75
    assert with_bik.confidence == dd.CONF_ACCOUNT_BIK == 0.95
    assert no_bik.confidence == dd.CONF_ACCOUNT_NO_BIK == 0.8


def test_account_with_wrong_key_is_skipped() -> None:
    broken = ORG_ACCOUNT[:-1] + str((int(ORG_ACCOUNT[-1]) + 1) % 10)
    assert run(f"р/с {broken}, БИК {BIK}") == []


def test_yo_equals_e() -> None:
    assert groups_found("Остаток на счёте 10 000 руб.") == ["cash_balance"]


def test_vat_value_is_not_markup_value() -> None:
    assert run("Наценка указана с учётом НДС 20%") == []


def test_value_used_once() -> None:
    assert groups_found("ретро-бонус 3% от оборота") == ["discounts"]


# ---------- Обрезка по ПДн ----------
def _pd(text: str, value: str) -> Entity:
    start = text.index(value)
    return Entity(
        type=T.PERSON, category=EntityCategory.pd, start=start, end=start + len(value),
        value_masked="█" * len(value), confidence=0.9, detector="ner",
    )


def test_trim_around_pd() -> None:
    text = "Наценка ИП Смирнова А.А. составляет 12%"
    person = _pd(text, "Смирнова А.А.")
    [e] = detect_dict(make_doc(text), pd_entities=[person])
    assert e.value_masked == "составляет 12%"
    assert e.start >= person.end
    assert text[e.start:e.end] == e.value_masked


def test_trim_pd_before_keyword() -> None:
    text = "12% — наценка Смирнова А.А. по договору"
    [e] = detect_dict(make_doc(text), pd_entities=[_pd(text, "Смирнова А.А.")])
    assert e.value_masked == "12% — наценка"


def test_drop_when_pd_covers_anchor() -> None:
    text = "Передача ноу-хау (схема 3) не допускается"
    assert detect_dict(make_doc(text), pd_entities=[_pd(text, "ноу-хау")]) == []


# ---------- Особые категории ПДн (T-019a) ----------
SPECIAL = T.SPECIAL_CATEGORY

# (текст, точный отрезок сущности)
SPECIAL_POSITIVES: list[tuple[str, str]] = [
    ("СВЕДЕНИЯ О СОСТОЯНИИ ЗДОРОВЬЯ: ИНВАЛИДНОСТЬ III ГРУППЫ", "ИНВАЛИДНОСТЬ III ГРУППЫ"),
    ("освобождён от командировок по медицинским показаниям: астма.", "астма"),
    ("Работнику установлена инвалидность II группы; допуск ограничен.",
     "инвалидность II группы"),
    ("Монтажник состоит на диспансерном учёте у невролога.",
     "состоит на диспансерном учёте у невролога"),
    ("Диагноз: гипертония 2 степени", "гипертония 2 степени"),
    ("Сотрудница: беременность 30 недель, перевод на лёгкий труд",
     "беременность 30 недель, перевод на лёгкий труд"),
    ("Работник: группа допуска V; ограничение по слуху (тугоухость)",
     "ограничение по слуху (тугоухость)"),
    ("Сведения о судимости: судим в 2011 году", "судим в 2011 году"),
    ("Вероисповедание: католик", "католик"),
    ("Политические взгляды: сторонник партии «Новый путь»", "сторонник партии «Новый путь»"),
    ("Сотрудник является членом профсоюза работников образования.",
     "членом профсоюза работников образования"),
    ("Национальность: татарин", "татарин"),
]

SPECIAL_NEGATIVES: list[str] = [
    "Поставка медицинского оборудования для медицинской организации",
    "Подрядчик обеспечивает охрану здоровья работников",
    "Закупка в рамках национального проекта «Здравоохранение»",
    "Политика обработки персональных данных размещена на сайте",
    "Требование: отсутствие судимости у руководителя участника закупки",
    "Участник закупки не должен иметь судимость за преступления в сфере экономики",
    "Организация состоит на учёте в налоговом органе по месту нахождения",
    "Федеральный закон «О социальной защите инвалидов в Российской Федерации»",
    "Преимущества организациям инвалидов (ст. 29 44-ФЗ)",
    "Реагенты для диагностики заболеваний поставляются в упаковке производителя",
    # медицинские закупки: общие слова о болезнях без конкретного человека (needs_subject)
    "Поставка лекарственных препаратов для лечения онкологических заболеваний",
    "Расходные материалы для отделения беременных и родильниц",
    "Средства реабилитации для инвалидов",
    "Медицинская помощь при заболеваниях системы кровообращения",
    "Закупка инсулина для пациентов с диабетом",
    "Лекарственный препарат противопоказан при беременности",
    "Обучение членов профсоюза и представителей национальных меньшинств",
    # членство: множественное число, организации, не профсоюз/партия (вариант «б», T-019a)
    "Для членов профсоюза предусмотрены льготы",
    "Профсоюзная организация Заказчика согласовала график",
    "Подрядчик является членом СРО",
    "Члены комиссии по закупкам подписали протокол",
]


@pytest.mark.parametrize(("text", "fragment"), SPECIAL_POSITIVES)
def test_special_category_positive(text: str, fragment: str) -> None:
    entities = [e for e in run(text) if e.type == SPECIAL]
    start = text.index(fragment)
    assert [(e.start, e.end) for e in entities] == [(start, start + len(fragment))]
    [ent] = entities
    assert ent.category == EntityCategory.pd_special
    assert ent.detector == "dict"


@pytest.mark.parametrize("text", SPECIAL_NEGATIVES)
def test_special_category_traps(text: str) -> None:
    assert [e for e in run(text) if e.type == SPECIAL] == []


# ---------- needs_subject: особая категория — сведения о конкретном человеке ----------
@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        # явные персональные формулировки — без проверки субъекта
        ("Инвалид II группы, работы только в помещении.",
         "Инвалид II группы, работы только в помещении"),
        ("Установлена группа инвалидности, бессрочно", "группа инвалидности, бессрочно"),
        ("Имеет заболевание сердца", "Имеет заболевание сердца"),
        # общие слова + маркер человека
        ("Сотрудница: беременность 20 недель", "беременность 20 недель"),
        ("У неё хроническое заболевание почек", "хроническое заболевание почек"),
        ("Кандидат указал: инвалидность с детства", "инвалидность с детства"),
        # членство в единственном числе — явная формулировка, ФИО рядом не нужно
        ("Общественная деятельность: член профсоюза работников связи",
         "член профсоюза работников связи"),
        ("Членство в политической партии с 2015 года",
         "Членство в политической партии с 2015 года"),
        ("Состоит в профсоюзе с 2010 года", "Состоит в профсоюзе с 2010 года"),
        ("Сведения о здоровье пациента: хроническое заболевание лёгких",
         "хроническое заболевание лёгких"),
    ],
)
def test_special_subject_found(text: str, fragment: str) -> None:
    start = text.index(fragment)
    assert [(e.start, e.end) for e in run(text) if e.type == SPECIAL] == [
        (start, start + len(fragment))
    ]


def test_special_subject_person_entity_in_window() -> None:
    text = "Поставка для отделения. Сафронов Л.И.: хроническое заболевание почек"
    person = _pd(text, "Сафронов Л.И.")
    assert run(text) == []                      # без PERSON — общие слова
    [ent] = detect_dict(make_doc(text), pd_entities=[person])
    assert text[ent.start:ent.end] == "хроническое заболевание почек"


def test_special_subject_person_too_far() -> None:
    filler = "Поставка выполняется партиями в течение года. " * 4   # > 150 символов
    text = "Сафронов Л.И.\n" + filler + "\nхроническое заболевание почек"
    person = _pd(text, "Сафронов Л.И.")
    assert detect_dict(make_doc(text), pd_entities=[person]) == []
    near = "Сафронов Л.И.\nхроническое заболевание почек"
    assert len(detect_dict(make_doc(near), pd_entities=[_pd(near, "Сафронов Л.И.")])) == 1


def test_special_category_is_masked_as_pd() -> None:
    from app.services.masking.masker import MASKED_CATEGORIES, mask, mask_preserving_length

    text = "Национальность: татарин. Наценка 12%."
    special, markup = run(text)
    assert special.category in MASKED_CATEGORIES
    assert special.value_masked == "т██████" and "татарин" not in special.value_masked
    assert markup.value_masked == "Наценка 12%"     # КТ — без маскирования (решение 17.10)
    masked = mask(text, [special, markup])
    assert "татарин" not in masked.masked_text and "Наценка 12%" in masked.masked_text
    assert mask_preserving_length(text, [special, markup]).startswith("Национальность: т██████.")


def test_special_category_phrase_capped() -> None:
    text = "Диагноз: " + "очень длинное описание " * 10
    [ent] = run(text)
    assert ent.end - ent.start <= dd.MAX_PHRASE_LEN
    assert text[ent.end - 1] != " "


def test_special_category_trimmed_around_person() -> None:
    text = "Инвалидность установлена Смирновой А.А. в 2020 году"
    person = _pd(text, "Смирновой А.А.")
    [ent] = detect_dict(make_doc(text), pd_entities=[person])
    assert text[ent.start:ent.end] == "Инвалидность установлена"


# ---------- Контекст группы, ставки налогов (T-019a) ----------
def test_cost_items_need_context_in_same_line() -> None:
    text = "Себестоимость работ — 900 000 руб.\nМатериалы 640 000 руб. оплачены"
    assert groups_found(text) == ["cost_price"]
    same_line = "Себестоимость работ — 900 000 руб. (ФОТ 300 000 руб.)"
    assert groups_found(same_line) == ["cost_price", "cost_items"]


def test_word_paired_once_across_groups() -> None:
    # «ФОТ» есть и в payroll, и в cost_items: значение берёт одна группа, пары не сдвигаются
    text = "Калькуляция: ФОТ 3 800 руб., накладные расходы 1 150 руб., транспорт 640 руб."
    found = [(c.group_id, text[c.start:c.end]) for c in find_candidates(make_doc(text))]
    assert found == [
        ("cost_items", "ФОТ 3 800 руб."),
        ("cost_items", "накладные расходы 1 150 руб."),
        ("cost_items", "транспорт 640 руб."),
    ]


@pytest.mark.parametrize(
    "text",
    ["Налог на прибыль 20%", "налог на имущество — 2,2%", "НДФЛ 13%", "УСН 6% от доходов"],
)
def test_tax_rates_are_not_values(text: str) -> None:
    assert run(f"Прибыль и заработная плата: {text}") == []


def test_profit_before_tax_is_financial() -> None:
    text = "Прибыль до налогообложения — 7,1 млн руб."
    [ent] = run(text)
    assert ent.type == FIN
    assert ent.value_masked == "Прибыль до налогообложения — 7,1 млн руб."


# ---------- Загрузка словаря ----------
def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "dict.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


GOOD_GROUP = {"id": "g1", "type": "TRADE_SECRET", "patterns": ["марж\\w*"], "needs_value": True}


@pytest.mark.parametrize(
    "broken",
    [
        {**GOOD_GROUP, "type": "PERSON"},
        {**GOOD_GROUP, "type": "SECRET"},
        {**GOOD_GROUP, "patterns": []},
        {**GOOD_GROUP, "patterns": ["(незакрытая"]},
        {**GOOD_GROUP, "needs_value": "yes"},
        {**GOOD_GROUP, "confidence": 1.5},
        {**GOOD_GROUP, "need_value": True},
        {**GOOD_GROUP, "context": []},
        {**GOOD_GROUP, "context": "себестоимость"},
        {**GOOD_GROUP, "context": ["(незакрытая"]},
        {**GOOD_GROUP, "needs_subject": True},          # только для SPECIAL_CATEGORY
        {**GOOD_GROUP, "type": "SPECIAL_CATEGORY", "needs_subject": "yes"},
    ],
)
def test_bad_format_names_group(tmp_path: Path, broken: dict[str, object]) -> None:
    broken = {**broken, "id": "BROKEN_GROUP"}
    with pytest.raises(ValueError, match="BROKEN_GROUP"):
        load_dictionaries(_write(tmp_path, [GOOD_GROUP, broken]))


def test_duplicate_id_and_not_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="g1"):
        load_dictionaries(_write(tmp_path, [GOOD_GROUP, GOOD_GROUP]))
    with pytest.raises(ValueError):
        load_dictionaries(_write(tmp_path, {"id": "x"}))


def test_custom_group_and_confidence(tmp_path: Path) -> None:
    group = {**GOOD_GROUP, "patterns": ["тариф\\w* поставщик\\w*"], "confidence": 0.6}
    groups = load_dictionaries(_write(tmp_path, [group]))
    [e] = detect_dict(make_doc("Тариф поставщика 5%"), groups=groups)
    assert (e.type, e.confidence) == (TS, 0.6)


def test_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    load_dictionaries.cache_clear()
    opened: list[Path] = []
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> IO[Any]:
        if self.name == "dictionaries.yaml":
            opened.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    first = load_dictionaries()
    for _ in range(3):
        run("торговая наценка 12%")
    assert load_dictionaries() is first
    assert len(opened) == 1


def test_actual_dictionary_groups() -> None:
    ids = {g.id for g in load_dictionaries()}
    assert {"cost_price", "margin", "markup", "know_how", "revenue_profit", "payroll"} <= ids


# ---------- Логи и граничные случаи ----------
def test_logs_have_no_fragments(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        run(f"Себестоимость 38 400 руб., р/с {ORG_ACCOUNT}")
    assert "entities=2" in caplog.text
    for fragment in ("38 400", "Себестоимость", ORG_ACCOUNT, ORG_ACCOUNT[:8]):
        assert fragment not in caplog.text


def test_empty_text() -> None:
    assert run("") == []


# ---------- Мини-метрика для слайда ----------
def test_mini_metric() -> None:
    total: Counter[str] = Counter()
    found: Counter[str] = Counter()
    kinds: dict[str, str] = {}
    for group, etype, text, fragment in POSITIVES:
        total[group] += 1
        kinds[group] = etype.value
        start = text.index(fragment)
        if any(
            c.group_id == group and c.start <= start and start + len(fragment) <= c.end
            for c in find_candidates(make_doc(text))
        ):
            found[group] += 1
    false_positives = sum(len(run(text)) for text in NEGATIVES)
    recall = sum(found.values()) / sum(total.values())

    lines = [f"{'группа':<22}{'тип':<14}{'найдено / всего':>16}"]
    lines += [f"{g:<22}{kinds[g]:<14}{found[g]:>8} / {total[g]:<6}" for g in total]
    lines.append(f"{'ИТОГО':<36}{sum(found.values()):>8} / {sum(total.values()):<6}")
    lines.append(f"recall = {recall:.3f}; негативов: {len(NEGATIVES)}, ложных срабатываний: "
                 f"{false_positives}")
    print("\n" + "\n".join(lines))  # таблица для слайда, видна в `pytest -s`

    assert len(POSITIVES) >= 30 and len(NEGATIVES) >= 25
    assert recall >= 0.9
    assert false_positives == 0


# ---------- Производительность ----------
def test_performance_200k_chars() -> None:
    block = " ".join(t for _, _, t, _ in POSITIVES) + " " + " ".join(NEGATIVES) + "\n"
    text = (block * (200_000 // len(block) + 1))[:200_000]
    load_dictionaries()     # загрузка словаря — один раз на процесс, не в замер
    started = time.perf_counter()
    entities = run(text)
    elapsed = time.perf_counter() - started
    assert entities
    assert elapsed < 1.0, f"{elapsed:.2f} с"
