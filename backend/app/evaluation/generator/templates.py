"""Семь шаблонов закупок. Слоты размечаются явно, ловушки остаются без разметки."""

from dataclasses import dataclass
from typing import Literal

from app.schemas import DocPurpose


@dataclass(frozen=True)
class Block:
    kind: Literal["paragraph", "table", "row"]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Template:
    name: str
    purpose: DocPurpose
    format: Literal["docx", "pdf", "xlsx", "txt"]
    blocks: tuple[Block, ...]
    uppercase: bool = False


def paragraph(text: str) -> Block:
    return Block("paragraph", ((text,),))


def table(*rows: tuple[str, ...]) -> Block:
    return Block("table", rows)


def row(*cells: str) -> Block:
    return Block("row", (cells,))


TRAPS = (
    "Реквизиты ООО «Синтетический пример»: ИНН 5051595154, КПП 701401001, "
    "ОГРН 1187031055123; расчётный счёт 40702810871701156960. "
    "ИКЗ 263701702467370170100100120010000244. "
    "Номер контракта 0365200012326000187-01. НМЦК 3 480 000 руб.; налог на прибыль 20%. "
    "Паспорт изделия № 4512-0087; серия поставки ИБП-2026/3."
)
MARKERS = (
    "Лист согласования: согласовано юридическим отделом и бухгалтерией.",
    "Коммерческая тайна. Обладатель: ООО «Синтетический пример».",
    "Срок хранения персональных данных: 3 года, затем уничтожить.",
    "Даю согласие на обработку персональных данных в целях участия в закупке.",
)

TEMPLATES = (
    Template("contract", DocPurpose.contract, "docx", (
        paragraph("ДОГОВОР ПОСТАВКИ. Все сведения вымышлены."),
        paragraph("Заказчик ООО «Макет» в лице {person_1}; поставщик в лице {person_2}."),
        paragraph("Телефон заказчика: {phone_1}; личная почта: {email_1}."),
        paragraph("Телефон подписанта поставщика: {phone_2}; личная почта: {email_2}."),
        table(("Реквизиты подписанта", "Значение"), ("Паспорт", "{passport_1}"),
              ("ИНН физлица", "{inn_person_1}"), ("Адрес проживания", "{address_1}"),
              ("Лицевой счёт", "{bank_account_1}")),
        paragraph(TRAPS),
    )),
    Template("application", DocPurpose.contract, "docx", (
        paragraph("ЗАЯВКА УЧАСТНИКА. Синтетические сведения."),
        paragraph("Участник: {person_1}; дата рождения: {birth_date_1}."),
        table(("Паспорт", "{passport_1}"), ("СНИЛС", "{snils_1}"),
              ("ИНН физлица", "{inn_person_1}"), ("Адрес регистрации", "{address_1}")),
        paragraph("Личный телефон: {phone_1}; личная почта: {email_1}."),
        paragraph("Карта для возврата обеспечения: {card_1}; счёт: {bank_account_1}."),
        paragraph("Сведения о здоровье: {special_category_1}."),
        paragraph(TRAPS),
    )),
    Template("questionnaire", DocPurpose.internal, "docx", (
        paragraph("АНКЕТА РАБОТНИКА. СИНТЕТИЧЕСКИЕ СВЕДЕНИЯ."),
        paragraph("ФАМИЛИЯ ИМЯ ОТЧЕСТВО: {person_1}; ДАТА РОЖДЕНИЯ: {birth_date_1}."),
        table(("ПАСПОРТ", "{passport_1}"), ("СНИЛС", "{snils_1}"),
              ("ИНН ФИЗЛИЦА", "{inn_person_1}"), ("АДРЕС", "{address_1}")),
        paragraph("ЛИЧНЫЙ ТЕЛЕФОН: {phone_1}; ЛИЧНАЯ ПОЧТА: {email_1}."),
        paragraph("ЗАРПЛАТНАЯ КАРТА: {card_1}; СЧЁТ: {bank_account_1}."),
        paragraph("СВЕДЕНИЯ О ЗДОРОВЬЕ: {special_category_1}."),
    ), uppercase=True),
    Template("offer", DocPurpose.publication, "docx", (
        paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ. Синтетический пример."),
        paragraph("Ответственный менеджер: {person_1}."),
        paragraph("Личный телефон: {phone_1}; личная почта: {email_1}."),
        table(("Позиция", "Внутренняя калькуляция"),
              ("Оборудование", "{trade_secret_phrase_1}"),
              ("Комплектующие", "{trade_secret_phrase_2}"),
              ("Доставка", "{trade_secret_phrase_3}")),
        paragraph("Условия для заказчика: {trade_secret_phrase_4}."),
        paragraph("Расчёт монтажа: {trade_secret_phrase_5}."),
        paragraph("Расчёт обслуживания: {trade_secret_phrase_6}."),
    )),
    Template("specification", DocPurpose.publication, "docx", (
        paragraph("ТЕХНИЧЕСКОЕ ЗАДАНИЕ БЕЗ ПЕРСОНАЛЬНЫХ ДАННЫХ."),
        paragraph("Синтетический пример. Поставка 12 источников бесперебойного питания."),
        table(("Параметр", "Требование"), ("Мощность", "не менее 3000 ВА"),
              ("Гарантия", "36 месяцев")),
        paragraph(TRAPS),
    )),
    Template("estimate", DocPurpose.internal, "xlsx", (
        row("СМЕТА. Синтетический пример", "Сумма"),
        row("Оборудование", "120000"), row("Монтаж", "30000"),
        row("Контактное лицо", "{person_1}"), row("Личный телефон", "{phone_1}"),
        row("Личная почта", "{email_1}"), row("ИНН физлица", "{inn_person_1}"),
        row("Карта", "{card_1}"), row("Лицевой счёт", "{bank_account_1}"),
        row("Калькуляция", "{trade_secret_phrase_1}"),
        row("Условия", "{trade_secret_phrase_2}"), row("Реквизиты организации", TRAPS),
    )),
    Template("international", DocPurpose.international, "txt", (
        paragraph("INTERNATIONAL SUPPLY CONTRACT / МЕЖДУНАРОДНЫЙ КОНТРАКТ. Synthetic data."),
        paragraph("Russian representative / Представитель: {person_1}."),
        paragraph("Counterparty representative / Подписант: {person_2}."),
        paragraph("Personal phone / Телефон: {phone_1}; personal email: {email_1}."),
        paragraph("Passport / Паспорт: {passport_1}; birth date / дата рождения: {birth_date_1}."),
        paragraph("СНИЛС: {snils_1}; personal tax ID / ИНН физлица: {inn_person_1}."),
        paragraph("Home address / Адрес проживания: {address_1}."),
        paragraph("Personal bank account / Лицевой счёт: {bank_account_1}."),
        paragraph("Confidential pricing / Расчёт цены: {trade_secret_phrase_1}."),
        paragraph("Discount terms / Условия скидки: {trade_secret_phrase_2}."),
    )),
)
