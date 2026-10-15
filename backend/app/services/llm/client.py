"""Клиент LLM. Задачи T-002, T-013, исполнитель — Данила.

В LLM уходит ТОЛЬКО замаскированный текст (AGENTS.md, правило 5).
"""

from pydantic import BaseModel


def complete_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    """Запрашивает у LLM ответ в JSON и валидирует его схемой schema.

    Провайдер из LLM_PROVIDER: mock — детерминированный ответ без сети и ключа;
    openai_compatible — запрос к LLM_BASE_URL. Кэш в Redis по sha256(запроса).

    Контракт: docs/contracts.md, раздел 3. Задачи T-002, T-013, исполнитель — Данила.
    """
    raise NotImplementedError("T-002, T-013 (Данила)")
