"""Сборщик отчёта на выдуманных данных без БД, Redis, сети и вызовов LLM."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from app.schemas import (
    AuditInfo,
    DocPurpose,
    Entity,
    EntityCategory,
    EntityType,
    Evidence,
    ExtractedDocument,
    LawRef,
    Marker,
    MarkerType,
    Report,
    RuleContext,
    Severity,
    TextSegment,
    Violation,
)
from app.services.masking.masker import mask, mask_preserving_length, mask_value
from app.services.report.builder import TEXT_REQUEST_FILENAME, build_report

VALUES: dict[EntityType, str] = {
    EntityType.PERSON: "Петров Пётр Петрович",
    EntityType.PASSPORT: "0000 000000",
    EntityType.SNILS: "123-456-789 00",
    EntityType.INN: "000000000000",
    EntityType.PHONE: "+7 (000) 000-00-00",
    EntityType.EMAIL: "demo@example.test",
    EntityType.ADDRESS: "Учебная улица, дом 0",
    EntityType.BIRTH_DATE: "01.01.1900",
    EntityType.CARD: "0000000000000000",
    EntityType.BANK_ACCOUNT: "00000000000000000000",
    EntityType.SPECIAL_CATEGORY: "Учебные сведения о здоровье",
    EntityType.TRADE_SECRET: "Учебная маржа 17%",
    EntityType.FINANCIAL: "Учебная сумма 100 руб.",
}
A = MarkerType.approval_sheet
C = MarkerType.classification_mark
R = MarkerType.retention_period


def _entity(kind: EntityType, value: str, start: int) -> Entity:
    category = {
        EntityType.SPECIAL_CATEGORY: EntityCategory.pd_special,
        EntityType.TRADE_SECRET: EntityCategory.trade_secret,
        EntityType.FINANCIAL: EntityCategory.financial,
        EntityType.BANK_ACCOUNT: EntityCategory.financial,
    }.get(kind, EntityCategory.pd)
    masked = (
        mask_value(value) if category in {EntityCategory.pd, EntityCategory.pd_special} else value
    )
    return Entity(
        type=kind, category=category, start=start, end=start + len(value),
        page=3, section="Учебный раздел", value_masked=masked,
        confidence=0.99, detector="regex",
    )


def _context(
    purpose: DocPurpose = DocPurpose.internal, kinds: tuple[EntityType, ...] = (),
    present: tuple[MarkerType, ...] = (), filename: str = "contract.docx",
) -> RuleContext:
    text = "Учебный документ.\n"
    entities: list[Entity] = []
    for kind in kinds:
        value = VALUES[kind]
        entities.append(_entity(kind, value, len(text)))
        text += value + "\n"
    doc = ExtractedDocument(
        document_id="synthetic-report-document", filename=filename, text=text, pages=3,
        segments=[TextSegment(id=0, text=text, offset=0, page=3, section="Учебный раздел")],
    )
    return RuleContext(
        doc=doc, entities=entities, masked_text=mask(text, entities).masked_text,
        markers=[Marker(type=kind, present=kind in present) for kind in MarkerType],
        purpose=purpose,
    )


def _audit() -> AuditInfo:
    return AuditInfo(
        document_sha256="0" * 64, ruleset_version="1" * 12,
        dictionaries_version="2" * 12, app_version="0.1.0",
        detectors=["regex", "ner:natasha-1.6.0"], llm_provider="mock", llm_model=None,
    )


def _violation(rule_id: str, severity: Severity) -> Violation:
    return Violation(
        rule_id=rule_id, title="Учебное нарушение", severity=severity,
        law_refs=[LawRef(law="Учебная политика", article="п. 1")], evidence=[],
        recommendation="Проверьте учебный документ", source="rule",
    )


def _approval_context() -> RuleContext:
    name = VALUES[EntityType.PERSON]
    quote = f"Согласовано: {name}"
    text = f"Учебное введение.\n{quote}\nУчебное продолжение."
    entity = _entity(EntityType.PERSON, name, text.index(name))
    start = text.index(quote)
    evidence = Evidence(
        quote=quote, start=start, end=start + len(quote), page=3, section="Учебный раздел",
    )
    return RuleContext(
        doc=ExtractedDocument(
            document_id="synthetic-approval", filename="contract.docx", text=text, pages=3,
            segments=[TextSegment(id=0, text=text, offset=0, page=3, section="Учебный раздел")],
        ),
        entities=[entity], markers=[Marker(type=A, present=True, evidence=evidence)],
        masked_text=mask(text, [entity]).masked_text, purpose=DocPurpose.internal,
    )


@pytest.mark.parametrize(("severities", "expected"), [
    ((Severity.yellow, Severity.red), "red"),
    ((Severity.yellow,), "yellow"),
    ((), "green"),
])
def test_overall_depends_only_on_violations(
    severities: tuple[Severity, ...], expected: str,
) -> None:
    violations = [
        _violation(f"DEMO_{index}", severity) for index, severity in enumerate(severities)
    ]
    report = build_report(_context(), violations, 17, False, _audit())
    assert report.overall == expected
    assert report.checklist[0].status == "missing"
    assert len(report.violations) == len(violations)


def test_violations_are_sorted_without_adding_duplicates_or_mutating_input() -> None:
    violations = [
        _violation("Y_B", Severity.yellow), _violation("R_B", Severity.red),
        _violation("Y_A", Severity.yellow), _violation("R_A", Severity.red),
    ]
    original = [item.model_dump() for item in violations]
    report = build_report(_context(), violations, 17, False, _audit())
    ids = [item.rule_id for item in report.violations]
    assert ids == ["R_A", "R_B", "Y_A", "Y_B"]
    assert len(ids) == len(set(ids))
    assert [item.model_dump() for item in violations] == original
    assert report.violations == [violations[3], violations[1], violations[2], violations[0]]


def test_stats_include_financial_and_entities_are_sorted_without_mutation() -> None:
    ctx = _context(kinds=(EntityType.PERSON, EntityType.SNILS,
                          EntityType.PERSON, EntityType.FINANCIAL))
    expected_entities = list(ctx.entities)
    ctx.entities.reverse()
    original = ctx.model_dump()
    report = build_report(ctx, [], 17, False, _audit())
    assert report.stats == {"FINANCIAL": 1, "PERSON": 2, "SNILS": 1}
    assert list(report.stats) == ["FINANCIAL", "PERSON", "SNILS"]
    assert report.entities == expected_entities
    assert report.entities[-1].value_masked == VALUES[EntityType.FINANCIAL]
    assert ctx.model_dump() == original


@pytest.mark.parametrize(("purpose", "kinds", "present", "expected"), [
    (DocPurpose.internal, (), (), ("missing", "not_required", "not_required")),
    (DocPurpose.contract, (), (), ("missing", "not_required", "not_required")),
    (DocPurpose.publication, (), (), ("not_required", "not_required", "not_required")),
    (DocPurpose.international, (), (), ("not_required", "not_required", "not_required")),
    (DocPurpose.internal, (EntityType.SNILS,), (), ("missing", "missing", "missing")),
    (DocPurpose.contract, (EntityType.PASSPORT,), (), ("missing", "missing", "missing")),
    (DocPurpose.publication, (EntityType.SNILS,), (), ("not_required", "not_required", "missing")),
    (DocPurpose.international, (EntityType.SNILS,), (), ("not_required", "missing", "missing")),
    (DocPurpose.internal, (EntityType.TRADE_SECRET,), (),
     ("missing", "missing", "not_required")),
    (DocPurpose.contract, (EntityType.TRADE_SECRET,), (),
     ("missing", "missing", "not_required")),
    (DocPurpose.publication, (EntityType.TRADE_SECRET,), (),
     ("not_required", "not_required", "not_required")),
    (DocPurpose.international, (EntityType.TRADE_SECRET,), (),
     ("not_required", "missing", "not_required")),
    (DocPurpose.internal, (EntityType.PERSON,), (), ("missing", "not_required", "not_required")),
    (DocPurpose.contract, (EntityType.FINANCIAL,), (),
     ("missing", "not_required", "not_required")),
    (DocPurpose.international, (EntityType.SPECIAL_CATEGORY,), (),
     ("not_required", "missing", "missing")),
    (DocPurpose.publication, (EntityType.SNILS,), (A, C, R), ("ok", "ok", "ok")),
    (DocPurpose.internal, (), (A, C, R), ("ok", "ok", "ok")),
    (DocPurpose.contract, (EntityType.SNILS,), (A,), ("ok", "missing", "missing")),
    (DocPurpose.international, (EntityType.SNILS,), (C,), ("not_required", "ok", "missing")),
    (DocPurpose.publication, (EntityType.SNILS,), (R,), ("not_required", "not_required", "ok")),
    (DocPurpose.internal, (), (R,), ("missing", "not_required", "ok")),
])
def test_checklist_status_table(
    purpose: DocPurpose, kinds: tuple[EntityType, ...], present: tuple[MarkerType, ...],
    expected: tuple[str, str, str],
) -> None:
    report = build_report(_context(purpose, kinds, present), [], 17, False, _audit())
    assert [item.category for item in report.checklist] == [
        "approvals", "classification", "retention",
    ]
    assert tuple(item.status for item in report.checklist) == expected
    assert all(item.evidence is None for item in report.checklist)
    assert all(item.comment.strip() and re.search("[А-Яа-яЁё]", item.comment)
               for item in report.checklist)


@pytest.mark.parametrize("kind", [
    EntityType.PASSPORT, EntityType.SNILS, EntityType.INN,
    EntityType.BIRTH_DATE, EntityType.SPECIAL_CATEGORY,
])
@pytest.mark.parametrize(("purpose", "expected"), [
    (DocPurpose.internal, ("missing", "missing", "missing")),
    (DocPurpose.publication, ("not_required", "not_required", "missing")),
])
def test_each_heavy_pd_type_requires_retention_and_respects_publication(
    kind: EntityType, purpose: DocPurpose, expected: tuple[str, str, str],
) -> None:
    report = build_report(_context(purpose, (kind,)), [], 0, False, _audit())
    assert tuple(item.status for item in report.checklist) == expected


@pytest.mark.parametrize("kind", [
    EntityType.PERSON, EntityType.PHONE, EntityType.EMAIL, EntityType.ADDRESS,
    EntityType.CARD, EntityType.BANK_ACCOUNT, EntityType.FINANCIAL,
])
def test_other_entity_types_do_not_require_classification_or_retention(kind: EntityType) -> None:
    report = build_report(_context(kinds=(kind,)), [], 0, False, _audit())
    assert [item.status for item in report.checklist] == ["missing", "not_required", "not_required"]


@pytest.mark.parametrize(("purpose", "expected"), [
    (DocPurpose.internal, ("missing", "missing", "missing")),
    (DocPurpose.contract, ("missing", "missing", "missing")),
    (DocPurpose.publication, ("not_required", "not_required", "missing")),
    (DocPurpose.international, ("not_required", "missing", "missing")),
])
def test_empty_marker_list_is_treated_as_absent(
    purpose: DocPurpose, expected: tuple[str, str, str],
) -> None:
    ctx = _context(purpose, (EntityType.SNILS,))
    ctx.markers = []
    report = build_report(ctx, [], 0, False, _audit())
    assert tuple(item.status for item in report.checklist) == expected
    assert all(item.evidence is None for item in report.checklist)


@pytest.mark.parametrize("kind", [
    MarkerType.consent_clause, MarkerType.processing_assignment, MarkerType.kii_mention,
])
def test_other_marker_types_do_not_replace_checklist_markers(kind: MarkerType) -> None:
    report = build_report(_context(present=(kind,)), [], 0, False, _audit())
    assert [item.status for item in report.checklist] == ["missing", "not_required", "not_required"]


def test_all_checklist_evidence_is_masked_once_and_keeps_coordinates_without_mutation() -> None:
    ctx = _approval_context()
    source = ctx.markers[0].evidence
    assert source is not None
    ctx.markers = [Marker(type=kind, present=True, evidence=source) for kind in (A, C, R)]
    original = ctx.model_dump()
    expected = mask_preserving_length(ctx.doc.text, ctx.entities)[source.start:source.end]
    with patch("app.services.report.builder.mask_preserving_length",
               wraps=mask_preserving_length) as masker:
        report = build_report(ctx, [], 17, False, _audit())
    masker.assert_called_once_with(ctx.doc.text, ctx.entities)
    for item in report.checklist:
        evidence = item.evidence
        assert evidence is not None and evidence is not source
        assert evidence.quote == expected == "Согласовано: П█████ П███ П███████"
        assert "Петров" not in evidence.quote and "[PERSON_1]" not in evidence.quote
        assert len(evidence.quote) == evidence.end - evidence.start
        assert (evidence.start, evidence.end) == (source.start, source.end)
        assert (evidence.page, evidence.section) == (3, "Учебный раздел")
    assert ctx.model_dump() == original
    assert source.quote == "Согласовано: Петров Пётр Петрович"


def test_absent_marker_cannot_expose_its_stale_evidence() -> None:
    ctx = _approval_context()
    ctx.markers[0].present = False
    report = build_report(ctx, [], 0, False, _audit())
    assert report.checklist[0].status == "missing"
    assert report.checklist[0].evidence is None
    assert "Петров" not in report.model_dump_json()


@pytest.mark.parametrize(("filename", "expected"), [
    ("Текстовый запрос", None), ("contract.docx", "contract.docx"),
])
def test_filename_for_text_request_and_uploaded_document(
    filename: str, expected: str | None,
) -> None:
    report = build_report(_context(filename=filename), [], 0, False, _audit())
    assert TEXT_REQUEST_FILENAME == "Текстовый запрос"
    assert report.filename == expected


@pytest.mark.parametrize(("processing_ms", "llm_used"), [(0, False), (247, True)])
def test_report_schema_identifiers_time_and_audit_are_preserved(
    processing_ms: int, llm_used: bool,
) -> None:
    ctx = _context(purpose=DocPurpose.international)
    audit = _audit()
    if llm_used:
        audit = audit.model_copy(update={
            "llm_provider": "openai_compatible", "llm_model": "synthetic-model", "llm_calls": 3,
        })
    original_audit = audit.model_dump()
    before = datetime.now(UTC)
    report = build_report(ctx, [], processing_ms, llm_used, audit)
    other = build_report(ctx, [], processing_ms, llm_used, audit)
    assert re.fullmatch("[0-9a-f]{32}", report.report_id)
    assert UUID(hex=report.report_id).version == 4
    assert report.report_id != other.report_id
    assert report.document_id == ctx.doc.document_id and report.purpose == ctx.purpose
    assert before <= report.created_at <= datetime.now(UTC)
    assert report.created_at.tzinfo == UTC
    assert report.processing_ms == processing_ms and report.llm_used is llm_used
    assert report.redacted_available is False
    assert report.audit is audit and audit.model_dump() == original_audit
    assert Report.model_validate(report.model_dump(mode="json")) == report
    excluded = {"report_id", "created_at"}
    assert report.model_dump(exclude=excluded) == other.model_dump(exclude=excluded)


def test_empty_document_has_empty_stats_and_entities() -> None:
    ctx = _context()
    ctx.doc.text = ""
    ctx.doc.segments = []
    ctx.masked_text = ""
    ctx.markers = []
    report = build_report(ctx, [], 0, False, _audit())
    assert report.stats == {} and report.entities == []
    assert report.violations == [] and report.overall == "green"
    assert len(report.checklist) == 3


def test_frontend_mock_matches_report_schema(repo_root: Path) -> None:
    path = repo_root / "frontend/src/mocks/report.example.json"
    if not path.exists():
        pytest.skip("Мок фронтенда недоступен в образе api")
    report = Report.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(report.checklist) == 3


def test_logs_never_contain_original_person_or_quote(caplog: pytest.LogCaptureFixture) -> None:
    ctx = _approval_context()
    with caplog.at_level(logging.DEBUG):
        build_report(ctx, [], 0, False, _audit())
    assert "Петров" not in caplog.text
    assert VALUES[EntityType.PERSON] not in caplog.text
    assert ctx.doc.text not in caplog.text
