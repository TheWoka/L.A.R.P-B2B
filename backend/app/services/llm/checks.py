"""LLM-проверки условий правил типа `llm`. Задачи T-002, T-013, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпты лежат в llm/prompts/.
"""

from app.schemas import Evidence


def check_llm_condition(question: str, masked_text: str) -> Evidence | None:
    """Задаёт LLM вопрос из условия правила по ЗАМАСКИРОВАННОМУ тексту.

    Возвращает Evidence, если LLM ответила «да» и её цитата дословно найдена
    в masked_text; иначе None (contracts.md, раздел 2, тип `llm`).
    Задачи T-002, T-013, исполнитель — Данила.
    """
    raise NotImplementedError("T-002, T-013 (Данила)")
