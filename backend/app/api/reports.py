"""Чтение сохранённых отчётов и текста проверки.

Заглушка: PDF — T-020. Журнал LLM — T-013b. Обезличенная копия — T-026.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.models import ReportRow
from app.db.session import get_db
from app.schemas import ExtractedDocument, LlmCall, Report, ReportText, TextSegment
from app.services.detection.detect import detect
from app.services.masking.masker import MASKED_CATEGORIES
from app.services.redaction.redact import MEDIA_TYPES

router = APIRouter(tags=["reports"])
DbSession = Annotated[Session, Depends(get_db)]

NOT_IMPLEMENTED_PDF = "Будет реализовано в T-020"
REDACTED_UNAVAILABLE = "Обезличенная копия недоступна"
REDACTED_SUFFIX = "_обезличено"
QUERY_NAME, SAFE_NAME = "запрос", "документ"
# Одиночную фамилию без имени («Иванов_заявка») NER не видит: в имени файла нет контекста.
# Имя копии — не место для риска: слово с фамильным окончанием тоже считаем ПДн.
_SURNAME_LIKE_RE = re.compile(
    r"(?<![\w])[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ова|ева|ёва|ина|ына|ский|цкий|ская|цкая)(?![\w])",
)


@router.get("/reports/{report_id}", response_model=Report, summary="Отчёт проверки")
def get_report(report_id: str, session: DbSession) -> Report:
    row = session.get(ReportRow, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return Report.model_validate(row.data)


@router.get(
    "/reports/{report_id}/text",
    response_model=ReportText,
    summary="Текст документа с замаскированными ПДн",
)
def get_report_text(report_id: str, session: DbSession) -> ReportText:
    """Текст для подсветки находок.

    ПДн скрыты маскированием с сохранением длины (`█`, первая буква слова видна),
    см. contracts.md, раздел 4.1. Смещения из Report применяются к тексту напрямую.
    """
    row = session.get(ReportRow, report_id)
    if row is None or row.text is None:
        raise HTTPException(status_code=404, detail="Текст отчёта не найден")
    return ReportText.model_validate(row.text)


@router.get(
    "/reports/{report_id}/export",
    response_class=Response,
    summary="Скачать отчёт файлом",
    responses={200: {"content": {"application/json": {}, "application/pdf": {}}}},
)
def export_report(
    report_id: str, session: DbSession, format: Literal["json", "pdf"] = "json",
) -> Response:
    if format == "pdf":
        from app.services.report.pdf_export import render_report_pdf

        report = get_report(report_id, session)
        return Response(
            content=render_report_pdf(report), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="report_{report_id}.pdf"'},
        )
    report = get_report(report_id, session)
    return Response(
        content=json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.json"'},
    )


@router.get(
    "/reports/{report_id}/llm-log",
    response_model=list[LlmCall],
    summary="Что видела нейросеть: журнал вызовов LLM",
)
def get_llm_log(report_id: str, session: DbSession) -> list[LlmCall]:
    """Каждый вызов LLM с РОВНО тем замаскированным фрагментом, что ушёл в модель.

    В режиме mock вызовов нет — пустой список. Нет отчёта → 404; отчёт без журнала
    (создан до T-013b) → []. Исходный текст не восстанавливается (AGENTS.md, правило 5).
    """
    row = session.get(ReportRow, report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Отчёт не найден")
    return [LlmCall.model_validate(call) for call in row.llm_log or []]


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
def get_redacted(report_id: str, session: DbSession) -> Response:
    """ПДн удалены физически (в PDF — редакция, а не закраска); коммерческая тайна не трогается.

    Имя файла — «<имя>_обезличено.<ext>»; <имя> — основа исходного имени, но если
    в нём самом найдены ПДн («Иванов_заявка.docx») — «документ», для запроса — «запрос».
    """
    row = session.get(ReportRow, report_id)
    if row is None or row.redacted is None or row.redacted_ext not in MEDIA_TYPES:
        raise HTTPException(status_code=404, detail=REDACTED_UNAVAILABLE)
    filename = f"{download_stem(row.data.get('filename'))}{REDACTED_SUFFIX}.{row.redacted_ext}"
    disposition = (
        f'attachment; filename="redacted.{row.redacted_ext}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        content=row.redacted, media_type=MEDIA_TYPES[row.redacted_ext],
        headers={"Content-Disposition": disposition},
    )


def download_stem(filename: str | None) -> str:
    """Основа имени скачиваемой копии без ПДн (RFC 5987 кодирует кириллицу в заголовке)."""
    if filename is None:
        return QUERY_NAME
    name = Path(filename).name
    stem = (name.rpartition(".")[0] if "." in name else name).strip()  # ".pdf" → ""
    if not stem or _has_personal_data(stem):
        return SAFE_NAME
    return stem


@lru_cache(maxsize=256)
def _has_personal_data(stem: str) -> bool:
    """Тот же детектор, что и для документа; «_» в именах файлов — вместо пробела."""
    text = stem.replace("_", " ")
    if _SURNAME_LIKE_RE.search(text):
        return True
    doc = ExtractedDocument(
        document_id="filename-check", filename=stem, text=text,
        segments=[TextSegment(id=0, text=text, offset=0)],
    )
    return any(entity.category in MASKED_CATEGORIES for entity in detect(doc))
