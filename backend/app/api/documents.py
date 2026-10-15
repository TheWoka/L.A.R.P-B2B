"""Создание задач проверки: загрузка файла и текстовый запрос.

Заглушки по контракту (contracts.md, раздел 4). Реализация — T-009.
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas import DocPurpose, QueryRequest, TaskCreated

router = APIRouter(tags=["documents"])

NOT_IMPLEMENTED = "Будет реализовано в T-009"


@router.post(
    "/documents",
    status_code=202,
    response_model=TaskCreated,
    summary="Загрузить документ на проверку",
)
def upload_document(
    file: Annotated[UploadFile, File(description=".pdf, .docx или .xlsx, до 50 МБ")],
    purpose: Annotated[DocPurpose, Form(description="Назначение документа")],
) -> TaskCreated:
    """Сохраняет файл, ставит задачу в Celery и сразу (< 1 с) возвращает task_id."""
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.post(
    "/queries",
    status_code=202,
    response_model=TaskCreated,
    summary="Проверить текст без файла",
)
def submit_query(body: QueryRequest) -> TaskCreated:
    """Ставит в очередь проверку текста, введённого пользователем."""
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)
