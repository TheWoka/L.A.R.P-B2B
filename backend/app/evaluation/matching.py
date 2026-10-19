"""Сопоставление находок детектора с эталоном и метрики. Задача T-014a.

Правило совпадения (оно же пишется в Metrics.matching): тот же type и пересечение отрезков
не меньше 50% длины ЭТАЛОННОЙ сущности. Сопоставление один к одному, жадно по убыванию
длины пересечения: одна находка закрывает не больше одного эталона и наоборот.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.schemas import EntityType

MIN_OVERLAP = 0.5
MATCHING_RULE = (
    "тот же type и пересечение ≥ 50% длины эталонной сущности; "
    "один к одному, жадно по убыванию пересечения"
)


class Span(Protocol):
    """Всё, у чего есть type, start, end: GoldEntity и Entity."""

    @property
    def type(self) -> EntityType: ...
    @property
    def start(self) -> int: ...
    @property
    def end(self) -> int: ...


@dataclass(frozen=True)
class MatchResult:
    """Индексы: пары (эталон, находка), пропущенные эталоны, лишние находки."""

    pairs: list[tuple[int, int]]
    missed: list[int]
    extra: list[int]


def overlap(a: Span, b: Span) -> int:
    """Длина пересечения полуинтервалов [start, end)."""
    return max(0, min(a.end, b.end) - max(a.start, b.start))


def is_match(gold: Span, pred: Span) -> bool:
    """Тот же тип и пересечение ≥ MIN_OVERLAP длины эталона."""
    length = gold.end - gold.start
    return gold.type == pred.type and length > 0 and overlap(gold, pred) >= MIN_OVERLAP * length


def match_entities(gold: Sequence[Span], predicted: Sequence[Span]) -> MatchResult:
    """Один к одному: кандидаты сортируются по убыванию пересечения и берутся жадно.

    При равном пересечении раньше идёт пара с меньшими индексами — результат
    детерминирован. Пропущенные и лишние — по возрастанию индекса.
    """
    candidates = [
        (overlap(g, p), gi, pi)
        for gi, g in enumerate(gold)
        for pi, p in enumerate(predicted)
        if is_match(g, p)
    ]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, gi, pi in candidates:
        if gi in used_gold or pi in used_pred:
            continue
        used_gold.add(gi)
        used_pred.add(pi)
        pairs.append((gi, pi))
    missed = [i for i in range(len(gold)) if i not in used_gold]
    extra = [i for i in range(len(predicted)) if i not in used_pred]
    return MatchResult(sorted(pairs), missed, extra)


@dataclass
class Counts:
    """Счётчики по одному типу или группе: верные (tp), лишние (fp), пропуски (fn)."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: "Counts") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn

    @property
    def support(self) -> int:
        """Число эталонных сущностей."""
        return self.tp + self.fn


def precision(c: Counts) -> float:
    """tp / (tp + fp). Нет ни одной находки → 1.0: детектор ничего лишнего не нашёл."""
    found = c.tp + c.fp
    return 1.0 if found == 0 else c.tp / found


def recall(c: Counts) -> float:
    """tp / (tp + fn). Нет эталонных сущностей → 1.0: пропускать было нечего."""
    return 1.0 if c.support == 0 else c.tp / c.support


def f1(c: Counts) -> float:
    """Гармоническое среднее precision и recall; оба равны 0 → 0.0."""
    p, r = precision(c), recall(c)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def count_by_type(
    gold: Sequence[Span], predicted: Sequence[Span], result: MatchResult,
) -> dict[EntityType, Counts]:
    """Раскладывает результат сопоставления в счётчики по типам."""
    counts: dict[EntityType, Counts] = {}
    for gi, _ in result.pairs:
        counts.setdefault(gold[gi].type, Counts()).tp += 1
    for gi in result.missed:
        counts.setdefault(gold[gi].type, Counts()).fn += 1
    for pi in result.extra:
        counts.setdefault(predicted[pi].type, Counts()).fp += 1
    return counts
