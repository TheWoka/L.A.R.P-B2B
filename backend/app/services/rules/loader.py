"""Загрузка и проверка правил из YAML без раскрытия входных данных в ошибках."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.schemas import Rule
from app.services.rules.engine import validate_condition


def load_rules(path: Path) -> list[Rule]:
    """Читает список правил, проверяет схему и уникальность идентификаторов."""
    data = _load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("Файл правил должен содержать список")

    rules: list[Rule] = []
    seen_ids: set[str] = set()
    for position, item in enumerate(data, start=1):
        rule_id = item.get("id") if isinstance(item, dict) else None
        label = rule_id if isinstance(rule_id, str) and rule_id else f"№{position}"
        try:
            rule = Rule.model_validate(item)
        except ValidationError as error:
            raise ValueError(f"Правило {label}: {_validation_details(error)}") from None
        try:
            validate_condition(rule.condition)
        except ValueError as error:
            raise ValueError(f"Правило {rule.id}: {error}") from None
        if rule.id in seen_ids:
            raise ValueError(f"Повторяющийся id правила: {rule.id}")
        seen_ids.add(rule.id)
        rules.append(rule)
    return rules


def _load_yaml(path: Path) -> object:
    """Разбирает YAML; ошибка сообщает координаты, но не содержимое файла."""
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = "" if mark is None else f": строка {mark.line + 1}, столбец {mark.column + 1}"
        raise ValueError(f"Некорректный YAML{location}") from None


def _validation_details(error: ValidationError) -> str:
    """Возвращает только имена полей и причину ошибки Pydantic."""
    details: list[str] = []
    for issue in error.errors(include_input=False, include_url=False):
        field = ".".join(str(part) for part in issue["loc"]) or "правило"
        details.append(f"{field}: {issue['msg']}")
    return "; ".join(details)
