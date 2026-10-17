"""Моки фронтенда и стартовые правила соответствуют контрактам."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from app.schemas import AuditInfo, LlmCall, Metrics, Report, Rule, TypeMetrics

MOCK_REPORT = Path("frontend/src/mocks/report.example.json")
MOCK_LLM_LOG = Path("frontend/src/mocks/llm-log.example.json")
MOCK_METRICS = Path("frontend/src/mocks/metrics.example.json")
RULES_YAML = Path("data/rules/rules.yaml")

# Открытые ПДн во фрагменте для LLM: ФИО (три слова с заглавной или «Фамилия И.О.»)
# и телефон (+7/8 и 10 цифр или хвост 123-45-67). Плейсхолдеры [PERSON_1] не совпадают.
FIO_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b"
    r"|\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\."
)
PHONE_RE = re.compile(
    r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}"
    r"|\d{3}-\d{2}-\d{2}"
)

AUDIT_MOCK: dict[str, Any] = {
    "document_sha256": "0" * 64,
    "ruleset_version": "3f9a1c7e2b40",
    "dictionaries_version": "8d2e6b1f0a93",
    "app_version": "0.1.0",
    "detectors": ["regex", "ner:natasha-1.6.0", "dict"],
    "llm_provider": "mock",
    "llm_model": None,
}


def _require(path: Path) -> Path:
    # В контейнере api есть только backend/, поэтому вне репозитория тест пропускается.
    if not path.exists():
        pytest.skip(f"{path} недоступен (запуск вне корня репозитория)")
    return path


def _load_json(path: Path) -> Any:
    return json.loads(_require(path).read_text(encoding="utf-8"))


def _llm_call(**overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "purpose": "rule_condition",
        "rule_id": "PD_IN_PUBLICATION",
        "question": "Есть ли ПДн?",
        "masked_fragment": "Контакт: [PERSON_1], тел. [PHONE_1].",
        "verdict": "yes",
        "quote": "[PERSON_1]",
        "cached": False,
    }
    call.update(overrides)
    return call


def _metrics(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "corpus": "test",
        "documents": 1,
        "created_at": datetime(2026, 10, 17, tzinfo=UTC).isoformat(),
        "by_type": [
            {"type": "PERSON", "precision": 1.0, "recall": 0.9, "f1": 0.947, "support": 10}
        ],
        "pd_recall": 0.9,
        "trade_secret_recall": 0.85,
        "matching": "overlap ≥ 50% и тот же type",
    }
    data.update(overrides)
    return data


# ---------- Моки фронтенда ----------


def test_mock_report_matches_contract(repo_root: Path) -> None:
    report = Report.model_validate(_load_json(repo_root / MOCK_REPORT))

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


def test_mock_report_audit_is_consistent(repo_root: Path) -> None:
    report = Report.model_validate(_load_json(repo_root / MOCK_REPORT))

    assert re.fullmatch(r"[0-9a-f]{64}", report.audit.document_sha256)
    assert re.fullmatch(r"[0-9a-f]{12}", report.audit.ruleset_version)
    assert re.fullmatch(r"[0-9a-f]{12}", report.audit.dictionaries_version)
    # Мок собран в режиме mock: модели нет, реальных вызовов нет, LLM не использовалась.
    assert report.audit.llm_provider == "mock"
    assert report.audit.llm_model is None
    assert report.audit.llm_calls == 0
    assert report.llm_used is False


def test_mock_report_value_masked_lengths(repo_root: Path) -> None:
    """value_masked всех категорий сохраняет длину (раздел 4.1)."""
    report = Report.model_validate(_load_json(repo_root / MOCK_REPORT))
    for entity in report.entities:
        assert len(entity.value_masked) == entity.end - entity.start


def test_report_requires_audit(repo_root: Path) -> None:
    data = _load_json(repo_root / MOCK_REPORT)
    data.pop("audit")
    with pytest.raises(ValidationError, match="audit"):
        Report.model_validate(data)


def test_report_redacted_available_defaults_to_false(repo_root: Path) -> None:
    data = _load_json(repo_root / MOCK_REPORT)
    data.pop("redacted_available", None)
    assert Report.model_validate(data).redacted_available is False


def test_mock_llm_log_matches_contract(repo_root: Path) -> None:
    calls = TypeAdapter(list[LlmCall]).validate_python(_load_json(repo_root / MOCK_LLM_LOG))

    assert 2 <= len(calls) <= 3
    verdicts = {call.verdict for call in calls}
    assert {"yes", "unknown"} <= verdicts
    assert any(call.verdict == "yes" and call.quote for call in calls)
    for call in calls:
        assert (call.rule_id is not None) == (call.purpose == "rule_condition")


def test_mock_llm_log_has_no_open_pd(repo_root: Path) -> None:
    """Во фрагментах, ушедших в LLM, только плейсхолдеры — ни ФИО, ни цифр телефона."""
    calls = TypeAdapter(list[LlmCall]).validate_python(_load_json(repo_root / MOCK_LLM_LOG))

    assert any("[PERSON_1]" in call.masked_fragment for call in calls)
    assert any("[PHONE_1]" in call.masked_fragment for call in calls)
    for call in calls:
        for text in (call.masked_fragment, call.quote or ""):
            assert not FIO_RE.search(text), text
            assert not PHONE_RE.search(text), text


def test_mock_metrics_matches_contract(repo_root: Path) -> None:
    metrics = Metrics.model_validate(_load_json(repo_root / MOCK_METRICS))

    assert metrics.corpus == "пример для разработки интерфейса"
    assert metrics.by_type
    for item in metrics.by_type:
        for value in (item.precision, item.recall, item.f1):
            assert 0 <= value <= 1
    for value in (metrics.pd_recall, metrics.trade_secret_recall):
        assert 0 <= value <= 1


def test_rules_yaml_matches_contract(repo_root: Path) -> None:
    path = _require(repo_root / RULES_YAML)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    rules = [Rule.model_validate(item) for item in raw]

    assert len(rules) >= 2
    assert len({rule.id for rule in rules}) == len(rules), "id правил должны быть уникальны"


# ---------- Модели контракта v2 без файлов (работают и в Docker) ----------


def test_audit_info_defaults_llm_calls_to_zero() -> None:
    assert AuditInfo.model_validate(AUDIT_MOCK).llm_calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("llm_calls", -1), ("llm_provider", "openai"), ("detectors", "regex")],
)
def test_audit_info_rejects_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuditInfo.model_validate({**AUDIT_MOCK, field: value})


def test_llm_call_valid() -> None:
    call = LlmCall.model_validate(_llm_call(cached=True, quote=None, verdict="unknown"))
    assert call.cached is True
    assert call.quote is None


@pytest.mark.parametrize(
    "overrides",
    [{"verdict": "maybe"}, {"purpose": "other"}, {"masked_fragment": None}],
)
def test_llm_call_rejects_invalid(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LlmCall.model_validate(_llm_call(**overrides))


def test_metrics_valid_bounds() -> None:
    metrics = Metrics.model_validate(_metrics(pd_recall=1.0, trade_secret_recall=0.0))
    assert metrics.pd_recall == 1.0
    assert metrics.trade_secret_recall == 0.0


@pytest.mark.parametrize(
    "overrides",
    [{"pd_recall": 1.2}, {"trade_secret_recall": -0.1}],
)
def test_metrics_rejects_out_of_range(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Metrics.model_validate(_metrics(**overrides))


@pytest.mark.parametrize(
    ("field", "value"),
    [("recall", 1.2), ("precision", -0.01), ("f1", 1.0001), ("support", -1)],
)
def test_type_metrics_rejects_invalid(field: str, value: float) -> None:
    item = {"type": "PERSON", "precision": 0.9, "recall": 0.9, "f1": 0.9, "support": 5}
    with pytest.raises(ValidationError):
        TypeMetrics.model_validate({**item, field: value})
