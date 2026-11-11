"""Контракт v3 (T-070): OCR, комплект и сводный отчёт, режимы нейросети.

Новые поля старых моделей необязательные: отчёты, сохранённые до v3, валидируются.
Моки фронтенда проверяются, если доступен корень репозитория (в контейнере api — пропуск);
те же формы продублированы встроенными данными, чтобы тесты работали и в Docker.
"""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.schemas import (
    AuditInfo,
    BatchCreate,
    BatchDocument,
    BatchRuleSummary,
    BatchSummary,
    ExtractedDocument,
    LlmMode,
    LlmModeInfo,
    LlmModesResponse,
    OcrInfo,
    OcrPage,
    QueryRequest,
    Report,
)

MOCK_REPORT = Path("frontend/src/mocks/report.example.json")
MOCK_BATCH = Path("frontend/src/mocks/batch.example.json")
MOCK_LLM_MODES = Path("frontend/src/mocks/llm-modes.example.json")

# Отчёт в форме контракта v2 — без ocr и audit.llm_mode (так лежат старые отчёты в БД).
LEGACY_REPORT: dict[str, Any] = {
    "report_id": "rep_00000001",
    "document_id": "doc_00000001",
    "filename": "Извещение.pdf",
    "purpose": "publication",
    "overall": "green",
    "created_at": "2026-10-20T10:00:00Z",
    "processing_ms": 1200,
    "stats": {},
    "entities": [],
    "checklist": [
        {"category": "approvals", "status": "ok", "comment": "Есть лист согласования"},
        {"category": "classification", "status": "not_required", "comment": "Гриф не нужен"},
        {"category": "retention", "status": "not_required", "comment": "ПДн нет"},
    ],
    "violations": [],
    "llm_used": False,
    "audit": {
        "document_sha256": "0" * 64,
        "ruleset_version": "3f9a1c7e2b40",
        "dictionaries_version": "8d2e6b1f0a93",
        "app_version": "0.1.0",
        "detectors": ["regex", "ner:natasha-1.6.0", "dict"],
        "llm_provider": "mock",
        "llm_model": None,
        "llm_calls": 0,
    },
    "redacted_available": False,
}

OCR_INFO: dict[str, Any] = {
    "engine": "tesseract-5.3.0 rus+eng",
    "pages": [{"page": 1, "confidence": 0.93}, {"page": 2, "confidence": 0.71}],
    "mean_confidence": 0.82,
    "low_confidence_pages": [2],
    "low_confidence_spans": [[120, 131], [204, 210]],
}


def _batch_document(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "document_id": "doc_1",
        "task_id": "tsk_1",
        "filename": "Документ_1.pdf",
        "purpose": "publication",
        "status": "done",
        "report_id": "rep_1",
        "overall": "red",
        "violations_red": 1,
        "violations_yellow": 0,
        "ocr": True,
        "error": None,
    }
    data.update(overrides)
    return data


BATCH_SUMMARY: dict[str, Any] = {
    "batch_id": "bat_1",
    "name": None,
    "purpose": "publication",
    "created_at": datetime(2026, 11, 11, tzinfo=UTC).isoformat(),
    "complete": False,
    "overall": "red",
    "documents_total": 2,
    "documents_done": 1,
    "documents_failed": 0,
    "violations_red": 1,
    "violations_yellow": 0,
    "rules": [
        {"rule_id": "PD_IN_PUBLICATION", "title": "ПДн в публикации", "severity": "red",
         "documents": 1},
    ],
    "documents": [
        _batch_document(),
        _batch_document(document_id="doc_2", task_id="tsk_2", status="processing",
                        report_id=None, overall=None, violations_red=0, ocr=False),
    ],
}

LLM_MODES: dict[str, Any] = {
    "default": "mock",
    "modes": [
        {"mode": "mock", "available": True},
        {"mode": "local", "available": False, "reason": "Ollama не запущена"},
        {"mode": "cloud", "available": True, "model": "gpt-4o-mini"},
    ],
}


def _load_mock(repo_root: Path, path: Path) -> Any:
    # В контейнере api есть только backend/, поэтому без корня репозитория тест пропускается.
    full = repo_root / path
    if not full.exists():
        pytest.skip(f"{path} недоступен (запуск вне корня репозитория)")
    return json.loads(full.read_text(encoding="utf-8"))


# ---------- Совместимость со старыми отчётами ----------
def test_legacy_report_validates_with_defaults() -> None:
    report = Report.model_validate(LEGACY_REPORT)
    assert report.ocr is None
    assert report.audit.llm_mode == "mock"


def test_audit_info_without_llm_mode_defaults_to_mock() -> None:
    assert AuditInfo.model_validate(LEGACY_REPORT["audit"]).llm_mode == "mock"


def test_legacy_report_roundtrip_adds_new_fields() -> None:
    dumped = Report.model_validate(LEGACY_REPORT).model_dump(mode="json")
    assert dumped["ocr"] is None
    assert dumped["audit"]["llm_mode"] == "mock"


def test_old_mock_report_validates(repo_root: Path) -> None:
    """report.example.json без полей v3 (как до T-070) валидируется."""
    data = _load_mock(repo_root, MOCK_REPORT)
    data.pop("ocr", None)
    data["audit"].pop("llm_mode", None)
    report = Report.model_validate(data)
    assert report.ocr is None and report.audit.llm_mode == "mock"


def test_mock_report_has_v3_fields(repo_root: Path) -> None:
    data = _load_mock(repo_root, MOCK_REPORT)
    assert data["ocr"] is None
    assert data["audit"]["llm_mode"] == "mock"
    Report.model_validate(data)


def test_report_accepts_ocr_and_llm_mode() -> None:
    data = deepcopy(LEGACY_REPORT)
    data["ocr"] = OCR_INFO
    data["audit"]["llm_mode"] = "local"
    report = Report.model_validate(data)
    assert report.ocr is not None and report.ocr.low_confidence_spans == [(120, 131), (204, 210)]
    assert report.audit.llm_mode == "local"


def test_audit_info_rejects_unknown_llm_mode() -> None:
    with pytest.raises(ValidationError):
        AuditInfo.model_validate({**LEGACY_REPORT["audit"], "llm_mode": "openai"})


# ---------- OCR ----------
def test_extracted_document_ocr_defaults_to_none() -> None:
    doc = ExtractedDocument(document_id="d", filename="a.pdf", text="x", segments=[])
    assert doc.ocr is None


def test_ocr_info_defaults() -> None:
    info = OcrInfo(engine="tesseract", pages=[], mean_confidence=0.9)
    assert info.low_confidence_pages == [] and info.low_confidence_spans == []


@pytest.mark.parametrize("confidence", [1.01, -0.1])
def test_ocr_page_rejects_confidence_out_of_range(confidence: float) -> None:
    with pytest.raises(ValidationError):
        OcrPage(page=1, confidence=confidence)


def test_ocr_info_rejects_mean_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        OcrInfo.model_validate({**OCR_INFO, "mean_confidence": 1.5})


def test_ocr_page_rejects_zero_page() -> None:
    with pytest.raises(ValidationError):
        OcrPage(page=0, confidence=0.9)


def test_ocr_confidence_bounds_inclusive() -> None:
    assert OcrPage(page=1, confidence=1).confidence == 1
    assert OcrPage(page=1, confidence=0).confidence == 0


# ---------- Комплект ----------
def test_batch_summary_valid() -> None:
    summary = BatchSummary.model_validate(BATCH_SUMMARY)
    assert summary.documents[1].overall is None


def test_batch_summary_matches_mock(repo_root: Path) -> None:
    summary = BatchSummary.model_validate(_load_mock(repo_root, MOCK_BATCH))
    assert summary.complete is False
    assert [d.status for d in summary.documents] == [
        "done", "done", "done", "processing", "failed",
    ]
    assert [d.overall for d in summary.documents[:3]] == ["red", "yellow", "green"]
    assert sum(d.ocr for d in summary.documents) == 1
    assert len(summary.rules) == 3


def test_batch_mock_is_consistent(repo_root: Path) -> None:
    """Счётчики и порядок правил в моке соответствуют правилам раздела 4.1."""
    summary = BatchSummary.model_validate(_load_mock(repo_root, MOCK_BATCH))
    docs = summary.documents
    assert summary.documents_total == len(docs)
    assert summary.documents_done == sum(d.status == "done" for d in docs)
    assert summary.documents_failed == sum(d.status == "failed" for d in docs)
    assert summary.violations_red == sum(d.violations_red for d in docs)
    assert summary.violations_yellow == sum(d.violations_yellow for d in docs)
    order = {"red": 0, "yellow": 1}
    keys = [(order[r.severity], -r.documents, r.rule_id) for r in summary.rules]
    assert keys == sorted(keys)


def test_batch_document_defaults() -> None:
    doc = BatchDocument.model_validate(
        {"document_id": "d", "task_id": "t", "filename": None, "purpose": "internal",
         "status": "queued"},
    )
    assert (doc.report_id, doc.overall, doc.violations_red, doc.ocr) == (None, None, 0, False)


@pytest.mark.parametrize(
    "overrides",
    [{"status": "cancelled"}, {"violations_red": -1}, {"overall": "blue"}],
)
def test_batch_document_rejects_invalid(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BatchDocument.model_validate(_batch_document(**overrides))


def test_batch_rule_summary_requires_at_least_one_document() -> None:
    with pytest.raises(ValidationError):
        BatchRuleSummary(rule_id="R", title="t", severity="red", documents=0)


def test_batch_create_name_limit() -> None:
    assert BatchCreate(purpose="internal").name is None  # type: ignore[arg-type]
    BatchCreate(name="а" * 120, purpose="internal")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BatchCreate(name="а" * 121, purpose="internal")  # type: ignore[arg-type]


# ---------- Режимы нейросети ----------
def test_llm_mode_values() -> None:
    assert get_args(LlmMode) == ("mock", "local", "cloud")


def test_llm_modes_response_valid() -> None:
    response = LlmModesResponse.model_validate(LLM_MODES)
    assert [m.mode for m in response.modes] == ["mock", "local", "cloud"]
    assert response.modes[1].model is None


def test_llm_modes_match_mock(repo_root: Path) -> None:
    response = LlmModesResponse.model_validate(_load_mock(repo_root, MOCK_LLM_MODES))
    assert response.default == "mock"
    modes = {m.mode: m for m in response.modes}
    assert list(modes) == ["mock", "local", "cloud"]
    assert modes["local"].available is False and modes["local"].reason == "Ollama не запущена"
    assert modes["cloud"].available is True and modes["cloud"].model == "gpt-4o-mini"


def test_llm_modes_mock_has_no_api_key(repo_root: Path) -> None:
    """Ключ API никогда не возвращается: в ответе нет полей с ключом."""
    raw = json.dumps(_load_mock(repo_root, MOCK_LLM_MODES)).lower()
    assert "api_key" not in raw and "sk-" not in raw


def test_llm_mode_info_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        LlmModeInfo(mode="gpu", available=True)  # type: ignore[arg-type]


def test_query_request_llm_mode_optional() -> None:
    assert QueryRequest(text="abc", purpose="internal").llm_mode is None  # type: ignore[arg-type]
    request = QueryRequest(text="abc", purpose="internal", llm_mode="cloud")  # type: ignore[arg-type]
    assert request.llm_mode == "cloud"
    with pytest.raises(ValidationError):
        QueryRequest(text="abc", purpose="internal", llm_mode="gpu")  # type: ignore[arg-type]
