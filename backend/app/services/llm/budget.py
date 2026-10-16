"""Лимит вызовов LLM на документ. Внутренний модуль T-002, не контракт.

В сигнатуре `complete_json` нет `document_id`, поэтому счётчик живёт в
`contextvars`: обработчик документа открывает `with llm_call_budget():`, и все
вызовы `complete_json` внутри (в том же потоке или asyncio-задаче) считаются
в его лимит. Разные потоки/документы не мешают друг другу.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.core.config import get_settings
from app.services.llm.providers import LLMBudgetExceeded

logger = logging.getLogger(__name__)


@dataclass
class CallBudget:
    """Состояние лимита: сколько реальных запросов к LLM уже сделано и сколько можно."""

    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)


_current_budget: ContextVar[CallBudget | None] = ContextVar("llm_call_budget", default=None)


@contextmanager
def llm_call_budget(limit: int | None = None) -> Iterator[CallBudget]:
    """Ограничивает число реальных вызовов LLM внутри блока `with`.

    limit=None → LLM_MAX_CALLS_PER_DOC из настроек. Вложенный блок получает
    собственный счётчик, после выхода из него снова действует внешний.
    Возвращает CallBudget: по нему удобно залогировать, сколько вызовов ушло.
    """
    effective = get_settings().llm_max_calls_per_doc if limit is None else limit
    if effective < 0:
        raise ValueError("Лимит вызовов LLM не может быть отрицательным")
    budget = CallBudget(limit=effective)
    token = _current_budget.set(budget)
    try:
        yield budget
    finally:
        _current_budget.reset(token)


def charge_llm_call() -> None:
    """Списывает один реальный вызов LLM из текущего лимита.

    Вызывается ПЕРЕД запросом к провайдеру. Попадания в кэш и mock не списываются.
    Вне `llm_call_budget` лимита нет — только запись в лог на уровне debug.
    """
    budget = _current_budget.get()
    if budget is None:
        logger.debug("Вызов LLM вне llm_call_budget: лимит на документ не применяется")
        return
    if budget.used >= budget.limit:
        raise LLMBudgetExceeded(f"Исчерпан лимит вызовов LLM на документ ({budget.limit})")
    budget.used += 1
