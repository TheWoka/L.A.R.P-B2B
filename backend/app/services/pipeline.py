"""Конвейер проверки: исходный текст и mapping остаются только в памяти."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import monotonic

import yaml

from app.core.config import get_settings
from app.core.version import APP_VERSION
from app.schemas import AuditInfo, DocPurpose, ExtractedDocument, Report, ReportText, Rule, Stage
from app.services.detection.detect import detect
from app.services.llm import client
from app.services.llm.client import CallBudget, llm_call_budget
from app.services.markers.find_markers import find_markers
from app.services.masking.masker import mask, mask_preserving_length
from app.services.report.builder import build_report
from app.services.rules.engine import RuleContext, evaluate
from app.services.rules.loader import load_rules


@dataclass
class AuditResult:
    report: Report
    report_text: ReportText


def _rules_path(filename: str) -> Path:
    """Первый существующий файл в томе Docker или каталоге репозитория."""
    repo_root = Path(__file__).resolve().parents[3]
    for path in (Path("/data/rules") / filename, repo_root / "data" / "rules" / filename):
        if path.is_file():
            return path
    raise FileNotFoundError(f"{filename} не найден в /data/rules и data/rules")


def _version_hash(data: object) -> str:
    """Первые 12 hex SHA-256 канонического JSON из контракта."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()[:12]


def build_audit(source_bytes: bytes, rules: list[Rule], budget: CallBudget) -> AuditInfo:
    """Фиксирует версии входов и фактический счётчик обращений к LLM без ПДн."""
    with _rules_path("dictionaries.yaml").open(encoding="utf-8") as stream:
        dictionaries = yaml.safe_load(stream)
    enabled = client.is_llm_enabled()
    return AuditInfo(
        document_sha256=sha256(source_bytes).hexdigest(),
        ruleset_version=_version_hash([
            rule.model_dump(mode="json") for rule in rules if rule.enabled
        ]),
        dictionaries_version=_version_hash(dictionaries),
        app_version=APP_VERSION,
        detectors=["regex", f"ner:natasha-{version('natasha')}", "dict"],
        llm_provider="openai_compatible" if enabled else "mock",
        llm_model=get_settings().llm_model if enabled else None,
        llm_calls=budget.used,
    )


def run_audit(
    doc: ExtractedDocument, purpose: DocPurpose, source_bytes: bytes,
    on_stage: Callable[[Stage, int], None] | None = None,
    rules: list[Rule] | None = None,
) -> AuditResult:
    """Проверяет переданные правила; без них читает YAML. Callback отмечает стадии."""
    started = monotonic()
    if on_stage is not None:
        on_stage(Stage.detecting, 30)
    entities = detect(doc)
    markers = find_markers(doc)
    if on_stage is not None:
        on_stage(Stage.masking, 55)
    masked = mask(doc.text, entities)
    text = mask_preserving_length(doc.text, entities)
    if on_stage is not None:
        on_stage(Stage.checking, 70)
    if rules is None:
        rules = load_rules(_rules_path("rules.yaml"))
    ctx = RuleContext(
        doc=doc, masked_text=masked.masked_text,
        entities=entities, markers=markers, purpose=purpose,
    )
    with llm_call_budget() as budget:
        violations = evaluate(rules, ctx)
    if on_stage is not None:
        on_stage(Stage.reporting, 90)
    audit = build_audit(source_bytes, rules, budget)
    llm_used = audit.llm_provider == "openai_compatible" and audit.llm_calls > 0
    report = build_report(ctx, violations, int((monotonic() - started) * 1000), llm_used, audit)
    segments = [segment.model_copy(update={
        "text": text[segment.offset:segment.offset + len(segment.text)],
    }) for segment in doc.segments]
    return AuditResult(report=report, report_text=ReportText(text=text, segments=segments))
