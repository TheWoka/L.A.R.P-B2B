"""Поиск коммерческой тайны нейросетью с проверкой цитат. Задача T-013d, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпт — prompts/trade_secret.md.

Как устроено:
1. Кандидаты без LLM: строки masked_text (длинные — по предложениям ≤ 400 символов), где есть
   цифра вне плейсхолдеров и денежный / процентный признак или слово из CANDIDATE_STEMS.
   Строки, целиком покрытые уже найденной TRADE_SECRET, отбрасываются.
2. Кандидаты пакуются в пакеты ≤ MAX_BATCH_CHARS («номер| текст»), не больше MAX_TS_BATCHES.
3. LLM только ПРЕДЛАГАЕТ цитаты. Находку засчитывает код: цитата дословно есть в строке пакета,
   длина 8–200 символов, внутри нет плейсхолдера ПДн, нет пересечения с уже найденной КТ.
   Пересечение с FINANCIAL — FINANCIAL на этом месте убирается (сведения важнее суммы).
4. Каждый пакет — LlmCall(purpose="trade_secret") в журнале документа (record_llm_call).

В LLM уходит только masked_text (AGENTS.md, правило 5). value_masked у КТ — исходный фрагмент
(contracts.md, раздел 4.1); плейсхолдеров в принятой цитате нет, поэтому значения ПДн туда
не попадают. В лог — только счётчики, без текста (правило 4). mock → шаг ничего не делает.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.schemas import (
    Entity,
    EntityCategory,
    EntityType,
    ExtractedDocument,
    LlmCall,
    MaskResult,
    MaskSpan,
)
from app.services.detection.detect import assign_location
from app.services.llm import client
from app.services.llm.checks import Verdict, defuse, record_llm_call
from app.services.llm.checks import _user_message as user_message
from app.services.llm.providers import LLMBudgetExceeded, LLMError
from app.services.masking.masker import (
    PLACEHOLDER_RE,
    expand_to_placeholders,
    to_original_offset,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "trade_secret.md"
QUESTION = "Какие сведения в тексте составляют коммерческую тайну?"
MAX_TS_BATCHES = 3            # пакетов на документ (бюджет общий с условиями правил)
MAX_BATCH_CHARS = 4000        # длина текста одного пакета вместе с номерами строк
MAX_CANDIDATE_CHARS = 400     # длинная строка режется по предложениям до этой длины
MIN_QUOTE_CHARS = 8
MAX_QUOTE_CHARS = 200
LLM_CONFIDENCE = 0.7
_EDGE_JUNK = " \t,;:"         # срезается с краёв принятой цитаты

# Денежный / процентный признак: руб, ₽, %, тыс, млн, «за кг», «/шт», «по 900».
MONEY_RE = re.compile(
    r"руб|₽|%|(?<!\w)(?:тыс|млн|млрд)(?!\w)"
    r"|(?:/\s?|(?<!\w)за\s+)(?:кг|г|т|шт|ед|м|м2|м3|л|час|сут|мес)(?!\w)"
    r"|(?<!\w)по\s+\d",
    re.IGNORECASE,
)
CANDIDATE_STEMS = (
    "закуп", "поставщик", "клиент", "себестоим", "марж", "наценк", "накрут", "скидк", "рентаб",
    "входн", "оптов", "прайс", "рецептур", "технолог", "ноу-хау", "план продаж", "объём", "объем",
)
_DIGIT_RE = re.compile(r"\d")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;])\s+")


class TradeSecretItem(BaseModel):
    quote: str


class TradeSecretAnswer(BaseModel):
    """Ответ LLM. Пустой список — безопасный ответ mock: ни одной находки."""

    items: list[TradeSecretItem] = []


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


@dataclass
class _Stats:
    accepted: int = 0
    rejected: int = 0
    replaced_financial: int = 0


# ---------------------------------------------------------------- вход ---


def detect_trade_secrets_llm(
    doc: ExtractedDocument, masked: MaskResult, entities: list[Entity],
) -> list[Entity]:
    """Дополняет entities находками КТ от LLM; возвращает новый список, отсортированный по start.

    masked — MaskResult того же документа (в LLM уходит только masked.masked_text).
    Вызовы идут в текущий llm_call_budget и журнал llm_call_log. mock → entities без изменений,
    ни одного вызова и ни одной записи журнала. Ошибки LLM наружу не выходят.
    """
    if not client.is_llm_enabled():
        return list(entities)
    candidates = select_candidates(masked.masked_text, masked.spans, entities)
    batches = pack_batches(candidates)
    kept = list(entities)
    stats = _Stats()
    sent = 0
    for batch in batches[:MAX_TS_BATCHES]:
        call = _ask_batch(batch, doc, masked.spans, kept, stats)
        if call is None:
            logger.warning("КТ через LLM: лимит вызовов исчерпан, пакетов отправлено %d", sent)
            break
        sent += 1
        record_llm_call(call)
    logger.info(
        "КТ через LLM: document_id=%s кандидатов=%d пакетов=%d отправлено=%d принято=%d "
        "отброшено=%d заменено_financial=%d",
        doc.document_id, len(candidates), len(batches), sent, stats.accepted, stats.rejected,
        stats.replaced_financial,
    )
    return sorted(kept, key=lambda e: (e.start, e.end))


# ----------------------------------------------------------- кандидаты ---


def select_candidates(
    masked_text: str, spans: list[MaskSpan], entities: list[Entity],
) -> list[Candidate]:
    """Строки с цифрой и денежным признаком или словом из CANDIDATE_STEMS, по порядку текста."""
    trade_secrets = [e for e in entities if e.type == EntityType.TRADE_SECRET]
    result: list[Candidate] = []
    for start, end in _pieces(masked_text):
        text = masked_text[start:end]
        if not _looks_like_candidate(text):
            continue
        orig = (to_original_offset(start, spans), to_original_offset(end, spans))
        if any(e.start <= orig[0] and orig[1] <= e.end for e in trade_secrets):
            continue
        result.append(Candidate(start, end, defuse(text)))
    return result


def _looks_like_candidate(text: str) -> bool:
    """Цифра вне плейсхолдеров И (денежный признак ИЛИ слово из списка)."""
    plain = PLACEHOLDER_RE.sub(" ", text)
    if not _DIGIT_RE.search(plain):
        return False
    lowered = plain.lower()
    return MONEY_RE.search(plain) is not None or any(s in lowered for s in CANDIDATE_STEMS)


def _pieces(masked_text: str) -> list[tuple[int, int]]:
    """Строки по «\\n»; длинные — по предложениям ≤ MAX_CANDIDATE_CHARS. Края без пробелов."""
    pieces: list[tuple[int, int]] = []
    offset = 0
    for line in masked_text.split("\n"):
        if len(line) <= MAX_CANDIDATE_CHARS:
            pieces.append((offset, offset + len(line)))
        else:
            pieces.extend(_split_long(line, offset))
        offset += len(line) + 1
    stripped = [_strip(masked_text, start, end) for start, end in pieces]
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


def pack_batches(candidates: list[Candidate]) -> list[Batch]:
    """Пакеты ≤ MAX_BATCH_CHARS по порядку текста (все; обрезка до MAX_TS_BATCHES — у вызова)."""
    batches: list[Batch] = []
    current = Batch()
    for candidate in candidates:
        trial = Batch([*current.candidates, candidate])
        if current.candidates and len(trial.text) > MAX_BATCH_CHARS:
            batches.append(current)
            current = Batch([candidate])
        else:
            current = trial
    if current.candidates:
        batches.append(current)
    return batches


# ---------------------------------------------------------- вызов LLM ---


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _ask_batch(
    batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan], kept: list[Entity],
    stats: _Stats,
) -> LlmCall | None:
    """Один пакет: вызов, проверка цитат, обновление kept. None — бюджет исчерпан."""
    fragment = batch.text
    try:
        with client.trace_cache() as trace:
            raw = client.complete_json(
                _system_prompt(), user_message(QUESTION, fragment), TradeSecretAnswer,
            )
        answer = TradeSecretAnswer.model_validate(raw.model_dump())
    except LLMBudgetExceeded:
        return None
    except (LLMError, ValidationError) as exc:
        logger.warning("КТ через LLM: %s, пакет без ответа (unknown)", type(exc).__name__)
        return _call(fragment, "unknown", None, cached=False)
    first_quote: str | None = None
    for item in answer.items:
        accepted = accept_quote(item.quote, batch, doc, spans, kept, stats)
        if accepted is None:
            stats.rejected += 1
            continue
        stats.accepted += 1
        first_quote = first_quote if first_quote is not None else accepted
    verdict: Verdict = "yes" if first_quote is not None else "no"
    return _call(fragment, verdict, first_quote, cached=trace.hit)


def _call(fragment: str, verdict: Verdict, quote: str | None, *, cached: bool) -> LlmCall:
    return LlmCall(
        purpose="trade_secret", rule_id=None, question=QUESTION, masked_fragment=fragment,
        verdict=verdict, quote=quote, cached=cached,
    )


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
        while start < end and candidate.text[start] in _EDGE_JUNK:
            start += 1
        while end > start and candidate.text[end - 1] in _EDGE_JUNK:
            end -= 1
        return candidate.start + start, candidate.start + end, candidate.text[start:end]
    return None


def accept_quote(
    quote: str, batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan],
    kept: list[Entity], stats: _Stats,
) -> str | None:
    """Засчитывает цитату: добавляет Entity в kept и возвращает цитату пакета, иначе None."""
    located = locate_in_batch(quote, batch)
    if located is None:
        return None
    m_start, m_end, shown = located
    if not MIN_QUOTE_CHARS <= m_end - m_start <= MAX_QUOTE_CHARS:
        return None
    if _touches_placeholder(m_start, m_end, spans):
        return None
    start, end = to_original_offset(m_start, spans), to_original_offset(m_end, spans)
    if any(_overlaps(e, start, end) for e in kept if e.type == EntityType.TRADE_SECRET):
        return None
    financial = [e for e in kept if e.type == EntityType.FINANCIAL and _overlaps(e, start, end)]
    for entity in financial:
        kept.remove(entity)
    stats.replaced_financial += len(financial)
    entity = Entity(
        type=EntityType.TRADE_SECRET, category=EntityCategory.trade_secret, start=start, end=end,
        value_masked=doc.text[start:end], confidence=LLM_CONFIDENCE, detector="llm",
    )
    kept.append(assign_location([entity], doc)[0])
    return shown


def _touches_placeholder(start: int, end: int, spans: list[MaskSpan]) -> bool:
    """Есть ли в [start, end) masked_text хотя бы часть плейсхолдера ПДн."""
    start, end = expand_to_placeholders(start, end, spans)
    return any(s.masked_start < end and start < s.masked_end for s in spans)


def _overlaps(entity: Entity, start: int, end: int) -> bool:
    return entity.start < end and start < entity.end
