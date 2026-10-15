"""Движок правил. Задача T-004, исполнитель — Данил."""

from app.schemas import Rule, RuleContext, Violation


def evaluate(rules: list[Rule], ctx: RuleContext) -> list[Violation]:
    """Проверяет включённые правила, применимые к ctx.purpose, и возвращает нарушения.

    Условия — дерево из contracts.md, раздел 2 (entity_present, marker_absent, all, any,
    not, llm ...). Условия типа llm работают только по ctx.masked_text.

    Контракт: docs/contracts.md, раздел 3. Задача T-004, исполнитель — Данил.
    """
    raise NotImplementedError("T-004 (Данил)")
