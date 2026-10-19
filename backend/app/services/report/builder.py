"""Сборка итогового отчёта без БД, LLM и раскрытия ПДн. Задача T-016."""

from collections import Counter
from datetime import UTC, datetime
from typing import Literal, TypeAlias
from uuid import uuid4

from app.schemas import (
    AuditInfo,
    ChecklistItem,
    DocPurpose,
    EntityType,
    Marker,
    MarkerType,
    Report,
    RuleContext,
    Severity,
    Violation,
)
from app.services.masking.masker import mask_preserving_length

ChecklistCategory: TypeAlias = Literal["approvals", "classification", "retention"]
ChecklistStatus: TypeAlias = Literal["ok", "missing", "not_required"]

# Строка совпадает с именем текстового запроса в worker/tasks.py.
TEXT_REQUEST_FILENAME = "Текстовый запрос"
HEAVY_PD = frozenset({
    EntityType.PASSPORT, EntityType.SNILS, EntityType.INN,
    EntityType.BIRTH_DATE, EntityType.SPECIAL_CATEGORY,
})
CHECKLIST_COMMENTS: dict[ChecklistCategory, dict[ChecklistStatus, str]] = {
    "approvals": {
        "ok": "Лист согласования найден",
        "missing": "Лист согласования не найден — для этого назначения он обязателен",
        "not_required": "Для этого назначения лист согласования не требуется",
    },
    "classification": {
        "ok": "Гриф конфиденциальности найден",
        "missing": "Гриф не найден — документ содержит сведения, требующие конфиденциальности",
        "not_required": "Для этого документа гриф не требуется",
    },
    "retention": {
        "ok": "Срок хранения или условие уничтожения указаны",
        "missing": "Не указан срок хранения документа с персональными данными",
        "not_required": "Для найденных данных указание срока хранения не требуется",
    },
}


def build_report(
    ctx: RuleContext, violations: list[Violation], processing_ms: int, llm_used: bool,
    audit: AuditInfo,
) -> Report:
    """Собирает Report; audit передаётся конвейером по согласованной сигнатуре T-009b.

    Чек-лист зависит только от контекста, светофор — только от нарушений.
    Входные объекты не изменяются; текст маскируется один раз в исходных координатах.
    """
    masked_text = mask_preserving_length(ctx.doc.text, ctx.entities)
    stats = Counter(entity.type.value for entity in ctx.entities)
    severities = {violation.severity for violation in violations}
    overall = "red" if Severity.red in severities else (
        "yellow" if Severity.yellow in severities else "green"
    )
    return Report(
        report_id=uuid4().hex, document_id=ctx.doc.document_id,
        filename=None if ctx.doc.filename == TEXT_REQUEST_FILENAME else ctx.doc.filename,
        purpose=ctx.purpose, created_at=datetime.now(UTC), processing_ms=processing_ms,
        llm_used=llm_used, audit=audit, redacted_available=False, overall=overall,
        entities=sorted(ctx.entities, key=lambda entity: (entity.start, entity.end)),
        stats=dict(sorted(stats.items())), checklist=_build_checklist(ctx, masked_text),
        violations=sorted(
            violations, key=lambda item: (item.severity != Severity.red, item.rule_id),
        ),
    )


def _build_checklist(ctx: RuleContext, masked_text: str) -> list[ChecklistItem]:
    """Определяет обязательность трёх реквизитов; отсутствующий маркер не считается найденным."""
    entity_types = {entity.type for entity in ctx.entities}
    has_heavy_pd = bool(entity_types & HEAVY_PD)
    present_markers = {marker.type: marker for marker in ctx.markers if marker.present}
    requirements: tuple[tuple[ChecklistCategory, MarkerType, bool], ...] = (
        ("approvals", MarkerType.approval_sheet,
         ctx.purpose in {DocPurpose.internal, DocPurpose.contract}),
        ("classification", MarkerType.classification_mark,
         ctx.purpose != DocPurpose.publication
         and (EntityType.TRADE_SECRET in entity_types or has_heavy_pd)),
        ("retention", MarkerType.retention_period, has_heavy_pd),
    )
    return [
        _checklist_item(category, present_markers.get(marker_type), required, masked_text)
        for category, marker_type, required in requirements
    ]


def _checklist_item(
    category: ChecklistCategory, marker: Marker | None, required: bool, masked_text: str,
) -> ChecklistItem:
    """Копирует доказательство из безопасного текста, сохраняя страницу и раздел."""
    present = marker is not None and marker.present
    status: ChecklistStatus = "ok" if present else "missing" if required else "not_required"
    evidence = marker.evidence if present else None
    safe_evidence = None if evidence is None else evidence.model_copy(update={
        "quote": masked_text[evidence.start:evidence.end],
    })
    return ChecklistItem(
        category=category, status=status, comment=CHECKLIST_COMMENTS[category][status],
        evidence=safe_evidence,
    )
