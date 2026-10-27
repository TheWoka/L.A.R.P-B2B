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
from app.services.report.md_export import render_report_md
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


@pytest.mark.parametrize("format", ["json", "pdf", "md"])
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


def test_export_md(
    client: TestClient, pipeline_report: Report, caplog: pytest.LogCaptureFixture,
) -> None:
    report = pipeline_report
    response = client.get(f"/api/reports/{report.report_id}/export?format=md")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="report_{report.report_id}.md"'
    )
    text = response.content.decode("utf-8")
    assert text == render_report_md(report)
    assert text.startswith("# Гриф — отчёт проверки\n")
    for section in ("Чек-лист", "Нарушения", "Найденные данные", "След проверки"):
        assert f"\n## {section}\n" in text
    assert "Документ: Текстовый запрос" in text
    assert "Назначение: Для публикации (ЕИС, сайт)" in text
    assert "Итог: есть серьёзные нарушения" in text
    assert "| Категория | Статус | Комментарий |" in text
    assert "| Тип | Количество |" in text
    assert "### Серьёзное нарушение:" in text
    assert "Основание:" in text and "Рекомендация:" in text and "\n> " in text
    assert report.audit.document_sha256 in text
    assert report.audit.ruleset_version in text
    assert report.audit.dictionaries_version in text
    assert "Модель LLM: не использовалась" in text
    for value in test_e2e.PRIVATE_VALUES:
        assert value not in text and value not in caplog.text


def test_md_escapes_user_fields(pipeline_report: Report) -> None:
    report = pipeline_report.model_copy(deep=True)
    literal = "|`*_#<> &\\[]()!+-~"
    escaped = "".join(" " if char == " " else f"&#{ord(char)};" for char in literal)
    report.filename = f"{literal}\r\n# injected"
    report.checklist[0].comment = literal + "\nsecond row"
    violation = report.violations[0]
    violation.title = literal
    violation.recommendation = literal
    violation.law_refs[0].law = literal
    violation.law_refs[0].article = literal
    violation.evidence[0].quote = literal + "\n\n" + literal
    violation.evidence[0].section = literal
    violation.evidence[0].page = 7
    report.audit.detectors = [literal]
    report.audit.llm_model = literal
    report.llm_used = True
    report.stats = {literal: 1}
    text = render_report_md(report)
    assert text.count(escaped) >= 12
    assert f"Документ: {escaped} &#35; injected\n" in text
    assert f"> {escaped}\n> \n> {escaped}" in text
    assert "Страница 7" in text
    assert f"Модель LLM: {escaped}" in text
    rows = [line for line in text.splitlines() if line.startswith("|")]
    assert len(rows) == 8  # два заголовка, два разделителя, три пункта, одна статистика
    assert all(line.count("|") == 4 for line in rows[:5])
    assert all(line.count("|") == 3 for line in rows[5:])


@pytest.mark.parametrize("purpose,label", [
    (DocPurpose.internal, "Внутренний документ"),
    (DocPurpose.publication, "Для публикации (ЕИС, сайт)"),
    (DocPurpose.contract, "Договор с контрагентом"),
    (DocPurpose.international, "Международный контракт"),
])
@pytest.mark.parametrize("overall,verdict", [
    ("red", "есть серьёзные нарушения"),
    ("yellow", "есть замечания"),
    ("green", "нарушений не найдено"),
])
def test_md_empty_report(
    pipeline_report: Report, purpose: DocPurpose, label: str, overall: str, verdict: str,
) -> None:
    report = Report.model_validate({
        **pipeline_report.model_dump(), "purpose": purpose, "overall": overall,
        "filename": None, "violations": [], "entities": [], "stats": {},
    })
    text = render_report_md(report)
    assert f"Назначение: {label}" in text and f"Итог: {verdict}" in text
    assert "Документ: Текстовый запрос" in text
    assert "Нарушений не найдено" in text and "## След проверки" in text


def test_md_warning_without_evidence_or_entity_values(pipeline_report: Report) -> None:
    report = pipeline_report.model_copy(deep=True)
    report.violations = [report.violations[0]]
    report.violations[0].severity = "yellow"
    report.violations[0].evidence = []
    report.violations[0].law_refs = []
    report.filename = "синтетический отчёт.pdf"
    report.audit.llm_model = "unused-model"
    for entity in report.entities:
        entity.value_masked = "ENTITY_VALUE_MUST_NOT_BE_RENDERED"
    text = render_report_md(report)
    assert f"### Замечание: {report.violations[0].title}" in text
    assert "Документ: синтетический отчёт.pdf" in text
    assert "unused-model" not in text
    assert "ENTITY_VALUE_MUST_NOT_BE_RENDERED" not in text
    assert "\n> " not in text
    for kind, count in report.stats.items():
        assert f"| {kind.replace('_', '&#95;')} | {count} |" in text
