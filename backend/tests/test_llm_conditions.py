"""T-013b: условие llm в правилах и журнал «Что видела нейросеть». Только выдуманные данные.

LLM подменяется: complete_json — monkeypatch; для бюджета и кэша — request_chat внутри
настоящего клиента. Сеть, Redis и PostgreSQL не нужны.
"""

import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base, DocumentRow, ReportRow, TaskRow
from app.db.session import get_db
from app.main import app
from app.schemas import (
    DocPurpose,
    Entity,
    EntityCategory,
    EntityType,
    ExtractedDocument,
    LawRef,
    LlmCall,
    Marker,
    MarkerType,
    Rule,
    RuleContext,
    Severity,
    TextSegment,
)
from app.services import pipeline
from app.services.llm import checks
from app.services.llm import client as llm_client
from app.services.llm.checks import (
    MAX_FRAGMENT_CHARS,
    LlmConditionAnswer,
    check_llm_condition,
    llm_call_log,
    locate_quote,
)
from app.services.llm.client import LLMRequestError, LLMResponseError, llm_call_budget
from app.services.llm.providers import ChatResult, build_default_instance
from app.services.masking.masker import mask, mask_preserving_length, mask_value
from app.services.rules.engine import evaluate, validate_condition
from app.services.rules.loader import load_rules
from app.worker import tasks

NAME = "Сидоров Семён Семёнович"
PHONE = "+7 (900) 111-22-33"
CLAUSE = "Исполнитель принимает необходимые меры для защиты данных."
TEXT = f"Ответственный: {NAME}, тел. {PHONE}.\nМеры защиты: {CLAUSE}"
QUESTION = "Описаны ли меры защиты только общими фразами?"
LLM_RULE = {"type": "llm", "question": QUESTION}

FakeLlm = Callable[[str, str, type[BaseModel]], BaseModel]


# ---------------------------------------------------------------- helpers ---


def _entity(text: str, value: str, kind: EntityType) -> Entity:
    start = text.index(value)
    end = start + len(value)
    return Entity(
        type=kind, category=EntityCategory.pd, start=start, end=end, page=2,
        section="Раздел 9", value_masked=mask_value(value), confidence=1.0, detector="regex",
    )


def make_ctx(
    text: str = TEXT, purpose: DocPurpose = DocPurpose.contract,
    markers: list[MarkerType] | None = None,
) -> RuleContext:
    entities = [_entity(text, NAME, EntityType.PERSON), _entity(text, PHONE, EntityType.PHONE)]
    masked = mask(text, entities)
    return RuleContext(
        doc=ExtractedDocument(
            document_id="synthetic-llm", filename="synthetic.docx", text=text,
            segments=[TextSegment(id=0, text=text, offset=0, page=2, section="Раздел 9")],
        ),
        masked_text=masked.masked_text, entities=entities, purpose=purpose,
        markers=[Marker(type=kind, present=kind in (markers or [])) for kind in MarkerType],
        mask_spans=masked.spans,
    )


def make_rule(condition: dict[str, object], rule_id: str = "SYNTHETIC_LLM") -> Rule:
    return Rule(
        id=rule_id, title="Синтетическое правило", description="Тест", severity=Severity.yellow,
        law_refs=[LawRef(law="152-ФЗ", article="ст. 19")], condition=condition,
        recommendation="Тест",
    )


def fragment_of(user: str) -> str:
    """Фрагмент документа из сообщения пользователя (между маркерами)."""
    match = re.search(r"<<<ТЕКСТ\n(.*)\nТЕКСТ>>>", user, re.DOTALL)
    assert match is not None
    return match.group(1)


def answer(verdict: str, quote: str | None = None) -> Mock:
    return Mock(return_value=LlmConditionAnswer(verdict=verdict, quote=quote))


@pytest.fixture
def real_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeLlm], None]:
    """Включает «реальный» режим и подменяет complete_json."""
    monkeypatch.setattr(llm_client, "is_llm_enabled", lambda settings=None: True)

    def install(fake: FakeLlm) -> None:
        monkeypatch.setattr(llm_client, "complete_json", fake)

    return install


def run_rules(rules: list[Rule], ctx: RuleContext) -> tuple[list, list[LlmCall]]:
    with llm_call_log() as calls:
        violations = evaluate(rules, ctx)
    return violations, calls


# ------------------------------------------------------- verdict и цитаты ---


def test_yes_with_found_quote_creates_violation_in_original_coordinates(
    real_llm: Callable[[FakeLlm], None],
) -> None:
    fake = answer("yes", f"Меры защиты: {CLAUSE}")
    real_llm(fake)
    ctx = make_ctx()
    violations, calls = run_rules([make_rule(LLM_RULE)], ctx)
    assert [item.rule_id for item in violations] == ["SYNTHETIC_LLM"]
    evidence = violations[0].evidence[0]
    report_text = mask_preserving_length(ctx.doc.text, ctx.entities)
    assert evidence.quote == report_text[evidence.start:evidence.end]
    assert ctx.doc.text[evidence.start:evidence.end] == f"Меры защиты: {CLAUSE}"
    assert (evidence.page, evidence.section) == (2, "Раздел 9")
    assert violations[0].source == "rule"
    assert calls == [LlmCall(
        purpose="rule_condition", rule_id="SYNTHETIC_LLM", question=QUESTION,
        masked_fragment=ctx.masked_text, verdict="yes", quote=f"Меры защиты: {CLAUSE}",
        cached=False,
    )]


def test_quote_with_placeholder_is_shown_masked_with_same_length(
    real_llm: Callable[[FakeLlm], None],
) -> None:
    real_llm(answer("yes", "Ответственный: [PERSON_1], тел. [PHONE_1]"))
    ctx = make_ctx()
    violations, calls = run_rules([make_rule(LLM_RULE)], ctx)
    evidence = violations[0].evidence[0]
    assert ctx.doc.text[evidence.start:evidence.end] == f"Ответственный: {NAME}, тел. {PHONE}"
    assert evidence.quote == "Ответственный: С██████ С████ С████████, тел. +█ (███) ███-██-██"
    assert len(evidence.quote) == evidence.end - evidence.start
    assert NAME not in evidence.quote and PHONE not in evidence.quote
    assert calls[0].quote == "Ответственный: [PERSON_1], тел. [PHONE_1]"


def test_quote_cut_inside_placeholder_is_expanded_to_whole_placeholder(
    real_llm: Callable[[FakeLlm], None],
) -> None:
    real_llm(answer("yes", "Ответственный: [PERS"))
    ctx = make_ctx()
    violations, calls = run_rules([make_rule(LLM_RULE)], ctx)
    evidence = violations[0].evidence[0]
    assert ctx.doc.text[evidence.start:evidence.end] == f"Ответственный: {NAME}"
    assert calls[0].quote == "Ответственный: [PERSON_1]"


def test_yes_with_quote_not_in_text_is_downgraded_to_unknown(
    real_llm: Callable[[FakeLlm], None],
) -> None:
    real_llm(answer("yes", "Исполнитель шифрует данные по ГОСТ"))
    violations, calls = run_rules([make_rule(LLM_RULE)], make_ctx())
    assert violations == []
    assert (calls[0].verdict, calls[0].quote) == ("unknown", None)


@pytest.mark.parametrize("quote", [None, "", "   "])
def test_yes_without_quote_is_unknown(
    real_llm: Callable[[FakeLlm], None], quote: str | None,
) -> None:
    real_llm(answer("yes", quote))
    violations, calls = run_rules([make_rule(LLM_RULE)], make_ctx())
    assert violations == [] and calls[0].verdict == "unknown"


@pytest.mark.parametrize("verdict", ["no", "unknown"])
def test_no_or_unknown_gives_no_violation(
    real_llm: Callable[[FakeLlm], None], verdict: str,
) -> None:
    real_llm(answer(verdict, CLAUSE))
    violations, calls = run_rules([make_rule(LLM_RULE)], make_ctx())
    assert violations == [] and calls[0].verdict == verdict


@pytest.mark.parametrize("error", [LLMRequestError("сбой"), LLMResponseError("не JSON")])
def test_provider_error_gives_unknown_without_exception(
    real_llm: Callable[[FakeLlm], None], error: Exception,
) -> None:
    real_llm(Mock(side_effect=error))
    ctx = make_ctx()
    violations, calls = run_rules([make_rule(LLM_RULE)], ctx)
    assert violations == []
    assert (calls[0].verdict, calls[0].quote) == ("unknown", None)
    assert calls[0].masked_fragment == ctx.masked_text


def test_mock_mode_never_calls_llm_and_logs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_client, "is_llm_enabled", lambda settings=None: False)
    fake = Mock()
    monkeypatch.setattr(llm_client, "complete_json", fake)
    violations, calls = run_rules([make_rule(LLM_RULE)], make_ctx())
    assert violations == [] and calls == []
    fake.assert_not_called()
    evidence, call = check_llm_condition(
        QUESTION, "текст", [], "R", report_text="текст",
    )
    assert evidence is None and (call.verdict, call.masked_fragment) == ("unknown", "")


def test_mock_default_answer_is_safe_unknown() -> None:
    """Если mock всё же вызовут, ответ по схеме — «не знаю», а не «да»."""
    assert build_default_instance(LlmConditionAnswer).verdict == "unknown"


# --------------------------------------------- что уходит в LLM и в журнал ---


def test_only_masked_text_goes_to_llm_and_logs(
    real_llm: Callable[[FakeLlm], None], caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[tuple[str, str]] = []

    def fake(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        sent.append((system, user))
        return LlmConditionAnswer(verdict="yes", quote="Ответственный: [PERSON_1]")

    real_llm(fake)
    ctx = make_ctx()
    with caplog.at_level(logging.DEBUG):
        _, calls = run_rules([make_rule(LLM_RULE)], ctx)
    system, user = sent[0]
    assert fragment_of(user) == ctx.masked_text == calls[0].masked_fragment
    assert "[PERSON_1]" in user and "[PHONE_1]" in user and QUESTION in user
    dumped = calls[0].model_dump_json()
    for secret in (NAME, PHONE, "Сидоров", "111-22-33"):
        assert secret not in user and secret not in system and secret not in dumped
        assert secret not in caplog.text
    assert QUESTION not in caplog.text and CLAUSE not in caplog.text


def test_long_text_is_cut_to_6000_chars_without_splitting_placeholder() -> None:
    prefix = "а" * (MAX_FRAGMENT_CHARS - 5)
    text = f"{prefix}{NAME}, тел. {PHONE}. {CLAUSE}"
    ctx = make_ctx(text)
    fragment = checks._fragment(ctx.masked_text, ctx.mask_spans)
    assert fragment == prefix
    short = make_ctx()
    assert checks._fragment(short.masked_text, short.mask_spans) == short.masked_text


def test_locate_quote_strips_edges_and_requires_exact_match() -> None:
    ctx = make_ctx()
    start = ctx.masked_text.index("Меры")
    assert locate_quote(ctx.masked_text, "  Меры защиты ", ctx.mask_spans) == (
        start, start + len("Меры защиты"),
    )
    assert locate_quote(ctx.masked_text, "меры ЗАЩИТЫ", ctx.mask_spans) is None
    assert locate_quote(ctx.masked_text, "Меры … данных", ctx.mask_spans) is None


# --------------------------------------------------- логика дерева условий ---


@pytest.mark.parametrize("condition", [
    {"not": LLM_RULE},
    {"not": {"all": [{"type": "marker_present", "marker": "kii_mention"}, LLM_RULE]}},
    {"any": [{"not": {"any": [LLM_RULE]}}]},
])
def test_llm_inside_not_is_rejected(condition: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="внутри not"):
        validate_condition(condition)
    with pytest.raises(ValueError, match="Правило SYNTHETIC_LLM: .*внутри not"):
        evaluate([make_rule(condition)], make_ctx())
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump([make_rule(condition).model_dump(mode="json")], allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Правило SYNTHETIC_LLM: .*внутри not"):
        load_rules(path)


def test_llm_outside_not_is_still_valid() -> None:
    validate_condition({"all": [{"not": {"type": "marker_present", "marker": "kii_mention"}},
                                LLM_RULE]})


def test_llm_is_not_asked_when_other_branch_already_decides(
    real_llm: Callable[[FakeLlm], None],
) -> None:
    fake = answer("yes", CLAUSE)
    real_llm(fake)
    marker = {"type": "marker_present", "marker": "processing_assignment"}
    violations, calls = run_rules([make_rule({"all": [LLM_RULE, marker]})], make_ctx())
    assert violations == [] and calls == []
    fake.assert_not_called()
    violations, calls = run_rules(
        [make_rule({"all": [LLM_RULE, marker]})],
        make_ctx(markers=[MarkerType.processing_assignment]),
    )
    assert len(violations) == 1 and len(calls) == 1


def test_catalog_llm_rule_triggers_with_quote(real_llm: Callable[[FakeLlm], None]) -> None:
    real_llm(answer("yes", CLAUSE))
    rules_path = Path(__file__).resolve().parents[2] / "data/rules/rules.yaml"
    if not rules_path.is_file():
        rules_path = Path("/data/rules/rules.yaml")
    rules = [rule for rule in load_rules(rules_path) if rule.id == "SECURITY_MEASURES_VAGUE"]
    ctx = make_ctx(markers=[MarkerType.processing_assignment])
    violations, calls = run_rules(rules, ctx)
    assert [item.rule_id for item in violations] == ["SECURITY_MEASURES_VAGUE"]
    assert ctx.doc.text[violations[0].evidence[0].start:violations[0].evidence[0].end] == CLAUSE
    assert calls[0].rule_id == "SECURITY_MEASURES_VAGUE"


# ---------------------------------------------- бюджет и кэш (клиент живой) ---


class DictCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self.data[key] = value


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Настоящий complete_json с openai_compatible; HTTP-запрос подменён."""
    settings = Settings(
        _env_file=None, llm_provider="openai_compatible",
        llm_base_url="https://llm.invalid/v1", llm_model="synthetic-model",
    )
    cache = DictCache()
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_default_cache", lambda url: cache)

    def chat(*args: object, **kwargs: object) -> ChatResult:
        return ChatResult(content='{"verdict": "yes", "quote": "Меры защиты:"}')

    fake = Mock(side_effect=chat)
    monkeypatch.setattr(llm_client, "request_chat", fake)
    return fake


def test_budget_exhausted_gives_unknown_without_provider_request(provider: Mock) -> None:
    rules = [make_rule(LLM_RULE, "FIRST"), make_rule({"type": "llm", "question": "Другой?"},
                                                     "SECOND")]
    with llm_call_budget(limit=1) as budget:
        violations, calls = run_rules(rules, make_ctx())
    assert provider.call_count == 1 and budget.used == 1
    assert [item.rule_id for item in violations] == ["FIRST"]
    assert [(call.rule_id, call.verdict) for call in calls] == [
        ("FIRST", "yes"), ("SECOND", "unknown"),
    ]
    assert calls[1].masked_fragment == ""  # в LLM ничего не ушло


def test_cached_answer_is_marked_in_log(provider: Mock) -> None:
    ctx = make_ctx()
    _, first = run_rules([make_rule(LLM_RULE)], ctx)
    _, second = run_rules([make_rule(LLM_RULE)], ctx)
    assert provider.call_count == 1
    assert (first[0].cached, second[0].cached) == (False, True)
    assert second[0].verdict == "yes"


# ------------------------------------------- конвейер, воркер и llm-log API ---


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def run_worker(db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskRow:
    """Текстовый запрос через настоящий воркер; правила — одно LLM-правило."""
    monkeypatch.setattr(pipeline, "load_rules", lambda path: [make_rule(LLM_RULE)])
    path = tmp_path / "query.txt"
    path.write_text(TEXT, encoding="utf-8")
    db.add(DocumentRow(id="doc-llm", filename=None, purpose="contract", stored_path=str(path)))
    db.flush()
    task = TaskRow(id="task-llm", document_id="doc-llm", status="queued")
    db.add(task)
    db.commit()
    tasks.run_pipeline(task.id, db)
    assert task.status == "done", task.error
    return task


def test_worker_saves_llm_log_and_api_returns_it(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    real_llm: Callable[[FakeLlm], None], client: TestClient,
) -> None:
    def fake(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        line = next(row for row in fragment_of(user).splitlines() if "[PERSON_1]" in row)
        return LlmConditionAnswer(verdict="yes", quote=line)

    real_llm(fake)
    task = run_worker(db, tmp_path, monkeypatch)
    response = client.get(f"/api/reports/{task.report_id}/llm-log")
    assert response.status_code == 200
    calls = [LlmCall.model_validate(item) for item in response.json()]
    assert len(calls) == 1 and calls[0].verdict == "yes"
    assert "[PERSON_1]" in calls[0].masked_fragment
    for secret in (NAME, PHONE, "Сидоров"):
        assert secret not in response.text
    report = client.get(f"/api/reports/{task.report_id}").json()
    assert [item["rule_id"] for item in report["violations"]] == ["SYNTHETIC_LLM"]
    assert NAME not in str(report["violations"])


def test_worker_in_mock_mode_returns_empty_llm_log(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    monkeypatch.setattr(llm_client, "is_llm_enabled", lambda settings=None: False)
    fake = Mock()
    monkeypatch.setattr(llm_client, "complete_json", fake)
    task = run_worker(db, tmp_path, monkeypatch)
    response = client.get(f"/api/reports/{task.report_id}/llm-log")
    assert (response.status_code, response.json()) == (200, [])
    fake.assert_not_called()


def test_llm_log_404_and_empty_for_old_report(db: Session, client: TestClient) -> None:
    response = client.get("/api/reports/missing/llm-log")
    assert (response.status_code, response.json()) == (404, {"detail": "Отчёт не найден"})
    db.add(DocumentRow(id="old", purpose="internal"))
    db.flush()
    db.add(ReportRow(id="old-report", document_id="old", data={}))
    db.commit()
    response = client.get("/api/reports/old-report/llm-log")
    assert (response.status_code, response.json()) == (200, [])


def test_provider_failure_does_not_break_pipeline(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    real_llm: Callable[[FakeLlm], None], client: TestClient,
) -> None:
    real_llm(Mock(side_effect=LLMRequestError("сбой")))
    task = run_worker(db, tmp_path, monkeypatch)
    calls = client.get(f"/api/reports/{task.report_id}/llm-log").json()
    assert [call["verdict"] for call in calls] == ["unknown"]
    assert client.get(f"/api/reports/{task.report_id}").json()["violations"] == []


def test_httpx_is_never_used_in_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Страховка правила 7: mock не открывает сетевых соединений."""
    monkeypatch.setattr(httpx.Client, "post", Mock(side_effect=AssertionError("сеть")))
    monkeypatch.setattr(llm_client, "is_llm_enabled", lambda settings=None: False)
    run_rules([make_rule(LLM_RULE)], make_ctx())
