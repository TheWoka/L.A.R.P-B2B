"""Тесты оценки качества детекции (T-014a): разметка, корпус, сопоставление, метрики, отчёт."""

import importlib.util
import json
import logging
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from docx import Document

from app.evaluation.corpus import load_corpus
from app.evaluation.markup import GoldEntity, parse_markup
from app.evaluation.matching import (
    MATCHING_RULE,
    Counts,
    count_by_type,
    f1,
    match_entities,
    precision,
    recall,
)
from app.evaluation.report import (
    ERRORS_FILE,
    METRICS_FILE,
    EvalResult,
    build_metrics,
    criteria_met,
    error_context,
    evaluate_corpus,
    format_table,
    pd_mask_entities,
    write_results,
)
from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, Metrics

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = REPO_ROOT / "eval" / "seed"
RUN_EVAL = REPO_ROOT / "eval" / "run_eval.py"

T = EntityType


def gold(t: EntityType, start: int, end: int) -> GoldEntity:
    category = EntityCategory.trade_secret if t == T.TRADE_SECRET else EntityCategory.pd
    return GoldEntity(t, category, start, end)


def pred(t: EntityType, start: int, end: int) -> Entity:
    return Entity(
        type=t, category=EntityCategory.pd, start=start, end=end,
        value_masked="█" * (end - start), confidence=0.9, detector="regex",
    )


# ---------- Парсер разметки ----------

def test_parse_markup_offsets_match_values() -> None:
    raw = "Представитель: [[PERSON:Иванов Иван Петрович]], тел. [[PHONE:+7 912 345-67-89]]"
    clean, entities = parse_markup(raw)
    assert clean == "Представитель: Иванов Иван Петрович, тел. +7 912 345-67-89"
    assert [clean[e.start:e.end] for e in entities] == ["Иванов Иван Петрович", "+7 912 345-67-89"]
    assert [e.category for e in entities] == [EntityCategory.pd, EntityCategory.pd]


def test_parse_markup_several_lines_and_categories() -> None:
    raw = (
        "Строка без разметки\n"
        "[[SNILS:112-233-445 95]] и [[TRADE_SECRET:наценка 18%]], [[FINANCIAL:выручка 5 млн]]\n"
        "Диагноз: [[SPECIAL_CATEGORY:диабет]]"
    )
    clean, entities = parse_markup(raw)
    values = [clean[e.start:e.end] for e in entities]
    assert values == ["112-233-445 95", "наценка 18%", "выручка 5 млн", "диабет"]
    assert [e.category for e in entities] == [
        EntityCategory.pd, EntityCategory.trade_secret, EntityCategory.financial,
        EntityCategory.pd_special,
    ]
    assert "[[" not in clean and "]]" not in clean


def test_parse_markup_text_without_markup_unchanged() -> None:
    assert parse_markup("ИНН 7707083893\nКПП 770701001") == ("ИНН 7707083893\nКПП 770701001", [])


def test_parse_markup_unknown_type_reports_file_and_line() -> None:
    with pytest.raises(ValueError, match=r"doc\.txt:2: неизвестный тип .*SURNAME"):
        parse_markup("ок\n[[SURNAME:Иванов]]", "doc.txt")


@pytest.mark.parametrize("raw", [
    "[[PERSON:Иванов [[PHONE:+7 912 345-67-89]] ]]",
    "[[PERSON:Иванов",
    "[[PERSON:]]",
    "лишнее ]] без начала",
    "[[Иванов]]",
])
def test_parse_markup_invalid_raises(raw: str) -> None:
    with pytest.raises(ValueError, match=r"x\.txt:1"):
        parse_markup(raw, "x.txt")


# ---------- Сопоставление ----------

def test_exact_match() -> None:
    result = match_entities([gold(T.PERSON, 0, 10)], [pred(T.PERSON, 0, 10)])
    assert result.pairs == [(0, 0)] and result.missed == [] and result.extra == []


def test_overlap_60_percent_matches_40_does_not() -> None:
    assert match_entities([gold(T.PERSON, 0, 10)], [pred(T.PERSON, 4, 20)]).pairs == [(0, 0)]
    result = match_entities([gold(T.PERSON, 0, 10)], [pred(T.PERSON, 6, 20)])
    assert result.pairs == [] and result.missed == [0] and result.extra == [0]


def test_overlap_threshold_is_relative_to_gold_length() -> None:
    # Находка длиннее эталона: 100% эталона покрыто — совпадение, хотя находка в 5 раз длиннее.
    assert match_entities([gold(T.ADDRESS, 10, 20)], [pred(T.ADDRESS, 0, 50)]).pairs == [(0, 0)]
    # Ровно 50% — совпадение (граница включается).
    assert match_entities([gold(T.ADDRESS, 0, 10)], [pred(T.ADDRESS, 5, 30)]).pairs == [(0, 0)]


def test_different_type_does_not_match() -> None:
    result = match_entities([gold(T.BANK_ACCOUNT, 0, 20)], [pred(T.FINANCIAL, 0, 20)])
    assert result.pairs == [] and result.missed == [0] and result.extra == [0]


def test_one_prediction_cannot_cover_two_gold() -> None:
    gold_list = [gold(T.PERSON, 0, 10), gold(T.PERSON, 11, 21)]
    result = match_entities(gold_list, [pred(T.PERSON, 0, 21)])
    assert len(result.pairs) == 1 and len(result.missed) == 1 and result.extra == []


def test_greedy_prefers_larger_overlap() -> None:
    # Находка 1 пересекает оба эталона; жадный выбор отдаёт её эталону с большим пересечением.
    gold_list = [gold(T.PERSON, 0, 10), gold(T.PERSON, 10, 20)]
    preds = [pred(T.PERSON, 4, 19), pred(T.PERSON, 0, 10)]
    result = match_entities(gold_list, preds)
    assert result.pairs == [(0, 1), (1, 0)]


# ---------- Метрики ----------

def test_precision_recall_f1_manual_example() -> None:
    # 3 эталона, 4 находки, 2 верных: P = 2/4, R = 2/3, F1 = 2·0.5·0.667 / 1.167 = 4/7.
    c = Counts(tp=2, fp=2, fn=1)
    assert precision(c) == pytest.approx(0.5)
    assert recall(c) == pytest.approx(2 / 3)
    assert f1(c) == pytest.approx(4 / 7)
    assert c.support == 3


def test_division_by_zero_rules() -> None:
    assert precision(Counts(tp=0, fp=0, fn=3)) == 1.0     # нет находок
    assert recall(Counts(tp=0, fp=2, fn=0)) == 1.0        # нет эталона
    assert f1(Counts(tp=0, fp=2, fn=3)) == 0.0            # P = 0, R = 0


def test_count_by_type_and_build_metrics() -> None:
    gold_list = [gold(T.PERSON, 0, 10), gold(T.TRADE_SECRET, 20, 40), gold(T.PHONE, 50, 60)]
    preds = [pred(T.PERSON, 0, 10), pred(T.EMAIL, 70, 80)]
    result = match_entities(gold_list, preds)
    counts = count_by_type(gold_list, preds, result)
    metrics = build_metrics(
        EvalResult(counts=counts, documents=1), "test", datetime(2026, 10, 19, tzinfo=UTC),
    )
    by_type = {m.type: m for m in metrics.by_type}
    assert list(by_type) == ["PERSON", "PHONE", "EMAIL", "TRADE_SECRET"]  # порядок EntityType
    assert by_type["EMAIL"].precision == 0.0 and by_type["EMAIL"].support == 0
    assert by_type["PHONE"].recall == 0.0 and by_type["PHONE"].precision == 1.0
    assert metrics.pd_recall == 0.5 and metrics.trade_secret_recall == 0.0
    assert metrics.matching == MATCHING_RULE
    assert not criteria_met(metrics)
    assert "❌" in format_table(metrics)


def test_empty_result_gives_recall_one() -> None:
    metrics = build_metrics(EvalResult(), "пусто")
    assert metrics.by_type == [] and metrics.pd_recall == 1.0 and criteria_met(metrics)


def test_error_context_window_and_markers() -> None:
    text = "a" * 40 + "VALUE" + "b" * 40
    ctx = error_context(text, gold(T.PERSON, 40, 45))
    assert ctx == "a" * 30 + "⟦VALUE⟧" + "b" * 30


# ---------- Корпус и сквозной прогон ----------

PD_VALUES = [
    "Белоусов Тимур Ренатович", "526-018-159 61", "+7 913 802-44-17",
    "t.belousov91@example.com", "Гусева Марина Олеговна", "626464460495",
]


def _write_corpus(folder: Path) -> None:
    (folder / "a.txt").write_text(
        "Участник: [[PERSON:Белоусов Тимур Ренатович]], СНИЛС [[SNILS:526-018-159 61]]\n"
        "ИНН организации 6723288166, КПП 701701001.\n"
        "[[TRADE_SECRET:Поставщик комплектующих — завод в Казани, договор до 2028 года]]\n",
        encoding="utf-8",
    )
    text_b = "Тел. +7 913 802-44-17, почта t.belousov91@example.com, наценка 14%."
    (folder / "b.txt").write_text(text_b, encoding="utf-8")
    spans_b = [("PHONE", "+7 913 802-44-17"), ("EMAIL", "t.belousov91@example.com")]
    _write_json(folder / "b.json", text_b, spans_b)
    docx_path = folder / "c.docx"
    document = Document()
    document.add_paragraph("Покупатель: ИП Гусева Марина Олеговна")
    document.add_paragraph("ИНН 626464460495")
    document.save(docx_path)
    from app.services.extraction.extract import extract
    text_c = extract(docx_path, "c.docx", "c").text
    spans_c = [("PERSON", "Гусева Марина Олеговна"), ("INN", "626464460495")]
    _write_json(folder / "c.json", text_c, spans_c)
    (folder / "README.md").write_text("не документ корпуса", encoding="utf-8")


def _write_json(path: Path, text: str, spans: list[tuple[str, str]]) -> None:
    entities = []
    for entity_type, value in spans:
        start = text.index(value)
        entities.append({"type": entity_type, "start": start, "end": start + len(value)})
    path.write_text(json.dumps({"purpose": "contract", "entities": entities}), encoding="utf-8")


def test_load_corpus_both_formats(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    docs = load_corpus(tmp_path)
    assert [d.name for d in docs] == ["a.txt", "b.txt", "c.docx"]
    for d in docs:
        assert all(d.doc.text[g.start:g.end].strip() for g in d.gold)
    assert docs[2].doc.text[docs[2].gold[0].start:docs[2].gold[0].end] == "Гусева Марина Олеговна"


def test_load_corpus_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="не найдена"):
        load_corpus(tmp_path / "нет")
    with pytest.raises(ValueError, match="нет документов"):
        load_corpus(tmp_path)
    (tmp_path / "x.txt").write_text("abc", encoding="utf-8")
    (tmp_path / "x.json").write_text(
        json.dumps({"entities": [
            {"type": "PERSON", "category": "financial", "start": 0, "end": 2},
        ]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="категория"):
        load_corpus(tmp_path)
    (tmp_path / "x.json").write_text(
        json.dumps({"entities": [{"type": "PERSON", "start": 0, "end": 99}]}), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="вне текста"):
        load_corpus(tmp_path)


def test_end_to_end_metrics_and_errors_without_pd(tmp_path: Path) -> None:
    corpus, out = tmp_path / "corpus", tmp_path / "out"
    corpus.mkdir()
    _write_corpus(corpus)
    result = evaluate_corpus(load_corpus(corpus))
    write_results(build_metrics(result, corpus.name), result.errors, out)

    metrics = Metrics.model_validate_json((out / METRICS_FILE).read_text(encoding="utf-8"))
    assert metrics.documents == 3 and metrics.corpus == "corpus"
    assert metrics.trade_secret_recall == 0.0  # сведения о поставщике словарём не ловятся
    errors_raw = (out / ERRORS_FILE).read_text(encoding="utf-8")
    errors = json.loads(errors_raw)
    assert any(e["kind"] == "missed" and e["type"] == "TRADE_SECRET" for e in errors)
    for value in PD_VALUES:
        assert value not in errors_raw


def test_errors_mask_gold_pd_in_context(tmp_path: Path) -> None:
    (tmp_path / "d.txt").write_text(
        "Сотрудник [[PERSON:Пономарёву Артёму Вадимовичу]] выдана справка", encoding="utf-8",
    )

    def no_detector(doc: ExtractedDocument) -> list[Entity]:
        return []

    result = evaluate_corpus(load_corpus(tmp_path), detector=no_detector)
    assert [e.kind for e in result.errors] == ["missed"]
    assert "Пономарёву" not in result.errors[0].context
    assert "⟦П█████████ А█████ В█████████⟧" in result.errors[0].context


def test_errors_mask_detected_pd_missing_from_gold(tmp_path: Path) -> None:
    # В эталоне ПДн нет (ошибка разметки), детектор нашёл ФИО и телефон: их значения
    # не должны попасть в errors.json, хотя это «лишние» находки.
    text = "Ответственный Лаптев Игорь Семёнович, тел. +7 913 222-41-07, срок 5 дней"
    (tmp_path / "e.txt").write_text(text, encoding="utf-8")
    values = ["Лаптев Игорь Семёнович", "+7 913 222-41-07"]

    def stub_detector(doc: ExtractedDocument) -> list[Entity]:
        found = []
        for entity_type, value in zip((T.PERSON, T.PHONE), values, strict=True):
            start = doc.text.index(value)
            found.append(pred(entity_type, start, start + len(value)))
        return found

    out = tmp_path / "out"
    result = evaluate_corpus(load_corpus(tmp_path), detector=stub_detector)
    write_results(build_metrics(result, "e"), result.errors, out)
    errors_raw = (out / ERRORS_FILE).read_text(encoding="utf-8")
    assert [e["kind"] for e in json.loads(errors_raw)] == ["extra", "extra"]
    for value in values:
        assert value not in errors_raw
    assert "Л█████ И████ С███████" in errors_raw and "срок 5 дней" in errors_raw


def test_pd_mask_entities_merges_overlapping_gold_and_predicted() -> None:
    text = "Иванов Иван Петрович и ИНН 7707083893"
    merged = pd_mask_entities(
        text, [gold(T.PERSON, 0, 11), gold(T.TRADE_SECRET, 23, 37)], [pred(T.PERSON, 7, 20)],
    )
    assert [(e.start, e.end) for e in merged] == [(0, 20)]  # КТ не маскируется
    assert merged[0].value_masked == "И█████ И███ П███████"


# ---------- Стартовый набор и командная строка ----------

def test_seed_corpus_is_valid_and_large_enough() -> None:
    if not SEED_DIR.is_dir():
        pytest.skip(f"{SEED_DIR} недоступен (образ api без папки eval/)")
    docs = load_corpus(SEED_DIR)
    counts = Counter(g.type for d in docs for g in d.gold)
    assert 20 <= len(docs) <= 40   # 25 + 6 отложенных документов T-019a
    assert sum(counts.values()) >= 120
    assert counts[T.TRADE_SECRET] >= 15
    for d in docs:
        assert 3 <= len(d.doc.text.strip().splitlines()) <= 15, d.name


def _load_run_eval() -> ModuleType:
    if not RUN_EVAL.is_file():
        pytest.skip(f"{RUN_EVAL} недоступен (образ api без папки eval/)")
    spec = importlib.util.spec_from_file_location("run_eval", RUN_EVAL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restore_app_logging() -> Iterator[None]:
    """run_eval.main приглушает логгер app — возвращаем уровень, чтобы не задеть другие тесты."""
    app_logger = logging.getLogger("app")
    level = app_logger.level
    yield
    app_logger.setLevel(level)


@pytest.mark.usefixtures("restore_app_logging")
def test_run_eval_strict_exit_codes(tmp_path: Path) -> None:
    run_eval = _load_run_eval()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_corpus(corpus)
    args = ["--corpus", str(corpus), "--out", str(tmp_path / "out")]
    assert run_eval.main(args) == 0
    assert run_eval.main([*args, "--strict"]) == 1
    assert (tmp_path / "out" / METRICS_FILE).is_file()
    (corpus / "bad.txt").write_text("[[NAME:x]]", encoding="utf-8")
    assert run_eval.main(args) == 2
