"""LLM-проверки условий правил типа `llm`. Задачи T-002, T-013b, T-013c, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпты лежат в llm/prompts/.

В LLM уходит ТОЛЬКО замаскированный текст (AGENTS.md, правило 5): masked_text из
MaskResult с плейсхолдерами [PERSON_1]. Нарушение засчитывается, только если LLM ответила
"yes" и её цитата дословно нашлась во фрагменте; иначе — молчание (verdict "unknown").

T-013c. Документ проверяется окнами (до MAX_WINDOWS_PER_CONDITION окон по
MAX_FRAGMENT_CHARS символов с перекрытием). Каждый фрагмент ограничен строками с меткой
из sha256 самого фрагмента: автор документа не может заранее знать метку и закрыть блок
текста раньше времени, а одинаковый фрагмент даёт одинаковое сообщение — кэш Redis работает.
"""

from __future__ import annotations

import hashlib
import logging
from bisect import bisect_right
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.schemas import Evidence, LlmCall, MaskSpan
from app.services.llm import client
from app.services.llm.providers import LLMBudgetExceeded, LLMError
from app.services.masking.masker import expand_to_placeholders, to_original_offset

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "rule_condition.md"
MAX_FRAGMENT_CHARS = 6000          # длина одного окна
WINDOW_OVERLAP_CHARS = 400         # перекрытие соседних окон: фраза на стыке видна целиком
BOUNDARY_LOOKBACK_CHARS = 500      # насколько назад искать конец абзаца или предложения
MAX_WINDOWS_PER_CONDITION = 4      # больше окон на одно условие не спрашиваем (бюджет)
DELIMITER_LABEL_CHARS = 12         # первые 12 hex sha256(фрагмента)
_DEFUSE = (("<<<", "\u2039\u2039\u2039"), (">>>", "\u203a\u203a\u203a"))  # ‹‹‹ ›››, 3 → 3 символа

Verdict = Literal["yes", "no", "unknown"]


class LlmConditionAnswer(BaseModel):
    """Ответ LLM на вопрос из условия правила.

    Порядок значений verdict важен: mock строит ответ по первому значению Literal,
    поэтому безопасное "unknown" стоит первым (mock не создаёт ложных нарушений).
    """

    verdict: Literal["unknown", "no", "yes"]
    quote: str | None = None


@dataclass(frozen=True)
class Window:
    """Окно замаскированного текста: [start, end) в masked_text и текст, ушедший в LLM."""

    start: int
    end: int
    text: str  # masked_text[start:end] с обезвреженными «<<<» и «>>>»; длина та же


@dataclass
class ConditionOutcome:
    """Итог условия по всем окнам: Evidence (если нарушение), записи журнала, вердикт."""

    evidence: Evidence | None = None
    calls: list[LlmCall] = field(default_factory=list)
    verdict: Verdict = "unknown"


def check_llm_condition(
    question: str,
    masked_text: str,
    spans: list[MaskSpan],
    rule_id: str,
    *,
    report_text: str,
    priority_offsets: Sequence[int] = (),
) -> tuple[Evidence | None, list[LlmCall]]:
    """Задаёт LLM вопрос из условия правила по ЗАМАСКИРОВАННОМУ тексту, окнами.

    masked_text и spans — из MaskResult; report_text — текст с маскированием, сохраняющим
    длину (ReportText.text), из него берётся Evidence.quote. priority_offsets — смещения
    в ИСХОДНОМ тексте (evidence маркеров того же правила): окно с ними спрашивается первым.
    Возвращает Evidence первого окна с "yes" и дословной цитатой (в исходных координатах;
    page/section заполняет движок) и записи журнала — по одной на каждое окно.
    Ошибки LLM наружу не выходят (см. ask_condition).
    """
    outcome = ask_condition(
        question, masked_text, spans, rule_id,
        report_text=report_text, priority_offsets=priority_offsets,
    )
    return outcome.evidence, outcome.calls


def ask_condition(
    question: str,
    masked_text: str,
    spans: list[MaskSpan],
    rule_id: str,
    *,
    report_text: str,
    priority_offsets: Sequence[int] = (),
) -> ConditionOutcome:
    """Спрашивает окна по порядку до первого "yes" с цитатой.

    Итог: "yes" — нарушение; иначе хотя бы одно "unknown" (или бюджет кончился) → "unknown";
    все окна "no" → "no". mock → "unknown" без запроса. Исчерпан бюджет — остальные окна
    не спрашиваются и в журнал не пишутся (если бюджета не хватило на первое окно — одна
    запись с пустым фрагментом: «в LLM ничего не ушло»).
    """
    if not client.is_llm_enabled():
        return ConditionOutcome(calls=[_call(rule_id, question, "", "unknown", None, cached=False)])
    windows = split_windows(masked_text, spans)
    hints = [_to_masked_offset(offset, spans) for offset in priority_offsets]
    selected = order_windows(windows, hints)[:MAX_WINDOWS_PER_CONDITION]
    if len(windows) > len(selected):
        logger.info(
            "LLM-условие правила %s: окон всего %d, проверено не более %d",
            rule_id, len(windows), len(selected),
        )
    outcome = ConditionOutcome(verdict="no")
    for index, window in enumerate(selected, start=1):
        result = _ask_window(question, window, spans, rule_id, report_text)
        if result is None:
            logger.warning(
                "LLM-условие правила %s: лимит вызовов исчерпан на окне %d из %d, ответ unknown",
                rule_id, index, len(selected),
            )
            if not outcome.calls:
                outcome.calls.append(_call(rule_id, question, "", "unknown", None, cached=False))
            outcome.verdict = "unknown"
            return outcome
        evidence, call = result
        outcome.calls.append(call)
        if evidence is not None:
            outcome.evidence, outcome.verdict = evidence, "yes"
            return outcome
        if call.verdict == "unknown":
            outcome.verdict = "unknown"
    return outcome


def _ask_window(
    question: str,
    window: Window,
    spans: list[MaskSpan],
    rule_id: str,
    report_text: str,
) -> tuple[Evidence | None, LlmCall] | None:
    """Один вызов LLM по одному окну. None — исчерпан бюджет (в LLM ничего не ушло)."""
    try:
        with client.trace_cache() as trace:
            raw = client.complete_json(
                _system_prompt(), _user_message(question, window.text), LlmConditionAnswer,
            )
        answer = LlmConditionAnswer.model_validate(raw.model_dump())
    except LLMBudgetExceeded:
        return None
    except LLMError as exc:
        logger.warning("LLM-условие правила %s: %s, ответ unknown", rule_id, type(exc).__name__)
        return None, _call(rule_id, question, window.text, "unknown", None, cached=False)
    return _interpret(answer, window, spans, rule_id, question, report_text, trace.hit)


def _interpret(
    answer: LlmConditionAnswer,
    window: Window,
    spans: list[MaskSpan],
    rule_id: str,
    question: str,
    report_text: str,
    cached: bool,
) -> tuple[Evidence | None, LlmCall]:
    """Проверяет цитату и понижает "yes" без дословной цитаты до "unknown"."""
    located = locate_quote(window.text, answer.quote, spans, offset=window.start)
    verdict: Verdict = answer.verdict
    if verdict == "yes" and located is None:
        verdict = "unknown"
    quote = None
    if located is not None:
        quote = window.text[located[0] - window.start:located[1] - window.start]
    logger.info(
        "LLM-условие правила %s: verdict=%s quote_found=%s fragment_chars=%d cached=%s",
        rule_id, verdict, located is not None, len(window.text), cached,
    )
    call = _call(rule_id, question, window.text, verdict, quote, cached=cached)
    if verdict != "yes" or located is None:
        return None, call
    start, end = (to_original_offset(offset, spans) for offset in located)
    return Evidence(quote=report_text[start:end], start=start, end=end), call


def locate_quote(
    fragment: str, quote: str | None, spans: list[MaskSpan], *, offset: int = 0,
) -> tuple[int, int] | None:
    """Ищет цитату во фрагменте дословно; границы расширяет до целых плейсхолдеров.

    Срезаются только пробелы по краям цитаты. Пустая цитата или цитата, которой нет
    во фрагменте, → None. fragment — masked_text[offset:...] (окно, возможно с
    обезвреженными «<<<»/«>>>»: замена 3 → 3 символа координаты не сдвигает).
    Возвращает координаты в masked_text.
    """
    needle = (quote or "").strip()
    if not needle:
        return None
    start = fragment.find(needle)
    if start == -1:
        return None
    return expand_to_placeholders(offset + start, offset + start + len(needle), spans)


# ------------------------------------------------------------------ окна ---


def split_windows(masked_text: str, spans: list[MaskSpan]) -> list[Window]:
    """Делит masked_text на окна ≤ MAX_FRAGMENT_CHARS с перекрытием WINDOW_OVERLAP_CHARS.

    Граница окна не режет плейсхолдер и по возможности стоит после перевода строки или «. »
    (поиск назад не дальше BOUNDARY_LOOKBACK_CHARS). Начало следующего окна тоже
    не попадает внутрь плейсхолдера. Пустой текст → одно пустое окно.
    """
    total = len(masked_text)
    bounds: list[tuple[int, int]] = []
    start = 0
    while start + MAX_FRAGMENT_CHARS < total:
        end = _window_end(masked_text, spans, start)
        bounds.append((start, end))
        start = max(_placeholder_start(end - WINDOW_OVERLAP_CHARS, spans), start + 1)
    bounds.append((start, total))
    return [Window(start, end, defuse(masked_text[start:end])) for start, end in bounds]


def _window_end(masked_text: str, spans: list[MaskSpan], start: int) -> int:
    """Конец окна: не внутри плейсхолдера, по возможности — конец абзаца или предложения."""
    limit = _placeholder_start(start + MAX_FRAGMENT_CHARS, spans)
    if limit <= start + WINDOW_OVERLAP_CHARS:  # гигантский плейсхолдер — режем по лимиту
        limit = start + MAX_FRAGMENT_CHARS
    floor = max(limit - BOUNDARY_LOOKBACK_CHARS, start + WINDOW_OVERLAP_CHARS + 1)
    newline = masked_text.rfind("\n", floor, limit)
    sentence = masked_text.rfind(". ", floor, limit)
    candidates = [pos + 1 for pos in (newline,) if pos != -1]
    candidates += [pos + 2 for pos in (sentence,) if pos != -1 and pos + 2 <= limit]
    return max(candidates, default=limit)


def _placeholder_start(offset: int, spans: list[MaskSpan]) -> int:
    """Если offset строго внутри плейсхолдера — его начало, иначе сам offset."""
    i = bisect_right(spans, offset, key=lambda s: s.masked_start) - 1
    if i >= 0 and spans[i].masked_start < offset < spans[i].masked_end:
        return spans[i].masked_start
    return offset


def order_windows(windows: list[Window], hints: Sequence[int]) -> list[Window]:
    """Окна с подсказками (evidence маркеров правила) — первыми, остальные — по тексту."""
    hinted = [w for w in windows if any(w.start <= hint < w.end for hint in hints)]
    return hinted + [w for w in windows if w not in hinted]


def _to_masked_offset(original_offset: int, spans: list[MaskSpan]) -> int:
    """Обратное к to_original_offset: смещение исходного текста → смещение в masked_text."""
    i = bisect_right(spans, original_offset, key=lambda s: s.orig_start) - 1
    if i < 0:
        return original_offset
    span = spans[i]
    if original_offset < span.orig_end:
        return span.masked_start  # внутри значения ПДн → начало плейсхолдера
    return span.masked_end + (original_offset - span.orig_end)


# ------------------------------------------------- промпт и разделители ---


def defuse(fragment: str) -> str:
    """Обезвреживает «<<<» и «>>>» во фрагменте: «‹‹‹» и «›››» (длина не меняется)."""
    for marker, safe in _DEFUSE:
        fragment = fragment.replace(marker, safe)
    return fragment


def delimiter_label(fragment: str) -> str:
    """Метка разделителя: первые 12 hex sha256 фрагмента. Детерминирована → кэш работает."""
    return hashlib.sha256(fragment.encode("utf-8")).hexdigest()[:DELIMITER_LABEL_CHARS]


def _system_prompt() -> str:
    """Системный промпт из prompts/rule_condition.md (читается при каждом вызове — файл мал)."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _user_message(question: str, fragment: str) -> str:
    """Сообщение пользователя: вопрос, метка и фрагмент между строками с меткой.

    fragment уже обезврежен (defuse): внутри нет «<<<» и «>>>», а метку из хэша
    фрагмента автор документа заранее знать не может.
    """
    fragment = defuse(fragment)
    label = delimiter_label(fragment)
    return (
        f"Вопрос: {question}\n\n"
        f"Метка текста: {label}\n"
        f"<<<ТЕКСТ_{label}\n{fragment}\nТЕКСТ_{label}>>>"
    )


def _call(
    rule_id: str,
    question: str,
    fragment: str,
    verdict: Verdict,
    quote: str | None,
    *,
    cached: bool,
) -> LlmCall:
    """Запись журнала «Что видела нейросеть». fragment == "" — в LLM ничего не ушло."""
    return LlmCall(
        purpose="rule_condition", rule_id=rule_id, question=question,
        masked_fragment=fragment, verdict=verdict, quote=quote, cached=cached,
    )


# ------------------------------------------------- журнал вызовов документа ---

_current_log: ContextVar[list[LlmCall] | None] = ContextVar("llm_call_log", default=None)


@contextmanager
def llm_call_log() -> Iterator[list[LlmCall]]:
    """Собирает LlmCall всех LLM-условий внутри блока (по образцу llm_call_budget).

    Конвейер открывает `with llm_call_log() as calls:` вокруг evaluate(); движок правил
    добавляет записи через record_llm_call. Вложенный блок получает свой список.
    """
    calls: list[LlmCall] = []
    token = _current_log.set(calls)
    try:
        yield calls
    finally:
        _current_log.reset(token)


def record_llm_call(call: LlmCall) -> None:
    """Добавляет запись в текущий журнал; вне llm_call_log запись не сохраняется."""
    calls = _current_log.get()
    if calls is not None:
        calls.append(call)
