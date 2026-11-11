"""Создание комплектов и вычисление сводки по текущим задачам и отчётам."""

import json
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BatchRow, DocumentRow, ReportRow, TaskRow
from app.db.session import get_db
from app.schemas.batch import BatchCreate, BatchCreated, BatchSummary
from app.schemas.document import DocPurpose
from app.schemas.report import Report
from app.services.report.batch_summary import BatchInfo, BatchItem, build_batch_summary

router = APIRouter(tags=["batches"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/batches", status_code=201, response_model=BatchCreated,
             summary="Создать комплект документов")
def create_batch(body: BatchCreate, session: DbSession) -> BatchCreated:
    batch_id = uuid4().hex
    session.add(BatchRow(id=batch_id, name=body.name, purpose=body.purpose.value))
    session.commit()
    return BatchCreated(batch_id=batch_id)


def _batch_items(session: Session, batch_id: str) -> list[BatchItem]:
    rows = session.execute(
        select(DocumentRow, TaskRow, ReportRow.data)
        .outerjoin(TaskRow, TaskRow.document_id == DocumentRow.id)
        .outerjoin(ReportRow, ReportRow.id == TaskRow.report_id)
        .where(DocumentRow.batch_id == batch_id)
        .order_by(TaskRow.created_at.desc(), TaskRow.id.desc())
    )
    items: dict[str, BatchItem] = {}
    for document, task, report_data in rows:
        # При повторной обработке документа берём последнюю задачу.
        if document.id not in items:
            items[document.id] = BatchItem(
                document_id=document.id, task_id=task.id if task else "",
                filename=document.filename, purpose=DocPurpose(document.purpose),
                status=task.status if task else "queued",
                report=Report.model_validate(report_data) if report_data is not None else None,
                error=task.error if task else None,
            )
    return list(items.values())


@router.get("/batches/{batch_id}", response_model=BatchSummary, summary="Сводка комплекта")
def get_batch(batch_id: str, session: DbSession) -> BatchSummary:
    batch = session.get(BatchRow, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Комплект не найден")
    info = BatchInfo(batch.id, batch.name, DocPurpose(batch.purpose), batch.created_at)
    return build_batch_summary(info, _batch_items(session, batch_id))


@router.get(
    "/batches/{batch_id}/export", response_class=Response, summary="Скачать сводный отчёт",
    responses={200: {"content": {
        "application/json": {}, "text/markdown": {}, "application/pdf": {},
    }}},
)
def export_batch(
    batch_id: str, session: DbSession, format: Literal["json", "md", "pdf"] = "json",
) -> Response:
    summary = get_batch(batch_id, session)
    if format == "md":
        from app.services.report.md_export import render_batch_md

        content, media_type = render_batch_md(summary), "text/markdown"
    elif format == "pdf":
        from app.services.report.pdf_export import render_batch_pdf

        content, media_type = render_batch_pdf(summary), "application/pdf"
    else:
        content = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2)
        media_type = "application/json"
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="batch_{batch_id}.{format}"',
    })
