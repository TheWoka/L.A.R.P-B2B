"""Проверка деревьев условий по контракту. LLM подключается отдельно в T-013."""

import logging
from enum import Enum
from typing import TypeAlias

from app.schemas import (
    DocPurpose,
    Entity,
    EntityCategory,
    EntityType,
    Evidence,
    MarkerType,
    Rule,
    RuleContext,
    Severity,
    Violation,
)

logger = logging.getLogger(__name__)
ConditionResult: TypeAlias = tuple[bool, list[Evidence]]
_LEAF_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "entity_present": ({"type", "categories"}, {"types", "min_count"}),
    "marker_present": ({"type", "marker"}, set()),
    "marker_absent": ({"type", "marker"}, set()),
    "purpose_is": ({"type", "purposes"}, set()),
    "llm": ({"type", "question"}, set()),
}


def evaluate(rules: list[Rule], ctx: RuleContext) -> list[Violation]:
    """Возвращает нарушения применимых правил: сначала red, затем yellow."""
    violations: list[Violation] = []
    for rule in rules:
        if not rule.enabled or (rule.applies_to and ctx.purpose not in rule.applies_to):
            continue
        try:
            matched, evidence = _check_condition(rule.condition, ctx)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"Правило {rule.id}: {exc}") from exc
        if matched:
            violations.append(
                Violation(
                    rule_id=rule.id,
                    title=rule.title,
                    severity=rule.severity,
                    law_refs=rule.law_refs,
                    evidence=_prepare_evidence(evidence),
                    recommendation=rule.recommendation,
                    source="rule",
                )
            )
    return sorted(violations, key=lambda violation: violation.severity != Severity.red)


def _check_condition(
    condition: object,
    ctx: RuleContext,
    ancestors: frozenset[int] = frozenset(),
) -> ConditionResult:
    """Проверяет узел целиком, включая ветви, не влияющие на итоговый ответ."""
    if not isinstance(condition, dict) or not condition:
        raise ValueError("условие должно быть непустым словарём")
    if id(condition) in ancestors:
        raise ValueError("условие содержит циклическую ссылку")
    if "type" in condition:
        return _check_leaf(condition, ctx)
    operators = set(condition) & {"all", "any", "not"}
    if len(condition) != 1 or len(operators) != 1:
        raise ValueError("ожидается type либо единственный оператор all, any или not")
    operator = operators.pop()
    return _check_logical(operator, condition[operator], ctx, ancestors | {id(condition)})


def _check_logical(
    operator: str,
    value: object,
    ctx: RuleContext,
    ancestors: frozenset[int],
) -> ConditionResult:
    """Вычисляет логическую операцию и сохраняет доказательства истинных частей."""
    if operator == "not":
        matched, _ = _check_condition(value, ctx, ancestors)
        return not matched, []
    if not isinstance(value, list):
        raise ValueError(f"оператор {operator} должен содержать список условий")
    results = [_check_condition(child, ctx, ancestors) for child in value]
    flags = [matched for matched, _ in results]
    matched = all(flags) if operator == "all" else any(flags)
    if not matched:
        return False, []
    return True, [item for truth, items in results if truth for item in items]


def _check_leaf(condition: dict[str, object], ctx: RuleContext) -> ConditionResult:
    """Проверяет поля и вычисляет одно условие без вложенных операторов."""
    kind = condition.get("type")
    if not isinstance(kind, str) or kind not in _LEAF_FIELDS:
        raise ValueError("неизвестный тип условия")
    required, optional = _LEAF_FIELDS[kind]
    if not required <= condition.keys() or condition.keys() - required - optional:
        raise ValueError(f"неправильный набор полей условия {kind}")
    if kind == "entity_present":
        return _check_entities(condition, ctx)
    if kind in {"marker_present", "marker_absent"}:
        return _check_marker(condition, ctx)
    if kind == "purpose_is":
        purposes = _enum_values(condition, "purposes", DocPurpose)
        return ctx.purpose in purposes, []
    question = condition["question"]
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question должен быть непустой строкой")
    logger.debug("LLM-условие пропущено: реализация запланирована в T-013")
    return False, []


def _enum_values(
    condition: dict[str, object], field: str, enum_type: type[Enum]
) -> set[str]:
    """Проверяет список значений перечисления без неявных преобразований."""
    values = condition[field]
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"{field} должен содержать список строк")
    allowed = {member.value for member in enum_type}
    if any(value not in allowed for value in values):
        raise ValueError(f"{field} содержит неизвестное значение")
    return set(values)


def _check_entities(condition: dict[str, object], ctx: RuleContext) -> ConditionResult:
    """Отбирает сущности по пересечению фильтров категорий и типов."""
    categories = _enum_values(condition, "categories", EntityCategory)
    types = _enum_values(condition, "types", EntityType) if "types" in condition else None
    min_count = condition.get("min_count", 1)
    if type(min_count) is not int or min_count < 0:
        raise ValueError("min_count должен быть целым неотрицательным числом")
    entities = [
        entity for entity in ctx.entities
        if entity.category in categories and (types is None or entity.type in types)
    ]
    if len(entities) < min_count:
        return False, []
    return True, [_entity_evidence(entity) for entity in entities]


def _entity_evidence(entity: Entity) -> Evidence:
    """Берёт цитату только из value_masked, не читая исходный текст документа."""
    return Evidence(
        quote=entity.value_masked,
        start=entity.start,
        end=entity.end,
        page=entity.page,
        section=entity.section,
    )


def _check_marker(condition: dict[str, object], ctx: RuleContext) -> ConditionResult:
    """Маркер присутствует, только если есть запись с present=True."""
    marker_type = condition["marker"]
    if not isinstance(marker_type, str) or marker_type not in {item.value for item in MarkerType}:
        raise ValueError("marker должен содержать известный тип маркера")
    markers = [marker for marker in ctx.markers if marker.type == marker_type and marker.present]
    if condition["type"] == "marker_absent":
        return not markers, []
    return bool(markers), [marker.evidence for marker in markers if marker.evidence is not None]


def _prepare_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Убирает дубли по координатам и оставляет первые десять цитат по тексту."""
    unique: dict[tuple[int, int], Evidence] = {}
    for item in evidence:
        unique.setdefault((item.start, item.end), item)
    return sorted(unique.values(), key=lambda item: (item.start, item.end))[:10]
