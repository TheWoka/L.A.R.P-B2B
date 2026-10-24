"""Экспорт настоящего отчёта на синтетическом текстовом документе."""

import json
from time import perf_counter
from unittest.mock import Mock

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import ReportRow
from app.schemas import DocPurpose, Report
from app.services.report.pdf_export import render_report_pdf
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


@pytest.mark.parametrize("format", ["json", "pdf"])
def test_export_missing_report(
    client: TestClient, api_db: tuple[Session, Mock], format: str,
) -> None:
    response = client.get(f"/api/reports/nonexistent/export?format={format}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Отчёт не найден"}


@pytest.fixture
def pipeline_report(client: TestClient, api_db: tuple[Session, Mock]) -> Report:
    session, _ = api_db
    response = client.post(
        "/api/queries", json={"text": test_e2e.PUBLICATION_TEXT, "purpose": "publication"},
    )
    assert response.status_code == 202
    report, _ = test_e2e.finish_audit(client, session, response.json()["task_id"])
    return report


def test_export_pdf(
    client: TestClient, pipeline_report: Report, caplog: pytest.LogCaptureFixture,
) -> None:
    report = pipeline_report
    response = client.get(f"/api/reports/{report.report_id}/export?format=pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="report_{report.report_id}.pdf"'
    )
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        assert len(document) >= 1
        text = "".join(page.get_text() for page in document)
        assert "Гриф" in text and "Текстовый запрос" in text
        violation = report.violations[0]
        assert violation.title in text
        for ref in violation.law_refs:
            assert ref.law in text and ref.article in text
        for evidence in violation.evidence:
            assert evidence.quote in text
        assert report.audit.document_sha256 in text
        assert report.audit.ruleset_version in text
        assert "не использовалась" in text
        for number, page in enumerate(document, 1):
            assert f"Гриф · стр. {number}" in page.get_text()
            assert abs(page.rect.width - pymupdf.paper_rect("a4").width) < 1
            for value in test_e2e.PRIVATE_VALUES:
                assert value not in page.get_text()
                assert value not in caplog.text


def test_pdf_escapes_html(pipeline_report: Report) -> None:
    report = pipeline_report.model_copy(deep=True)
    literal = "<script>&"
    report.filename = literal
    report.checklist[0].comment = literal
    violation = report.violations[0]
    violation.title = literal
    violation.recommendation = literal
    violation.law_refs[0].law = literal
    violation.law_refs[0].article = literal
    violation.evidence[0].quote = literal
    violation.evidence[0].page = 7
    violation.evidence[0].section = literal
    report.audit.detectors = [literal]
    report.stats = {literal: 1}
    with pymupdf.open(stream=render_report_pdf(report), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert text.count(literal) >= 10
    assert "Страница 7" in text


@pytest.mark.parametrize("purpose,label", [
    (DocPurpose.internal, "Внутренний документ"),
    (DocPurpose.publication, "Для публикации (ЕИС, сайт)"),
    (DocPurpose.contract, "Договор с контрагентом"),
    (DocPurpose.international, "Международный контракт"),
])
@pytest.mark.parametrize("overall,verdict", [
    ("red", "Есть серьёзные нарушения"),
    ("yellow", "Есть замечания"),
    ("green", "Нарушений не найдено"),
])
def test_pdf_empty_report(
    pipeline_report: Report, purpose: DocPurpose, label: str, overall: str, verdict: str,
) -> None:
    report = Report.model_validate({
        **pipeline_report.model_dump(), "purpose": purpose, "overall": overall,
        "violations": [], "entities": [], "stats": {},
    })
    with pymupdf.open(stream=render_report_pdf(report), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert label in text and verdict in text
    assert "Нарушений не найдено" in text and "След проверки" in text


def test_pdf_large_report(pipeline_report: Report) -> None:
    report = pipeline_report.model_copy(deep=True)
    report.violations = [report.violations[0].model_copy(deep=True) for _ in range(30)]
    report.entities = [report.entities[0].model_copy(deep=True) for _ in range(500)]
    for entity in report.entities:
        entity.value_masked = "ENTITY_VALUE_MUST_NOT_BE_RENDERED"
    report.stats = {report.entities[0].type.value: 500}
    for index, violation in enumerate(report.violations):
        violation.title = f"Нарушение номер {index + 1}"
    started = perf_counter()
    pdf = render_report_pdf(report)
    elapsed = perf_counter() - started
    assert elapsed < 5, f"Генерация заняла {elapsed:.2f} с"
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert len(document) > 1
        text = "".join(page.get_text() for page in document)
        for violation in report.violations:
            assert violation.title in text
        assert "500" in text and "След проверки" in text
        assert "ENTITY_VALUE_MUST_NOT_BE_RENDERED" not in text
        for number, page in enumerate(document, 1):
            assert f"Гриф · стр. {number}" in page.get_text()
            for value in test_e2e.PRIVATE_VALUES:
                assert value not in page.get_text()


def test_pdf_warning_without_evidence_and_model(pipeline_report: Report) -> None:
    report = pipeline_report.model_copy(deep=True)
    report.violations = [report.violations[0]]
    report.violations[0].severity = "yellow"
    report.violations[0].evidence = []
    report.violations[0].law_refs = []
    report.audit.llm_model = "synthetic<model>&"
    report.llm_used = True
    with pymupdf.open(stream=render_report_pdf(report), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
    assert f"Замечание: {report.violations[0].title}" in text
    assert "synthetic<model>&" in text
    assert report.violations[0].recommendation in text.replace("\n", " ")
