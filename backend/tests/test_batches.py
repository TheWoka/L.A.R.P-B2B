"""Комплекты: чистая сводка, SQLite API, безопасные выгрузки и срок хранения."""

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock
from uuid import UUID

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import session as db_session
from app.db.models import Base, BatchRow, DocumentRow, ReportRow, TaskRow
from app.schemas import DocPurpose, Evidence, OcrInfo, Report, Violation
from app.services.report.batch_summary import BatchInfo, BatchItem, build_batch_summary
from app.services.report.md_export import render_batch_md
from app.services.report.pdf_export import OCR_WARNING, render_batch_pdf
from app.services.retention import purge_expired
from tests import test_api_documents, test_contract_v3, test_retention
from tests.test_e2e import PRIVATE_VALUES

api_db = test_api_documents.api_db
retention_db = test_retention.retention_db
NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
BATCH = BatchInfo("batch-test", "Закупка оборудования", DocPurpose.publication, NOW)


@pytest.fixture
def demo_report(repo_root: Path) -> Report:
    path = repo_root / "frontend/src/mocks/report.example.json"
    report = (Report.model_validate_json(path.read_text(encoding="utf-8")) if path.exists()
              else Report.model_validate(test_contract_v3.LEGACY_REPORT))
    # Синтетические значения не должны попасть в сводку даже из полей старого отчёта.
    report.violations = [make_violation("PD", "red")]
    private_text = "; ".join(PRIVATE_VALUES)
    report.violations[0].recommendation = private_text
    report.violations[0].evidence = [Evidence(quote=private_text, start=0, end=len(private_text))]
    return report


def make_violation(rule_id: str, severity: str) -> Violation:
    return Violation.model_validate({
        "rule_id": rule_id, "title": f"Правило {rule_id}", "severity": severity,
        "law_refs": [], "evidence": [], "recommendation": "Проверьте документ", "source": "rule",
    })


def make_item(
    identifier: str, filename: str | None, report: Report | None, status: str = "done",
) -> BatchItem:
    error = "Не удалось прочитать документ" if status == "failed" else None
    return BatchItem(identifier, f"task-{identifier}", filename, DocPurpose.publication,
                     status, report, error)


def test_summary_mixed_statuses(demo_report: Report) -> None:
    red = demo_report.model_copy(deep=True)
    red.overall = "red"
    red.violations = [make_violation("Z", "red"), make_violation("Z", "red"),
                      make_violation("A", "red"), make_violation("Y", "yellow")]
    yellow = demo_report.model_copy(update={
        "overall": "yellow", "violations": [make_violation("Y", "yellow")],
    })
    green = demo_report.model_copy(update={"overall": "green", "violations": []})
    red.ocr = OcrInfo.model_validate(test_contract_v3.OCR_INFO)
    items = [make_item("failed", "а.pdf", None, "failed"),
             make_item("queued", "б.pdf", None, "queued"),
             make_item("green", "я.pdf", green), make_item("yellow", "я.pdf", yellow),
             make_item("processing", "а.pdf", None, "processing"),
             make_item("red-z", "я.pdf", red), make_item("red-a", "а.pdf", red)]
    summary = build_batch_summary(BATCH, items)
    assert [d.document_id for d in summary.documents] == [
        "red-a", "red-z", "yellow", "green", "processing", "queued", "failed",
    ]
    assert summary.overall == "red" and not summary.complete
    assert (summary.documents_total, summary.documents_done, summary.documents_failed) == (7, 4, 1)
    assert (summary.violations_red, summary.violations_yellow) == (6, 3)
    assert [(r.rule_id, r.documents, r.severity) for r in summary.rules] == [
        ("A", 2, "red"), ("Z", 2, "red"), ("Y", 3, "yellow"),
    ]
    assert summary.documents[0].ocr and not summary.documents[2].ocr
    assert red.violations[0].rule_id == "Z"  # Входные данные не меняются.
    assert build_batch_summary(BATCH, items[:1] + items[2:4]).complete


@pytest.mark.parametrize("overall", ["red", "yellow", "green"])
def test_summary_ready_overall(demo_report: Report, overall: str) -> None:
    report = demo_report.model_copy(update={"overall": overall})
    assert build_batch_summary(BATCH, [make_item("ready", "а.pdf", report)]).overall == overall


def test_summary_empty_and_failed() -> None:
    empty = build_batch_summary(BATCH, [])
    assert empty.overall is None and empty.complete and empty.documents_total == 0
    assert empty.documents_done == empty.documents_failed == 0
    assert empty.violations_red == empty.violations_yellow == 0
    assert empty.rules == empty.documents == []
    failed = build_batch_summary(BATCH, [make_item("failed", "а.pdf", None, "failed")])
    assert failed.overall is None and failed.complete and failed.documents_failed == 1


def test_rule_frequency_severity_and_stale_report(demo_report: Report) -> None:
    first = demo_report.model_copy(update={"violations": [
        make_violation("Z", "red"), make_violation("A", "yellow"),
    ]})
    second = demo_report.model_copy(update={"violations": [
        make_violation("A", "red"), make_violation("B", "red"),
    ]})
    items = [make_item("one", "а.pdf", first), make_item("two", "б.pdf", second),
             make_item("pending", "в.pdf", second, "processing")]
    summary = build_batch_summary(BATCH, items)
    assert [(r.rule_id, r.severity, r.documents) for r in summary.rules] == [
        ("A", "red", 2), ("B", "red", 1), ("Z", "red", 1),
    ]
    assert summary.violations_red == 3 and summary.violations_yellow == 1
    assert summary.documents[-1].report_id is None and summary.documents[-1].overall is None


@pytest.mark.parametrize("filename,expected", [
    ("Иванов_Иван_Иванович.pdf", "документ.pdf"), ("Извещение.docx", "Извещение.docx"),
    (None, None), (".pdf", "документ"),
])
def test_safe_filename(filename: str | None, expected: str | None) -> None:
    summary = build_batch_summary(BATCH, [make_item("queued", filename, None, "queued")])
    assert summary.documents[0].filename == expected


def create_batch(client: TestClient) -> str:
    response = client.post("/api/batches", json={"name": BATCH.name, "purpose": "publication"})
    assert response.status_code == 201
    assert set(response.json()) == {"batch_id"}
    batch_id = response.json()["batch_id"]
    assert UUID(batch_id).version == 4 and len(batch_id) == 32
    return batch_id


def upload(client: TestClient, batch_id: str) -> dict[str, str]:
    response = client.post(
        "/api/documents", files={"file": ("Извещение.pdf", b"%PDF-synthetic")},
        data={"purpose": "publication", "batch_id": batch_id, "llm_mode": "mock"},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_create_upload_get(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    session, delay = api_db
    batch_id = create_batch(client)
    empty = client.get(f"/api/batches/{batch_id}").json()
    assert empty["overall"] is None and empty["complete"] and empty["documents_total"] == 0
    uploaded = upload(client, batch_id)
    summary = client.get(f"/api/batches/{batch_id}").json()
    assert summary["batch_id"] == batch_id and summary["name"] == BATCH.name
    assert summary["purpose"] == "publication" and summary["created_at"]
    assert summary["documents_total"] == 1 and not summary["complete"]
    assert summary["documents"][0]["task_id"] == uploaded["task_id"]
    assert summary["documents"][0]["status"] == "queued"
    assert session.get(DocumentRow, uploaded["document_id"]).batch_id == batch_id
    delay.assert_called_once_with(uploaded["task_id"])


def test_create_without_name_and_validation(
    client: TestClient, api_db: tuple[Session, Mock],
) -> None:
    response = client.post("/api/batches", json={"purpose": "internal"})
    assert response.status_code == 201
    assert client.get(f"/api/batches/{response.json()['batch_id']}").json()["name"] is None
    for body in ({}, {"purpose": "wrong"}, {"purpose": "internal", "name": "x" * 121}):
        response = client.post("/api/batches", json=body)
        assert response.status_code == 422 and isinstance(response.json()["detail"], str)


def test_get_latest_task_and_isolation(
    client: TestClient, api_db: tuple[Session, Mock], demo_report: Report,
) -> None:
    session, _ = api_db
    batch_id = create_batch(client)
    uploaded = upload(client, batch_id)
    upload(client, create_batch(client))
    previous = session.get(TaskRow, uploaded["task_id"])
    previous.created_at = NOW - timedelta(days=1)
    session.add(ReportRow(id=demo_report.report_id, document_id=uploaded["document_id"],
                          data=demo_report.model_dump(mode="json")))
    session.add(TaskRow(id="latest", document_id=uploaded["document_id"], status="done",
                        report_id=demo_report.report_id, created_at=NOW))
    session.commit()
    summary = client.get(f"/api/batches/{batch_id}").json()
    assert summary["documents_total"] == summary["documents_done"] == 1
    assert summary["complete"] and summary["documents"][0]["task_id"] == "latest"
    assert summary["violations_red"] == 1


@pytest.mark.parametrize("suffix", ["", "/export", "/export?format=md", "/export?format=pdf"])
def test_missing_batch(client: TestClient, api_db: tuple[Session, Mock], suffix: str) -> None:
    response = client.get(f"/api/batches/missing{suffix}")
    assert response.status_code == 404 and response.json() == {"detail": "Комплект не найден"}


def test_upload_missing_batch(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
) -> None:
    response = client.post(
        "/api/documents", files={"file": ("а.pdf", b"%PDF-synthetic")},
        data={"purpose": "internal", "batch_id": "missing"},
    )
    assert response.status_code == 404 and response.json()["detail"] == "Комплект не найден"
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(DocumentRow)) is None
    api_db[1].assert_not_called()


@pytest.mark.parametrize("limit", [100, 2])
def test_document_limit(
    client: TestClient, api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, limit: int,
) -> None:
    if limit == 100:
        assert get_settings().batch_max_documents == 100
    monkeypatch.setattr(get_settings(), "batch_max_documents", limit)
    batch_id = create_batch(client)
    for _ in range(limit):
        upload(client, batch_id)
    response = client.post(
        "/api/documents", files={"file": ("а.pdf", b"%PDF-synthetic")},
        data={"purpose": "internal", "batch_id": batch_id},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == f"В комплекте не больше {limit} документов"
    assert client.get(f"/api/batches/{batch_id}").json()["documents_total"] == limit
    assert len(list(tmp_path.iterdir())) == api_db[1].call_count == limit
    upload(client, create_batch(client))  # Лимит относится только к выбранному комплекту.


@pytest.mark.parametrize("format", ["json", "md", "pdf"])
def test_export_privacy_and_current_status(
    client: TestClient, api_db: tuple[Session, Mock], demo_report: Report, format: str,
) -> None:
    session, _ = api_db
    batch_id = create_batch(client)
    uploaded = upload(client, batch_id)
    upload(client, batch_id)  # Незавершённая проверка.
    document = session.get(DocumentRow, uploaded["document_id"])
    document.filename = "Иванов_Иван_Иванович.pdf"
    demo_report.ocr = OcrInfo.model_validate(test_contract_v3.OCR_INFO)
    session.add(ReportRow(id=demo_report.report_id, document_id=document.id,
                          data=demo_report.model_dump(mode="json")))
    task = session.get(TaskRow, uploaded["task_id"])
    task.status, task.report_id = "done", demo_report.report_id
    session.commit()
    response = client.get(f"/api/batches/{batch_id}/export?format={format}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="batch_{batch_id}.{format}"'
    )
    if format == "pdf":
        assert response.headers["content-type"] == "application/pdf"
        with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
            assert len(pdf) >= 1
            text = " ".join(" ".join(page.get_text().split()) for page in pdf)
            assert "Гриф · стр. 1" in text
    else:
        text = html.unescape(response.text)
    for value in PRIVATE_VALUES:
        assert value not in text
    assert "Иванов" not in text and "документ.pdf" in text
    if format == "json":
        data = response.json()
        assert data["overall"] == demo_report.overall and data["documents_done"] == 1
        assert data["documents"][0]["report_id"] == demo_report.report_id
        assert data["rules"][0]["rule_id"] == "PD" and not data["complete"]
    else:
        assert "Сводный отчёт по комплекту" in text and BATCH.name in text
        assert "Что исправить в первую очередь" in text and "Правило PD" in text
        assert "Проверено 1 из 2" in text and OCR_WARNING in text
    assert session.get(DocumentRow, document.id).filename == "Иванов_Иван_Иванович.pdf"


@pytest.mark.parametrize("format", ["md", "pdf"])
def test_empty_export_and_escaping(format: str) -> None:
    summary = build_batch_summary(BATCH, [])
    summary.name = "<b>Закупка</b> | &"
    if format == "pdf":
        with pymupdf.open(stream=render_batch_pdf(summary), filetype="pdf") as pdf:
            text = " ".join(pdf[0].get_text().split())
    else:
        text = html.unescape(render_batch_md(summary))
    assert summary.name in text and "Нет готовых отчётов" in text
    assert "Проверено 0 из 0" not in text and OCR_WARNING not in text


def test_empty_api_exports_and_invalid_format(
    client: TestClient, api_db: tuple[Session, Mock],
) -> None:
    batch_id = create_batch(client)
    for format in ("json", "md", "pdf"):
        response = client.get(f"/api/batches/{batch_id}/export?format={format}")
        assert response.status_code == 200
        if format == "json":
            assert response.json()["overall"] is None and response.json()["complete"]
        elif format == "pdf":
            with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
                assert len(pdf) == 1
        else:
            assert "Нет готовых отчётов" in response.text
    response = client.get(f"/api/batches/{batch_id}/export?format=wrong")
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)


def test_retention_removes_only_empty_batches(retention_db: Session) -> None:
    session = retention_db
    for identifier in ("expired", "current", "queued", "empty"):
        session.add(BatchRow(id=identifier, purpose="internal",
                             created_at=test_retention.NOW - timedelta(days=10)))
    # Только что созданный комплект без документов ждёт первую загрузку — не удаляется.
    session.add(BatchRow(id="fresh-empty", purpose="internal",
                         created_at=test_retention.NOW - timedelta(minutes=1)))
    session.commit()
    for identifier, days in (("expired", 10), ("current", 1)):
        test_retention.add_report(session, identifier, timedelta(days=days))
        session.get(DocumentRow, identifier).batch_id = identifier
    session.add(DocumentRow(id="queued", batch_id="queued", purpose="internal"))
    session.commit()
    assert purge_expired(session, test_retention.NOW) == 1
    session.expire_all()
    assert set(session.scalars(select(BatchRow.id))) == {"current", "queued", "fresh-empty"}
    assert purge_expired(session, test_retention.NOW) == 0


def test_init_db_sqlite(api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    engine = api_db[0].get_bind()
    monkeypatch.setattr(db_session, "engine", engine)
    db_session.init_db()
    db_session.init_db()
    assert "batches" in inspect(engine).get_table_names()
    assert "batch_id" in {column["name"] for column in inspect(engine).get_columns("documents")}


def test_init_db_postgresql_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    create_all = Mock()
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(Base.metadata, "create_all", create_all)
    db_session.init_db()
    create_all.assert_called_once_with(bind=engine)
    statements = [str(call.args[0]) for call in
                  engine.begin.return_value.__enter__.return_value.execute.call_args_list]
    assert "CREATE EXTENSION IF NOT EXISTS vector" in statements
    assert "ALTER TABLE documents ADD COLUMN IF NOT EXISTS batch_id VARCHAR(64)" in statements


def test_batch_delete_cascades(retention_db: Session) -> None:
    session = retention_db
    session.add(BatchRow(id="cascade", purpose="internal"))
    session.flush()
    session.add(DocumentRow(id="cascade", batch_id="cascade", purpose="internal"))
    session.flush()
    session.add(TaskRow(id="cascade", document_id="cascade", status="queued"))
    session.add(ReportRow(id="cascade", document_id="cascade", data={}))
    session.commit()
    session.delete(session.get(BatchRow, "cascade"))
    session.commit()
    session.expire_all()
    for model in (BatchRow, DocumentRow, TaskRow, ReportRow):
        assert session.get(model, "cascade") is None
