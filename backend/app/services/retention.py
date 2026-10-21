"""Удаление отчётов с истёкшим сроком хранения и оставшихся исходников."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DocumentRow, ReportRow, TaskRow
from app.services.storage import delete_file

logger = logging.getLogger(__name__)


def purge_expired(session: Session, now: datetime | None = None) -> int:
    """Удаляет отчёты строго старше TTL; фиксирует изменения и возвращает их число."""
    cutoff = (now if now is not None else datetime.now(timezone.utc)) - timedelta(
        days=get_settings().report_ttl_days,
    )
    expired = list(session.scalars(select(ReportRow).where(ReportRow.created_at < cutoff)))
    document_ids = {report.document_id for report in expired}
    for report in expired:
        session.execute(delete(TaskRow).where(TaskRow.report_id == report.id))
        session.delete(report)
    session.flush()
    for document_id in document_ids:
        # Не удаляем другой, ещё действующий отчёт каскадом через документ.
        if session.scalar(select(ReportRow.id).where(ReportRow.document_id == document_id)):
            continue
        document = session.get(DocumentRow, document_id)
        if document is not None:
            if document.stored_path is not None:
                delete_file(document.stored_path)
            session.execute(delete(TaskRow).where(TaskRow.document_id == document_id))
            session.delete(document)
    session.commit()
    logger.info("purged_reports=%d", len(expired))
    return len(expired)
