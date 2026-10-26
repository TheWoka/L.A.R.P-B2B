"""Задачи обработки и модели создания задач (contracts.md, разделы 1 и 4.1)."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.schemas.document import DocPurpose


class TaskStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class Stage(str, Enum):
    extracting = "extracting"
    detecting = "detecting"
    masking = "masking"
    checking = "checking"
    reporting = "reporting"


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    stage: Stage | None = None
    progress: int = Field(0, ge=0, le=100)
    report_id: str | None = None
    error: str | None = None         # понятный пользователю текст, без технических деталей


class TaskCreated(BaseModel):
    """Ответ POST /api/documents и POST /api/queries."""

    task_id: str
    document_id: str


class QueryRequest(BaseModel):
    """Тело POST /api/queries."""

    text: str = Field(min_length=1, max_length=200_000)
    purpose: DocPurpose

    @field_validator("text", mode="before")
    @classmethod
    def reject_blank_text(cls, value: object) -> object:
        """Отклоняет пробелы, сохраняя допустимый текст и его смещения без изменений."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("blank_text")
        return value
