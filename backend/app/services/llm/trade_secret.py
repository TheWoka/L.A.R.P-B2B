"""Поиск коммерческой тайны нейросетью с проверкой цитат. Задача T-013d, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпт — prompts/trade_secret.md.
Общие функции (куски, пакеты, вызов, проверка цитат) — в quotes.py (вынесены в T-013e).

Как устроено:
1. Кандидаты без LLM: строки masked_text (длинные — по предложениям ≤ 400 символов), где есть
   цифра вне плейсхолдеров и денежный / процентный признак или слово из CANDIDATE_STEMS.
   T-019b: строки без цифры — тоже кандидаты, если в них есть корень WORD_ONLY_STEMS_RE
   («клиент», «поставщик», «рецептур», «ноу-хау», «договорённ», «перечень» …): ст. 3 98-ФЗ
   защищает любые сведения, а не только числа — «перечень ключевых клиентов: ООО …»,
   «рецептура: …», «условия, которые мы согласовали с заводом». Строки, целиком покрытые уже
   найденной TRADE_SECRET, отбрасываются.
2. Кандидаты пакуются в пакеты ≤ MAX_BATCH_CHARS («номер| текст»), не больше MAX_TS_BATCHES
   на документ. Сначала строки с цифрой (сигнал сильнее), строкам без цифры — не больше
   MAX_WORD_BATCHES пакета и только если общий лимит MAX_TS_BATCHES ещё не исчерпан:
   стоимость проверки документа не растёт.
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
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

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
from app.services.llm.quotes import (
    MAX_BATCH_CHARS,
    MAX_CANDIDATE_CHARS,
    Batch,
    Candidate,
    covered_by,
    locate_in_batch,
    locate_quote,
    overlaps,
    pack_batches,
    pieces,
    request_quotes,
)
from app.services.masking.masker import PLACEHOLDER_RE

__all__ = [
    "MAX_BATCH_CHARS", "MAX_CANDIDATE_CHARS", "MAX_TS_BATCHES", "QUESTION", "Batch", "Candidate",
    "MAX_WORD_BATCHES", "TradeSecretAnswer", "TradeSecretItem", "accept_quote",
    "detect_trade_secrets_llm", "locate_in_batch", "pack_batches", "plan_batches",
    "select_candidates", "select_word_candidates",
]

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "trade_secret.md"
QUESTION = "Какие сведения в тексте составляют коммерческую тайну?"
MAX_TS_BATCHES = 3            # пакетов на документ (бюджет общий с условиями правил)
MAX_WORD_BATCHES = 1          # из них — пакетов строк без цифры (T-019b)
MIN_QUOTE_CHARS = 8
MAX_QUOTE_CHARS = 200
LLM_CONFIDENCE = 0.7

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
# Строка без цифры — кандидат, если есть корень сведений, которые 98-ФЗ защищает и без чисел:
# состав клиентов и поставщиков, рецептуры и технологии, договорённости, планы (T-019b).
WORD_ONLY_STEMS_RE = re.compile(
    r"клиент|поставщик|рецептур|технолог|ноу-хау|договор[её]нн|услови\w*\s+поставк"
    r"|план\w*\s+продаж|перечень",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")


class TradeSecretItem(BaseModel):
    quote: str


class TradeSecretAnswer(BaseModel):
    """Ответ LLM. Пустой список — безопасный ответ mock: ни одной находки."""

    items: list[TradeSecretItem] = []


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
    words = select_word_candidates(masked.masked_text, masked.spans, entities)
    batches = plan_batches(candidates, words)
    kept = list(entities)
    stats = _Stats()
    sent = 0
    for batch in batches:
        call = _ask_batch(batch, doc, masked.spans, kept, stats)
        if call is None:
            logger.warning("КТ через LLM: лимит вызовов исчерпан, пакетов отправлено %d", sent)
            break
        sent += 1
        record_llm_call(call)
    logger.info(
        "КТ через LLM: document_id=%s кандидатов=%d без_цифр=%d пакетов=%d отправлено=%d "
        "принято=%d отброшено=%d заменено_financial=%d",
        doc.document_id, len(candidates), len(words), len(batches), sent, stats.accepted,
        stats.rejected, stats.replaced_financial,
    )
    return sorted(kept, key=lambda e: (e.start, e.end))


# ----------------------------------------------------------- кандидаты ---


def select_candidates(
    masked_text: str, spans: list[MaskSpan], entities: list[Entity],
) -> list[Candidate]:
    """Строки с цифрой и денежным признаком или словом из CANDIDATE_STEMS, по порядку текста."""
    trade_secrets = [e for e in entities if e.type == EntityType.TRADE_SECRET]
    result: list[Candidate] = []
    for start, end in pieces(masked_text):
        text = masked_text[start:end]
        if not _looks_like_candidate(text):
            continue
        if covered_by(start, end, spans, trade_secrets):
            continue
        result.append(Candidate(start, end, defuse(text)))
    return result


def select_word_candidates(
    masked_text: str, spans: list[MaskSpan], entities: list[Entity],
) -> list[Candidate]:
    """Строки без цифры вне плейсхолдеров, но с корнем WORD_ONLY_STEMS_RE (T-019b)."""
    trade_secrets = [e for e in entities if e.type == EntityType.TRADE_SECRET]
    result: list[Candidate] = []
    for start, end in pieces(masked_text):
        plain = PLACEHOLDER_RE.sub(" ", masked_text[start:end])
        if _DIGIT_RE.search(plain) or WORD_ONLY_STEMS_RE.search(plain) is None:
            continue
        if len(plain.strip()) < MIN_QUOTE_CHARS or covered_by(start, end, spans, trade_secrets):
            continue
        result.append(Candidate(start, end, defuse(masked_text[start:end])))
    return result


def plan_batches(candidates: list[Candidate], words: list[Candidate]) -> list[Batch]:
    """Пакеты на документ: строки с цифрой, затем ≤ MAX_WORD_BATCHES пакета строк без цифры;
    всего не больше MAX_TS_BATCHES."""
    batches = pack_batches(candidates)[:MAX_TS_BATCHES]
    room = min(MAX_WORD_BATCHES, MAX_TS_BATCHES - len(batches))
    return batches + pack_batches(words)[:room]


def _looks_like_candidate(text: str) -> bool:
    """Цифра вне плейсхолдеров И (денежный признак ИЛИ слово из списка)."""
    plain = PLACEHOLDER_RE.sub(" ", text)
    if not _DIGIT_RE.search(plain):
        return False
    lowered = plain.lower()
    return MONEY_RE.search(plain) is not None or any(s in lowered for s in CANDIDATE_STEMS)


# ---------------------------------------------------------- вызов LLM ---


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _ask_batch(
    batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan], kept: list[Entity],
    stats: _Stats,
) -> LlmCall | None:
    """Один пакет: вызов, проверка цитат, обновление kept. None — бюджет исчерпан."""
    fragment = batch.text
    reply = request_quotes(
        _system_prompt(), QUESTION, fragment, TradeSecretAnswer, "КТ через LLM",
    )
    if reply is None:
        return None
    if reply.failed:
        return _call(fragment, "unknown", None, cached=False)
    first_quote: str | None = None
    for quote in reply.quotes:
        accepted = accept_quote(quote, batch, doc, spans, kept, stats)
        if accepted is None:
            stats.rejected += 1
            continue
        stats.accepted += 1
        first_quote = first_quote if first_quote is not None else accepted
    verdict: Verdict = "yes" if first_quote is not None else "no"
    return _call(fragment, verdict, first_quote, cached=reply.cached)


def _call(fragment: str, verdict: Verdict, quote: str | None, *, cached: bool) -> LlmCall:
    return LlmCall(
        purpose="trade_secret", rule_id=None, question=QUESTION, masked_fragment=fragment,
        verdict=verdict, quote=quote, cached=cached,
    )


# ------------------------------------------------------ проверка цитат ---


def accept_quote(
    quote: str, batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan],
    kept: list[Entity], stats: _Stats,
) -> str | None:
    """Засчитывает цитату: добавляет Entity в kept и возвращает цитату пакета, иначе None."""
    located = locate_quote(quote, batch, spans, MIN_QUOTE_CHARS, MAX_QUOTE_CHARS)
    if located is None:
        return None
    start, end = located.start, located.end
    if any(overlaps(e, start, end) for e in kept if e.type == EntityType.TRADE_SECRET):
        return None
    financial = [e for e in kept if e.type == EntityType.FINANCIAL and overlaps(e, start, end)]
    for entity in financial:
        kept.remove(entity)
    stats.replaced_financial += len(financial)
    entity = Entity(
        type=EntityType.TRADE_SECRET, category=EntityCategory.trade_secret, start=start, end=end,
        value_masked=doc.text[start:end], confidence=LLM_CONFIDENCE, detector="llm",
    )
    kept.append(assign_location([entity], doc)[0])
    return located.shown
