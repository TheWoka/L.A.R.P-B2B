"""Проверки T-004 на выдуманных данных без БД, сети и вызовов LLM."""

import logging
from pathlib import Path

import pytest
import yaml

from app.schemas import (
    DocPurpose,
    Entity,
    EntityCategory,
    EntityType,
    Evidence,
    ExtractedDocument,
    LawRef,
    Marker,
    MarkerType,
    Rule,
    RuleContext,
    Severity,
    TextSegment,
)
from app.services.rules.engine import evaluate, validate_condition
from app.services.rules.loader import load_rules

PERSON = {"type": "entity_present", "categories": ["pd"], "types": ["PERSON"]}
TRADE_SECRET = {"type": "entity_present", "categories": ["trade_secret"]}
APPROVAL = {"type": "marker_present", "marker": "approval_sheet"}
CLASSIFICATION = {"type": "marker_present", "marker": "classification_mark"}


def _entity(
    text: str, original: str, masked: str, kind: EntityType, category: EntityCategory
) -> Entity:
    start = text.index(original)
    return Entity(
        type=kind, category=category, start=start, end=start + len(original),
        page=1, section="Демо", value_masked=masked, confidence=0.99, detector="regex",
    )


@pytest.fixture
def rule_context() -> RuleContext:
    text = "Тестов Тест; demo@example.test; маржа 17%. Согласовано."
    entities = [
        _entity(text, "Тестов Тест", "Т█████ Т███", EntityType.PERSON, EntityCategory.pd),
        _entity(
            text, "demo@example.test", "d███@e██████.t███", EntityType.EMAIL, EntityCategory.pd,
        ),
        _entity(
            text, "маржа 17%", "маржа 17%", EntityType.TRADE_SECRET, EntityCategory.trade_secret,
        ),
    ]
    start = text.index("Согласовано.")
    approval = Evidence(quote="Согласовано.", start=start, end=len(text), page=1, section="Демо")
    doc = ExtractedDocument(
        document_id="synthetic-document", filename="demo.txt", text=text, pages=1,
        segments=[TextSegment(id=1, text=text, offset=0, page=1, section="Демо")],
    )
    return RuleContext(
        doc=doc,
        masked_text="[PERSON_1]; [EMAIL_1]; маржа 17%. Согласовано.",
        entities=entities,
        markers=[
            Marker(type=MarkerType.approval_sheet, present=True, evidence=approval),
            Marker(type=MarkerType.classification_mark, present=False),
        ],
        purpose=DocPurpose.publication,
    )


def _rule(
    condition: dict[str, object],
    rule_id: str = "SYNTHETIC_RULE",
    *,
    severity: Severity = Severity.red,
    enabled: bool = True,
    applies_to: list[DocPurpose] | None = None,
) -> Rule:
    return Rule(
        id=rule_id, title="Синтетическая проверка", description="Учебное правило",
        severity=severity, law_refs=[LawRef(law="Демо-политика", article="п. 1")],
        condition=condition, recommendation="Проверьте документ", enabled=enabled,
        applies_to=[] if applies_to is None else applies_to,
    )


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"type": "entity_present", "categories": ["pd"], "min_count": 2}, True),
        ({"type": "entity_present", "categories": ["pd"], "min_count": 3}, False),
        ({**PERSON, "min_count": 1}, True),
        ({**PERSON, "min_count": 2}, False),
        ({**PERSON, "types": ["EMAIL"]}, True),
        ({**PERSON, "types": ["PASSPORT"]}, False),
        ({**PERSON, "categories": ["trade_secret"]}, False),
        ({"type": "marker_absent", "marker": "classification_mark"}, True),
        ({"type": "marker_absent", "marker": "approval_sheet"}, False),
        ({"type": "marker_absent", "marker": "retention_period"}, True),
        (APPROVAL, True),
        (CLASSIFICATION, False),
        ({"type": "marker_present", "marker": "retention_period"}, False),
        ({"type": "purpose_is", "purposes": ["publication"]}, True),
        ({"type": "purpose_is", "purposes": ["contract"]}, False),
        ({"all": [PERSON, {"not": CLASSIFICATION}]}, True),
        ({"all": [PERSON, CLASSIFICATION]}, False),
        ({"any": [CLASSIFICATION, TRADE_SECRET]}, True),
        ({"any": [CLASSIFICATION, {"type": "purpose_is", "purposes": ["contract"]}]}, False),
        ({"not": CLASSIFICATION}, True),
        ({"not": PERSON}, False),
        ({"not": {"not": PERSON}}, True),
        ({"type": "entity_present", "categories": []}, False),
        ({**PERSON, "types": []}, False),
        ({"type": "purpose_is", "purposes": []}, False),
    ],
)
def test_condition_truth_table(
    rule_context: RuleContext, condition: dict[str, object], expected: bool
) -> None:
    assert validate_condition(condition) is None
    violations = evaluate([_rule(condition)], rule_context)
    assert bool(violations) is expected


def test_entity_evidence_uses_masked_values_and_preserves_location(
    rule_context: RuleContext,
) -> None:
    rule = _rule({"type": "entity_present", "categories": ["pd"]})
    violation, = evaluate([rule], rule_context)
    assert len(violation.evidence) == 2
    for entity, evidence in zip(rule_context.entities[:2], violation.evidence, strict=True):
        assert evidence.quote == entity.value_masked
        assert rule_context.doc.text[entity.start:entity.end] not in evidence.quote
        assert (evidence.start, evidence.end) == (entity.start, entity.end)
        assert (evidence.page, evidence.section) == (entity.page, entity.section)
        assert len(evidence.quote) == evidence.end - evidence.start
    assert violation.rule_id == rule.id
    assert violation.title == rule.title
    assert violation.severity == rule.severity
    assert violation.law_refs == rule.law_refs
    assert violation.recommendation == rule.recommendation
    assert violation.source == "rule"


def test_entity_condition_with_no_entities(rule_context: RuleContext) -> None:
    ctx = rule_context.model_copy(update={"entities": []})
    assert evaluate([_rule(PERSON)], ctx) == []


def test_all_combines_evidence_from_each_part(rule_context: RuleContext) -> None:
    violation, = evaluate([_rule({"all": [PERSON, APPROVAL]})], rule_context)
    assert [item.quote for item in violation.evidence] == ["Т█████ Т███", "Согласовано."]


def test_any_includes_all_true_parts_and_excludes_false_parts(rule_context: RuleContext) -> None:
    false_part = {"type": "entity_present", "categories": ["pd"], "min_count": 3}
    condition = {"any": [false_part, TRADE_SECRET, APPROVAL]}
    violation, = evaluate([_rule(condition)], rule_context)
    assert [item.quote for item in violation.evidence] == ["маржа 17%", "Согласовано."]


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "marker_absent", "marker": "classification_mark"},
        {"type": "purpose_is", "purposes": ["publication"]},
        {"not": {"all": [PERSON, CLASSIFICATION]}},
        {"not": {"not": PERSON}},
    ],
)
def test_conditions_without_evidence(
    rule_context: RuleContext, condition: dict[str, object]
) -> None:
    violation, = evaluate([_rule(condition)], rule_context)
    assert violation.evidence == []


def test_false_marker_is_absent_even_with_evidence(rule_context: RuleContext) -> None:
    ctx = rule_context.model_copy(deep=True)
    ctx.markers[0].present = False
    assert evaluate([_rule(APPROVAL)], ctx) == []
    violation, = evaluate([_rule({"type": "marker_absent", "marker": "approval_sheet"})], ctx)
    assert violation.evidence == []


def test_present_marker_without_evidence(rule_context: RuleContext) -> None:
    ctx = rule_context.model_copy(deep=True)
    ctx.markers[0].evidence = None
    violation, = evaluate([_rule(APPROVAL)], ctx)
    assert violation.evidence == []


def test_duplicate_marker_does_not_override_present_record(rule_context: RuleContext) -> None:
    ctx = rule_context.model_copy(deep=True)
    ctx.markers.insert(0, Marker(type=MarkerType.approval_sheet, present=False))
    violation, = evaluate([_rule(APPROVAL)], ctx)
    assert violation.evidence == [rule_context.markers[0].evidence]


def test_evidence_deduplicates_entity_and_marker_at_same_span(rule_context: RuleContext) -> None:
    ctx = rule_context.model_copy(deep=True)
    entity = ctx.entities[0]
    ctx.markers[0].evidence = Evidence(
        quote=entity.value_masked, start=entity.start, end=entity.end,
        page=entity.page, section=entity.section,
    )
    violation, = evaluate([_rule({"all": [PERSON, APPROVAL, PERSON]})], ctx)
    assert len(violation.evidence) == 1
    assert violation.evidence[0].quote == entity.value_masked


def test_evidence_sorts_and_deduplicates_before_limiting_to_ten(rule_context: RuleContext) -> None:
    values = [f"Запись{index:02d}" for index in range(12)]
    text = "\n".join(values)
    entities = [
        _entity(text, value, "З███████", EntityType.PERSON, EntityCategory.pd)
        for value in values
    ]
    ctx = rule_context.model_copy(update={
        "doc": ExtractedDocument(document_id="many-entities", filename="demo.txt", text=text,
                                 segments=[TextSegment(id=1, text=text, offset=0)]),
        "masked_text": "\n".join(f"[PERSON_{index}]" for index in range(12)),
        "entities": list(reversed(entities)) + [entities[0].model_copy()], "markers": [],
    })
    violation, = evaluate([_rule({"any": [PERSON, PERSON]})], ctx)
    spans = [(item.start, item.end) for item in violation.evidence]
    assert spans == [(entity.start, entity.end) for entity in entities[:10]]
    assert len(spans) == len(set(spans)) == 10


def test_applies_to_excludes_publication_rule_for_contract(rule_context: RuleContext) -> None:
    rule = _rule(PERSON, applies_to=[DocPurpose.publication])
    assert len(evaluate([rule], rule_context)) == 1
    contract = rule_context.model_copy(update={"purpose": DocPurpose.contract})
    assert evaluate([rule], contract) == []
    assert len(evaluate([_rule(PERSON)], contract)) == 1


@pytest.mark.parametrize("enabled", [True, False])
def test_enabled_filter(rule_context: RuleContext, enabled: bool) -> None:
    assert bool(evaluate([_rule(PERSON, enabled=enabled)], rule_context)) is enabled


def test_inactive_rules_are_not_evaluated(rule_context: RuleContext) -> None:
    rules = [
        _rule({"type": "unknown"}, enabled=False),
        _rule({"type": "unknown"}, applies_to=[DocPurpose.contract]),
    ]
    assert evaluate(rules, rule_context) == []


def test_severity_sort_is_stable_within_each_level(rule_context: RuleContext) -> None:
    rules = [
        _rule(PERSON, "YELLOW_1", severity=Severity.yellow),
        _rule(PERSON, "RED_1"),
        _rule(PERSON, "YELLOW_2", severity=Severity.yellow),
        _rule(PERSON, "RED_2"),
    ]
    assert [item.rule_id for item in evaluate(rules, rule_context)] == [
        "RED_1", "RED_2", "YELLOW_1", "YELLOW_2",
    ]


def test_llm_stub_returns_no_violation_and_logs_no_document(
    rule_context: RuleContext, caplog: pytest.LogCaptureFixture
) -> None:
    question = "Синтетический вопрос, который не нужно логировать"
    with caplog.at_level(logging.DEBUG, logger="app.services.rules.engine"):
        assert evaluate([_rule({"type": "llm", "question": question})], rule_context) == []
    assert any(record.levelno == logging.DEBUG for record in caplog.records)
    assert question not in caplog.text
    assert rule_context.doc.text not in caplog.text
    assert rule_context.masked_text not in caplog.text
    assert "Тестов Тест" not in caplog.text


@pytest.mark.parametrize(
    "condition",
    [
        {}, {"type": "unknown"}, {"type": "entity_presnt"}, {"type": None}, {"type": []},
        {"all": []}, {"any": []},
        {"all": PERSON}, {"any": None}, {"not": []}, {"not": None},
        {"all": [None]}, {"any": ["invalid"]}, {"all": [], "any": []},
        {**PERSON, "extra": True}, {**PERSON, "all": []},
        {"type": "entity_present"}, {**PERSON, "categories": "pd"},
        {**PERSON, "categories": [123]}, {**PERSON, "categories": ["unknown"]},
        {**PERSON, "types": None}, {**PERSON, "types": ["unknown"]},
        {**PERSON, "min_count": 0}, {**PERSON, "min_count": -1},
        {**PERSON, "min_count": True},
        {**PERSON, "min_count": "1"}, {**PERSON, "min_count": 1.5},
        {"type": "marker_present"}, {"type": "marker_absent", "marker": []},
        {"type": "marker_present", "marker": "unknown"},
        {"type": "purpose_is"}, {"type": "purpose_is", "purposes": "publication"},
        {"type": "purpose_is", "purposes": ["unknown"]},
        {"type": "llm"}, {"type": "llm", "question": None},
        {"type": "llm", "question": "  "},
        {"all": [CLASSIFICATION, {"type": "unknown"}]},
        {"any": [PERSON, {"type": "unknown"}]},
    ],
)
def test_invalid_condition_is_rejected_before_and_during_evaluation(
    rule_context: RuleContext, tmp_path: Path, condition: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        validate_condition(condition)
    with pytest.raises(ValueError, match="BROKEN_RULE"):
        evaluate([_rule(condition, "BROKEN_RULE")], rule_context)
    data = _rule(condition, "BROKEN_RULE").model_dump(mode="json")
    with pytest.raises(ValueError, match="Правило BROKEN_RULE:"):
        load_rules(_write_rules(tmp_path, [data]))


@pytest.mark.parametrize("operator", ["not", "all", "any"])
def test_cyclic_condition_is_rejected(
    rule_context: RuleContext, tmp_path: Path, operator: str
) -> None:
    condition: dict[str, object] = {}
    condition[operator] = condition if operator == "not" else [condition]
    with pytest.raises(ValueError, match="циклическую"):
        validate_condition(condition)
    with pytest.raises(ValueError, match="CYCLIC_RULE"):
        evaluate([_rule(condition, "CYCLIC_RULE")], rule_context)
    data = _rule(PERSON, "CYCLIC_RULE").model_dump(mode="json")
    data["condition"] = condition
    with pytest.raises(ValueError, match="Правило CYCLIC_RULE:"):
        load_rules(_write_rules(tmp_path, [data]))


@pytest.mark.parametrize("condition", [None, [], [PERSON], "invalid", True, 1])
def test_validate_condition_rejects_non_mapping_root(condition: object) -> None:
    with pytest.raises(ValueError, match="непустым словарём"):
        validate_condition(condition)


def test_validate_condition_checks_all_kinds_without_evaluation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    condition = {"all": [
        PERSON, APPROVAL, {"not": CLASSIFICATION},
        {"type": "marker_absent", "marker": "retention_period"},
        {"any": [PERSON, {"type": "purpose_is", "purposes": ["publication"]}]},
        {"type": "llm", "question": "Синтетический вопрос"},
    ]}
    with caplog.at_level(logging.DEBUG, logger="app.services.rules.engine"):
        assert validate_condition(condition) is None
    assert caplog.records == []


def test_validate_condition_rejects_indirect_cycle() -> None:
    condition: dict[str, object] = {}
    condition["all"] = [{"any": [{"not": condition}]}]
    with pytest.raises(ValueError, match="циклическую"):
        validate_condition(condition)


def test_validate_condition_rejects_excessive_depth() -> None:
    condition: dict[str, object] = PERSON
    for _ in range(2000):
        condition = {"not": condition}
    with pytest.raises(ValueError, match="глубину"):
        validate_condition(condition)


@pytest.mark.parametrize(
    "overrides", [{"enabled": False}, {"applies_to": ["contract"]}],
)
def test_load_validates_inactive_rules(tmp_path: Path, overrides: dict[str, object]) -> None:
    data = _rule({"type": "entity_presnt"}, "INACTIVE_RULE").model_dump(mode="json")
    data.update(overrides)
    with pytest.raises(ValueError, match="Правило INACTIVE_RULE: неизвестный тип"):
        load_rules(_write_rules(tmp_path, [data]))


def test_empty_rule_list(rule_context: RuleContext) -> None:
    assert evaluate([], rule_context) == []


def _write_rules(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_actual_rules(repo_root: Path) -> None:
    rules = load_rules(repo_root / "data/rules/rules.yaml")
    rule_ids = {rule.id for rule in rules}
    assert len(rules) == 16
    assert len(rule_ids) == len(rules)
    assert {"PD_IN_PUBLICATION", "TRADE_SECRET_WITHOUT_MARK"} <= rule_ids
    assert all(isinstance(rule, Rule) for rule in rules)


@pytest.mark.parametrize("purpose", [DocPurpose.publication, DocPurpose.contract])
def test_actual_yaml_rules_evaluate_document(
    repo_root: Path, rule_context: RuleContext, purpose: DocPurpose
) -> None:
    ctx = rule_context.model_copy(update={"purpose": purpose})
    rules = load_rules(repo_root / "data/rules/rules.yaml")
    expected = ["TRADE_SECRET_WITHOUT_MARK"]
    if purpose == DocPurpose.publication:
        # По docs/rules.md: есть ПДн и коммерческая тайна в документе для публикации.
        expected.insert(0, "PD_IN_PUBLICATION")
        expected.append("TRADE_SECRET_IN_PUBLICATION")
    assert [item.rule_id for item in evaluate(rules, ctx)] == expected


@pytest.mark.parametrize("content", ["", "# Только комментарий\n", "[]\n"])
def test_load_empty_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text(content, encoding="utf-8")
    assert load_rules(path) == []


@pytest.mark.parametrize("data", [{}, False, 0, "", "invalid"])
def test_load_requires_list(tmp_path: Path, data: object) -> None:
    with pytest.raises(ValueError):
        load_rules(_write_rules(tmp_path, data))


def test_load_duplicate_id(tmp_path: Path) -> None:
    data = _rule(PERSON, "DUPLICATE_RULE").model_dump(mode="json")
    with pytest.raises(ValueError, match="DUPLICATE_RULE"):
        load_rules(_write_rules(tmp_path, [data, data]))


def test_load_validation_error_identifies_rule_and_field(tmp_path: Path) -> None:
    data = _rule(PERSON, "INVALID_SEVERITY").model_dump(mode="json")
    data["severity"] = "НЕ_ВЫВОДИТЬ_ВХОДНОЕ_ЗНАЧЕНИЕ"
    with pytest.raises(ValueError) as error:
        load_rules(_write_rules(tmp_path, [data]))
    assert "INVALID_SEVERITY" in str(error.value)
    assert "severity" in str(error.value)
    assert data["severity"] not in str(error.value)


def test_load_missing_id_reports_one_based_position(tmp_path: Path) -> None:
    valid = _rule(PERSON).model_dump(mode="json")
    invalid = {key: value for key, value in valid.items() if key != "id"}
    with pytest.raises(ValueError) as error:
        load_rules(_write_rules(tmp_path, [valid, invalid]))
    assert "2" in str(error.value)
    assert "id" in str(error.value)


@pytest.mark.parametrize("item", [None, [], 42, "invalid"])
def test_load_non_mapping_rule_reports_position(tmp_path: Path, item: object) -> None:
    with pytest.raises(ValueError, match="1"):
        load_rules(_write_rules(tmp_path, [item]))


def test_load_malformed_yaml_reports_error_without_content(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("- id: НЕ_ВЫВОДИТЬ_СОДЕРЖИМОЕ\n  condition: [", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_rules(path)
    assert "YAML" in str(error.value)
    assert "НЕ_ВЫВОДИТЬ_СОДЕРЖИМОЕ" not in str(error.value)
