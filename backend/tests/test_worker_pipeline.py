"""Конвейер с реальным извлечением, сохранением отчёта и удалением исходников."""

import logging
from collections.abc import Callable, Iterator
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from docx import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, DocumentRow, ReportRow, TaskRow
from app.schemas import DocPurpose, ExtractedDocument, Report, ReportText, Rule, Stage, TextSegment
from app.services import pipeline
from app.services.extraction.extract import extract
from app.services.pipeline import AuditResult
from app.worker import tasks
from tests.test_pipeline import NAME, TEXT, make_report


@pytest.fixture(autouse=True)
def report_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def create_task(session: Session, path: Path, filename: str | None) -> TaskRow:
    session.add(DocumentRow(
        id="synthetic-document", filename=filename, purpose="internal", stored_path=str(path),
    ))
    session.flush()
    task = TaskRow(id="synthetic-task", document_id="synthetic-document", status="queued")
    session.add(task)
    session.commit()
    return task


def test_docx_pipeline(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "synthetic.docx"
    document = Document()
    document.add_paragraph("Синтетический договор")
    document.save(path)
    source_bytes = path.read_bytes()
    task = create_task(session, path, "synthetic.docx")

    def checked_extract(source: Path, filename: str, document_id: str) -> ExtractedDocument:
        session.refresh(task)
        assert (task.status, task.stage, task.progress) == ("processing", "extracting", 10)
        result = extract(source, filename, document_id)
        assert result.text == "Синтетический договор"
        return result

    extractor = Mock(side_effect=checked_extract)
    monkeypatch.setattr(tasks, "extract", extractor)
    tasks.run_pipeline(task.id, session)
    assert (task.status, task.stage, task.progress, task.error) == ("done", "reporting", 100, None)
    row = session.get(ReportRow, task.report_id)
    report = Report.model_validate(row.data)
    report_text = ReportText.model_validate(row.text)
    assert report.report_id == task.report_id
    assert report.document_id == task.document_id
    assert report.audit.document_sha256 == sha256(source_bytes).hexdigest()
    assert report_text.text == "Синтетический договор"
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None
    extractor.assert_called_once_with(path, "synthetic.docx", task.document_id)


def test_corrupt_pdf_pipeline(session: Session, tmp_path: Path) -> None:
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"%PDF broken synthetic file")
    task = create_task(session, path, "synthetic.pdf")
    tasks.run_pipeline(task.id, session)
    assert task.status == "failed"
    assert task.error == (
        "Не удалось прочитать файл. Он повреждён, недоступен или защищён паролем."
    )
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None


@pytest.mark.parametrize("failure", [
    ValueError("Не удалось прочитать файл. Он повреждён, недоступен или защищён паролем."),
    RuntimeError("СЕКРЕТНОЕ СОДЕРЖИМОЕ"),
    NotImplementedError("T-016 (Данил)"),
])
def test_failed_pipeline(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, failure: Exception,
) -> None:
    path = tmp_path / "confidential-name.pdf"
    path.write_bytes(b"%PDF broken synthetic file")
    task = create_task(session, path, "confidential-name.pdf")
    monkeypatch.setattr(tasks, "extract", Mock(side_effect=failure))
    with caplog.at_level(logging.INFO, logger=tasks.__name__):
        tasks.run_pipeline(task.id, session)
    assert task.status == "failed"
    expected = str(failure) if isinstance(failure, ValueError) else tasks.PROCESSING_ERROR
    if isinstance(failure, NotImplementedError):
        expected = tasks.MODULE_UNAVAILABLE
    assert task.error == expected
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None
    assert "confidential-name" not in caplog.text
    assert "СЕКРЕТНОЕ СОДЕРЖИМОЕ" not in caplog.text
    if type(failure) is RuntimeError:
        assert "RuntimeError" in caplog.text
        assert task.id in caplog.text


@pytest.mark.parametrize("text", ["Синтетический текст\nВторой абзац", ""])
def test_text_pipeline(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str,
) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text(text, encoding="utf-8")
    task = create_task(session, path, None)
    extractor = Mock()
    monkeypatch.setattr(tasks, "extract", extractor)
    result = tasks.extract_document(session.get(DocumentRow, task.document_id))
    assert result.filename == "Текстовый запрос"
    assert result.text == text
    assert result.segments == [TextSegment(id=0, text=text, offset=0)]
    tasks.run_pipeline(task.id, session)
    assert (task.status, task.progress) == ("done", 100)
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None
    extractor.assert_not_called()
    row = session.get(ReportRow, task.report_id)
    assert Report.model_validate(row.data).filename is None
    assert ReportText.model_validate(row.text).text == text
    assert Report.model_validate(row.data).audit.document_sha256 == sha256(
        text.encode("utf-8"),
    ).hexdigest()


def test_worker_stages_and_private_data(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text(TEXT, encoding="utf-8")
    task = create_task(session, path, None)
    states: list[tuple[str, int]] = []
    original_run = tasks.run_audit

    def checked_run(
        doc: ExtractedDocument, purpose: DocPurpose, source_bytes: bytes,
        on_stage: Callable[[Stage, int], None], rules: list[Rule] | None = None,
    ) -> AuditResult:
        def checked_stage(stage: Stage, progress: int) -> None:
            on_stage(stage, progress)
            with Session(session.bind) as observer:
                row = observer.get(TaskRow, task.id)
                assert row.status == "processing"
                states.append((row.stage, row.progress))
        return original_run(doc, purpose, source_bytes, checked_stage, rules=rules)

    monkeypatch.setattr(tasks, "run_audit", checked_run)
    with caplog.at_level(logging.INFO):
        tasks.run_pipeline(task.id, session)
    assert states == [("detecting", 30), ("masking", 55), ("checking", 70), ("reporting", 90)]
    assert task.status == "done"
    assert NAME not in caplog.text
    assert "by_type=" in caplog.text
    row = session.get(ReportRow, task.report_id)
    assert NAME not in str(row.data) and NAME not in str(row.text)
    assert not path.exists()


@pytest.mark.parametrize("failure", [NotImplementedError("T-016"), ValueError("Ошибка правил"),
                                    RuntimeError(NAME)])
def test_audit_failure_removes_source(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, failure: Exception,
) -> None:
    path = tmp_path / "synthetic.txt"
    path.write_text(TEXT, encoding="utf-8")
    task = create_task(session, path, None)
    monkeypatch.setattr(tasks, "run_audit", Mock(side_effect=failure))
    with caplog.at_level(logging.INFO):
        tasks.run_pipeline(task.id, session)
    assert task.status == "failed" and task.report_id is None
    expected = tasks.PROCESSING_ERROR
    if isinstance(failure, NotImplementedError):
        expected = tasks.MODULE_UNAVAILABLE
    elif isinstance(failure, ValueError):
        expected = str(failure)
    assert task.error == expected
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None
    assert session.query(ReportRow).count() == 0
    assert NAME not in caplog.text


@pytest.mark.parametrize("stored_path_missing", [False, True])
def test_missing_source(
    session: Session, tmp_path: Path, stored_path_missing: bool,
) -> None:
    task = create_task(session, tmp_path / "missing.txt", None)
    if stored_path_missing:
        session.get(DocumentRow, task.document_id).stored_path = None
        session.commit()
    tasks.run_pipeline(task.id, session)
    assert task.status == "failed"
    assert task.error == tasks.PROCESSING_ERROR
    assert session.get(DocumentRow, task.document_id).stored_path is None


def test_missing_task(session: Session) -> None:
    tasks.run_pipeline("missing", session)
    assert session.get(TaskRow, "missing") is None


@pytest.mark.parametrize("fails", [False, True])
def test_celery_wrapper(
    session: Session, monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    pipeline = Mock(side_effect=RuntimeError("db unavailable") if fails else None)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "run_pipeline", pipeline)
    if fails:
        with pytest.raises(RuntimeError, match="db unavailable"):
            tasks.process_document.run("synthetic-task")
    else:
        tasks.process_document.run("synthetic-task")
    pipeline.assert_called_once_with("synthetic-task", session)
    factory.return_value.__exit__.assert_called_once()
