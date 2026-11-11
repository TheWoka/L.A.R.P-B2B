"""Комплект документов и сводный отчёт (contracts.md, разделы 1, 4 и 4.1, контракт v3).

Сводный отчёт вычисляется при чтении из отчётов документов и значений ПДн не содержит.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.document import DocPurpose

Overall = Literal["red", "yellow", "green"]
BatchDocumentStatus = Literal["queued", "processing", "done", "failed"]


class BatchCreate(BaseModel):
    """Тело POST /api/batches."""

    name: str | None = Field(None, max_length=120)
    purpose: DocPurpose


class BatchCreated(BaseModel):
    """Ответ 201 POST /api/batches."""

    batch_id: str


class BatchDocument(BaseModel):
    document_id: str
    task_id: str
    filename: str | None             # безопасное имя: ПДн в имени -> нейтральное
    purpose: DocPurpose
    status: BatchDocumentStatus
    report_id: str | None = None
    overall: Overall | None = None   # None, пока отчёта нет
    violations_red: int = Field(0, ge=0)
    violations_yellow: int = Field(0, ge=0)
    ocr: bool = False                # хотя бы одна страница распознана OCR
    error: str | None = None         # понятный пользователю текст, без технических деталей


class BatchRuleSummary(BaseModel):
    rule_id: str
    title: str
    severity: Literal["red", "yellow"]
    documents: int = Field(ge=1)     # в скольких документах сработало


class BatchSummary(BaseModel):
    """Ответ GET /api/batches/{batch_id}."""

    batch_id: str
    name: str | None
    purpose: DocPurpose
    created_at: datetime
    complete: bool                   # нет документов queued/processing
    overall: Overall | None          # худший среди готовых; None — готовых нет
    documents_total: int
    documents_done: int
    documents_failed: int
    violations_red: int
    violations_yellow: int
    rules: list[BatchRuleSummary]    # red раньше yellow, затем по убыванию documents, затем rule_id
    documents: list[BatchDocument]   # red -> yellow -> green -> processing/queued -> failed
