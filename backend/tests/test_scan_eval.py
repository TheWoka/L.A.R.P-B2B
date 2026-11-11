"""Корпус сканов (T-077b): перенос эталона в координаты OCR, lost-сущности, детерминизм."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.evaluation.corpus import load_corpus, validate_gold
from app.evaluation.markup import GoldEntity, parse_markup
from app.evaluation.matching import match_entities
from app.evaluation.report import evaluate_document
from app.evaluation.scan import DEFAULT_FONT, char_map, render_scan_pages, transfer_gold
from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument

T = EntityType
REPO_ROOT = Path(__file__).resolve().parents[2]
MAKE_SCAN = REPO_ROOT / "eval" / "make_scan_corpus.py"

RAW = (
    "Поставщик [[PERSON:Петров Олег Иванович]] из ООО «Обод»,\n"
    "почта [[EMAIL:oleg@example.ru]]; резерв [[PHONE:+7 900 000-00-02]] конец."
)


def ocr_like(clean: str) -> str:
    """Имитация OCR: «О» → «0», перенос строки вместо пробела, выпали «Олег» и телефон."""
    return (
        clean.replace("Олег ", "")
        .replace("+7 900 000-00-02 ", "")
        .replace("О", "0")
        .replace("из ", "из\n")
    )


def pred(t: EntityType, start: int, end: int) -> Entity:
    return Entity(
        type=t, category=EntityCategory.pd, start=start, end=end,
        value_masked="█" * (end - start), confidence=0.9, detector="regex",
    )


# ---------- Выравнивание ----------


def test_char_map_equal_and_replace_are_mapped_delete_is_none() -> None:
    mapping = char_map("ООО Сокол", "000 Сокол")
    assert mapping == list(range(9))  # замена О → 0 — символ прочитан, позиция сохраняется
    mapping = char_map("один два три", "один три")
    assert mapping[5:9] == [None] * 4  # «два » выпало
    assert mapping[:5] == list(range(5)) and mapping[9:] == [5, 6, 7]


def test_transfer_gold_with_replacements_and_dropped_words() -> None:
    clean, gold = parse_markup(RAW)
    ocr = ocr_like(clean)
    person, email, phone = transfer_gold(gold, clean, ocr)

    # ФИО: «Олег» выпал, «П…» осталось, «О» в «Обод» не входит. Отрезок — от «Петров» до «Иванович».
    assert ocr[person.start:person.end] == "Петров Иванович"
    assert not person.lost and person.type == T.PERSON
    # e-mail перенесён без изменений, хотя до него текст сдвинулся
    assert ocr[email.start:email.end] == "oleg@example.ru"
    assert not email.lost
    # телефон потерян целиком: нулевой отрезок там, где он был, перед « конец»
    assert phone.lost and phone.start == phone.end
    assert ocr[phone.start:].startswith("конец")
    validate_gold([person, email, phone], len(ocr), "ocr")


def test_transfer_gold_depends_only_on_texts() -> None:
    clean, gold = parse_markup(RAW)
    ocr = ocr_like(clean)
    assert transfer_gold(gold, clean, ocr) == transfer_gold(gold, clean, ocr)
    assert transfer_gold(gold, clean, clean) == gold  # без искажений эталон не меняется


# ---------- lost-сущности в корпусе и метриках ----------


def test_lost_entity_is_missed_whatever_is_predicted() -> None:
    lost = GoldEntity(T.PHONE, EntityCategory.pd, 10, 10, lost=True)
    for p in (pred(T.PHONE, 0, 20), pred(T.PHONE, 10, 11), pred(T.PHONE, 9, 10)):
        result = match_entities([lost], [p])
        assert result.pairs == [] and result.missed == [0] and result.extra == [0]


def test_corpus_format2_accepts_lost_and_counts_it_as_miss(tmp_path: Path) -> None:
    text = "Тел. +7 900 000-00-01, резерв утерян."
    (tmp_path / "a.txt").write_text(text, encoding="utf-8")
    entities = [
        {"type": "PHONE", "category": "pd", "start": 5, "end": 21},
        {"type": "PHONE", "category": "pd", "start": 29, "end": 29, "lost": True},
    ]
    (tmp_path / "a.json").write_text(
        json.dumps({"purpose": "scan", "entities": entities}), encoding="utf-8",
    )
    [item] = load_corpus(tmp_path)
    assert [g.lost for g in item.gold] == [False, True]

    def detector(doc: ExtractedDocument) -> list[Entity]:
        return [pred(T.PHONE, 5, 21), pred(T.PHONE, 22, 36)]

    result = evaluate_document(item, detector)
    phone = result.counts[T.PHONE]
    assert (phone.tp, phone.fn, phone.fp) == (1, 1, 1)
    assert [e.kind for e in result.errors].count("missed") == 1


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"type": "PHONE", "start": 3, "end": 3}, "вне текста"),
        ({"type": "PHONE", "start": 3, "end": 5, "lost": True}, "start == end"),
        ({"type": "PHONE", "start": 3, "end": 3, "lost": "да"}, "lost"),
    ],
)
def test_corpus_rejects_bad_zero_length(tmp_path: Path, item: dict, message: str) -> None:
    (tmp_path / "a.txt").write_text("Тел. +7 900 000-00-01", encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps({"entities": [item]}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_corpus(tmp_path)


# ---------- Детерминизм генератора ----------


def needs_render() -> None:
    pytest.importorskip("PIL")
    if not DEFAULT_FONT.is_file():
        pytest.skip(f"нет шрифта {DEFAULT_FONT} (fonts-dejavu-core)")


def test_render_is_deterministic_by_seed() -> None:
    needs_render()
    text = "Строка один\nСтрока два, достаточно длинная, чтобы проверить перенос по словам " * 3
    first = render_scan_pages(text, seed=7, dpi=100)
    assert first == render_scan_pages(text, seed=7, dpi=100)
    assert first != render_scan_pages(text, seed=8, dpi=100)
    assert all(page[:3] == b"\xff\xd8\xff" for page in first)  # JPEG


@pytest.fixture
def make_scan(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    if not MAKE_SCAN.is_file():
        pytest.skip(f"{MAKE_SCAN} недоступен (образ api без папки eval/)")
    spec = importlib.util.spec_from_file_location("make_scan_corpus", MAKE_SCAN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # нужно @dataclass при exec_module
    spec.loader.exec_module(module)
    return module


def test_generator_is_deterministic(tmp_path: Path, make_scan: ModuleType) -> None:
    needs_render()
    source = tmp_path / "src"
    source.mkdir()
    (source / "01_a.txt").write_text(RAW, encoding="utf-8")
    (source / "02_b.txt").write_text("Клиент [[PERSON:Сидорова Ольга]].", encoding="utf-8")
    cleans = {p.stem: parse_markup(p.read_text(encoding="utf-8"))[0] for p in source.iterdir()}

    def fake_ocr(pdf: Path) -> str:  # OCR детерминирован; здесь — без tesseract
        assert pdf.read_bytes().startswith(b"%PDF")
        return ocr_like(cleans[pdf.stem])

    runs = []
    for n in (1, 2):
        out = tmp_path / f"out{n}"
        make_scan.make_corpus(source, out, seed=7, dpi=100, extractor=fake_ocr)
        runs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert runs[0] == runs[1]
    assert set(runs[0]) == {"01_a.json", "01_a.pdf", "02_b.json", "02_b.pdf", "README.md"}
    data = json.loads(runs[0]["01_a.json"])
    assert [e.get("lost", False) for e in data["entities"]] == [False, False, True]
    assert all("value" not in e for e in data["entities"])  # значения ПДн не пишутся
    assert "| PHONE | 1 | 1 |" in runs[0]["README.md"].decode("utf-8")
