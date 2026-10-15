"""Отчёты проверки. Заглушки по контракту, реализация — T-009."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from app.schemas import Report, ReportText

router = APIRouter(tags=["reports"])

NOT_IMPLEMENTED = "Будет реализовано в T-009"


@router.get("/reports/{report_id}", response_model=Report, summary="Отчёт проверки")
def get_report(report_id: str) -> Report:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.get(
    "/reports/{report_id}/text",
    response_model=ReportText,
    summary="Текст документа с замаскированными ПДн",
)
def get_report_text(report_id: str) -> ReportText:
    """Текст для подсветки находок. ПДн в нём уже заменены на [PERSON_1] и т.п."""
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.get(
    "/reports/{report_id}/export",
    response_class=Response,
    summary="Скачать отчёт файлом",
    responses={200: {"content": {"application/json": {}, "application/pdf": {}}}},
)
def export_report(report_id: str, format: Literal["json", "pdf"] = "json") -> Response:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)
