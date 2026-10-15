"""Сборка итогового отчёта. Задача T-016, исполнитель — Данил."""

from app.schemas import Report, RuleContext, Violation


def build_report(
    ctx: RuleContext, violations: list[Violation], processing_ms: int, llm_used: bool
) -> Report:
    """Собирает Report: статистика по типам сущностей, чек-лист ровно из 3 пунктов
    (approvals, classification, retention) и светофор по правилу из contracts.md:
    red, если есть red-нарушение; иначе yellow, если есть yellow; иначе green.

    Контракт: docs/contracts.md, раздел 3. Задача T-016, исполнитель — Данил.
    """
    raise NotImplementedError("T-016 (Данил)")
