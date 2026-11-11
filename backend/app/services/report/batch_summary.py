"""Сводка комплекта из снимка данных: без БД, LLM и исходных текстов."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from app.api.reports import download_stem
from app.schemas.batch import (
    BatchDocument,
    BatchDocumentStatus,
    BatchRuleSummary,
    BatchSummary,
)
from app.schemas.document import DocPurpose
from app.schemas.report import Report

OVERALL_ORDER = {"red": 0, "yellow": 1, "green": 2}


@dataclass(frozen=True)
class BatchInfo:
    id: str
    name: str | None
    purpose: DocPurpose
    created_at: datetime


class BatchItem(NamedTuple):
    document_id: str
    task_id: str
    filename: str | None
    purpose: DocPurpose
    status: BatchDocumentStatus
    report: Report | None
    error: str | None


def _document_summary(item: BatchItem) -> BatchDocument:
    report = item.report if item.status == "done" else None
    filename = None
    if item.filename is not None:
        filename = download_stem(item.filename) + Path(item.filename).suffix
    return BatchDocument(
        document_id=item.document_id, task_id=item.task_id, filename=filename,
        purpose=item.purpose, status=item.status, error=item.error,
        report_id=report.report_id if report else None,
        overall=report.overall if report else None,
        violations_red=sum(v.severity == "red" for v in report.violations) if report else 0,
        violations_yellow=sum(v.severity == "yellow" for v in report.violations) if report else 0,
        ocr=report is not None and report.ocr is not None,
    )


def _rule_summaries(items: list[BatchItem]) -> list[BatchRuleSummary]:
    rules: dict[str, BatchRuleSummary] = {}
    document_ids: dict[str, set[str]] = {}
    for item in items:
        if item.status != "done" or item.report is None:
            continue
        for violation in item.report.violations:
            rule_id = violation.rule_id
            if rule_id not in rules:
                rules[rule_id] = BatchRuleSummary(
                    rule_id=rule_id, title=violation.title,
                    severity=violation.severity.value, documents=1,
                )
                document_ids[rule_id] = set()
            document_ids[rule_id].add(item.document_id)
            rules[rule_id].documents = len(document_ids[rule_id])
            if violation.severity == "red":
                rules[rule_id].severity = "red"
    return sorted(rules.values(), key=lambda r: (
        OVERALL_ORDER[r.severity], -r.documents, r.rule_id,
    ))


def _document_order(document: BatchDocument) -> tuple[int, str, str]:
    rank = 4 if document.status == "failed" else OVERALL_ORDER.get(document.overall, 3)
    return rank, (document.filename or "").casefold(), document.document_id


def build_batch_summary(batch: BatchInfo, items: list[BatchItem]) -> BatchSummary:
    """Готовые отчёты определяют итог; повторы правила считаются один раз на документ."""
    documents = sorted((_document_summary(item) for item in items), key=_document_order)
    overall = min(
        (doc.overall for doc in documents if doc.overall is not None),
        key=OVERALL_ORDER.__getitem__, default=None,
    )
    return BatchSummary(
        batch_id=batch.id, name=batch.name, purpose=batch.purpose, created_at=batch.created_at,
        complete=all(doc.status not in {"queued", "processing"} for doc in documents),
        overall=overall, documents_total=len(documents),
        documents_done=sum(doc.status == "done" for doc in documents),
        documents_failed=sum(doc.status == "failed" for doc in documents),
        violations_red=sum(doc.violations_red for doc in documents),
        violations_yellow=sum(doc.violations_yellow for doc in documents),
        rules=_rule_summaries(items), documents=documents,
    )
