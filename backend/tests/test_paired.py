"""Тесты A/B двух моделей (T-080b): попарная таблица, тест Макнемара, правило решения."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from app.evaluation.corpus import CorpusDocument, text_document
from app.evaluation.markup import CATEGORY_BY_TYPE, GoldEntity
from app.evaluation.matching import Counts
from app.evaluation.paired import (
    PD_RECALL_TOLERANCE,
    ModelRun,
    PairedTable,
    build_report,
    decide,
    format_report,
    gold_hits,
    mcnemar_exact,
    paired_table,
    run_detector,
    use_model_dir,
)
from app.evaluation.report import EvalResult, build_metrics
from app.schemas import Entity, EntityType, ExtractedDocument
from app.services.detection import model_detector

REPO_ROOT = Path(__file__).resolve().parents[2]
AB_MODELS = REPO_ROOT / "eval" / "ab_models.py"
T = EntityType
TEXT = "Наценка составляет сорок процентов. Контакт: Тестов Тест Тестович. Диагноз: условный."
SECRET, PERSON, SPECIAL = (0, 34), (45, 65), (67, 84)


def gold(t: EntityType, span: tuple[int, int]) -> GoldEntity:
    return GoldEntity(t, CATEGORY_BY_TYPE[t], *span)


def pred(t: EntityType, span: tuple[int, int]) -> Entity:
    return Entity(type=t, category=CATEGORY_BY_TYPE[t], start=span[0], end=span[1],
                  value_masked="█" * (span[1] - span[0]), confidence=0.9, detector="model")


def corpus() -> list[CorpusDocument]:
    item = CorpusDocument(
        "synthetic.txt", text_document(TEXT, "synthetic.txt"),
        [gold(T.TRADE_SECRET, SECRET), gold(T.PERSON, PERSON), gold(T.SPECIAL_CATEGORY, SPECIAL)],
    )
    return [item, item]


def detector(*found: tuple[EntityType, tuple[int, int]]):
    def run(_: ExtractedDocument) -> list[Entity]:
        return [pred(t, span) for t, span in found]
    return run


def counts(pd: tuple[int, int], ts: tuple[int, int, int], special: tuple[int, int, int],
           ) -> dict[EntityType, Counts]:
    """pd = (tp, fn) по PERSON; ts и special = (tp, fp, fn)."""
    return {
        T.PERSON: Counts(tp=pd[0], fn=pd[1]),
        T.TRADE_SECRET: Counts(*ts),
        T.SPECIAL_CATEGORY: Counts(*special),
    }


# ---------- Попадания по эталонам ----------

def test_run_detector_hits_agree_with_metrics() -> None:
    result, hits = run_detector(corpus(), detector((T.PERSON, PERSON), (T.TRADE_SECRET, SECRET)))
    assert hits == [(T.TRADE_SECRET, True), (T.PERSON, True), (T.SPECIAL_CATEGORY, False)] * 2
    for etype, c in result.counts.items():
        assert c.tp == sum(1 for t, hit in hits if t == etype and hit)
        assert c.fn == sum(1 for t, hit in hits if t == etype and not hit)


def test_gold_hits_uses_type_and_overlap_rule() -> None:
    docs = corpus()[:1]
    wrong_type = [pred(T.FINANCIAL, SECRET)]                       # место то же, тип другой
    short = [pred(T.TRADE_SECRET, (0, 10))]                        # меньше 50% эталона
    assert not any(hit for _, hit in gold_hits(docs, [wrong_type]))
    assert not any(hit for _, hit in gold_hits(docs, [short]))


def test_gold_hits_requires_one_prediction_list_per_document() -> None:
    with pytest.raises(ValueError):
        gold_hits(corpus(), [[]])


# ---------- Таблица 2×2 ----------

def test_paired_table_counts_all_four_cells() -> None:
    a = [(T.PERSON, True), (T.PERSON, True), (T.TRADE_SECRET, False), (T.TRADE_SECRET, False)]
    b = [(T.PERSON, True), (T.PERSON, False), (T.TRADE_SECRET, True), (T.TRADE_SECRET, False)]
    assert paired_table(a, b) == PairedTable(both=1, only_a=1, only_b=1, neither=1)
    only_secrets = paired_table(a, b, frozenset({T.TRADE_SECRET}))
    assert only_secrets == PairedTable(both=0, only_a=0, only_b=1, neither=1)
    assert only_secrets.total == 2


def test_paired_table_rejects_different_gold_lists() -> None:
    with pytest.raises(ValueError):
        paired_table([(T.PERSON, True)], [(T.PHONE, True)])
    with pytest.raises(ValueError):
        paired_table([(T.PERSON, True)], [])


# ---------- Тест Макнемара ----------

@pytest.mark.parametrize(("only_a", "only_b", "expected"), [
    (0, 0, 1.0),          # несовпадений нет
    (3, 3, 1.0),          # поровну
    (0, 5, 0.0625),       # 2 · (1/2)^5
    (5, 0, 0.0625),       # симметрия
    (1, 9, 0.0215),       # 2 · (1 + 10) / 1024
    (2, 12, 0.0129),
])
def test_mcnemar_exact(only_a: int, only_b: int, expected: float) -> None:
    assert mcnemar_exact(only_a, only_b) == pytest.approx(expected, abs=5e-5)


# ---------- Правило решения ----------

def test_decide_all_three_conditions_pass() -> None:
    a = counts(pd=(180, 8), ts=(30, 10, 9), special=(15, 2, 8))
    b = counts(pd=(180, 8), ts=(35, 5, 4), special=(18, 2, 5))
    assert [c.passed for c in decide(a, b)] == [True, True, True]


def test_decide_pd_recall_tolerance_is_one_gold_of_188() -> None:
    a = counts(pd=(165, 0), ts=(30, 0, 9), special=(20, 0, 3))    # ПДн 185 из 188
    minus_one = counts(pd=(165, 0), ts=(30, 0, 9), special=(19, 0, 4))
    minus_two = counts(pd=(165, 0), ts=(30, 0, 9), special=(18, 0, 5))
    assert 1 / 188 < PD_RECALL_TOLERANCE < 2 / 188
    assert decide(a, minus_one)[0].passed
    assert not decide(a, minus_two)[0].passed


def test_decide_trade_secret_recall_must_not_drop() -> None:
    a = counts(pd=(10, 0), ts=(30, 10, 9), special=(5, 0, 5))
    b = counts(pd=(10, 0), ts=(29, 0, 10), special=(10, 0, 0))    # F1 выше, recall КТ ниже
    checks = decide(a, b)
    assert not checks[1].passed and checks[2].passed


def test_decide_f1_sum_must_be_strictly_greater() -> None:
    same = counts(pd=(10, 0), ts=(30, 10, 9), special=(5, 0, 5))
    assert [c.passed for c in decide(same, same)] == [True, True, False]


def test_decide_type_without_findings_has_zero_f1() -> None:
    a = {T.PERSON: Counts(tp=5), T.TRADE_SECRET: Counts(fn=3), T.SPECIAL_CATEGORY: Counts(fn=2)}
    b = {T.PERSON: Counts(tp=5), T.TRADE_SECRET: Counts(tp=1, fn=2),
         T.SPECIAL_CATEGORY: Counts(fn=2)}
    assert decide(a, b)[2].a == 0.0
    assert decide(a, b)[2].passed and not decide(b, a)[2].passed


# ---------- Сводка ----------

def make_run(name: str, *found: tuple[EntityType, tuple[int, int]]) -> ModelRun:
    result, hits = run_detector(corpus(), detector(*found))
    return ModelRun(name, f"grif-cls-{name}", {"TRADE_SECRET": 0.5}, result, hits)


def test_build_report_picks_b_only_when_rule_passes() -> None:
    old = make_run("old", (T.PERSON, PERSON))
    new = make_run("new", (T.PERSON, PERSON), (T.TRADE_SECRET, SECRET))
    report = build_report(old, new, "synthetic")
    assert report["winner"] == "new"
    assert report["paired"]["КТ"] == {
        "both": 0, "only_a": 0, "only_b": 2, "neither": 0, "total": 2, "mcnemar_p": 0.5,
    }
    assert report["paired"]["все эталоны"]["total"] == 6
    assert build_report(new, old, "synthetic")["winner"] == "new"   # A остаётся: B хуже


def test_report_has_numbers_only() -> None:
    report = build_report(make_run("old", (T.PERSON, PERSON)),
                          make_run("new", (T.TRADE_SECRET, SECRET)), "synthetic")
    dumped = json.dumps(report, ensure_ascii=False) + format_report(report)
    for fragment in ("Тестов", "Наценка", "условный"):
        assert fragment not in dumped
    assert "Решение: в релиз идёт old" in format_report(report)     # recall ПДн упал


def test_metrics_from_paired_run_match_run_eval_schema() -> None:
    run = make_run("old", (T.PERSON, PERSON))
    metrics = build_metrics(run.result, corpus="synthetic")
    assert metrics.pd_recall == 0.5 and metrics.trade_secret_recall == 0.0


# ---------- Подмена папки модели ----------

def test_use_model_dir_fails_loudly_and_restores(tmp_path: Path) -> None:
    saved = model_detector.MODEL_DIRS
    with pytest.raises(ValueError, match="не загрузилась"):
        with use_model_dir(tmp_path):
            pass
    assert model_detector.MODEL_DIRS == saved


# ---------- Командная строка ----------

def load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ab_models", AB_MODELS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not AB_MODELS.is_file(), reason="в образе api нет eval/")
def test_cli_writes_metrics_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = load_cli()
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "doc.txt").write_text(
        "[[TRADE_SECRET:Наценка составляет сорок процентов]]. Иное.", encoding="utf-8",
    )
    found = {"old": [], "new": [pred(T.TRADE_SECRET, SECRET)]}

    def fake_run_model(docs: list[CorpusDocument], name: str, _: Path) -> ModelRun:
        result, hits = run_detector(docs, lambda doc: found[name])
        return ModelRun(name, f"grif-cls-{name}", {}, result, hits)

    monkeypatch.setattr("app.evaluation.paired.run_model", fake_run_model)
    out = tmp_path / "out"
    code = cli.main(["--corpus", str(corpus_dir), "--a", "old=/a", "--b", "new=/b",
                     "--out", str(out)])
    assert code == 0
    report = json.loads((out / "ab.json").read_text(encoding="utf-8"))
    assert report["winner"] == "new" and report["corpus"] == "corpus"
    for name in ("old", "new"):
        metrics = json.loads((out / name / "metrics.json").read_text(encoding="utf-8"))
        assert metrics["corpus"] == "corpus"
    assert "Решение" in (out / "ab.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not AB_MODELS.is_file(), reason="в образе api нет eval/")
def test_cli_rejects_same_model_and_bad_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = load_cli()
    (tmp_path / "doc.txt").write_text("Текст без сущностей.", encoding="utf-8")
    monkeypatch.setattr(
        "app.evaluation.paired.run_model",
        lambda docs, name, _: ModelRun(name, "grif-cls-same", {}, EvalResult(), []),
    )
    args = ["--corpus", str(tmp_path), "--a", "old=/a", "--b", "new=/b", "--out", str(tmp_path)]
    assert cli.main(args) == 2
    with pytest.raises(SystemExit):
        cli.main(["--corpus", str(tmp_path), "--a", "old", "--b", "new=/b", "--out", "x"])
