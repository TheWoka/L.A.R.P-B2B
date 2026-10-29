"""Срабатывания каталога T-011a: только выдуманные документы и режим mock."""

import re
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest

from app.schemas import (
    DocPurpose,
    Entity,
    EntityCategory,
    EntityType,
    ExtractedDocument,
    Marker,
    MarkerType,
    Rule,
    RuleContext,
    TextSegment,
)
from app.services.masking.masker import mask, mask_preserving_length
from app.services.rules.engine import evaluate
from app.services.rules.loader import load_rules


class ContextArgs(TypedDict):
    purpose: str
    entity_types: NotRequired[list[str]]
    present_markers: NotRequired[list[str]]


def ctx(
    purpose: str,
    entity_types: list[str] | None = None,
    present_markers: list[str] | None = None,
) -> RuleContext:
    """Создаёт синтетические сущности и полный набор маркеров без детекторов."""
    kinds = [EntityType(kind) for kind in entity_types or []]
    values = [f"Образец{index}" for index in range(len(kinds))]
    text = "Синтетический документ. " + " ".join(values)
    categories = {
        EntityType.SPECIAL_CATEGORY: EntityCategory.pd_special,
        EntityType.TRADE_SECRET: EntityCategory.trade_secret,
        EntityType.FINANCIAL: EntityCategory.financial,
    }
    entities = [
        Entity(
            type=kind, category=categories.get(kind, EntityCategory.pd),
            start=text.index(value), end=text.index(value) + len(value),
            value_masked=value, confidence=1.0, detector="dict",
        )
        for kind, value in zip(kinds, values, strict=True)
    ]
    report_text = mask_preserving_length(text, entities)
    for entity in entities:
        entity.value_masked = report_text[entity.start:entity.end]
    return RuleContext(
        doc=ExtractedDocument(
            document_id="catalog-synthetic", filename="synthetic.txt", text=text,
            segments=[TextSegment(id=1, text=text, offset=0)],
        ),
        masked_text=mask(text, entities).masked_text,
        entities=entities,
        markers=[
            Marker(type=kind, present=kind.value in (present_markers or []))
            for kind in MarkerType
        ],
        purpose=DocPurpose(purpose),
    )


@pytest.fixture(scope="module")
def rules() -> list[Rule]:
    paths = [
        Path("/data/rules/rules.yaml"),
        Path(__file__).resolve().parents[2] / "data/rules/rules.yaml",
    ]
    path = next((candidate for candidate in paths if candidate.is_file()), None)
    assert path is not None, "Каталог rules.yaml недоступен"
    return load_rules(path)


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")


# Каждая negative-строка меняет ровно одну предпосылку соответствующей positive.
CASES: list[tuple[str, ContextArgs, bool]] = [
    ("PD_IN_PUBLICATION", {"purpose": "publication", "entity_types": ["PERSON"]}, True),
    ("PD_IN_PUBLICATION", {"purpose": "internal", "entity_types": ["PERSON"]}, False),
    ("PD_IN_PUBLICATION", {"purpose": "publication", "entity_types": []}, False),
    ("BANK_DATA_IN_PUBLICATION", {"purpose": "publication", "entity_types": ["CARD"]}, True),
    ("BANK_DATA_IN_PUBLICATION", {"purpose": "contract", "entity_types": ["CARD"]}, False),
    ("BANK_DATA_IN_PUBLICATION", {"purpose": "publication", "entity_types": ["PERSON"]}, False),
    ("TRADE_SECRET_WITHOUT_MARK", {"purpose": "internal", "entity_types": ["TRADE_SECRET"]}, True),
    ("TRADE_SECRET_WITHOUT_MARK", {"purpose": "contract", "entity_types": ["TRADE_SECRET"]}, True),
    ("TRADE_SECRET_WITHOUT_MARK", {
        "purpose": "international", "entity_types": ["TRADE_SECRET"],
    }, True),
    ("TRADE_SECRET_WITHOUT_MARK", {
        "purpose": "publication", "entity_types": ["TRADE_SECRET"],
    }, False),
    ("TRADE_SECRET_WITHOUT_MARK", {"purpose": "internal", "entity_types": []}, False),
    ("TRADE_SECRET_WITHOUT_MARK", {
        "purpose": "internal", "entity_types": ["TRADE_SECRET"],
        "present_markers": ["classification_mark"],
    }, False),
    ("TRADE_SECRET_IN_PUBLICATION", {
        "purpose": "publication", "entity_types": ["TRADE_SECRET"],
    }, True),
    ("TRADE_SECRET_IN_PUBLICATION", {
        "purpose": "internal", "entity_types": ["TRADE_SECRET"],
    }, False),
    ("TRADE_SECRET_IN_PUBLICATION", {"purpose": "publication", "entity_types": []}, False),
    ("PD_ENTRUSTMENT_MISSING", {"purpose": "contract", "entity_types": ["PASSPORT"]}, True),
    ("PD_ENTRUSTMENT_MISSING", {"purpose": "internal", "entity_types": ["PASSPORT"]}, False),
    ("PD_ENTRUSTMENT_MISSING", {"purpose": "contract", "entity_types": ["PERSON"]}, False),
    ("PD_ENTRUSTMENT_MISSING", {
        "purpose": "contract", "entity_types": ["PASSPORT"],
        "present_markers": ["processing_assignment"],
    }, False),
    ("CONSENT_MISSING_FOR_CONTRACT", {"purpose": "contract", "entity_types": ["SNILS"]}, True),
    ("CONSENT_MISSING_FOR_CONTRACT", {"purpose": "internal", "entity_types": ["SNILS"]}, False),
    ("CONSENT_MISSING_FOR_CONTRACT", {"purpose": "contract", "entity_types": ["ADDRESS"]}, False),
    ("CONSENT_MISSING_FOR_CONTRACT", {
        "purpose": "contract", "entity_types": ["SNILS"], "present_markers": ["consent_clause"],
    }, False),
    ("SPECIAL_PD_NO_CONSENT", {
        "purpose": "internal", "entity_types": ["SPECIAL_CATEGORY"],
    }, True),
    ("SPECIAL_PD_NO_CONSENT", {"purpose": "internal", "entity_types": ["PERSON"]}, False),
    ("SPECIAL_PD_NO_CONSENT", {
        "purpose": "internal", "entity_types": ["SPECIAL_CATEGORY"],
        "present_markers": ["consent_clause"],
    }, False),
    ("KII_IN_PUBLICATION", {
        "purpose": "publication", "present_markers": ["kii_mention"],
    }, True),
    ("KII_IN_PUBLICATION", {"purpose": "contract", "present_markers": ["kii_mention"]}, False),
    ("KII_IN_PUBLICATION", {"purpose": "publication", "present_markers": []}, False),
    ("GDPR_CROSS_BORDER", {"purpose": "international", "entity_types": ["PERSON"]}, True),
    ("GDPR_CROSS_BORDER", {"purpose": "contract", "entity_types": ["PERSON"]}, False),
    ("GDPR_CROSS_BORDER", {"purpose": "international", "entity_types": []}, False),
    ("GDPR_NO_DPA", {"purpose": "international", "entity_types": ["PERSON"]}, True),
    ("GDPR_NO_DPA", {"purpose": "contract", "entity_types": ["PERSON"]}, False),
    ("GDPR_NO_DPA", {"purpose": "international", "entity_types": []}, False),
    ("GDPR_NO_DPA", {
        "purpose": "international", "entity_types": ["PERSON"],
        "present_markers": ["processing_assignment"],
    }, False),
    ("CHECKLIST_NO_APPROVAL", {"purpose": "internal"}, True),
    ("CHECKLIST_NO_APPROVAL", {"purpose": "contract"}, True),
    ("CHECKLIST_NO_APPROVAL", {"purpose": "publication"}, False),
    ("CHECKLIST_NO_APPROVAL", {
        "purpose": "internal", "present_markers": ["approval_sheet"],
    }, False),
    ("CHECKLIST_NO_MARK", {"purpose": "internal", "entity_types": ["INN"]}, True),
    ("CHECKLIST_NO_MARK", {"purpose": "contract", "entity_types": ["INN"]}, False),
    ("CHECKLIST_NO_MARK", {"purpose": "internal", "entity_types": ["ADDRESS"]}, False),
    ("CHECKLIST_NO_MARK", {
        "purpose": "internal", "entity_types": ["INN"],
        "present_markers": ["classification_mark"],
    }, False),
    ("CHECKLIST_NO_MARK", {
        "purpose": "internal", "entity_types": ["SPECIAL_CATEGORY"],
        "present_markers": ["classification_mark"],
    }, False),
    ("CHECKLIST_NO_MARK", {
        "purpose": "publication", "entity_types": ["SPECIAL_CATEGORY"],
    }, False),
    ("CHECKLIST_NO_MARK", {
        "purpose": "contract", "entity_types": ["SPECIAL_CATEGORY"],
    }, False),
    ("CHECKLIST_NO_RETENTION", {"purpose": "publication", "entity_types": ["BIRTH_DATE"]}, True),
    ("CHECKLIST_NO_RETENTION", {"purpose": "publication", "entity_types": ["PERSON"]}, False),
    ("CHECKLIST_NO_RETENTION", {
        "purpose": "publication", "entity_types": ["BIRTH_DATE"],
        "present_markers": ["retention_period"],
    }, False),
    ("CHECKLIST_NO_RETENTION", {
        "purpose": "internal", "entity_types": ["SPECIAL_CATEGORY"],
        "present_markers": ["retention_period"],
    }, False),
]

# Проверяем каждый допустимый тип отдельно: списки типов означают «или», а не «и».
for rule_id, purpose, kinds in [
    ("PD_IN_PUBLICATION", "publication", ["SPECIAL_CATEGORY"]),
    ("BANK_DATA_IN_PUBLICATION", "publication", ["BANK_ACCOUNT"]),
    ("PD_ENTRUSTMENT_MISSING", "contract", ["SNILS", "INN", "ADDRESS", "BIRTH_DATE"]),
    ("CONSENT_MISSING_FOR_CONTRACT", "contract", ["PASSPORT", "INN"]),
    ("GDPR_CROSS_BORDER", "international", ["SPECIAL_CATEGORY"]),
    ("GDPR_NO_DPA", "international", ["SPECIAL_CATEGORY"]),
    ("CHECKLIST_NO_MARK", "internal", ["PASSPORT", "SNILS", "BIRTH_DATE", "SPECIAL_CATEGORY"]),
    ("CHECKLIST_NO_RETENTION", "internal", ["PASSPORT", "SNILS", "INN", "SPECIAL_CATEGORY"]),
    ("CHECKLIST_NO_RETENTION", "publication", ["SPECIAL_CATEGORY"]),
]:
    CASES.extend((rule_id, {"purpose": purpose, "entity_types": [kind]}, True) for kind in kinds)


@pytest.mark.parametrize(("rule_id", "context_args", "expected"), CASES)
def test_catalog_rule(
    rules: list[Rule], rule_id: str, context_args: ContextArgs, expected: bool,
) -> None:
    violation_ids = {violation.rule_id for violation in evaluate(rules, ctx(**context_args))}
    assert (rule_id in violation_ids) is expected


@pytest.mark.parametrize(
    ("rule_id", "context_args"),
    [
        ("TRADE_SECRET_CALCULABLE", {"purpose": "publication"}),
        ("SECURITY_MEASURES_VAGUE", {
            "purpose": "contract", "present_markers": ["processing_assignment"],
        }),
        ("KII_INCIDENTS_NOT_REGULATED", {
            "purpose": "contract", "present_markers": ["kii_mention"],
        }),
    ],
)
def test_llm_rules_do_not_trigger_in_mock(
    rules: list[Rule], rule_id: str, context_args: ContextArgs,
) -> None:
    violation_ids = {violation.rule_id for violation in evaluate(rules, ctx(**context_args))}
    assert rule_id not in violation_ids


def test_contract_signatory_contacts_do_not_require_sensitive_pd_checks(rules: list[Rule]) -> None:
    context = ctx("contract", ["PERSON", "PHONE"], [kind.value for kind in MarkerType])
    violation_ids = {violation.rule_id for violation in evaluate(rules, context)}
    assert not any(rule_id.startswith("CHECKLIST_") for rule_id in violation_ids)
    assert "PD_ENTRUSTMENT_MISSING" not in violation_ids


def test_financial_data_in_publication_is_not_trade_secret(rules: list[Rule]) -> None:
    violation_ids = {
        violation.rule_id for violation in evaluate(rules, ctx("publication", ["FINANCIAL"]))
    }
    assert not any(rule_id.startswith("TRADE_SECRET_") for rule_id in violation_ids)


def test_publication_requires_removing_trade_secret_without_requiring_mark(
    rules: list[Rule],
) -> None:
    violation_ids = {
        violation.rule_id for violation in evaluate(rules, ctx("publication", ["TRADE_SECRET"]))
    }
    assert "TRADE_SECRET_IN_PUBLICATION" in violation_ids
    assert "TRADE_SECRET_WITHOUT_MARK" not in violation_ids


def test_catalog_has_sixteen_rules_in_table_order(rules: list[Rule]) -> None:
    assert [rule.id for rule in rules] == [
        "PD_IN_PUBLICATION", "BANK_DATA_IN_PUBLICATION", "TRADE_SECRET_WITHOUT_MARK",
        "TRADE_SECRET_IN_PUBLICATION", "TRADE_SECRET_CALCULABLE", "PD_ENTRUSTMENT_MISSING",
        "CONSENT_MISSING_FOR_CONTRACT", "SPECIAL_PD_NO_CONSENT", "SECURITY_MEASURES_VAGUE",
        "KII_IN_PUBLICATION", "KII_INCIDENTS_NOT_REGULATED", "GDPR_CROSS_BORDER", "GDPR_NO_DPA",
        "CHECKLIST_NO_APPROVAL", "CHECKLIST_NO_MARK", "CHECKLIST_NO_RETENTION",
    ]


def test_catalog_ids_match_documented_table(rules: list[Rule]) -> None:
    path = Path(__file__).resolve().parents[2] / "docs/rules.md"
    if not path.is_file():
        pytest.skip("docs/rules.md недоступен (в образе api нет docs/)")
    documented_ids = set(re.findall(
        r"^\|\s*`([A-Z_]+)`\s*\|", path.read_text(encoding="utf-8"), re.M,
    ))
    assert {rule.id for rule in rules} == documented_ids


def article_text(source: str, law: str, article: str) -> str | None:
    """Повторяет поиск раздела из frontend/src/components/legalArticles.ts."""
    number = re.search(r"[0-9]+(?:\.[0-9]+)?", article)
    if number is None:
        return None
    prefix = "Article" if law == "GDPR" else "Статья"
    sections = re.split(r"^## ", source, flags=re.M)[1:]
    return next((
        section.strip() for section in sections
        if section.startswith(f"{prefix} {number[0]}. ")
    ), None)


def test_catalog_law_refs_open_local_articles(rules: list[Rule]) -> None:
    paths = [Path("/data/legal"), Path(__file__).resolve().parents[2] / "data/legal"]
    legal_dir = next((path for path in paths if path.is_dir()), None)
    if legal_dir is None:
        pytest.skip("data/legal недоступен (в образе api нет текстов законов)")
    filenames = {
        "152-ФЗ": "152-fz.md", "187-ФЗ": "187-fz.md",
        "98-ФЗ": "98-fz.md", "GDPR": "gdpr.md",
        "44-ФЗ": "44-fz.md", "223-ФЗ": "223-fz.md",
    }
    errors: list[str] = []
    for rule in rules:
        for ref in rule.law_refs:
            label = f"{rule.id}: {ref.law}, {ref.article}"
            # Текст локального акта заказчика не включаем по решению T-054.
            if (rule.id, ref.law, ref.article, ref.title, ref.url) == (
                "CHECKLIST_NO_APPROVAL", "Внутренний регламент организации",
                "согласование документов", "Устанавливается локальным актом заказчика", None,
            ):
                continue
            filename = filenames.get(ref.law)
            if filename is None:
                errors.append(f"{label}: закон не поддерживается legalArticles.ts")
                continue
            path = legal_dir / filename
            if not path.is_file():
                errors.append(f"{label}: нет файла {filename}")
                continue
            if article_text(path.read_text(encoding="utf-8"), ref.law, ref.article) is None:
                errors.append(f"{label}: нет точного заголовка статьи")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize(
    ("law", "article", "expected"),
    [
        ("152-ФЗ", "ст. 10", "Статья 10. Особые категории\nТекст 10."),
        ("152-ФЗ", "ст. 10.1", "Статья 10.1. Распространение\nТекст 10.1."),
        ("152-ФЗ", "ст. 6 ч. 3", "Статья 6. Условия обработки\nВся статья 6."),
        ("152-ФЗ", "ст. 18.1", "Статья 18.1. Меры оператора\nТекст 18.1."),
        ("GDPR", "Art. 28(3)", "Article 28. Processor\nWhole article 28."),
        ("152-ФЗ", "ст. 1", None),
        ("152-ФЗ", "ст. 10.2", None),
        ("152-ФЗ", "документооборот", None),
        ("Внутренний регламент организации", "согласование документов", None),
        ("GDPR", "Art. 10", None),
    ],
)
def test_article_lookup_preserves_decimal_numbers_and_article_boundaries(
    law: str, article: str, expected: str | None,
) -> None:
    source = (
        "# Синтетические тексты\n\n"
        "## Статья 10.1. Распространение\nТекст 10.1.\n\n"
        "## Статья 10. Особые категории\nТекст 10.\n\n"
        "## Статья 6. Условия обработки\nВся статья 6.\n\n"
        "## Статья 18.1. Меры оператора\nТекст 18.1.\n\n"
        "## Article 28. Processor\nWhole article 28.\n"
    )
    assert article_text(source, law, article) == expected
