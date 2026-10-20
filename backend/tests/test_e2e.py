"""Сквозная проверка API и настоящего конвейера на выдуманных данных."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import documents
from app.core.config import get_settings
from app.db.models import Base, DocumentRow, ReportRow
from app.db.session import get_db
from app.main import app
from app.schemas import EntityType, Report, ReportText
from app.services import storage
from app.worker.tasks import run_pipeline

# Синтетические реквизиты; СНИЛС с корректной контрольной суммой.
NAME = "Иванов Иван Иванович"
PASSPORT = "45 12 654321"
PHONE = "+7 (912) 345-67-89"
SNILS = "112-233-445 95"
PRIVATE_VALUES = (NAME, PASSPORT, PHONE, SNILS)
PUBLICATION_TEXT = (
    f"Документ для публикации. Контакт: {NAME}.\n"
    f"Паспорт: {PASSPORT}.\nТелефон: {PHONE}.\n"
    "Себестоимость единицы — 38 400 руб."
)


@pytest.fixture
def api_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Session, Mock]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    delay = Mock()
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(documents.process_document, "delay", delay)
    with Session(engine, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session, delay
        finally:
            app.dependency_overrides.pop(get_db, None)
            get_settings.cache_clear()
    engine.dispose()


def finish_audit(client: TestClient, session: Session, task_id: str) -> tuple[Report, ReportText]:
    """Выполняет отложенную задачу и читает результат через публичный API."""
    run_pipeline(task_id, session)
    response = client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "done", task
    assert task["progress"] == 100 and task["report_id"]
    report_response = client.get(f"/api/reports/{task['report_id']}")
    text_response = client.get(f"/api/reports/{task['report_id']}/text")
    assert report_response.status_code == text_response.status_code == 200
    report = Report.model_validate(report_response.json())
    text = ReportText.model_validate(text_response.json())
    assert report.audit.llm_provider == "mock" and report.audit.llm_calls == 0
    assert not report.llm_used and len(report.checklist) == 3
    return report, text


def assert_private_data_absent(
    report: Report, text: ReportText, session: Session, caplog: pytest.LogCaptureFixture,
) -> None:
    """Проверяет все поля JSON, сегменты текста, сохранённые строки БД и логи."""
    row = session.get(ReportRow, report.report_id)
    assert row is not None
    payloads = (
        report.model_dump_json(), text.model_dump_json(), caplog.text,
        json.dumps(row.data, ensure_ascii=False), json.dumps(row.text, ensure_ascii=False),
    )
    for value in PRIVATE_VALUES:
        for payload in payloads:
            assert value not in payload
    for violation in report.violations:
        for evidence in violation.evidence:
            assert evidence.quote == text.text[evidence.start:evidence.end]
    document = session.get(DocumentRow, report.document_id)
    assert document is not None and document.stored_path is None
    assert "stage=reporting" in caplog.text


def test_publication_query(
    client: TestClient, api_db: tuple[Session, Mock], caplog: pytest.LogCaptureFixture,
) -> None:
    session, delay = api_db
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/queries", json={"text": PUBLICATION_TEXT, "purpose": "publication"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        delay.assert_called_once_with(task_id)
        report, text = finish_audit(client, session, task_id)
    assert report.filename is None and report.overall == "red"
    assert {violation.rule_id for violation in report.violations} >= {
        "PD_IN_PUBLICATION", "TRADE_SECRET_IN_PUBLICATION",
    }
    assert {entity.type for entity in report.entities} >= {
        EntityType.PERSON, EntityType.PASSPORT, EntityType.PHONE, EntityType.TRADE_SECRET,
    }
    assert len(text.text) == len(PUBLICATION_TEXT)
    assert_private_data_absent(report, text, session, caplog)
    assert not list(storage.UPLOAD_DIR.iterdir())


def test_contract_docx(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, delay = api_db
    path = tmp_path / "synthetic-contract.docx"
    paragraphs = ("Договор с контрагентом", f"Представитель: {NAME}.",
                  f"СНИЛС: {SNILS}.", "Лист согласования")
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(path)
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/documents", files={"file": (path.name, path.read_bytes())},
            data={"purpose": "contract"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        delay.assert_called_once_with(task_id)
        uploaded = session.scalar(select(DocumentRow))
        assert uploaded is not None and uploaded.stored_path is not None
        uploaded_path = Path(uploaded.stored_path)
        assert uploaded_path.is_file() and uploaded_path.parent == storage.UPLOAD_DIR
        report, text = finish_audit(client, session, task_id)
    assert report.filename == path.name
    checklist = {item.category: item.status for item in report.checklist}
    assert checklist["approvals"] == "ok" and checklist["classification"] == "missing"
    assert {entity.type for entity in report.entities} >= {EntityType.PERSON, EntityType.SNILS}
    assert len(text.text) == len("\n".join(paragraphs))
    assert_private_data_absent(report, text, session, caplog)
    assert not uploaded_path.exists() and not list(storage.UPLOAD_DIR.iterdir())


def test_missing_report(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = client.get(f"/api/reports/{uuid4().hex}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Отчёт не найден"}
