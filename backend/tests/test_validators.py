"""Тесты контрольных сумм и форматов (T-007).

Реальных ПДн нет: валидные номера генерируются помощниками ниже, которые считают
контрольные суммы независимо от app.services.detection.validators. Помощники
импортируются и в другие тесты детекции.
"""

import random
from datetime import date

import pytest

from app.services.detection.validators import (
    is_toll_free,
    is_valid_account_key,
    is_valid_bik,
    is_valid_birth_date,
    is_valid_card,
    is_valid_inn,
    is_valid_passport_series,
    is_valid_ru_phone,
    is_valid_snils,
    luhn_ok,
    only_digits,
    ru_phone_national,
    snils_checksum,
)


# ---------- Генераторы синтетических номеров ----------
def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _snils_control(first9: str) -> str:
    total = sum(int(d) * w for d, w in zip(first9, range(9, 0, -1), strict=True))
    if total > 101:
        total %= 101
    return "00" if total in (100, 101) else f"{total:02d}"


def make_snils(seed: int, sep: bool = True) -> str:
    """Синтетический СНИЛС с верным контрольным числом (номер > 001-001-998)."""
    first9 = str(_rng(seed).randint(2_000_000, 999_999_999)).zfill(9)
    ctrl = _snils_control(first9)
    if not sep:
        return first9 + ctrl
    return f"{first9[:3]}-{first9[3:6]}-{first9[6:]} {ctrl}"


def _inn_digit(digits: str, weights: tuple[int, ...]) -> str:
    return str(sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 % 10)


def make_inn12(seed: int) -> str:
    """Синтетический ИНН физлица (12 цифр) с верными контрольными цифрами."""
    base = str(_rng(seed).randint(10**9, 10**10 - 1))
    base += _inn_digit(base, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))
    return base + _inn_digit(base, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))


def make_inn10(seed: int) -> str:
    """Синтетический ИНН юрлица (10 цифр)."""
    base = str(_rng(seed).randint(10**8, 10**9 - 1))
    return base + _inn_digit(base, (2, 4, 10, 3, 5, 9, 4, 6, 8))


def _luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:  # с учётом будущей контрольной цифры справа
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def make_card(prefix: str, seed: int, length: int = 16, sep: str = " ") -> str:
    """Синтетический номер карты с верной цифрой Луна; группы по 4 через sep."""
    rng = _rng(seed)
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(length - len(prefix) - 1))
    digits = body + _luhn_digit(body)
    if not sep:
        return digits
    return sep.join(digits[i:i + 4] for i in range(0, len(digits), 4))


def make_bik(seed: int) -> str:
    return "04" + str(_rng(seed).randint(1_000_000, 9_999_999))[:5] + "5" + str(seed % 10)


def make_account(prefix: str, bik: str, seed: int) -> str:
    """20-значный счёт с верным ключом (9-я цифра) для данного БИК."""
    rng = _rng(seed)
    acc = prefix + "".join(str(rng.randint(0, 9)) for _ in range(20 - len(prefix)))
    acc = acc[:8] + "0" + acc[9:]
    payload = bik[-3:] + acc
    total = sum(int(d) * (7, 1, 3)[i % 3] for i, d in enumerate(payload))
    key = total % 10 * 3 % 10
    return acc[:8] + str(key) + acc[9:]


def break_digit(value: str, index: int = -1) -> str:
    """Меняет одну цифру (по индексу среди цифр) на соседнюю."""
    positions = [i for i, ch in enumerate(value) if ch.isdigit()]
    pos = positions[index]
    new = str((int(value[pos]) + 1) % 10)
    return value[:pos] + new + value[pos + 1:]


# ---------- СНИЛС ----------
@pytest.mark.parametrize("seed", range(20))
def test_snils_generated_valid(seed: int) -> None:
    assert is_valid_snils(make_snils(seed))
    assert is_valid_snils(make_snils(seed, sep=False))


@pytest.mark.parametrize("seed", range(10))
def test_snils_wrong_control(seed: int) -> None:
    assert not is_valid_snils(break_digit(make_snils(seed)))


def test_snils_sum_100_and_101_give_00() -> None:
    # подбираем первые 9 цифр, у которых взвешенная сумма ровно 100 и 101
    for first9 in _first9_with_sum(100), _first9_with_sum(101):
        assert snils_checksum(first9) == 0
        assert is_valid_snils(first9 + "00")
        assert not is_valid_snils(first9 + "01")


def _first9_with_sum(target: int) -> str:
    rng = random.Random(target)
    while True:
        first9 = "".join(str(rng.randint(0, 9)) for _ in range(9))
        total = sum(int(d) * (9 - i) for i, d in enumerate(first9))
        if total == target and int(first9) > 1_001_998:
            return first9


def test_snils_over_101_uses_mod_101() -> None:
    first9 = "999999999"  # сумма 405, 405 % 101 = 1
    assert snils_checksum(first9) == 1
    assert is_valid_snils("999-999-999 01")


def test_snils_small_numbers_not_checked() -> None:
    assert is_valid_snils("001-001-998 77")
    assert is_valid_snils("000-123-456 00")


@pytest.mark.parametrize("bad", ["123-456-789", "1234567890123", "abc-def-ghi jk", ""])
def test_snils_bad_format(bad: str) -> None:
    assert not is_valid_snils(bad)


def test_snils_separators_nbsp_and_dash() -> None:
    value = make_snils(3)
    assert is_valid_snils(value.replace("-", "—").replace(" ", " "))


# ---------- ИНН ----------
@pytest.mark.parametrize("seed", range(10))
def test_inn12_valid_and_broken(seed: int) -> None:
    inn = make_inn12(seed)
    assert is_valid_inn(inn)
    assert not is_valid_inn(break_digit(inn))
    assert not is_valid_inn(break_digit(inn, -2))


@pytest.mark.parametrize("seed", range(10))
def test_inn10_valid_and_broken(seed: int) -> None:
    inn = make_inn10(seed)
    assert is_valid_inn(inn)
    assert not is_valid_inn(break_digit(inn))


def test_inn10_public_legal_entity() -> None:
    assert is_valid_inn("7707083893")  # ИНН ПАО Сбербанк — открытые реквизиты юрлица


@pytest.mark.parametrize("bad", ["12345", "12345678901", "1234567890123", "77070838ab", ""])
def test_inn_bad_length_or_letters(bad: str) -> None:
    assert not is_valid_inn(bad)


# ---------- Карта ----------
@pytest.mark.parametrize("length", [13, 16, 19])
def test_card_luhn_lengths(length: int) -> None:
    card = make_card("4", 7, length=length, sep="")
    assert len(card) == length
    assert luhn_ok(card)
    assert is_valid_card(card)
    assert not is_valid_card(break_digit(card, 5))


def test_card_with_separators() -> None:
    assert is_valid_card(make_card("5469", 1, sep=" "))
    assert is_valid_card(make_card("2200", 2, sep="-"))


def test_card_wrong_first_digit_and_length() -> None:
    assert not is_valid_card(make_card("1", 3, sep=""))
    assert not is_valid_card(make_card("4", 3, length=12, sep=""))
    assert not is_valid_card(make_card("4", 3, length=20, sep=""))


# ---------- Счёт и БИК ----------
@pytest.mark.parametrize("seed", range(10))
def test_account_key_valid_and_broken(seed: int) -> None:
    bik = make_bik(seed)
    acc = make_account("40817810", bik, seed)
    assert is_valid_bik(bik)
    assert is_valid_account_key(acc, bik)
    assert not is_valid_account_key(break_digit(acc, 10), bik)


def test_account_key_public_correspondent_account() -> None:
    # Открытые реквизиты банка: к/с 30101810400000000225 при БИК 044525225
    assert is_valid_account_key("30101810400000000225", "044525225")
    assert not is_valid_account_key("30101810400000000226", "044525225")


def test_account_key_bad_input() -> None:
    assert not is_valid_account_key("4081781", "044525225")
    assert not is_valid_account_key("40817810000000000000", "0445")
    assert not is_valid_bik("123456789")


# ---------- Паспорт ----------
def test_passport_series() -> None:
    today = date(2026, 10, 16)
    assert is_valid_passport_series("4506", today)
    assert is_valid_passport_series("45 97", today)
    assert is_valid_passport_series("4526", today)
    assert not is_valid_passport_series("0006", today)   # регион 00
    assert not is_valid_passport_series("4550", today)   # год бланка 50
    assert not is_valid_passport_series("4527", today)   # бланк из будущего
    assert not is_valid_passport_series("450", today)


# ---------- Телефон и дата ----------
@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("+7 (912) 345-67-89", True),
        ("8 (3822) 12-34-56", True),
        ("+74951234567", True),
        ("88001234567", True),
        ("8 (112) 345-67-89", False),   # код начинается с 1
        ("9123456789", False),          # 10 цифр
        ("+1 (912) 345-67-89", False),
    ],
)
def test_ru_phone(value: str, ok: bool) -> None:
    assert is_valid_ru_phone(value) is ok


@pytest.mark.parametrize(
    ("value", "national"),
    [
        ("+7 (912) 345-67-89", "9123456789"),
        ("8 (3822) 12-34-56", "3822123456"),
        ("(912) 345-67-89", "9123456789"),
        ("912 345-67-89", "9123456789"),
        ("(3822) 12-34-56", "3822123456"),
        ("(112) 345-67-89", None),      # код с 1
        ("612 345-67-89", None),        # код с 6
        ("12345678", None),
        ("+7 (912) 345-67-8", None),
    ],
)
def test_ru_phone_national(value: str, national: str | None) -> None:
    assert ru_phone_national(value) == national


def test_toll_free() -> None:
    assert is_toll_free("8001234567")
    assert is_toll_free("8041234567")
    assert not is_toll_free("9123456789")
    assert not is_toll_free("8121234567")  # Санкт-Петербург, не бесплатная линия


def test_birth_date() -> None:
    today = date(2026, 10, 16)
    assert is_valid_birth_date(1, 2, 1990, today)
    assert is_valid_birth_date(29, 2, 2000, today)
    assert not is_valid_birth_date(29, 2, 2001, today)
    assert not is_valid_birth_date(31, 4, 1990, today)
    assert not is_valid_birth_date(1, 1, 1899, today)
    assert not is_valid_birth_date(1, 12, 2026, today)  # в будущем


def test_only_digits() -> None:
    assert only_digits("12 34—56") == "123456"
    assert only_digits("12a4") is None
    assert only_digits("1²3") is None
    assert only_digits("") is None
