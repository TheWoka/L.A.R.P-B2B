"""Первая стадия с реальным извлечением и удалением исходников."""

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from docx import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, DocumentRow, TaskRow
from app.schemas import ExtractedDocument, TextSegment
from app.services.extraction.extract import extract
from app.worker import tasks


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
    assert (task.status, task.progress, task.error, task.report_id) == ("done", 100, None, None)
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
    assert task.error == expected
    assert not path.exists()
    assert session.get(DocumentRow, task.document_id).stored_path is None
    assert "confidential-name" not in caplog.text
    assert "СЕКРЕТНОЕ СОДЕРЖИМОЕ" not in caplog.text
    if isinstance(failure, RuntimeError):
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
