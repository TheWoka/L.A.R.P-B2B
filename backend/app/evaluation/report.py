"""Прогон детектора по корпусу, сборка Metrics, файла ошибок и таблицы. Задача T-014a.

Файлы результатов (eval/results/, в .gitignore):
- metrics.json — Metrics.model_dump(mode="json"), схема contracts.md, раздел 1;
- errors.json  — пропуски и лишние находки: {file, kind, type, start, end, context}.
`context` — ±30 символов вокруг сущности. ПДн в нём замаскированы mask_preserving_length:
и эталонные, и найденные детектором (pd, pd_special) — значения ПДн не попадают в файлы
даже на синтетике.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.evaluation.corpus import CorpusDocument
from app.evaluation.markup import CATEGORY_BY_TYPE, GoldEntity
from app.evaluation.matching import (
    MATCHING_RULE,
    Counts,
    Span,
    count_by_type,
    f1,
    match_entities,
    precision,
    recall,
)
from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument, Metrics, TypeMetrics
from app.services.detection.detect import detect
from app.services.llm.client import llm_call_budget
from app.services.llm.trade_secret import detect_trade_secrets_llm
from app.services.masking.masker import MASKED_CATEGORIES, mask, mask_preserving_length, mask_value

PD_THRESHOLD = 0.90
TRADE_SECRET_THRESHOLD = 0.85
PD_CATEGORIES = frozenset({EntityCategory.pd, EntityCategory.pd_special})
CONTEXT_CHARS = 30
METRICS_FILE, ERRORS_FILE = "metrics.json", "errors.json"
LLM_CORPUS_SUFFIX = " + LLM"  # Metrics.corpus при --llm: страница «Качество» не спутает прогоны

Detector = Callable[[ExtractedDocument], list[Entity]]


@dataclass
class LlmDetector:
    """Детектор для --llm (T-013d): detect → mask → detect_trade_secrets_llm, как в конвейере.

    На каждый документ — свой llm_call_budget (LLM_MAX_CALLS_PER_DOC). calls — сколько
    реальных запросов ушло за прогон (попадания в кэш не считаются) — для оценки стоимости.
    """

    calls: int = 0
    documents: int = 0

    def __call__(self, doc: ExtractedDocument) -> list[Entity]:
        entities = detect(doc)
        masked = mask(doc.text, entities)
        with llm_call_budget() as budget:
            found = detect_trade_secrets_llm(doc, masked, entities)
        self.calls += budget.used
        self.documents += 1
        return found


class EvalError(BaseModel):
    """Одна ошибка детектора для разбора: пропуск эталона или лишняя находка."""

    file: str
    kind: Literal["missed", "extra"]
    type: str
    start: int
    end: int
    context: str  # ±30 символов, ПДн эталона и находок замаскированы, сущность в ⟦ ⟧


@dataclass
class EvalResult:
    """Итог прогона: счётчики по типам, ошибки и число документов."""

    counts: dict[EntityType, Counts] = field(default_factory=dict)
    errors: list[EvalError] = field(default_factory=list)
    documents: int = 0


def pd_mask_entities(
    text: str, gold: Sequence[GoldEntity], predicted: Sequence[Entity],
) -> list[Entity]:
    """ПДн эталона и находок (pd, pd_special) → непересекающиеся Entity для маскирования.

    Объединяем оба источника: находка детектора, которой нет в эталоне (ошибка разметки
    или лишняя находка), тоже может быть настоящим ПДн. Пересекающиеся и смежные отрезки
    сливаются, потому что mask_preserving_length не принимает пересечений. Тип слитого
    отрезка — тип первого из них, на маскирование он не влияет.
    """
    spans = sorted(
        (s for s in [*gold, *predicted] if s.category in MASKED_CATEGORIES),
        key=lambda s: (s.start, s.end),
    )
    merged: list[list[int]] = []
    types: list[EntityType] = []
    for s in spans:
        if merged and s.start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s.end)
        else:
            merged.append([s.start, s.end])
            types.append(s.type)
    return [
        Entity(
            type=entity_type, category=EntityCategory.pd, start=start, end=end,
            value_masked=mask_value(text[start:end]), confidence=1.0, detector="model",
        )
        for (start, end), entity_type in zip(merged, types, strict=True)
    ]


def error_context(masked_text: str, span: Span) -> str:
    """±CONTEXT_CHARS символов вокруг отрезка; сам отрезок в ⟦ ⟧, переводы строк → пробел."""
    left = masked_text[max(0, span.start - CONTEXT_CHARS):span.start]
    right = masked_text[span.end:span.end + CONTEXT_CHARS]
    inner = masked_text[span.start:span.end]
    return f"{left}⟦{inner}⟧{right}".replace("\n", " ")


def _make_error(name: str, kind: Literal["missed", "extra"], span: Span, masked: str) -> EvalError:
    return EvalError(
        file=name, kind=kind, type=span.type.value, start=span.start, end=span.end,
        context=error_context(masked, span),
    )


def evaluate_document(item: CorpusDocument, detector: Detector = detect) -> EvalResult:
    """Детекция одного документа и сравнение с эталоном."""
    predicted = sorted(detector(item.doc), key=lambda e: (e.start, e.end))
    result = match_entities(item.gold, predicted)
    masked = mask_preserving_length(
        item.doc.text, pd_mask_entities(item.doc.text, item.gold, predicted),
    )
    errors = [_make_error(item.name, "missed", item.gold[i], masked) for i in result.missed]
    errors += [_make_error(item.name, "extra", predicted[i], masked) for i in result.extra]
    errors.sort(key=lambda e: e.start)
    return EvalResult(count_by_type(item.gold, predicted, result), errors, 1)


def evaluate_corpus(docs: Sequence[CorpusDocument], detector: Detector = detect) -> EvalResult:
    """Прогон по всему корпусу; счётчики суммируются (микро-усреднение)."""
    total = EvalResult()
    for item in docs:
        part = evaluate_document(item, detector)
        for entity_type, counts in part.counts.items():
            total.counts.setdefault(entity_type, Counts()).add(counts)
        total.errors.extend(part.errors)
        total.documents += 1
    return total


def group_counts(counts: dict[EntityType, Counts], categories: frozenset[EntityCategory]) -> Counts:
    """Сумма счётчиков по типам нужных категорий."""
    total = Counts()
    for entity_type, c in counts.items():
        if CATEGORY_BY_TYPE[entity_type] in categories:
            total.add(c)
    return total


def build_metrics(
    result: EvalResult, corpus: str, created_at: datetime | None = None,
) -> Metrics:
    """EvalResult → Metrics. Типы без эталона и без находок не выводятся.

    pd_recall — микро-recall по pd и pd_special, trade_secret_recall — по trade_secret.
    FINANCIAL есть в by_type, но в критерии не входит. Нет эталона группы → recall 1.0.
    """
    by_type = [
        TypeMetrics(
            type=t.value, precision=round(precision(c), 4), recall=round(recall(c), 4),
            f1=round(f1(c), 4), support=c.support,
        )
        for t in EntityType
        if (c := result.counts.get(t)) is not None and c.support + c.fp > 0
    ]
    pd = group_counts(result.counts, PD_CATEGORIES)
    trade_secret = group_counts(result.counts, frozenset({EntityCategory.trade_secret}))
    return Metrics(
        corpus=corpus, documents=result.documents,
        created_at=created_at or datetime.now(UTC), by_type=by_type,
        pd_recall=round(recall(pd), 4), trade_secret_recall=round(recall(trade_secret), 4),
        matching=MATCHING_RULE,
    )


def criteria_met(metrics: Metrics) -> bool:
    """Критерии приёмки хакатона: recall ПДн ≥ 0.90 и коммерческой тайны ≥ 0.85."""
    return (
        metrics.pd_recall >= PD_THRESHOLD
        and metrics.trade_secret_recall >= TRADE_SECRET_THRESHOLD
    )


def write_results(metrics: Metrics, errors: Sequence[EvalError], out: Path) -> None:
    """Пишет metrics.json (повторно валидирует схемой) и errors.json в папку out."""
    out.mkdir(parents=True, exist_ok=True)
    payload = Metrics.model_validate(metrics.model_dump(mode="json")).model_dump(mode="json")
    _dump(out / METRICS_FILE, payload)
    _dump(out / ERRORS_FILE, [e.model_dump(mode="json") for e in errors])


def _dump(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def format_table(metrics: Metrics) -> str:
    """Таблица «тип → P / R / F1 / support» и два критерия с отметкой ✅/❌."""
    lines = [f"{'Тип':<18}{'P':>8}{'R':>8}{'F1':>8}{'support':>9}"]
    for m in metrics.by_type:
        lines.append(
            f"{m.type:<18}{m.precision:>8.3f}{m.recall:>8.3f}{m.f1:>8.3f}{m.support:>9}"
        )
    pd_ok = metrics.pd_recall >= PD_THRESHOLD
    ts_ok = metrics.trade_secret_recall >= TRADE_SECRET_THRESHOLD
    lines += [
        "",
        f"Recall ПДн:                {metrics.pd_recall:.3f} (≥ {PD_THRESHOLD:.2f}) {_mark(pd_ok)}",
        f"Recall коммерческой тайны: {metrics.trade_secret_recall:.3f} "
        f"(≥ {TRADE_SECRET_THRESHOLD:.2f}) {_mark(ts_ok)}",
        f"Документов: {metrics.documents}. Совпадение: {metrics.matching}",
    ]
    return "\n".join(lines)
