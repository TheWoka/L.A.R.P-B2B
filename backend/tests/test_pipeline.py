"""Конвейер и след проверки без БД, Redis и сетевых запросов."""

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
import yaml

from app.core.config import Settings
from app.core.version import APP_VERSION
from app.main import app
from app.schemas import (
    AuditInfo,
    ChecklistItem,
    DocPurpose,
    EntityType,
    ExtractedDocument,
    Report,
    Rule,
    RuleContext,
    Stage,
    TextSegment,
    Violation,
)
from app.services import pipeline
from app.services.llm.client import CallBudget
from app.services.rules.loader import load_rules

# Только выдуманные данные.
NAME = "Иванов Иван Иванович"
PHONE = "+7 (912) 345-67-89"
TEXT = f"Контакт: {NAME}, телефон {PHONE}.\nСебестоимость изделия 38 400 руб."


def make_report(
    ctx: RuleContext, violations: list[Violation], processing_ms: int,
    llm_used: bool, audit: AuditInfo,
) -> Report:
    """Тестовая замена T-016, использующая настоящую схему Report."""
    return Report(
        report_id=uuid4().hex, document_id=ctx.doc.document_id,
        filename=None if ctx.doc.filename == "Текстовый запрос" else ctx.doc.filename,
        purpose=ctx.purpose, overall="red" if violations else "green",
        created_at=datetime.now(UTC), processing_ms=processing_ms,
        stats=dict(Counter(entity.type.value for entity in ctx.entities)),
        entities=ctx.entities, violations=violations, llm_used=llm_used, audit=audit,
        checklist=[ChecklistItem(category=category, status="not_required", comment="Тест")
                   for category in ("approvals", "classification", "retention")],
    )


def make_doc(text: str = TEXT) -> ExtractedDocument:
    return ExtractedDocument(
        document_id="synthetic-document", filename="synthetic.docx", text=text,
        segments=[TextSegment(id=0, text=text, offset=0, page=1, section="Контакты")],
    )


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, llm_provider="mock")
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline.client, "get_settings", lambda: settings)


def test_run_audit_stages_and_masking(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    builder = Mock(side_effect=make_report)
    evaluator = Mock(wraps=pipeline.evaluate)
    monkeypatch.setattr(pipeline, "build_report", builder)
    monkeypatch.setattr(pipeline, "evaluate", evaluator)
    stages: list[tuple[Stage, int]] = []
    doc = make_doc()
    with caplog.at_level(logging.INFO):
        result = pipeline.run_audit(doc, DocPurpose.publication, b"original file bytes",
                                    lambda stage, progress: stages.append((stage, progress)))
    assert stages == [(Stage.detecting, 30), (Stage.masking, 55),
                      (Stage.checking, 70), (Stage.reporting, 90)]
    ctx = evaluator.call_args.args[1]
    assert NAME not in ctx.masked_text and PHONE not in ctx.masked_text
    assert "[PERSON_1]" in ctx.masked_text and "[PHONE_1]" in ctx.masked_text
    assert "38 400 руб." in ctx.masked_text
    assert len(result.report_text.text) == len(doc.text)
    assert NAME not in result.report_text.text and PHONE not in result.report_text.text
    assert NAME not in result.report_text.model_dump_json()
    assert PHONE not in result.report_text.model_dump_json()
    assert result.report_text.segments[0].text == result.report_text.text
    assert (result.report_text.segments[0].offset, result.report_text.segments[0].page,
            result.report_text.segments[0].section) == (0, 1, "Контакты")
    assert doc.segments[0].text == TEXT
    assert {entity.type for entity in ctx.entities} >= {
        EntityType.PERSON, EntityType.PHONE, EntityType.TRADE_SECRET,
    }
    assert len(ctx.markers) == 6
    assert not result.report.llm_used and result.report.processing_ms >= 0
    assert result.report.audit.document_sha256 == sha256(b"original file bytes").hexdigest()
    assert NAME not in caplog.text and PHONE not in caplog.text
    assert "mapping" not in result.report.model_dump()
    assert builder.call_args.args[4] == result.report.audit


@pytest.mark.parametrize("text", ["", "Документ без персональных данных"])
def test_run_audit_without_callback(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)
    result = pipeline.run_audit(make_doc(text), DocPurpose.internal, text.encode("utf-8"))
    assert result.report_text.text == text
    assert result.report.entities == []


def test_masking_segments_across_entity_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)
    doc = make_doc()
    split = doc.text.index(NAME) + 3
    doc.segments = [TextSegment(id=1, offset=0, text=doc.text[:split], page=1),
                    TextSegment(id=2, offset=split, text=doc.text[split:], page=2)]
    result = pipeline.run_audit(doc, DocPurpose.internal, b"synthetic")
    joined = "".join(segment.text for segment in result.report_text.segments)
    assert joined == result.report_text.text
    for source, masked in zip(doc.segments, result.report_text.segments, strict=True):
        assert (source.id, source.offset, source.page) == (masked.id, masked.offset, masked.page)
        assert len(source.text) == len(masked.text)
    assert NAME not in result.report_text.model_dump_json()


def test_builder_stub_is_preserved() -> None:
    with pytest.raises(NotImplementedError, match="T-016"):
        pipeline.run_audit(make_doc(""), DocPurpose.internal, b"")


def test_audit_hashes(repo_root: Path) -> None:
    rules = load_rules(repo_root / "data/rules/rules.yaml")
    audit = pipeline.build_audit(TEXT.encode("utf-8"), rules, CallBudget(30))
    canonical = json.dumps([rule.model_dump(mode="json") for rule in rules if rule.enabled],
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    dictionaries = yaml.safe_load((repo_root / "data/rules/dictionaries.yaml").read_text("utf-8"))
    canonical_dict = json.dumps(dictionaries, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False)
    assert audit.document_sha256 == sha256(TEXT.encode("utf-8")).hexdigest()
    assert audit.ruleset_version == sha256(canonical.encode("utf-8")).hexdigest()[:12]
    assert audit.dictionaries_version == sha256(canonical_dict.encode("utf-8")).hexdigest()[:12]
    assert audit.app_version == APP_VERSION == app.version
    assert audit.detectors == ["regex", "ner:natasha-1.6.0", "dict"]
    assert (audit.llm_provider, audit.llm_model, audit.llm_calls) == ("mock", None, 0)
    changed = rules[0].model_copy(update={"title": "Изменённое правило"})
    assert pipeline.build_audit(b"", [changed, *rules[1:]], CallBudget(30)).ruleset_version != (
        audit.ruleset_version
    )
    disabled = changed.model_copy(update={"enabled": False})
    assert pipeline.build_audit(b"", [*rules, disabled], CallBudget(30)).ruleset_version == (
        audit.ruleset_version
    )


def test_audit_empty_rules_and_source() -> None:
    audit = pipeline.build_audit(b"", [], CallBudget(0))
    assert audit.document_sha256 == sha256(b"").hexdigest()
    assert audit.ruleset_version == sha256(b"[]").hexdigest()[:12]


@pytest.mark.parametrize("calls", [0, 2])
def test_real_provider_metadata_and_llm_used(
    monkeypatch: pytest.MonkeyPatch, calls: int,
) -> None:
    settings = Settings(_env_file=None, llm_provider="openai_compatible",
                        llm_base_url="https://example.invalid/v1", llm_model="test-model")
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline.client, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "build_report", make_report)

    def evaluate(rules: list[Rule], ctx: RuleContext) -> list[Violation]:
        for _ in range(calls):
            pipeline.client.charge_llm_call()
        return []

    monkeypatch.setattr(pipeline, "evaluate", evaluate)
    result = pipeline.run_audit(make_doc(""), DocPurpose.internal, b"")
    assert result.report.audit.llm_provider == "openai_compatible"
    assert result.report.audit.llm_model == "test-model"
    assert result.report.audit.llm_calls == calls
    assert result.report.llm_used == (calls > 0)


def test_unconfigured_provider_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, llm_provider="openai_compatible", llm_model="unused")
    monkeypatch.setattr(pipeline.client, "get_settings", lambda: settings)
    audit = pipeline.build_audit(b"", [], CallBudget(30))
    assert (audit.llm_provider, audit.llm_model) == ("mock", None)


def test_rules_path_fallback_and_missing(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda path: path == repo_root / "data/rules/rules.yaml")
    assert pipeline._rules_path("rules.yaml") == repo_root / "data/rules/rules.yaml"
    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        pipeline._rules_path("missing.yaml")


def test_rules_path_prefers_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    assert pipeline._rules_path("rules.yaml") == Path("/data/rules/rules.yaml")


def test_version_hash_is_canonical() -> None:
    assert pipeline._version_hash({"a": 1, "б": 2}) == pipeline._version_hash({"б": 2, "a": 1})
    assert pipeline._version_hash({"a": 1}) != pipeline._version_hash({"a": 2})
