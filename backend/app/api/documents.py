"""Приём документов и текстовых запросов без извлечения текста в HTTP."""

from pathlib import Path
from typing import Annotated, BinaryIO
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DocumentRow, TaskRow
from app.db.session import get_db
from app.schemas import DocPurpose, QueryRequest, TaskCreated
from app.services.storage import delete_file, save_text, save_upload
from app.worker.tasks import process_document

router = APIRouter(tags=["documents"])
UNSUPPORTED_FORMAT = "Формат файла не поддерживается. Загрузите .pdf, .docx, .xlsx или .xml"
DbSession = Annotated[Session, Depends(get_db)]
UTF8_BOM = b"\xef\xbb\xbf"
XML_HEAD_BYTES = 64


def looks_like_xml(stream: BinaryIO) -> bool:
    """Первые 64 байта без UTF-8 BOM и пробелов начинаются с «<» (T-064a).

    Отсекает PDF, zip-архивы и прочие файлы, переименованные в .xml, до постановки в очередь.
    Указатель потока возвращается в начало.
    """
    head = stream.read(XML_HEAD_BYTES)
    stream.seek(0)
    head = head.removeprefix(UTF8_BOM).lstrip()
    return head.startswith(b"<")


def enqueue_document(
    session: Session, document_id: str, filename: str | None, purpose: DocPurpose, path: Path,
) -> TaskCreated:
    """Фиксирует документ и задачу до отправки идентификатора воркеру."""
    task_id = uuid4().hex
    try:
        session.add(DocumentRow(
            id=document_id, filename=filename, purpose=purpose.value, stored_path=str(path),
        ))
        session.flush()
        session.add(TaskRow(id=task_id, document_id=document_id, status="queued"))
        session.commit()
    except Exception:
        session.rollback()
        delete_file(path)
        raise
    try:
        process_document.delay(task_id)
    except Exception:
        delete_file(path)
        document = session.get(DocumentRow, document_id)
        task = session.get(TaskRow, task_id)
        document.stored_path = None
        task.status = "failed"
        task.error = "Не удалось начать проверку. Попробуйте ещё раз"
        session.commit()
        raise HTTPException(status_code=503, detail=task.error) from None
    return TaskCreated(task_id=task_id, document_id=document_id)


@router.post(
    "/documents", status_code=202, response_model=TaskCreated,
    summary="Загрузить документ на проверку",
)
def upload_document(
    file: Annotated[UploadFile, File(description=".pdf, .docx, .xlsx или .xml, до 50 МБ")],
    purpose: Annotated[DocPurpose, Form(description="Назначение документа")],
    session: DbSession,
) -> TaskCreated:
    """Проверяет формат и размер, сохраняет файл и ставит проверку в очередь."""
    extension = Path(file.filename or "").suffix.lower()
    signatures = {".pdf": b"%PDF", ".docx": b"PK\x03\x04", ".xlsx": b"PK\x03\x04"}
    signature = file.file.read(4)
    file.file.seek(0)
    if not signature:
        raise HTTPException(status_code=400, detail="Файл пустой.")
    if extension in {".docx", ".xlsx"} and signature == b"\xd0\xcf\x11\xe0":
        raise HTTPException(
            status_code=400,
            detail="Старый формат .doc/.xls не поддерживается. Сохраните файл как .docx/.xlsx.",
        )
    if extension == ".xml":
        if not looks_like_xml(file.file):
            raise HTTPException(status_code=400, detail=UNSUPPORTED_FORMAT)
    elif extension not in signatures or signature != signatures[extension]:
        raise HTTPException(status_code=400, detail=UNSUPPORTED_FORMAT)
    document_id = uuid4().hex
    try:
        path = save_upload(file, document_id, get_settings().max_upload_mb * 1024 * 1024)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return enqueue_document(session, document_id, file.filename, purpose, path)


@router.post(
    "/queries", status_code=202, response_model=TaskCreated,
    summary="Проверить текст без файла",
)
def submit_query(body: QueryRequest, session: DbSession) -> TaskCreated:
    """Сохраняет текст запроса и ставит проверку в очередь."""
    document_id = uuid4().hex
    path = save_text(body.text, document_id)
    return enqueue_document(session, document_id, None, body.purpose, path)
