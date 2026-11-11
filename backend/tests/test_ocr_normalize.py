"""Тесты T-077c: цифровой вид OCR-текста и regex-детектор на OCR-документах.

Только синтетика: номера — с верными контрольными суммами из test_validators или выдуманные.
"""

import json
import os
from pathlib import Path

import pytest

from app.evaluation.corpus import load_corpus
from app.schemas import Entity, EntityType, ExtractedDocument, OcrInfo, TextSegment
from app.services.detection.detect import detect
from app.services.detection.ocr_normalize import CONFUSABLE_DIGITS, digits_view
from app.services.detection.regex_detector import (
    CONF_CHECKSUM,
    CONF_EMAIL,
    OCR_FIX_FACTOR,
    detect_regex,
)
from tests.test_validators import make_inn12, make_snils

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
SNAPSHOT = Path(__file__).parent / "fixtures" / "detect_snapshot.json"
SNAPSHOT_CORPORA = ("seed", "dev")  # eval/blind в тестах не используется

OCR = OcrInfo(engine="tesseract-test rus+eng", pages=[], mean_confidence=0.9)
# Цифра → путаемый символ (обратная таблица для «порчи» номеров в тестах).
SPOIL = {"0": "О", "1": "l", "3": "З", "5": "S", "6": "б", "8": "B"}


def make_doc(text: str, *, ocr: bool) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="doc-ocr", filename="scan.pdf", text=text,
        segments=[TextSegment(id=0, text=text, offset=0, page=1)],
        ocr=OCR if ocr else None,
    )


def spoil(number: str, limit: int = 2) -> str:
    """Первые limit цифр из SPOIL заменить путаемыми буквами «как у OCR»."""
    out, left = [], limit
    for ch in number:
        if left and ch in SPOIL:
            out.append(SPOIL[ch])
            left -= 1
        else:
            out.append(ch)
    assert left == 0, "в номере мало путаемых цифр — возьмите другой seed"
    return "".join(out)


def of_type(entities: list[Entity], etype: EntityType) -> list[Entity]:
    return [e for e in entities if e.type == etype]


# ---------- digits_view ----------
@pytest.mark.parametrize("text", [
    "",
    "СНИЛС 1l2-233-445 9S",
    "Река Обь, ООО «Ромашка», ОГРН и ОКПО — без цифр",
    "тел. +7 (9l2) 345-67-8O, паспорт 45O6 l23456, д.р. 01.О2.l99О",
    "| 123 | S 2024 | Б | lll | 1OO руб.\n2О24\n",
])
def test_digits_view_keeps_length(text: str) -> None:
    assert len(digits_view(text)) == len(text)


def test_digits_view_fixes_snils_from_task() -> None:
    assert digits_view("СНИЛС 1l2-233-445 9S") == "СНИЛС 112-233-445 95"


@pytest.mark.parametrize("text", [
    "Обь", "река Обь", "ООО 12345678", "ОГРН 1027700132195", "Строка 2024", "OOO «Ромашка»",
])
def test_digits_view_keeps_words(text: str) -> None:
    """Буквы вне числовых токенов и слова рядом с числами не меняются."""
    assert digits_view(text) == text


@pytest.mark.parametrize(("text", "expected"), [
    ("+7 (9l2) 345-67-8O", "+7 (912) 345-67-80"),     # разделители не мешают доле цифр
    ("паспорт 45O6 l23456", "паспорт 4506 123456"),
    ("01.О2.l99О г.р.", "01.02.1990 г.р."),
    ("карта 22OO 1234 5678 9O12", "карта 2200 1234 5678 9012"),
    ("сумма 1OO руб.", "сумма 1OO руб."),              # цифр 1 из 3 — меньше 60%
    ("код l2", "код l2"),                              # короче 4 символов
])
def test_digits_view_numeric_tokens(text: str, expected: str) -> None:
    assert digits_view(text) == expected


def test_digits_view_replaces_only_table_chars() -> None:
    text = "СНИЛС 1l2-233-445 9S, ИНН 7707O83893"
    view = digits_view(text)
    for a, b in zip(text, view, strict=True):
        assert a == b or CONFUSABLE_DIGITS[a] == b


# ---------- regex на OCR-документе ----------
def test_regex_finds_spoiled_snils_only_in_ocr_doc() -> None:
    text = "Работник: СНИЛС 1l2-233-445 9S, стаж 5 лет."
    value = "1l2-233-445 9S"
    start = text.index(value)

    found = of_type(detect_regex(make_doc(text, ocr=True)), EntityType.SNILS)
    assert [(e.start, e.end) for e in found] == [(start, start + len(value))]
    assert found[0].confidence == round(CONF_CHECKSUM * OCR_FIX_FACTOR, 4)
    # value_masked — из исходного текста той же длины, без значения целиком
    assert len(found[0].value_masked) == len(value)
    assert found[0].value_masked != value

    assert of_type(detect_regex(make_doc(text, ocr=False)), EntityType.SNILS) == []


@pytest.mark.parametrize("seed", [3, 11, 42])
def test_regex_finds_spoiled_generated_numbers(seed: int) -> None:
    snils, inn = make_snils(seed), make_inn12(seed)
    bad_snils, bad_inn = spoil(snils), spoil(inn)
    text = f"СНИЛС {bad_snils}\nИНН физлица: {bad_inn}\n"
    entities = detect_regex(make_doc(text, ocr=True))
    assert [text[e.start:e.end] for e in of_type(entities, EntityType.SNILS)] == [bad_snils]
    assert [text[e.start:e.end] for e in of_type(entities, EntityType.INN)] == [bad_inn]
    plain = detect_regex(make_doc(text, ocr=False))
    assert of_type(plain, EntityType.SNILS) == of_type(plain, EntityType.INN) == []


def test_ocr_doc_without_spoiling_keeps_confidence() -> None:
    """Замен внутри спана не было — уверенность прежняя, результат как у обычного документа."""
    text = f"СНИЛС {make_snils(5)}, почта oleg.sokolov2024@example.ru"
    assert detect_regex(make_doc(text, ocr=True)) == detect_regex(make_doc(text, ocr=False))


def test_ocr_email_is_searched_in_original_text() -> None:
    text = "Пишите: oleg.SOKOLOV.l990@example.ru"
    emails = of_type(detect_regex(make_doc(text, ocr=True)), EntityType.EMAIL)
    assert len(emails) == 1
    assert emails[0].confidence == CONF_EMAIL
    assert text[emails[0].start:emails[0].end] == "oleg.SOKOLOV.l990@example.ru"


# ---------- регрессия: обычные документы — побитово прежний результат ----------
def _snapshot_rows(entities: list[Entity]) -> list[list[object]]:
    return [
        [e.type.value, e.category.value, e.start, e.end, e.detector, e.confidence]
        for e in entities
    ]


def test_detect_on_text_corpora_matches_snapshot() -> None:
    """detect() на eval/seed и eval/dev даёт ровно тот же список, что до T-077c.

    Снимок без значений: тип, категория, координаты, детектор, уверенность. Если детектор
    меняется намеренно — пересоздать: UPDATE_DETECT_SNAPSHOT=1 pytest -k snapshot.
    """
    if not all((EVAL_DIR / c).is_dir() for c in SNAPSHOT_CORPORA):
        pytest.skip(f"{EVAL_DIR} недоступен (образ api без папки eval/)")
    actual = {
        f"{corpus}/{item.name}": _snapshot_rows(detect(item.doc))
        for corpus in SNAPSHOT_CORPORA
        for item in load_corpus(EVAL_DIR / corpus)
    }
    if os.environ.get("UPDATE_DETECT_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n", "utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert actual.keys() == expected.keys()
    for name, rows in expected.items():
        assert actual[name] == rows, name
