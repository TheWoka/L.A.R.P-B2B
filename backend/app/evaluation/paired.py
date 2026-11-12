"""Сравнение двух версий классификатора grif-cls на одном корпусе (A/B). Задача T-080b.

Обе модели прогоняются по одним и тем же документам, каждая один раз. Из одного прогона
берутся и обычные метрики (как у run_eval), и список «эталон найден / не найден» — второго
прогона для попарной таблицы нет.

Попарная таблица по эталонным сущностям:

                    B нашла   B не нашла
    A нашла          both       only_a
    A не нашла       only_b     neither

Значение имеют только несовпадения only_a и only_b: на них строится точный тест Макнемара
(двусторонний биномиальный). Он отвечает на вопрос «могла ли такая разница получиться
случайно, если модели одинаково хороши».

Правило решения записано в константах до прогона и в коде не подбирается. В отчёт попадают
только счётчики: ни текстов, ни значений сущностей (AGENTS.md, правило 4).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from math import comb
from pathlib import Path

from app.evaluation.corpus import CorpusDocument
from app.evaluation.markup import CATEGORY_BY_TYPE
from app.evaluation.matching import Counts, f1, match_entities, precision, recall
from app.evaluation.report import (
    PD_CATEGORIES,
    EvalResult,
    detect_with_model,
    evaluate_corpus,
    group_counts,
)
from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection import model_detector

# --- Правило A/B (записано 12.11 до прогона blind-2). B заменяет A, если одновременно: ---
# 1) recall ПДн у B не ниже, чем у A, больше чем на PD_RECALL_TOLERANCE;
# 2) recall коммерческой тайны у B не ниже, чем у A;
# 3) F1(SPECIAL_CATEGORY) + F1(TRADE_SECRET) у B строго больше, чем у A.
PD_RECALL_TOLERANCE = 0.006          # не больше одного эталона из 188 на blind-2
F1_TYPES: tuple[EntityType, ...] = (EntityType.SPECIAL_CATEGORY, EntityType.TRADE_SECRET)
TRADE_SECRET_CATEGORIES = frozenset({EntityCategory.trade_secret})

Detector = Callable[[ExtractedDocument], list[Entity]]
GoldHit = tuple[EntityType, bool]    # тип эталонной сущности и «найдена ли она»


@dataclass
class ModelRun:
    """Один прогон одной модели: метрики и список попаданий по эталонам (в порядке корпуса)."""

    name: str
    version: str
    thresholds: dict[str, float]
    result: EvalResult
    hits: list[GoldHit] = field(default_factory=list)


@dataclass(frozen=True)
class PairedTable:
    """Таблица 2×2 по эталонам: нашли обе, только A, только B, ни одна."""

    both: int
    only_a: int
    only_b: int
    neither: int

    @property
    def total(self) -> int:
        return self.both + self.only_a + self.only_b + self.neither


@dataclass(frozen=True)
class Check:
    """Одно условие правила: что сравнивали и выполнено ли."""

    name: str
    a: float
    b: float
    passed: bool


@contextmanager
def use_model_dir(model_dir: Path) -> Iterator[model_detector.LoadedModel]:
    """Временно подставляет папку модели в model_detector и возвращает загруженную модель.

    Модель не загрузилась → ValueError: иначе шаг классификатора молча пропустился бы
    и сравнивались бы одни словари.
    """
    saved = model_detector.MODEL_DIRS
    model_detector.MODEL_DIRS = (model_dir,)
    model_detector.load_model.cache_clear()
    try:
        model = model_detector.load_model()
        if model is None:
            raise ValueError(f"модель не загрузилась из {model_dir}")
        yield model
    finally:
        model_detector.MODEL_DIRS = saved
        model_detector.load_model.cache_clear()


def gold_hits(docs: Sequence[CorpusDocument], found: Sequence[list[Entity]]) -> list[GoldHit]:
    """Для каждой эталонной сущности корпуса — (тип, найдена ли). Правило совпадения и порядок
    находок те же, что в evaluate_document, поэтому счётчики сходятся с metrics.json."""
    hits: list[GoldHit] = []
    for item, entities in zip(docs, found, strict=True):
        predicted = sorted(entities, key=lambda e: (e.start, e.end))
        matched = {gi for gi, _ in match_entities(item.gold, predicted).pairs}
        hits += [(g.type, i in matched) for i, g in enumerate(item.gold)]
    return hits


def run_detector(
    docs: Sequence[CorpusDocument], detector: Detector,
) -> tuple[EvalResult, list[GoldHit]]:
    """Один прогон детектора: метрики run_eval и попадания по эталонам из тех же находок."""
    found: list[list[Entity]] = []

    def recording(doc: ExtractedDocument) -> list[Entity]:
        entities = detector(doc)
        found.append(entities)
        return entities

    result = evaluate_corpus(docs, recording)
    return result, gold_hits(docs, found)


def run_model(docs: Sequence[CorpusDocument], name: str, model_dir: Path) -> ModelRun:
    """Прогон корпуса с моделью из model_dir в режиме по умолчанию (без LLM)."""
    with use_model_dir(model_dir) as model:
        result, hits = run_detector(docs, detect_with_model)
        thresholds = {t.value: v for t, v in model.thresholds.items()}
        return ModelRun(name, model.version, thresholds, result, hits)


def paired_table(
    a: Sequence[GoldHit], b: Sequence[GoldHit], types: frozenset[EntityType] | None = None,
) -> PairedTable:
    """Таблица 2×2 по эталонам типов types (None — все). Списки должны идти в одном порядке."""
    if len(a) != len(b) or any(x[0] != y[0] for x, y in zip(a, b, strict=False)):
        raise ValueError("списки эталонов двух прогонов не совпадают")
    cells = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    for (etype, hit_a), (_, hit_b) in zip(a, b, strict=True):
        if types is None or etype in types:
            cells[hit_a, hit_b] += 1
    return PairedTable(
        both=cells[True, True], only_a=cells[True, False],
        only_b=cells[False, True], neither=cells[False, False],
    )


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Точный двусторонний тест Макнемара: p-значение по несовпадениям.

    При нулевой гипотезе «модели одинаковы» каждое несовпадение с вероятностью 1/2 в пользу
    любой из моделей, то есть min(only_a, only_b) ~ Bin(n, 1/2). Несовпадений нет → 1.0.
    """
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def types_of(categories: frozenset[EntityCategory]) -> frozenset[EntityType]:
    return frozenset(t for t, c in CATEGORY_BY_TYPE.items() if c in categories)


def f1_sum(counts: dict[EntityType, Counts]) -> float:
    """F1(SPECIAL_CATEGORY) + F1(TRADE_SECRET). Есть эталоны, но нет находок → F1 = 0."""
    return sum(f1(counts.get(t, Counts())) for t in F1_TYPES)


def decide(a: dict[EntityType, Counts], b: dict[EntityType, Counts]) -> list[Check]:
    """Три условия правила A/B для замены A на B (все значения — без округления)."""
    pd_a, pd_b = (recall(group_counts(c, PD_CATEGORIES)) for c in (a, b))
    ts_a, ts_b = (recall(group_counts(c, TRADE_SECRET_CATEGORIES)) for c in (a, b))
    sum_a, sum_b = f1_sum(a), f1_sum(b)
    return [
        Check(f"recall ПДн: B ≥ A − {PD_RECALL_TOLERANCE}", pd_a, pd_b,
              pd_b >= pd_a - PD_RECALL_TOLERANCE),
        Check("recall КТ: B ≥ A", ts_a, ts_b, ts_b >= ts_a),
        Check("F1(SPECIAL_CATEGORY) + F1(TRADE_SECRET): B > A", sum_a, sum_b, sum_b > sum_a),
    ]


def _type_row(counts: dict[EntityType, Counts], etype: EntityType) -> dict[str, float | int]:
    c = counts.get(etype, Counts())
    return {"tp": c.tp, "fp": c.fp, "fn": c.fn, "support": c.support,
            "precision": round(precision(c), 4), "recall": round(recall(c), 4),
            "f1": round(f1(c), 4)}


def _paired_row(a: ModelRun, b: ModelRun, types: frozenset[EntityType] | None) -> dict[str, object]:
    t = paired_table(a.hits, b.hits, types)
    return {"both": t.both, "only_a": t.only_a, "only_b": t.only_b, "neither": t.neither,
            "total": t.total, "mcnemar_p": round(mcnemar_exact(t.only_a, t.only_b), 4)}


def build_report(a: ModelRun, b: ModelRun, corpus: str) -> dict[str, object]:
    """Сводка A/B для ab.json: метрики, попарные таблицы, условия правила и решение."""
    groups: dict[str, frozenset[EntityType] | None] = {
        "все эталоны": None,
        "ПДн": types_of(PD_CATEGORIES),
        "КТ": types_of(TRADE_SECRET_CATEGORIES),
        "SPECIAL_CATEGORY": frozenset({EntityType.SPECIAL_CATEGORY}),
        "SPECIAL_CATEGORY + TRADE_SECRET": frozenset(F1_TYPES),
    }
    checks = decide(a.result.counts, b.result.counts)
    winner = b if all(c.passed for c in checks) else a
    models = {}
    for run in (a, b):
        counts = run.result.counts
        pd, ts = group_counts(counts, PD_CATEGORIES), group_counts(counts, TRADE_SECRET_CATEGORIES)
        models[run.name] = {
            "version": run.version, "thresholds": run.thresholds,
            "pd": {"tp": pd.tp, "support": pd.support, "recall": round(recall(pd), 4)},
            "trade_secret": {"tp": ts.tp, "support": ts.support, "recall": round(recall(ts), 4)},
            "f1_sum": round(f1_sum(counts), 4),
            "by_type": {t.value: _type_row(counts, t) for t in F1_TYPES},
        }
    return {
        "corpus": corpus, "documents": a.result.documents, "a": a.name, "b": b.name,
        "models": models,
        "paired": {name: _paired_row(a, b, types) for name, types in groups.items()},
        "rule": [{"check": c.name, "a": round(c.a, 4), "b": round(c.b, 4), "passed": c.passed}
                 for c in checks],
        "winner": winner.name,
    }


def format_report(report: dict[str, object]) -> str:
    """Таблицы A/B для консоли и ab.md: только числа."""
    a, b = str(report["a"]), str(report["b"])
    models: dict = report["models"]  # type: ignore[assignment]
    lines = [f"A/B на {report['corpus']}: A = {a} ({models[a]['version']}), "
             f"B = {b} ({models[b]['version']}), документов {report['documents']}", ""]
    lines.append(f"{'Показатель':<34}{a:>16}{b:>16}")
    for label, key in (("Recall ПДн", "pd"), ("Recall КТ", "trade_secret")):
        cells = [f"{g['recall']:.3f} ({g['tp']}/{g['support']})"
                 for g in (models[n][key] for n in (a, b))]
        lines.append(f"{label:<34}{cells[0]:>16}{cells[1]:>16}")
    for t in F1_TYPES:
        for key in ("precision", "recall", "f1"):
            va, vb = (models[n]["by_type"][t.value][key] for n in (a, b))
            lines.append(f"{t.value + ' ' + key:<34}{va:>16.3f}{vb:>16.3f}")
        fa, fb = (models[n]["by_type"][t.value]["fp"] for n in (a, b))
        lines.append(f"{t.value + ' лишних находок':<34}{fa:>16}{fb:>16}")
    lines.append(f"{'Сумма F1 (особые + КТ)':<34}{models[a]['f1_sum']:>16.4f}"
                 f"{models[b]['f1_sum']:>16.4f}")
    lines += ["", f"{'Эталоны':<34}{'обе':>6}{'только ' + a:>12}{'только ' + b:>12}"
                  f"{'ни одна':>9}{'p':>8}"]
    paired: dict = report["paired"]  # type: ignore[assignment]
    for name, row in paired.items():
        lines.append(f"{name:<34}{row['both']:>6}{row['only_a']:>12}{row['only_b']:>12}"
                     f"{row['neither']:>9}{row['mcnemar_p']:>8.3f}")
    lines.append("")
    for check in report["rule"]:  # type: ignore[union-attr]
        mark = "✅" if check["passed"] else "❌"
        lines.append(f"{mark} {check['check']}: A {check['a']:.4f}, B {check['b']:.4f}")
    lines.append(f"Решение: в релиз идёт {report['winner']}")
    return "\n".join(lines)
