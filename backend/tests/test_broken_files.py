"""T-047: ошибки синтетических файлов проходят через API и настоящий worker."""

import logging
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentRow, TaskRow
from app.services.extraction.docx import extract_docx
from app.services.extraction.pdf import extract_pdf
from app.services.extraction.xlsx import extract_xlsx
from app.worker.tasks import run_pipeline
from tests.test_api_documents import api_db as api_db
from tests.test_worker_pipeline import create_task

CORRUPT = "Файл повреждён или сохранён не полностью. Откройте его и сохраните заново."
PASSWORD = "Файл защищён паролем. Снимите пароль и загрузите снова."
EMPTY = "В документе нет текста для проверки."
FORMULAS = (
    "В таблице формулы без сохранённых значений. Откройте файл в Excel и сохраните его."
)
SCAN = (
    "Документ похож на скан: текст не распознаётся. Загрузите документ с текстовым слоем."
)


def make_file(path: Path, case: str) -> None:
    """Создаёт минимальный документ без пользовательских данных."""
    if path.suffix == ".pdf":
        with pymupdf.open() as document:
            page = document.new_page()
            if case == "scan":
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 10, 10), False)
                pixmap.clear_with(255)
                page.insert_image(page.rect, pixmap=pixmap)
            else:
                page.insert_text((72, 72), "Synthetic private content")
            options = {"encryption": pymupdf.PDF_ENCRYPT_AES_256,
                       "owner_pw": "synthetic-owner", "user_pw": "synthetic-password"}
            document.save(path, **(options if case == "password" else {}))
    elif path.suffix == ".docx":
        Document().save(path)
    else:
        workbook = Workbook()
        if case == "formulas":
            workbook.active["A1"] = "=1+1"
        workbook.save(path)
        workbook.close()
    if case == "corrupt":
        path.write_bytes(path.read_bytes()[:32])


@pytest.mark.parametrize(("extension", "case", "expected"), [
    ("pdf", "password", PASSWORD), ("pdf", "corrupt", CORRUPT),
    ("docx", "corrupt", CORRUPT), ("xlsx", "corrupt", CORRUPT),
    ("docx", "empty", EMPTY), ("xlsx", "empty", EMPTY),
    ("xlsx", "formulas", FORMULAS), ("pdf", "scan", SCAN),
])
def test_file_failure_via_api_and_worker(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    caplog: pytest.LogCaptureFixture, extension: str, case: str, expected: str,
) -> None:
    path = tmp_path / f"private-filename.{extension}"
    make_file(path, case)
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/documents", files={"file": (path.name, path.read_bytes())},
            data={"purpose": "internal"},
        )
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        run_pipeline(task_id, api_db[0])
        result = client.get(f"/api/tasks/{task_id}").json()
    assert (result["status"], result["error"], result["report_id"]) == (
        "failed", expected, None,
    )
    assert api_db[0].get(DocumentRow, response.json()["document_id"]).stored_path is None
    assert "private-filename" not in caplog.text
    assert str(tmp_path) not in caplog.text
    assert "Synthetic private content" not in caplog.text
    assert "synthetic-password" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "case=" in caplog.text and response.json()["document_id"] in caplog.text
    assert "OCR" not in result["error"]


@pytest.mark.parametrize(("filename", "content", "expected"), [
    ("private.pdf", b"", "Файл пустой."),
    ("private.docx", b"", "Файл пустой."),
    ("private.xlsx", b"", "Файл пустой."),
    ("private.docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", None),
    ("private.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", None),
    ("renamed.pdf", b"Synthetic text file", "Формат файла не поддерживается. "
     "Загрузите .pdf, .docx или .xlsx"),
])
def test_rejected_upload(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
    caplog: pytest.LogCaptureFixture, filename: str, content: bytes, expected: str | None,
) -> None:
    expected = expected or (
        "Старый формат .doc/.xls не поддерживается. Сохраните файл как .docx/.xlsx."
    )
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/documents", files={"file": (filename, content)},
            data={"purpose": "internal"},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": expected}
    assert filename not in caplog.text
    assert not list(tmp_path.iterdir())
    assert api_db[0].scalar(select(TaskRow)) is None
    api_db[1].assert_not_called()


@pytest.mark.parametrize("extension", ["docx", "xlsx"])
@pytest.mark.parametrize("content", [b"not a zip", b"PK\x03\x04truncated zip"])
def test_broken_office_pipeline(
    api_db: tuple[Session, Mock], tmp_path: Path, extension: str, content: bytes,
) -> None:
    path = tmp_path / f"private.{extension}"
    path.write_bytes(content)
    task = create_task(api_db[0], path, path.name)
    run_pipeline(task.id, api_db[0])
    assert (task.status, task.error) == ("failed", CORRUPT)
    assert not path.exists()


@pytest.mark.parametrize("extension", ["pdf", "docx", "xlsx"])
def test_parser_hides_library_exception(tmp_path: Path, extension: str) -> None:
    path = tmp_path / f"private.{extension}"
    path.write_bytes(b"private parser exception contents")
    parser = {"pdf": extract_pdf, "docx": extract_docx, "xlsx": extract_xlsx}[extension]
    with pytest.raises(ValueError) as error:
        parser(path)
    assert str(error.value) == CORRUPT
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("cached", ["0", "2"])
def test_formula_with_saved_value(tmp_path: Path, cached: str) -> None:
    path = tmp_path / "cached.xlsx"
    make_file(path, "formulas")
    stream = BytesIO()
    with ZipFile(path) as source, ZipFile(stream, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b"<v></v>", f"<v>{cached}</v>".encode())
            target.writestr(item, content)
    path.write_bytes(stream.getvalue())
    assert extract_xlsx(path)[0].text == cached
