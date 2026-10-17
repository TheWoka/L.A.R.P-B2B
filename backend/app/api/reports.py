"""Отчёты проверки. Заглушки по контракту.

Реализация: отчёт, текст, экспорт — T-009; журнал LLM — T-013; обезличенная копия — T-026.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from app.schemas import LlmCall, Report, ReportText

router = APIRouter(tags=["reports"])

NOT_IMPLEMENTED = "Будет реализовано в T-009"
NOT_IMPLEMENTED_LLM_LOG = "Будет реализовано в T-013"
NOT_IMPLEMENTED_REDACTED = "Будет реализовано в T-026"


@router.get("/reports/{report_id}", response_model=Report, summary="Отчёт проверки")
def get_report(report_id: str) -> Report:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.get(
    "/reports/{report_id}/text",
    response_model=ReportText,
    summary="Текст документа с замаскированными ПДн",
)
def get_report_text(report_id: str) -> ReportText:
    """Текст для подсветки находок.

    ПДн скрыты маскированием с сохранением длины (`█`, первая буква слова видна),
    см. contracts.md, раздел 4.1. Смещения из Report применяются к тексту напрямую.
    """
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.get(
    "/reports/{report_id}/export",
    response_class=Response,
    summary="Скачать отчёт файлом",
    responses={200: {"content": {"application/json": {}, "application/pdf": {}}}},
)
def export_report(report_id: str, format: Literal["json", "pdf"] = "json") -> Response:
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)


@router.get(
    "/reports/{report_id}/llm-log",
    response_model=list[LlmCall],
    summary="Что видела нейросеть: журнал вызовов LLM",
)
def get_llm_log(report_id: str) -> list[LlmCall]:
    """Каждый вызов LLM с РОВНО тем замаскированным фрагментом, что ушёл в модель.

    В режиме mock — пустой список или mock-вызовы с verdict="unknown".
    """
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED_LLM_LOG)


@router.get(
    "/reports/{report_id}/redacted",
    response_class=Response,
    summary="Скачать обезличенную копию документа",
    responses={
        200: {
            "description": "Файл в формате исходника (.pdf / .docx / .xlsx; для запроса — .txt). "
            'Content-Disposition: attachment; filename="<имя>_обезличено.<ext>"',
            "content": {
                "application/pdf": {},
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                "text/plain": {},
            },
        },
        404: {"description": "Обезличенная копия недоступна"},
    },
)
def get_redacted(report_id: str) -> Response:
    """ПДн удалены физически (в PDF — редакция, а не закраска); коммерческая тайна не трогается."""
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED_REDACTED)
