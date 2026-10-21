"""Изменения API правил влияют на настоящий конвейер и след новых проверок."""

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import documents
from app.core.config import get_settings
from app.db.models import Base, RuleRow
from app.db.session import get_db
from app.main import app
from app.schemas import DocPurpose, EntityType, Report, Rule
from app.services import pipeline, storage
from app.services.rules import repository
from app.worker import tasks
from tests.test_pipeline import make_doc

TEXT = "Документ для публикации. Представитель: Иванов Иван Иванович."


@pytest.fixture(autouse=True)
def mock_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def rules_pipeline_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(documents.process_document, "delay", Mock())
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def audit_publication(client: TestClient, session: Session) -> Report:
    response = client.post("/api/queries", json={"text": TEXT, "purpose": "publication"})
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    tasks.run_pipeline(task_id, session)
    status = client.get(f"/api/tasks/{task_id}").json()
    assert status["status"] == "done", status
    response = client.get(f"/api/reports/{status['report_id']}")
    assert response.status_code == 200
    report = Report.model_validate(response.json())
    assert any(entity.type == EntityType.PERSON for entity in report.entities)
    assert report.audit.document_sha256 == sha256(TEXT.encode("utf-8")).hexdigest()
    assert report.audit.llm_provider == "mock" and report.audit.llm_calls == 0
    return report


def test_rule_toggle_changes_new_reports_and_ruleset_version(
    client: TestClient, rules_pipeline_db: Session,
) -> None:
    yaml_before = repository.rules_yaml_path().read_bytes()
    before = audit_publication(client, rules_pipeline_db)
    assert "PD_IN_PUBLICATION" in {item.rule_id for item in before.violations}
    assert len(list(rules_pipeline_db.scalars(select(RuleRow)))) == 16
    rule = next(item for item in client.get("/api/rules").json()
                if item["id"] == "PD_IN_PUBLICATION")
    rule["enabled"] = False
    assert client.put("/api/rules/PD_IN_PUBLICATION", json=rule).status_code == 200
    disabled = audit_publication(client, rules_pipeline_db)
    assert "PD_IN_PUBLICATION" not in {item.rule_id for item in disabled.violations}
    assert disabled.audit.ruleset_version != before.audit.ruleset_version
    rule["enabled"] = True
    assert client.put("/api/rules/PD_IN_PUBLICATION", json=rule).status_code == 200
    restored = audit_publication(client, rules_pipeline_db)
    assert "PD_IN_PUBLICATION" in {item.rule_id for item in restored.violations}
    assert restored.audit.ruleset_version == before.audit.ruleset_version
    saved_before = client.get(f"/api/reports/{before.report_id}").json()
    assert saved_before == before.model_dump(mode="json")
    assert repository.rules_yaml_path().read_bytes() == yaml_before


@pytest.mark.parametrize("state", ["empty", "enabled", "disabled"])
def test_explicit_rules_bypass_yaml_and_define_audit(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    rules = [] if state == "empty" else [Rule(
        id="SYNTHETIC_PUBLICATION", title="Учебное правило", description="Выдуманная проверка",
        severity="red", law_refs=[], condition={"type": "entity_present", "categories": ["pd"]},
        recommendation="Обезличьте учебный текст", enabled=state == "enabled",
    )]
    loader = Mock(side_effect=AssertionError("YAML не нужен при переданных правилах"))
    evaluator = Mock(wraps=pipeline.evaluate)
    audit_builder = Mock(wraps=pipeline.build_audit)
    monkeypatch.setattr(pipeline, "load_rules", loader)
    monkeypatch.setattr(pipeline, "evaluate", evaluator)
    monkeypatch.setattr(pipeline, "build_audit", audit_builder)
    result = pipeline.run_audit(make_doc(TEXT), DocPurpose.publication, b"synthetic", rules=rules)
    assert evaluator.call_args.args[0] is rules
    assert audit_builder.call_args.args[1] is rules
    assert [item.rule_id for item in result.report.violations] == (
        ["SYNTHETIC_PUBLICATION"] if state == "enabled" else []
    )
    if state != "enabled":
        assert result.report.audit.ruleset_version == sha256(b"[]").hexdigest()[:12]
    loader.assert_not_called()


def test_none_rules_preserves_yaml_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = Mock(wraps=pipeline.load_rules)
    monkeypatch.setattr(pipeline, "load_rules", loader)
    result = pipeline.run_audit(make_doc(TEXT), DocPurpose.publication, b"synthetic", rules=None)
    loader.assert_called_once_with(pipeline._rules_path("rules.yaml"))
    assert "PD_IN_PUBLICATION" in {item.rule_id for item in result.report.violations}
