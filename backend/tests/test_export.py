"""Экспорт настоящего отчёта на синтетическом текстовом документе."""

import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import ReportRow
from app.schemas import Report
from app.worker.tasks import run_pipeline
from tests import test_e2e

api_db = test_e2e.api_db


@pytest.mark.parametrize("query", ["", "?format=json"])
def test_export_pipeline_report(
    client: TestClient, api_db: tuple[Session, Mock], query: str,
) -> None:
    session, _ = api_db
    response = client.post(
        "/api/queries", json={"text": test_e2e.PUBLICATION_TEXT, "purpose": "publication"},
    )
    assert response.status_code == 202
    run_pipeline(response.json()["task_id"], session)
    task = client.get(f"/api/tasks/{response.json()['task_id']}").json()
    assert task["status"] == "done", task
    report_id = task["report_id"]
    response = client.get(f"/api/reports/{report_id}/export{query}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="report_{report_id}.json"'
    )
    report = Report.model_validate(response.json())
    assert report.report_id == report_id and report.audit.llm_provider == "mock"
    assert test_e2e.NAME not in response.text
    assert test_e2e.NAME not in response.headers["content-disposition"]
    row = session.get(ReportRow, report_id)
    assert row is not None
    assert response.text == json.dumps(row.data, ensure_ascii=False, indent=2)
    assert "\\u" not in response.text


def test_export_missing_report(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = client.get("/api/reports/nonexistent/export?format=json")
    assert response.status_code == 404
    assert response.json() == {"detail": "Отчёт не найден"}


def test_export_pdf(client: TestClient) -> None:
    response = client.get("/api/reports/nonexistent/export?format=pdf")
    assert response.status_code == 501
    assert response.json() == {"detail": "Будет реализовано в T-020"}
