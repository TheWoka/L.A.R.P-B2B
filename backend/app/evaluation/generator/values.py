"""Выдуманные значения: только переданный Random, без Faker и глобального состояния."""

from datetime import date
from random import Random

from app.services.detection import validators

SURNAMES = (
    "Иванов Петров Сидоров Смирнов Кузнецов Попов Соколов Лебедев Козлов Новиков "
    "Морозов Волков Алексеев Васильев Зайцев Павлов Семёнов Голубев Виноградов Богданов "
    "Воробьёв Фёдоров Михайлов Беляев Тарасов Белов Комаров Орлов Киселёв Макаров"
).split()
MALE_NAMES = (
    "Иван Пётр Алексей Сергей Андрей Николай Михаил Павел Дмитрий Антон "
    "Артём Максим Олег Виктор Роман Кирилл Денис Илья Борис Глеб "
    "Тимур Вадим Юрий Евгений Игорь Валерий Леонид Степан Фёдор Руслан"
).split()
FEMALE_NAMES = (
    "Анна Мария Елена Ольга Ирина Наталья Татьяна Светлана Екатерина Юлия "
    "Алина Вера Надежда Любовь Дарья Ксения Полина Валентина Нина Галина "
    "Лидия Людмила Евгения Виктория Софья Алла Инна Тамара Яна Зоя"
).split()
MALE_PATRONYMICS = (
    "Иванович Петрович Алексеевич Сергеевич Андреевич Николаевич Михайлович Павлович "
    "Дмитриевич Антонович Артёмович Максимович Олегович Викторович Романович Кириллович "
    "Денисович Ильич Борисович Глебович Тимурович Вадимович Юрьевич Евгеньевич Игоревич "
    "Валерьевич Леонидович Степанович Фёдорович Русланович"
).split()
FEMALE_PATRONYMICS = (
    "Ивановна Петровна Алексеевна Сергеевна Андреевна Николаевна Михайловна Павловна "
    "Дмитриевна Антоновна Артёмовна Максимовна Олеговна Викторовна Романовна Кирилловна "
    "Денисовна Ильинична Борисовна Глебовна Тимуровна Вадимовна Юрьевна Евгеньевна Игоревна "
    "Валерьевна Леонидовна Степановна Фёдоровна Руслановна"
).split()
MONTHS = (
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"
).split()
BIK = "049999999"  # синтетический банк, как в eval/seed


def person(rng: Random) -> str:
    """Три формы ФИО, согласованные по полу."""
    female = rng.choice((False, True))
    surname = rng.choice(SURNAMES) + ("а" if female else "")
    name = rng.choice(FEMALE_NAMES if female else MALE_NAMES)
    patronymic = rng.choice(FEMALE_PATRONYMICS if female else MALE_PATRONYMICS)
    return rng.choice((
        f"{surname} {name} {patronymic}",
        f"{surname} {name[0]}. {patronymic[0]}.",
        f"{surname} {name} {patronymic}".upper(),
    ))


def passport(rng: Random) -> str:
    series = f"{rng.randint(1, 99):02d}{rng.randint(0, 20):02d}"
    number = f"{rng.randint(100000, 999999)}"
    return rng.choice((
        f"{series[:2]} {series[2:]} {number}", f"{series} {number}",
        f"серия {series} № {number}",
    ))


def snils(rng: Random) -> str:
    first = f"{rng.randint(1001999, 999999999):09d}"
    digits = f"{first}{validators.snils_checksum(first):02d}"
    assert validators.is_valid_snils(digits)
    return rng.choice((digits, f"{digits[:3]}-{digits[3:6]}-{digits[6:9]} {digits[9:]}"))


def inn_person(rng: Random) -> str:
    digits = f"{rng.randint(1000000000, 9999999999)}"
    for weights in ((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)):
        digits += str(sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 % 10)
    assert validators.is_valid_inn(digits)
    return digits


def phone(rng: Random) -> str:
    prefix = rng.choice(("+7", "8"))
    code = rng.choice(("900", "913", "923", "952", "962", "999"))
    tail = f"{rng.randint(0, 9999999):07d}"
    return rng.choice((
        f"{prefix}{code}{tail}",
        f"{prefix} ({code}) {tail[:3]}-{tail[3:5]}-{tail[5:]}",
        f"{prefix} {code} {tail[:3]} {tail[3:5]} {tail[5:]}",
    ))


def email(rng: Random) -> str:
    return f"synthetic.person{rng.randrange(1000000):06d}@example.invalid"


def address(rng: Random) -> str:
    city = rng.choice(("Примерск", "Образцовск", "Макетово", "Тестоград"))
    street = rng.choice(("Макетная", "Образцовая", "Учебная", "Вымышленная"))
    return f"г. {city}, ул. {street}, д. {rng.randint(1, 150)}, кв. {rng.randint(1, 300)}"


def birth_date(rng: Random) -> str:
    value = date(rng.randint(1950, 2005), rng.randint(1, 12), rng.randint(1, 28))
    return rng.choice((
        value.strftime("%d.%m.%Y"), f"{value.day} {MONTHS[value.month - 1]} {value.year} г.",
    ))


def card(rng: Random) -> str:
    first = "2200" + f"{rng.randrange(10**11):011d}"
    digits = next(first + str(d) for d in range(10) if validators.luhn_ok(first + str(d)))
    assert validators.is_valid_card(digits)
    return rng.choice((digits, " ".join(digits[i:i + 4] for i in range(0, 16, 4))))


def bank_account(rng: Random) -> str:
    digits = "40817810" + "0" + f"{rng.randrange(10**11):011d}"
    account = next(
        digits[:8] + str(d) + digits[9:] for d in range(10)
        if validators.is_valid_account_key(digits[:8] + str(d) + digits[9:], BIK)
    )
    return account


def trade_secret_phrase(rng: Random) -> str:
    amount = f"{rng.randint(10, 150)} {rng.randrange(1000):03d}"
    return rng.choice((
        f"себестоимость единицы составляет {amount} руб.",
        f"наценка {rng.randint(5, 45)}%",
        f"индивидуальная скидка для Заказчика {rng.randint(3, 20)}%",
        f"закупочная цена у производителя {amount} руб.",
    ))


def special_category(rng: Random) -> str:
    return rng.choice((
        "инвалидность II группы", "инвалидность III группы",
        "состоит на учёте у кардиолога", "состоит на учёте у эндокринолога",
        "сахарный диабет 1 типа", "ограничение по зрению (миопия высокой степени)",
    ))
