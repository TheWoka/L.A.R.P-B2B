"""Общие функции поиска сущностей нейросетью «по цитатам». Задачи T-013d, T-013e.

Внутренний интерфейс модуля llm — не контракт. Используют trade_secret.py (коммерческая тайна)
и special_category.py (особые категории ПДн). Схема одна:
1. masked_text режется на куски-строки (pieces) — длинные строки по предложениям;
2. отобранные куски (Candidate) пакуются в пакеты (Batch) «номер| текст»;
3. LLM получает пакет (request_quotes) и ПРЕДЛАГАЕТ цитаты;
4. код засчитывает цитату (locate_quote): дословно в пакете, нужной длины, без плейсхолдеров ПДн;
   координаты переводятся в исходный текст.

В LLM уходит только masked_text (AGENTS.md, правило 5). Логи — только счётчики (правило 4).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.schemas import Entity, MaskSpan
from app.services.llm import client
from app.services.llm.checks import _user_message as user_message
from app.services.llm.providers import LLMBudgetExceeded, LLMError
from app.services.masking.masker import expand_to_placeholders, to_original_offset

logger = logging.getLogger(__name__)

MAX_BATCH_CHARS = 4000        # длина текста одного пакета вместе с номерами строк
MAX_CANDIDATE_CHARS = 400     # длинная строка режется по предложениям до этой длины
EDGE_JUNK = " \t,;:"          # срезается с краёв принятой цитаты
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class Candidate:
    """Строка-кандидат: [start, end) в masked_text; text — обезврежен (defuse), длина та же."""

    start: int
    end: int
    text: str


@dataclass
class Batch:
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Ровно то, что уходит в LLM: «номер| строка» через перевод строки."""
        return "\n".join(f"{i}| {c.text}" for i, c in enumerate(self.candidates, start=1))


@dataclass(frozen=True)
class QuoteReply:
    """Ответ LLM на один пакет: предложенные цитаты; failed — сбой (в журнал — unknown)."""

    quotes: list[str]
    cached: bool
    failed: bool = False


@dataclass(frozen=True)
class LocatedQuote:
    """Принятая по форме цитата: координаты в исходном тексте и вид в пакете (для журнала)."""

    start: int
    end: int
    masked_start: int
    masked_end: int
    shown: str


# --------------------------------------------------------------- куски ---


def pieces(masked_text: str) -> list[tuple[int, int]]:
    """Строки по «\\n»; длинные — по предложениям ≤ MAX_CANDIDATE_CHARS. Края без пробелов."""
    result: list[tuple[int, int]] = []
    offset = 0
    for line in masked_text.split("\n"):
        if len(line) <= MAX_CANDIDATE_CHARS:
            result.append((offset, offset + len(line)))
        else:
            result.extend(_split_long(line, offset))
        offset += len(line) + 1
    stripped = [_strip(masked_text, start, end) for start, end in result]
    return [(start, end) for start, end in stripped if end > start]


def _split_long(line: str, offset: int) -> list[tuple[int, int]]:
    """Склеивает предложения в куски ≤ MAX_CANDIDATE_CHARS; длинное предложение — по пробелу."""
    bounds = [0] + [m.end() for m in _SENTENCE_END_RE.finditer(line)] + [len(line)]
    result: list[tuple[int, int]] = []
    start = 0
    for prev, cur in zip(bounds, bounds[1:], strict=False):
        if cur - start > MAX_CANDIDATE_CHARS and prev > start:
            result.append((start, prev))
            start = prev
        while cur - start > MAX_CANDIDATE_CHARS:
            cut = line.rfind(" ", start + 1, start + MAX_CANDIDATE_CHARS)
            cut = cut if cut > start else start + MAX_CANDIDATE_CHARS
            result.append((start, cut))
            start = cut
    if start < len(line):
        result.append((start, len(line)))
    return [(offset + s, offset + e) for s, e in result]


def _strip(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def covered_by(
    start: int, end: int, spans: list[MaskSpan], entities: list[Entity],
) -> bool:
    """Кусок [start, end) masked_text целиком внутри одной из entities (исходные координаты)."""
    orig = (to_original_offset(start, spans), to_original_offset(end, spans))
    return any(e.start <= orig[0] and orig[1] <= e.end for e in entities)


def pack_batches(candidates: list[Candidate], max_chars: int = MAX_BATCH_CHARS) -> list[Batch]:
    """Пакеты ≤ max_chars в порядке candidates (все; обрезка по числу пакетов — у вызова)."""
    batches: list[Batch] = []
    current = Batch()
    for candidate in candidates:
        trial = Batch([*current.candidates, candidate])
        if current.candidates and len(trial.text) > max_chars:
            batches.append(current)
            current = Batch([candidate])
        else:
            current = trial
    if current.candidates:
        batches.append(current)
    return batches


# ---------------------------------------------------------- вызов LLM ---


def request_quotes(
    system: str, question: str, fragment: str, schema: type[BaseModel], log_name: str,
) -> QuoteReply | None:
    """Один вызов LLM по пакету. schema — ответ вида {"items": [{"quote": str}]}.

    None — бюджет исчерпан (в LLM ничего не ушло). Сбой LLM или неверный ответ →
    QuoteReply(failed=True). Наружу ошибки не выходят. В лог — только имя ошибки.
    """
    try:
        with client.trace_cache() as trace:
            raw = client.complete_json(system, user_message(question, fragment), schema)
        answer = schema.model_validate(raw.model_dump())
    except LLMBudgetExceeded:
        return None
    except (LLMError, ValidationError) as exc:
        logger.warning("%s: %s, пакет без ответа (unknown)", log_name, type(exc).__name__)
        return QuoteReply(quotes=[], cached=False, failed=True)
    quotes = [str(item["quote"]) for item in answer.model_dump().get("items", [])]
    return QuoteReply(quotes=quotes, cached=trace.hit)


# ------------------------------------------------------ проверка цитат ---


def locate_in_batch(quote: str, batch: Batch) -> tuple[int, int, str] | None:
    """Ищет цитату дословно в строках пакета (без номера строки).

    Возвращает (start, end) в masked_text и фрагмент, как он выглядит в пакете (для журнала).
    Края цитаты: пробелы, «,», «;», «:» срезаются.
    """
    needle = quote.strip()
    if not needle:
        return None
    for candidate in batch.candidates:
        pos = candidate.text.find(needle)
        if pos == -1:
            continue
        start, end = pos, pos + len(needle)
        while start < end and candidate.text[start] in EDGE_JUNK:
            start += 1
        while end > start and candidate.text[end - 1] in EDGE_JUNK:
            end -= 1
        return candidate.start + start, candidate.start + end, candidate.text[start:end]
    return None


def locate_quote(
    quote: str, batch: Batch, spans: list[MaskSpan], min_chars: int, max_chars: int,
) -> LocatedQuote | None:
    """Проверка формы цитаты: дословно в пакете, длина min..max, без плейсхолдеров ПДн."""
    located = locate_in_batch(quote, batch)
    if located is None:
        return None
    m_start, m_end, shown = located
    if not min_chars <= m_end - m_start <= max_chars:
        return None
    if touches_placeholder(m_start, m_end, spans):
        return None
    start, end = to_original_offset(m_start, spans), to_original_offset(m_end, spans)
    return LocatedQuote(start, end, m_start, m_end, shown)


def touches_placeholder(start: int, end: int, spans: list[MaskSpan]) -> bool:
    """Есть ли в [start, end) masked_text хотя бы часть плейсхолдера ПДн."""
    start, end = expand_to_placeholders(start, end, spans)
    return any(s.masked_start < end and start < s.masked_end for s in spans)


def overlaps(entity: Entity, start: int, end: int) -> bool:
    return entity.start < end and start < entity.end
