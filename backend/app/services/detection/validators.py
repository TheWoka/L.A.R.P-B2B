"""Проверка контрольных сумм и форматов идентификаторов. Задача T-007, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.

Все функции принимают строку как есть (разделители допускаются) и возвращают bool.
Значения никуда не логируются (AGENTS.md, правило 4).
"""

from datetime import date

# Разделители внутри номеров: пробел, NBSP, тонкий пробел, дефисы и тире.
SEPARATORS = "   -‐‑–—"
_ASCII_DIGITS = frozenset("0123456789")

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_ACCOUNT_KEY_WEIGHTS = (7, 1, 3)

# Номера СНИЛС не больше 001-001-998 выдавались без проверки контрольного числа.
_SNILS_UNCHECKED_MAX = 1_001_998


def only_digits(value: str) -> str | None:
    """Убирает разделители; None, если остались не-ASCII-цифры (буквы, '²' и т.п.)."""
    digits = "".join(ch for ch in value if ch not in SEPARATORS)
    if not digits or not set(digits) <= _ASCII_DIGITS:
        return None
    return digits


def _weighted_sum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False))


def snils_checksum(first9: str) -> int:
    """Контрольное число СНИЛС по первым 9 цифрам (веса 9…1)."""
    total = sum(int(d) * (9 - i) for i, d in enumerate(first9))
    if total < 100:
        return total
    if total in (100, 101):
        return 0
    rest = total % 101
    return 0 if rest == 100 else rest


def is_valid_snils(value: str) -> bool:
    """True, если СНИЛС (11 цифр, разделители допускаются) проходит проверку контрольного числа."""
    digits = only_digits(value)
    if digits is None or len(digits) != 11:
        return False
    if int(digits[:9]) <= _SNILS_UNCHECKED_MAX:
        return True
    return snils_checksum(digits[:9]) == int(digits[9:])


def _inn_digit(digits: str, weights: tuple[int, ...]) -> int:
    return _weighted_sum(digits, weights) % 11 % 10


def is_valid_inn(value: str) -> bool:
    """True, если ИНН (10 цифр — юрлицо, 12 — физлицо) проходит проверку контрольных цифр."""
    digits = only_digits(value)
    if digits is None:
        return False
    if len(digits) == 10:
        return _inn_digit(digits, _INN10_WEIGHTS) == int(digits[9])
    if len(digits) == 12:
        return (
            _inn_digit(digits, _INN12_WEIGHTS_11) == int(digits[10])
            and _inn_digit(digits, _INN12_WEIGHTS_12) == int(digits[11])
        )
    return False


def luhn_ok(digits: str) -> bool:
    """Алгоритм Луна для строки из ASCII-цифр."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def is_valid_card(value: str) -> bool:
    """True, если номер карты (13–19 цифр) начинается с 2–6 и проходит алгоритм Луна."""
    digits = only_digits(value)
    if digits is None or not 13 <= len(digits) <= 19:
        return False
    return digits[0] in "23456" and luhn_ok(digits)


def is_valid_bik(value: str) -> bool:
    """БИК: 9 цифр, начинается с 04 (код РФ)."""
    digits = only_digits(value)
    return digits is not None and len(digits) == 9 and digits.startswith("04")


def is_valid_account_key(account: str, bik: str) -> bool:
    """Контрольный ключ 20-значного счёта по БИК (веса 7-1-3, сумма кратна 10).

    Для счёта клиента в кредитной организации берутся 3 последние цифры БИК; для
    корреспондентского счёта (301…) и для БИК подразделения ЦБ (оканчивается на 000
    или 001) — "0" + 5-я и 6-я цифры БИК.
    """
    acc = only_digits(account)
    b = only_digits(bik)
    if acc is None or b is None or len(acc) != 20 or len(b) != 9:
        return False
    use_rkc = acc.startswith("301") or b[6:] in ("000", "001")
    prefix = "0" + b[4:6] if use_rkc else b[6:]
    payload = prefix + acc
    total = sum(int(d) * _ACCOUNT_KEY_WEIGHTS[i % 3] for i, d in enumerate(payload))
    return total % 10 == 0


def is_valid_passport_series(series: str, today: date | None = None) -> bool:
    """Серия паспорта РФ: код региона 01–99, год бланка 97–99 или 00…(текущий год % 100)."""
    digits = only_digits(series)
    if digits is None or len(digits) != 4:
        return False
    region, year = int(digits[:2]), int(digits[2:])
    current = (today or date.today()).year % 100
    return region >= 1 and (year >= 97 or year <= current)


def is_valid_ru_phone(value: str) -> bool:
    """Российский номер: 11 цифр, начинается с 7 или 8, код начинается с 3, 4, 8 или 9."""
    digits = only_digits("".join(ch for ch in value if ch not in "+()"))
    return (
        digits is not None
        and len(digits) == 11
        and digits[0] in "78"
        and digits[1] in "3489"
    )


def is_valid_birth_date(day: int, month: int, year: int, today: date | None = None) -> bool:
    """Реальная календарная дата с годом 1900…текущий и не позже сегодняшнего дня."""
    today = today or date.today()
    if not 1900 <= year <= today.year:
        return False
    try:
        value = date(year, month, day)
    except ValueError:
        return False
    return value <= today
