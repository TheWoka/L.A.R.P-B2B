"""Статус задачи обработки из базы данных."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import TaskRow
from app.db.session import get_db
from app.schemas import TaskInfo

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskInfo, summary="Статус проверки")
def get_task(task_id: str, session: Annotated[Session, Depends(get_db)]) -> TaskInfo:
    """Возвращает статус, текущий этап и прогресс задачи."""
    task = session.get(TaskRow, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Проверка не найдена")
    return TaskInfo(
        task_id=task.id, status=task.status, stage=task.stage, progress=task.progress,
        report_id=task.report_id, error=task.error,
    )
