"""Тесты mask_value — правило маскирования с сохранением длины (contracts.md, раздел 4.1)."""

import pytest

from app.services.masking.masker import mask_value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Иванов Иван", "И█████ И███"),
        ("123-456-789 00", "███-███-███ ██"),
        ("+7 (912) 345-67-89", "+█ (███) ███-██-██"),
        ("ivan.petrov@mail.ru", "i███.p█████@m███.r█"),
        ("", ""),
    ],
)
def test_contract_examples(value: str, expected: str) -> None:
    assert mask_value(value) == expected


@pytest.mark.parametrize(
    "value",
    ["4506 № 123456", "1 февраля 1990", "A1b2 3c", "x", "ё", " — ", "_ab"],
)
def test_length_preserved(value: str) -> None:
    assert len(mask_value(value)) == len(value)


def test_mixed_words() -> None:
    assert mask_value("A1b2 3c") == "A███ ██"       # буквенное слово и цифровое
    assert mask_value("12 34") == "██ ██"  # NBSP — разделитель, остаётся
    assert mask_value("x") == "x"                   # однобуквенное слово не меняется
