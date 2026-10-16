"""Клиент LLM. Задачи T-002, T-013, исполнитель — Данила.

В LLM уходит ТОЛЬКО замаскированный текст (AGENTS.md, правило 5).

Публичное API модуля:
- `complete_json` — контракт (docs/contracts.md, раздел 3);
- `is_llm_enabled` — True только при реальном провайдере, идёт в `Report.llm_used`;
- `llm_call_budget` — лимит вызовов на документ;
- ошибки `LLMError` → `LLMRequestError`, `LLMResponseError`, `LLMBudgetExceeded`;
- `set_mock_response` / `reset_mock_responses` — подмена ответов mock в тестах.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.services.llm.budget import CallBudget, charge_llm_call, llm_call_budget
from app.services.llm.cache import CACHE_TTL_S, Cache, get_default_cache, make_cache_key
from app.services.llm.providers import (
    LLMBudgetExceeded,
    LLMError,
    LLMRequestError,
    LLMResponseError,
    SleepFn,
    add_repair_turn,
    build_messages,
    mock_complete,
    parse_json_response,
    request_chat,
    reset_mock_responses,
    set_mock_response,
)

__all__ = [
    "CallBudget",
    "LLMBudgetExceeded",
    "LLMError",
    "LLMRequestError",
    "LLMResponseError",
    "complete_json",
    "is_llm_enabled",
    "llm_call_budget",
    "reset_mock_responses",
    "run_complete_json",
    "set_mock_response",
]

logger = logging.getLogger(__name__)

ProviderName = Literal["mock", "openai_compatible"]
MAX_JSON_ATTEMPTS = 2  # первый ответ + один повтор с текстом ошибки валидации

_fallback_warned = False


def resolve_provider(settings: Settings) -> ProviderName:
    """Фактический провайдер. openai_compatible без LLM_BASE_URL/LLM_MODEL → mock.

    Падать нельзя: жюри запускает без нашего ключа (AGENTS.md, правило 7).
    Предупреждение пишется в лог один раз за процесс.
    """
    global _fallback_warned
    if settings.llm_provider != "openai_compatible":
        return "mock"
    if settings.llm_base_url.strip() and settings.llm_model.strip():
        return "openai_compatible"
    if not _fallback_warned:
        _fallback_warned = True
        logger.warning(
            "LLM_PROVIDER=openai_compatible, но не заданы LLM_BASE_URL или LLM_MODEL: "
            "работаем в режиме mock"
        )
    return "mock"


def is_llm_enabled(settings: Settings | None = None) -> bool:
    """True, только если запросы реально уходят в LLM (не mock). Для Report.llm_used."""
    return resolve_provider(settings or get_settings()) == "openai_compatible"


def complete_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    """Запрашивает у LLM ответ в JSON и валидирует его схемой schema.

    user — только замаскированный текст (`MaskResult.masked_text`). Исходный
    текст документа сюда передавать нельзя (AGENTS.md, правило 5).

    Провайдер из LLM_PROVIDER: mock — детерминированный безопасный ответ без сети
    и ключа; openai_compatible — запрос к LLM_BASE_URL. Успешные ответы реального
    провайдера кэшируются в Redis по sha256(запроса) на 7 дней.
    Внутри `with llm_call_budget():` действует лимит вызовов на документ.

    Ошибки: LLMRequestError (сеть/HTTP), LLMResponseError (ответ не по схеме),
    LLMBudgetExceeded (лимит исчерпан). Все — наследники LLMError.
    Контракт: docs/contracts.md, раздел 3.
    """
    return run_complete_json(system, user, schema, settings=get_settings())


def run_complete_json(
    system: str,
    user: str,
    schema: type[BaseModel],
    *,
    settings: Settings,
    cache: Cache | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: SleepFn = time.sleep,
) -> BaseModel:
    """Реализация complete_json с явными зависимостями (подменяются в тестах).

    cache=None → общий RedisCache из REDIS_URL; transport=None → обычная сеть httpx.
    """
    if resolve_provider(settings) == "mock":
        return mock_complete(schema)
    active_cache = cache if cache is not None else get_default_cache(settings.redis_url)
    key = make_cache_key("openai_compatible", settings.llm_model, system, user, schema)
    cached = _read_cache(active_cache, key, schema)
    if cached is not None:
        _log_call(settings, len(system) + len(user), 0.0, {}, cache_hit=True, attempt=0)
        return cached
    result = _call_with_repair(system, user, schema, settings, transport, sleep)
    active_cache.set(key, result.model_dump_json(), CACHE_TTL_S)
    return result


def _read_cache(cache: Cache, key: str, schema: type[BaseModel]) -> BaseModel | None:
    """Ответ из кэша или None. Запись, не прошедшая валидацию, считается промахом."""
    raw = cache.get(key)
    if raw is None:
        return None
    try:
        return schema.model_validate_json(raw)
    except ValidationError:
        logger.warning("Кэш LLM: запись не соответствует схеме %s, игнорируем", schema.__name__)
        return None


def _call_with_repair(
    system: str,
    user: str,
    schema: type[BaseModel],
    settings: Settings,
    transport: httpx.BaseTransport | None,
    sleep: SleepFn,
) -> BaseModel:
    """Запрос к провайдеру; при невалидном JSON — один повтор с текстом ошибки.

    Каждая попытка (включая повтор) списывается из llm_call_budget.
    """
    messages = build_messages(system, user, schema)
    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        charge_llm_call()
        started = time.perf_counter()
        chat = request_chat(settings, messages, transport=transport, sleep=sleep)
        elapsed_ms = (time.perf_counter() - started) * 1000
        prompt_chars = sum(len(m["content"]) for m in messages)
        _log_call(settings, prompt_chars, elapsed_ms, chat.usage, cache_hit=False, attempt=attempt)
        try:
            return parse_json_response(chat.content, schema)
        except LLMResponseError as exc:
            if attempt == MAX_JSON_ATTEMPTS:
                raise LLMResponseError(
                    f"Ответ LLM не соответствует схеме {schema.__name__} "
                    f"после {MAX_JSON_ATTEMPTS} попыток"
                ) from None
            logger.warning("LLM: ответ не прошёл проверку схемой %s, повтор", schema.__name__)
            messages = add_repair_turn(messages, chat.content, exc.repair_hint)
    raise AssertionError("недостижимо")  # pragma: no cover


def _log_call(
    settings: Settings,
    prompt_chars: int,
    elapsed_ms: float,
    usage: dict[str, int],
    *,
    cache_hit: bool,
    attempt: int,
) -> None:
    """Пишет в лог только метаданные вызова — без текста промпта, ответа и ключа."""
    logger.info(
        "LLM вызов: provider=%s model=%s prompt_chars=%d time_ms=%.0f "
        "prompt_tokens=%s completion_tokens=%s total_tokens=%s cache_hit=%s attempt=%d",
        "openai_compatible",
        settings.llm_model,
        prompt_chars,
        elapsed_ms,
        usage.get("prompt_tokens", "-"),
        usage.get("completion_tokens", "-"),
        usage.get("total_tokens", "-"),
        cache_hit,
        attempt,
    )
