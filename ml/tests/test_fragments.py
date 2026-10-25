"""Тесты нарезки на фрагменты (T-036a). Запуск: python -m pytest ml/tests -q"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import ensure_app_importable  # noqa: E402

ensure_app_importable()  # нарезка живёт в backend (T-036b): один код для оценки и конвейера

from app.services.detection.fragments import MAX_FRAGMENT_CHARS, split_fragments  # noqa: E402


def texts(text: str) -> list[str]:
    return [text[s:e] for s, e in split_fragments(text)]


def test_two_sentences() -> None:
    text = "Наценка на кабель составит 14%. Срок поставки — 10 рабочих дней."
    assert texts(text) == ["Наценка на кабель составит 14%.", "Срок поставки — 10 рабочих дней."]


def test_offsets_are_in_original_coordinates() -> None:
    text = "  Первое предложение здесь.\nВторая строка таблицы: маржа 18%  "
    for s, e in split_fragments(text):
        assert text[s:e] == text[s:e].strip()
        assert text[s:e] in text
    assert texts(text)[-1] == "Вторая строка таблицы: маржа 18%"


def test_newline_is_always_boundary() -> None:
    assert texts("Себестоимость 48 300 руб\nНМЦК 1,2 млн руб") == [
        "Себестоимость 48 300 руб", "НМЦК 1,2 млн руб",
    ]


def test_initials_and_abbreviations_do_not_split() -> None:
    text = "Договор подписал И.П. Сидоров, адрес г. Томск, ул. Ленина, д. 5. Далее текст идёт."
    assert texts(text) == [
        "Договор подписал И.П. Сидоров, адрес г. Томск, ул. Ленина, д. 5.", "Далее текст идёт.",
    ]


def test_year_abbreviation_ends_sentence() -> None:
    assert texts("Выручка выросла в 2025 г. Компания расширяется.") == [
        "Выручка выросла в 2025 г.", "Компания расширяется.",
    ]


def test_placeholders_survive() -> None:
    text = "[PERSON_1] перенёс операцию на сердце. [PERSON_2] в отпуске до мая."
    assert texts(text) == ["[PERSON_1] перенёс операцию на сердце.", "[PERSON_2] в отпуске до мая."]


def test_long_sentence_is_split_on_semicolon_and_conjunction() -> None:
    text = (
        "Закупочная цена у завода составляет 2 140 руб. за метр без учёта доставки до склада; "
        "дилерская скидка производителя для нас равна 18 процентам от прайса, "
        "а минимальная цена для торгов, ниже которой мы не опускаемся, — 2 600 руб. за метр."
    )
    assert len(text) > MAX_FRAGMENT_CHARS
    parts = texts(text)
    assert len(parts) >= 2
    assert all(len(p.split()) >= 4 for p in parts)
    assert parts[0].endswith(";")


def test_short_pieces_are_merged() -> None:
    text = ("Итог: " + "очень длинное перечисление позиций калькуляции без пауз " * 4).strip()
    text += "; да."
    parts = texts(text)
    assert all(len(p.split()) >= 4 for p in parts)


def test_single_word_lines_and_empty_text_are_dropped() -> None:
    assert split_fragments("") == []
    assert texts("Итого\n\n   \nМаржа 18%") == ["Маржа 18%"]


def test_lowercase_after_dot_does_not_split() -> None:
    assert texts("Цена 100 руб. за штуку без НДС.") == ["Цена 100 руб. за штуку без НДС."]
