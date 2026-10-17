"""Журнал вызовов LLM — «Что видела нейросеть» (contracts.md, раздел 1)."""

from typing import Literal

from pydantic import BaseModel


class LlmCall(BaseModel):
    """Один вызов LLM при проверке документа. Ответ GET /api/reports/{report_id}/llm-log.

    masked_fragment — РОВНО тот текст, что ушёл в LLM: только замаскированный ([PERSON_1] …).
    Исходный текст в журнал не пишется (AGENTS.md, правило 5).
    """

    purpose: Literal["rule_condition", "trade_secret"]
    rule_id: str | None              # для purpose="rule_condition", иначе None
    question: str
    masked_fragment: str             # текст с плейсхолдерами, как его видела LLM
    verdict: Literal["yes", "no", "unknown"]
    quote: str | None                # цитата из ответа LLM (в координатах masked_fragment)
    cached: bool                     # ответ взят из кэша Redis
