"""Проверка контрольных сумм идентификаторов. Задача T-007, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.
"""


def is_valid_snils(value: str) -> bool:
    """True, если СНИЛС (11 цифр, разделители допускаются) проходит проверку контрольного числа."""
    raise NotImplementedError("T-007 (Данила)")


def is_valid_inn(value: str) -> bool:
    """True, если ИНН (10 цифр — юрлицо, 12 — физлицо) проходит проверку контрольных цифр."""
    raise NotImplementedError("T-007 (Данила)")


def is_valid_card(value: str) -> bool:
    """True, если номер карты (13–19 цифр) проходит алгоритм Луна."""
    raise NotImplementedError("T-007 (Данила)")
