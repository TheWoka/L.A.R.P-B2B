"""Кэш ответов LLM. Внутренний модуль T-002, не контракт.

Ключ — sha256 от канонического JSON запроса, значение — уже провалидированный
JSON ответа. Redis необязателен: если он недоступен, клиент работает без кэша.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Protocol

import redis
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "llm:v1:"
CACHE_TTL_S = 7 * 24 * 60 * 60
REDIS_TIMEOUT_S = 0.5
REDIS_RETRY_AFTER_S = 60.0


class Cache(Protocol):
    """Минимальный интерфейс кэша: строковые ключи и значения, TTL в секундах."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl: int) -> None: ...


def make_cache_key(
    provider: str, model: str, system: str, user: str, schema: type[BaseModel]
) -> str:
    """Ключ кэша: `llm:v1:` + sha256 от канонического JSON всех параметров запроса.

    В ключ входит JSON-схема ответа: изменилась схема — старый ответ не подойдёт.
    Сам ключ — хэш, по нему нельзя восстановить текст документа.
    """
    payload = {
        "provider": provider,
        "model": model,
        "system": system,
        "user": user,
        "schema_name": schema.__name__,
        "schema_json": schema.model_json_schema(),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return CACHE_KEY_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RedisCache:
    """Кэш в Redis с коротким таймаутом.

    При ошибке Redis кэш отключается на REDIS_RETRY_AFTER_S секунд, чтобы не ждать
    таймаут на каждом вызове. Предупреждение в лог пишется один раз.
    """

    def __init__(self, url: str, timeout_s: float = REDIS_TIMEOUT_S) -> None:
        self._client = redis.Redis.from_url(
            url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s
        )
        self._disabled_until = 0.0
        self._warned = False

    def __repr__(self) -> str:
        # URL может содержать пароль Redis — в repr его не показываем.
        return f"RedisCache(disabled={self.disabled})"

    @property
    def disabled(self) -> bool:
        return time.monotonic() < self._disabled_until

    def get(self, key: str) -> str | None:
        if self.disabled:
            return None
        try:
            value = self._client.get(key)
        except (redis.RedisError, OSError) as exc:
            self._disable(exc)
            return None
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def set(self, key: str, value: str, ttl: int) -> None:
        if self.disabled:
            return
        try:
            self._client.set(key, value, ex=ttl)
        except (redis.RedisError, OSError) as exc:
            self._disable(exc)

    def _disable(self, exc: Exception) -> None:
        self._disabled_until = time.monotonic() + REDIS_RETRY_AFTER_S
        if self._warned:
            return
        self._warned = True
        logger.warning(
            "Кэш LLM отключён: Redis недоступен (%s). Работаем без кэша.", type(exc).__name__
        )


_default_cache: RedisCache | None = None


def get_default_cache(redis_url: str) -> RedisCache:
    """Общий на процесс RedisCache (создаётся лениво при первом реальном вызове LLM)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = RedisCache(redis_url)
    return _default_cache
