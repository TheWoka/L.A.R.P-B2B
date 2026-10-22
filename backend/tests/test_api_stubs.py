"""Все эндпоинты из contracts.md, раздел 4, есть в API (видны в Swagger на /docs)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.version import APP_VERSION
from app.db.models import Base, DocumentRow, ReportRow
from app.db.session import get_db
from app.main import app
from app.schemas import DocPurpose, Report, ReportText
from app.services import pipeline
from tests.test_pipeline import NAME, PHONE, make_doc, make_report


@pytest.fixture
def report_db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()

CONTRACT_ENDPOINTS = {
    ("post", "/api/documents"),
    ("post", "/api/queries"),
    ("get", "/api/tasks/{task_id}"),
    ("get", "/api/reports/{report_id}"),
    ("get", "/api/reports/{report_id}/text"),
    ("get", "/api/reports/{report_id}/export"),
    ("get", "/api/rules"),
    ("post", "/api/rules"),
    ("put", "/api/rules/{rule_id}"),
    ("delete", "/api/rules/{rule_id}"),
    ("get", "/api/health"),
    # контракт v2 (17.10)
    ("get", "/api/reports/{report_id}/llm-log"),
    ("get", "/api/reports/{report_id}/redacted"),
    ("get", "/api/metrics"),
}


def test_openapi_has_all_contract_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    actual = {(method, path) for path, methods in paths.items() for method in methods}
    assert CONTRACT_ENDPOINTS <= actual


@pytest.mark.parametrize("suffix", ["", "/text"])
def test_missing_report_returns_404(
    client: TestClient, report_db: Session, suffix: str,
) -> None:
    response = client.get(f"/api/reports/some-id{suffix}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Текст отчёта не найден" if suffix else "Отчёт не найден"}


def test_saved_report_and_text(
    client: TestClient, report_db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)
    result = pipeline.run_audit(make_doc(), DocPurpose.publication, b"synthetic")
    report_db.add(DocumentRow(id=result.report.document_id, filename="synthetic.docx",
                              purpose="publication"))
    report_db.flush()
    report_db.add(ReportRow(
        id=result.report.report_id, document_id=result.report.document_id,
        data=result.report.model_dump(mode="json"), text=result.report_text.model_dump(mode="json"),
    ))
    report_db.commit()
    response = client.get(f"/api/reports/{result.report.report_id}")
    assert response.status_code == 200
    assert Report.model_validate(response.json()) == result.report
    response = client.get(f"/api/reports/{result.report.report_id}/text")
    assert response.status_code == 200
    assert ReportText.model_validate(response.json()) == result.report_text
    assert NAME not in response.text and PHONE not in response.text


def test_report_without_text_returns_404(client: TestClient, report_db: Session) -> None:
    report_db.add(DocumentRow(id="synthetic", purpose="internal"))
    report_db.flush()
    report_db.add(ReportRow(id="old-report", document_id="synthetic", data={}))
    report_db.commit()
    assert client.get("/api/reports/old-report/text").status_code == 404


def test_api_version(client: TestClient) -> None:
    assert client.get("/openapi.json").json()["info"]["version"] == APP_VERSION


def test_export_json_returns_404(client: TestClient, report_db: Session) -> None:
    response = client.get("/api/reports/some-id/export?format=json")
    assert response.status_code == 404
    assert response.json() == {"detail": "Отчёт не найден"}


def test_export_pdf_still_returns_501(client: TestClient) -> None:
    response = client.get("/api/reports/some-id/export?format=pdf")
    assert response.status_code == 501
    assert response.json() == {"detail": "Будет реализовано в T-020"}


@pytest.mark.parametrize(
    ("path", "task"),
    [
        ("/api/reports/rep_1/redacted", "T-026"),
        ("/api/metrics", "T-028"),
    ],
)
def test_contract_v2_stubs_return_501_with_task_number(
    client: TestClient, path: str, task: str
) -> None:
    response = client.get(path)
    assert response.status_code == 501
    assert task in response.json()["detail"]


def test_llm_log_response_schema_is_list_of_llm_call(client: TestClient) -> None:
    """В Swagger у журнала LLM — массив LlmCall, у метрик — Metrics."""
    paths = client.get("/openapi.json").json()["paths"]
    llm_log = paths["/api/reports/{report_id}/llm-log"]["get"]["responses"]["200"]
    schema = llm_log["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/LlmCall")
    metrics = paths["/api/metrics"]["get"]["responses"]["200"]
    assert metrics["content"]["application/json"]["schema"]["$ref"].endswith("/Metrics")


def test_query_validation_rejects_empty_text(client: TestClient) -> None:
    """Граничный случай: пустой текст отсекается валидацией до заглушки."""
    response = client.post("/api/queries", json={"text": "", "purpose": "internal"})
    assert response.status_code == 422
