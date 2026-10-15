"""Статус задачи обработки. Заглушка по контракту, реализация — T-009."""

from fastapi import APIRouter, HTTPException

from app.schemas import TaskInfo

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskInfo, summary="Статус проверки")
def get_task(task_id: str) -> TaskInfo:
    """Возвращает статус, текущий этап и прогресс задачи."""
    raise HTTPException(status_code=501, detail="Будет реализовано в T-009")
