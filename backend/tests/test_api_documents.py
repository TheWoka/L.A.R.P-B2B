"""API загрузок: SQLite и подмена очереди без PostgreSQL и Redis."""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from docx import Document
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import documents
from app.db.models import Base, DocumentRow, TaskRow
from app.db.session import get_db
from app.main import app
from app.services import storage


@pytest.fixture
def api_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Session, Mock]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    delay = Mock()
    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(documents.process_document, "delay", delay)
    with Session(engine, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session, delay
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("Синтетический документ закупки")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_upload_docx(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
) -> None:
    session, delay = api_db
    content = docx_bytes()
    response = client.post(
        "/api/documents", files={"file": ("пример.DOCX", content)},
        data={"purpose": "internal"},
    )
    assert response.status_code == 202
    result = response.json()
    assert UUID(result["document_id"]).version == UUID(result["task_id"]).version == 4
    document = session.get(DocumentRow, result["document_id"])
    assert document.filename == "пример.DOCX"
    assert document.purpose == "internal"
    assert Path(document.stored_path).read_bytes() == content
    assert list(tmp_path.iterdir()) == [Path(document.stored_path)]
    task = session.get(TaskRow, result["task_id"])
    assert task.document_id == document.id
    assert task.status == "queued"
    delay.assert_called_once_with(task.id)
    assert client.get(f"/api/tasks/{task.id}").json() == {
        "task_id": task.id, "status": "queued", "stage": None, "progress": 0,
        "report_id": None, "error": None,
    }


@pytest.mark.parametrize(("filename", "content"), [
    ("file.exe", b"PK\x03\x04"), ("file.docx", b"not a zip"),
    ("file.xlsx", b"wrong"), ("file.pdf", b"PK\x03\x04"),
])
def test_invalid_format(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    filename: str, content: bytes,
) -> None:
    response = client.post(
        "/api/documents", files={"file": (filename, content)}, data={"purpose": "internal"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == documents.UNSUPPORTED_FORMAT
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(TaskRow)) is None
    api_db[1].assert_not_called()


def test_upload_limit(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        documents, "get_settings", lambda: SimpleNamespace(max_upload_mb=1 / 1024),
    )
    response = client.post(
        "/api/documents", files={"file": ("large.pdf", b"%PDF" + b"x" * 1024)},
        data={"purpose": "internal"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Файл больше 50 МБ"}
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(TaskRow)) is None
    api_db[1].assert_not_called()


def test_query(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = client.post("/api/queries", json={"text": "Запрос № 1", "purpose": "internal"})
    assert response.status_code == 202
    result = response.json()
    document = api_db[0].get(DocumentRow, result["document_id"])
    assert document.filename is None
    assert Path(document.stored_path).read_text(encoding="utf-8") == "Запрос № 1"
    assert api_db[0].get(TaskRow, result["task_id"]).status == "queued"
    api_db[1].assert_called_once_with(result["task_id"])


def test_empty_query(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = client.post("/api/queries", json={"text": "", "purpose": "internal"})
    assert response.status_code == 422
    api_db[1].assert_not_called()


def test_missing_task(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = client.get("/api/tasks/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "Проверка не найдена"}


@pytest.mark.parametrize("size", [1024, 2 * 1024 * 1024 + 7])
def test_storage_chunks(
    api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    content = b"x" * size
    file = UploadFile(BytesIO(content), filename="file.PDF")
    monkeypatch.setattr(storage, "CHUNK_BYTES", 1024)
    path = storage.save_upload(file, "synthetic", size)
    assert path.read_bytes() == content
    storage.delete_file(path)
    storage.delete_file(path)
    assert not path.exists()


def test_storage_removes_partial_upload(
    api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(storage, "CHUNK_BYTES", 4)
    file = UploadFile(BytesIO(b"123456789"), filename="file.pdf")
    with pytest.raises(ValueError, match="Файл больше 50 МБ"):
        storage.save_upload(file, "synthetic", 8)
    assert not list(tmp_path.iterdir())


def test_empty_text_storage(api_db: tuple[Session, Mock]) -> None:
    path = storage.save_text("", "synthetic")
    assert path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(("filename", "content"), [
    ("document.PDF", b"%PDF synthetic"), ("document.XLSX", b"PK\x03\x04synthetic"),
])
def test_supported_signatures(
    client: TestClient, api_db: tuple[Session, Mock], filename: str, content: bytes,
) -> None:
    response = client.post(
        "/api/documents", files={"file": (filename, content)}, data={"purpose": "contract"},
    )
    assert response.status_code == 202
    document = api_db[0].get(DocumentRow, response.json()["document_id"])
    assert document.purpose == "contract"
    assert Path(document.stored_path).read_bytes() == content
    api_db[1].assert_called_once_with(response.json()["task_id"])


def test_broker_failure(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
) -> None:
    api_db[1].side_effect = RuntimeError("broker unavailable")
    response = client.post("/api/queries", json={"text": "Запрос", "purpose": "internal"})
    assert response.status_code == 503
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(DocumentRow)).stored_path is None
    assert api_db[0].scalar(select(TaskRow)).status == "failed"


def test_database_failure_cleans_file(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_db[0], "flush", Mock(side_effect=RuntimeError("db unavailable")))
    with pytest.raises(RuntimeError, match="db unavailable"):
        client.post("/api/queries", json={"text": "Запрос", "purpose": "internal"})
    assert not list(tmp_path.iterdir())
    api_db[1].assert_not_called()


XML_NOTICE = '<?xml version="1.0" encoding="UTF-8"?>\n<notice><v>1</v></notice>'.encode()


@pytest.mark.parametrize("content", [
    XML_NOTICE,
    b"\xef\xbb\xbf" + XML_NOTICE,                  # UTF-8 BOM (выгрузки ЕИС из Windows)
    b"\xef\xbb\xbf \r\n\t" + XML_NOTICE,           # BOM и пробелы перед прологом
])
def test_upload_xml(
    client: TestClient, api_db: tuple[Session, Mock], content: bytes,
) -> None:
    response = client.post(
        "/api/documents", files={"file": ("notice.XML", content)},
        data={"purpose": "publication"},
    )
    assert response.status_code == 202
    document = api_db[0].get(DocumentRow, response.json()["document_id"])
    assert Path(document.stored_path).suffix == ".xml"
    assert Path(document.stored_path).read_bytes() == content
    api_db[1].assert_called_once_with(response.json()["task_id"])


@pytest.mark.parametrize("content", [
    b"%PDF-1.7 synthetic",                         # PDF, переименованный в .xml
    b"PK\x03\x04synthetic",                        # zip (.docx/.xlsx)
    b"\xef\xbb\xbf   ",                            # только BOM и пробелы
    b"\xff\xfe<\x00r\x00>\x00",                    # UTF-16 — выгрузки ЕИС в UTF-8
    b" " * 64 + b"<r/>",                           # «<» дальше первых 64 байт
])
def test_invalid_xml_upload(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path, content: bytes,
) -> None:
    response = client.post(
        "/api/documents", files={"file": ("notice.xml", content)}, data={"purpose": "internal"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == documents.UNSUPPORTED_FORMAT
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(TaskRow)) is None
    api_db[1].assert_not_called()


def test_looks_like_xml_rewinds_stream() -> None:
    stream = BytesIO(b"\xef\xbb\xbf\n<r/>")
    assert documents.looks_like_xml(stream)
    assert stream.tell() == 0
