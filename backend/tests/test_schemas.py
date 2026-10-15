"""Мок-отчёт фронтенда и стартовые правила соответствуют контрактам."""

import json
from pathlib import Path

import pytest
import yaml

from app.schemas import Report, Rule

MOCK_REPORT = Path("frontend/src/mocks/report.example.json")
RULES_YAML = Path("data/rules/rules.yaml")


def _require(path: Path) -> Path:
    # В контейнере api есть только backend/, поэтому вне репозитория тест пропускается.
    if not path.exists():
        pytest.skip(f"{path} недоступен (запуск вне корня репозитория)")
    return path


def test_mock_report_matches_contract(repo_root: Path) -> None:
    path = _require(repo_root / MOCK_REPORT)
    report = Report.model_validate(json.loads(path.read_text(encoding="utf-8")))

    assert len(report.checklist) == 3
    assert {item.category for item in report.checklist} == {
        "approvals",
        "classification",
        "retention",
    }
    # Светофор по правилу из contracts.md
    severities = {v.severity.value for v in report.violations}
    expected = "red" if "red" in severities else "yellow" if "yellow" in severities else "green"
    assert report.overall == expected
    # Статистика согласована со списком сущностей
    counts: dict[str, int] = {}
    for entity in report.entities:
        counts[entity.type.value] = counts.get(entity.type.value, 0) + 1
    assert report.stats == counts


def test_rules_yaml_matches_contract(repo_root: Path) -> None:
    path = _require(repo_root / RULES_YAML)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    rules = [Rule.model_validate(item) for item in raw]

    assert len(rules) >= 2
    assert len({rule.id for rule in rules}) == len(rules), "id правил должны быть уникальны"
