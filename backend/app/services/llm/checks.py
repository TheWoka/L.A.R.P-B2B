"""LLM-проверки условий правил типа `llm`. Задачи T-002, T-013b, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпты лежат в llm/prompts/.

В LLM уходит ТОЛЬКО замаскированный текст (AGENTS.md, правило 5): masked_text из
MaskResult с плейсхолдерами [PERSON_1]. Нарушение засчитывается, только если LLM ответила
"yes" и её цитата дословно нашлась во фрагменте; иначе — молчание (verdict "unknown").
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.schemas import Evidence, LlmCall, MaskSpan
from app.services.llm import client
from app.services.llm.providers import LLMBudgetExceeded, LLMError
from app.services.masking.masker import expand_to_placeholders, to_original_offset

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "rule_condition.md"
MAX_FRAGMENT_CHARS = 6000  # TODO(T-013c): окна по документу вместо первых 6000 символов

Verdict = Literal["yes", "no", "unknown"]


class LlmConditionAnswer(BaseModel):
    """Ответ LLM на вопрос из условия правила.

    Порядок значений verdict важен: mock строит ответ по первому значению Literal,
    поэтому безопасное "unknown" стоит первым (mock не создаёт ложных нарушений).
    """

    verdict: Literal["unknown", "no", "yes"]
    quote: str | None = None


def check_llm_condition(
    question: str,
    masked_text: str,
    spans: list[MaskSpan],
    rule_id: str,
    *,
    report_text: str,
) -> tuple[Evidence | None, LlmCall]:
    """Задаёт LLM вопрос из условия правила по ЗАМАСКИРОВАННОМУ тексту.

    masked_text и spans — из MaskResult; report_text — текст с маскированием, сохраняющим
    длину (ReportText.text), из него берётся Evidence.quote. Evidence — в исходных
    координатах (to_original_offset), только при verdict "yes" и дословно найденной цитате;
    page/section заполняет движок правил. Ошибки LLM наружу не выходят: mock, нет ключа,
    исчерпан бюджет, сбой провайдера → (None, LlmCall(verdict="unknown")).
    """
    fragment = _fragment(masked_text, spans)
    if not client.is_llm_enabled():
        return None, _call(rule_id, question, "", "unknown", None, cached=False)
    try:
        with client.trace_cache() as trace:
            raw = client.complete_json(
                _system_prompt(), _user_message(question, fragment), LlmConditionAnswer,
            )
        answer = LlmConditionAnswer.model_validate(raw.model_dump())
    except LLMBudgetExceeded:
        logger.warning("LLM-условие правила %s: лимит вызовов исчерпан, ответ unknown", rule_id)
        return None, _call(rule_id, question, "", "unknown", None, cached=False)
    except LLMError as exc:
        logger.warning("LLM-условие правила %s: %s, ответ unknown", rule_id, type(exc).__name__)
        return None, _call(rule_id, question, fragment, "unknown", None, cached=False)
    return _interpret(answer, fragment, spans, rule_id, question, report_text, trace.hit)


def _interpret(
    answer: LlmConditionAnswer,
    fragment: str,
    spans: list[MaskSpan],
    rule_id: str,
    question: str,
    report_text: str,
    cached: bool,
) -> tuple[Evidence | None, LlmCall]:
    """Проверяет цитату и понижает "yes" без дословной цитаты до "unknown"."""
    located = locate_quote(fragment, answer.quote, spans)
    verdict: Verdict = answer.verdict
    if verdict == "yes" and located is None:
        verdict = "unknown"
    quote = fragment[located[0]:located[1]] if located is not None else None
    logger.info(
        "LLM-условие правила %s: verdict=%s quote_found=%s fragment_chars=%d cached=%s",
        rule_id, verdict, located is not None, len(fragment), cached,
    )
    call = _call(rule_id, question, fragment, verdict, quote, cached=cached)
    if verdict != "yes" or located is None:
        return None, call
    start, end = (to_original_offset(offset, spans) for offset in located)
    return Evidence(quote=report_text[start:end], start=start, end=end), call


def locate_quote(
    fragment: str, quote: str | None, spans: list[MaskSpan]
) -> tuple[int, int] | None:
    """Ищет цитату во фрагменте дословно; границы расширяет до целых плейсхолдеров.

    Срезаются только пробелы по краям цитаты. Пустая цитата или цитата, которой нет
    во фрагменте, → None. Координаты — в masked_text (фрагмент — его начало).
    """
    needle = (quote or "").strip()
    if not needle:
        return None
    start = fragment.find(needle)
    if start == -1:
        return None
    return expand_to_placeholders(start, start + len(needle), spans)


def _fragment(masked_text: str, spans: list[MaskSpan]) -> str:
    """Первые MAX_FRAGMENT_CHARS символов; плейсхолдер на границе не разрезается."""
    if len(masked_text) <= MAX_FRAGMENT_CHARS:
        return masked_text
    cut = MAX_FRAGMENT_CHARS
    for span in spans:
        if span.masked_start < cut < span.masked_end:
            cut = span.masked_start
            break
    return masked_text[:cut]


def _system_prompt() -> str:
    """Системный промпт из prompts/rule_condition.md (читается при каждом вызове — файл мал)."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _user_message(question: str, fragment: str) -> str:
    """Сообщение пользователя: вопрос правила и замаскированный фрагмент между маркерами."""
    return f"Вопрос: {question}\n\n<<<ТЕКСТ\n{fragment}\nТЕКСТ>>>"


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
