"""Pydantic-модели из docs/contracts.md. Менять — только через тимлида.

Импортируйте отсюда: `from app.schemas import Report, Entity`.
"""

from app.schemas.document import DocPurpose, ExtractedDocument, TextSegment
from app.schemas.entity import Entity, EntityCategory, EntityType, MaskResult, MaskSpan
from app.schemas.health import HealthStatus
from app.schemas.llm_log import LlmCall
from app.schemas.marker import Evidence, Marker, MarkerType
from app.schemas.metrics import Metrics, TypeMetrics
from app.schemas.report import AuditInfo, ChecklistItem, Report, ReportText
from app.schemas.rule import LawRef, Rule, RuleContext, Severity, Violation
from app.schemas.task import QueryRequest, Stage, TaskCreated, TaskInfo, TaskStatus

__all__ = [
    "AuditInfo",
    "ChecklistItem",
    "DocPurpose",
    "Entity",
    "EntityCategory",
    "EntityType",
    "Evidence",
    "ExtractedDocument",
    "HealthStatus",
    "LawRef",
    "LlmCall",
    "Marker",
    "MarkerType",
    "MaskResult",
    "MaskSpan",
    "Metrics",
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
    "TypeMetrics",
    "Violation",
]
