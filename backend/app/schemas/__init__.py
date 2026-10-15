"""Pydantic-модели из docs/contracts.md. Менять — только через тимлида.

Импортируйте отсюда: `from app.schemas import Report, Entity`.
"""

from app.schemas.document import DocPurpose, ExtractedDocument, TextSegment
from app.schemas.entity import Entity, EntityCategory, EntityType, MaskResult, MaskSpan
from app.schemas.health import HealthStatus
from app.schemas.marker import Evidence, Marker, MarkerType
from app.schemas.report import ChecklistItem, Report, ReportText
from app.schemas.rule import LawRef, Rule, RuleContext, Severity, Violation
from app.schemas.task import QueryRequest, Stage, TaskCreated, TaskInfo, TaskStatus

__all__ = [
    "ChecklistItem",
    "DocPurpose",
    "Entity",
    "EntityCategory",
    "EntityType",
    "Evidence",
    "ExtractedDocument",
    "HealthStatus",
    "LawRef",
    "Marker",
    "MarkerType",
    "MaskResult",
    "MaskSpan",
    "QueryRequest",
    "Report",
    "ReportText",
    "Rule",
    "RuleContext",
    "Severity",
    "Stage",
    "TaskCreated",
    "TaskInfo",
    "TaskStatus",
    "TextSegment",
    "Violation",
]
