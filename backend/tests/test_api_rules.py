"""CRUD правил: SQLite в памяти, синтетические записи, YAML только для засева."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base, RuleRow
from app.db.session import get_db
from app.main import app
from app.schemas import Rule
from app.services.rules import repository
from app.services.rules.loader import load_rules


@pytest.fixture
def rules_db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def synthetic_rule(rule_id: str = "SYNTHETIC_RULE") -> Rule:
    return Rule(
        id=rule_id, title="Учебное правило", description="Синтетическая проверка",
        severity="red", law_refs=[{"law": "Учебная политика", "article": "п. 1"}],
        condition={"type": "entity_present", "categories": ["pd"], "min_count": 1},
        recommendation="Проверьте учебный документ", enabled=True,
    )


def test_get_seeds_once_in_yaml_order(client: TestClient, rules_db: Session) -> None:
    expected = load_rules(repository.rules_yaml_path())
    first = client.get("/api/rules")
    second = client.get("/api/rules")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == [rule.model_dump(mode="json") for rule in expected]
    rows = list(rules_db.scalars(select(RuleRow).order_by(RuleRow.position)))
    assert len(rows) == len(expected) == 16
    assert [row.position for row in rows] == list(range(1, 17))
    assert [row.id for row in rows] == [rule.id for rule in expected]
    assert all(row.data["enabled"] == row.enabled for row in rows)


def test_create_and_duplicate(client: TestClient, rules_db: Session) -> None:
    payload = synthetic_rule().model_dump(mode="json")
    created = client.post("/api/rules", json=payload)
    assert created.status_code == 201
    assert created.json() == payload
    assert rules_db.get(RuleRow, payload["id"]).position == 17
    duplicate = client.post("/api/rules", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "Правило с таким id уже существует"}
    listed = client.get("/api/rules").json()
    assert len(listed) == 17
    assert listed[-1] == payload


@pytest.mark.parametrize(("condition", "message"), [
    ({"type": "entity_presnt"}, "неизвестный тип условия"),
    ({"type": "entity_present", "categories": ["pd"], "min_count": 0}, "min_count"),
    ({"type": "entity_present", "categories": []}, "categories"),
    ({"type": "entity_present", "categories": ["pd"], "types": []}, "types"),
    ({"type": "purpose_is", "purposes": []}, "purposes"),
    ({"any": [{"type": "purpose_is", "purposes": ["internal"]}, {"all": []}]}, "all"),
])
@pytest.mark.parametrize("method", ["post", "put"])
def test_invalid_condition_is_rejected_atomically(
    client: TestClient, rules_db: Session, condition: dict[str, object],
    message: str, method: str,
) -> None:
    original = client.get("/api/rules").json()
    payload = synthetic_rule().model_dump(mode="json") if method == "post" else original[0].copy()
    payload.update(condition=condition, enabled=False)
    url = "/api/rules" if method == "post" else f"/api/rules/{payload['id']}"
    response = client.request(method, url, json=payload)
    assert response.status_code == 400
    assert response.json()["detail"].startswith(f"Правило {payload['id']}: ")
    assert message in response.json()["detail"]
    assert client.get("/api/rules").json() == original


def test_update_preserves_position_and_persists(client: TestClient, rules_db: Session) -> None:
    original = client.get("/api/rules").json()
    payload = {**original[0], "severity": "yellow", "enabled": False}
    response = client.put(f"/api/rules/{payload['id']}", json=payload)
    assert response.status_code == 200
    assert response.json() == payload
    rules_db.expire_all()
    row = rules_db.get(RuleRow, payload["id"])
    assert row.enabled is False
    assert row.data == payload
    assert row.position == 1
    assert client.get("/api/rules").json() == [payload, *original[1:]]
    with Session(rules_db.bind) as another_session:
        assert repository.get_rule(another_session, payload["id"]).enabled is False


def test_update_id_mismatch(client: TestClient, rules_db: Session) -> None:
    original = client.get("/api/rules").json()
    payload = {**original[0], "id": "OTHER_SYNTHETIC_ID"}
    response = client.put(f"/api/rules/{original[0]['id']}", json=payload)
    assert response.status_code == 400
    assert response.json() == {"detail": "id в пути и теле должны совпадать"}
    assert client.get("/api/rules").json() == original


@pytest.mark.parametrize("method", ["put", "delete"])
def test_missing_rule(client: TestClient, rules_db: Session, method: str) -> None:
    kwargs = {"json": synthetic_rule().model_dump(mode="json")} if method == "put" else {}
    response = client.request(method, "/api/rules/SYNTHETIC_RULE", **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": "Правило не найдено"}
    assert len(client.get("/api/rules").json()) == 16


def test_delete_does_not_change_yaml(client: TestClient, rules_db: Session) -> None:
    yaml_path = repository.rules_yaml_path()
    before = yaml_path.read_bytes()
    original = client.get("/api/rules").json()
    url = f"/api/rules/{original[0]['id']}"
    response = client.delete(url)
    assert response.status_code == 204
    assert response.content == b""
    assert rules_db.get(RuleRow, original[0]["id"]) is None
    assert client.get("/api/rules").json() == original[1:]
    assert client.delete(url).status_code == 404
    assert yaml_path.read_bytes() == before


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_mutation_seeds_before_first_request(
    client: TestClient, rules_db: Session, method: str,
) -> None:
    catalog = load_rules(repository.rules_yaml_path())
    rule = catalog[0] if method != "post" else synthetic_rule()
    kwargs = {"json": rule.model_dump(mode="json")} if method != "delete" else {}
    url = "/api/rules" if method == "post" else f"/api/rules/{rule.id}"
    response = client.request(method, url, **kwargs)
    assert response.status_code == {"post": 201, "put": 200, "delete": 204}[method]
    assert len(client.get("/api/rules").json()) == {"post": 17, "put": 16, "delete": 15}[method]


def test_insertion_order_after_update_and_delete(rules_db: Session) -> None:
    catalog = repository.list_rules(rules_db)
    for rule_id in ["SYNTHETIC_Z", "SYNTHETIC_A"]:
        repository.create_rule(rules_db, synthetic_rule(rule_id))
    changed = synthetic_rule("SYNTHETIC_Z").model_copy(update={"enabled": False})
    repository.update_rule(rules_db, changed.id, changed)
    repository.delete_rule(rules_db, catalog[0].id)
    repository.create_rule(rules_db, synthetic_rule("SYNTHETIC_B"))
    assert [rule.id for rule in repository.list_rules(rules_db)] == [
        *[rule.id for rule in catalog[1:]], "SYNTHETIC_Z", "SYNTHETIC_A", "SYNTHETIC_B",
    ]


def test_existing_table_does_not_read_yaml(rules_db: Session, tmp_path: Path) -> None:
    rule = synthetic_rule()
    repository.create_rule(rules_db, rule)
    repository.ensure_seeded(rules_db, tmp_path / "missing.yaml")
    assert repository.list_rules(rules_db) == [rule]


@pytest.mark.parametrize("existing", [(True, True), (False, True), (False, False)])
def test_yaml_path_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: tuple[bool, bool],
) -> None:
    paths = (tmp_path / "docker.yaml", tmp_path / "repository.yaml")
    for path, exists in zip(paths, existing, strict=True):
        if exists:
            path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(repository, "RULES_PATHS", paths)
    if any(existing):
        assert repository.rules_yaml_path() == paths[existing.index(True)]
    else:
        with pytest.raises(FileNotFoundError, match="Стартовый каталог правил не найден"):
            repository.rules_yaml_path()


def test_bad_catalog_does_not_seed_partially(rules_db: Session, tmp_path: Path) -> None:
    good = synthetic_rule().model_dump(mode="json")
    bad = {**good, "id": "SYNTHETIC_BAD", "condition": {"type": "entity_presnt"}}
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump([good, bad], allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="Правило SYNTHETIC_BAD: неизвестный тип условия"):
        repository.ensure_seeded(rules_db, path)
    assert list(rules_db.scalars(select(RuleRow))) == []


def test_empty_catalog(rules_db: Session, tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("[]", encoding="utf-8")
    repository.ensure_seeded(rules_db, path)
    assert list(rules_db.scalars(select(RuleRow))) == []


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_repository_missing_rule(rules_db: Session, operation: str) -> None:
    with pytest.raises(repository.RuleNotFoundError, match="Правило не найдено"):
        if operation == "get":
            repository.get_rule(rules_db, "SYNTHETIC_RULE")
        elif operation == "update":
            repository.update_rule(rules_db, "SYNTHETIC_RULE", synthetic_rule())
        else:
            repository.delete_rule(rules_db, "SYNTHETIC_RULE")


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("field", ["severity", "condition"])
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
def test_repository_validates_writes(rules_db: Session, operation: str, field: str) -> None:
    rule = synthetic_rule()
    repository.create_rule(rules_db, rule)
    value = "green" if field == "severity" else {"type": "entity_presnt"}
    invalid = rule.model_copy(update={field: value})
    with pytest.raises(ValueError):
        if operation == "create":
            repository.create_rule(rules_db, invalid)
        else:
            repository.update_rule(rules_db, rule.id, invalid)
    assert repository.get_rule(rules_db, rule.id) == rule


@pytest.mark.parametrize("operation", ["get", "list", "delete"])
@pytest.mark.parametrize("field", ["severity", "condition"])
def test_repository_validates_stored_json(
    rules_db: Session, operation: str, field: str,
) -> None:
    rule = synthetic_rule()
    repository.create_rule(rules_db, rule)
    row = rules_db.get(RuleRow, rule.id)
    value = "green" if field == "severity" else {"type": "entity_presnt"}
    row.data = {**row.data, field: value}
    rules_db.commit()
    error = ValidationError if field == "severity" else ValueError
    with pytest.raises(error):
        if operation == "get":
            repository.get_rule(rules_db, rule.id)
        elif operation == "list":
            repository.list_rules(rules_db)
        else:
            repository.delete_rule(rules_db, rule.id)
    assert rules_db.get(RuleRow, rule.id) is not None


def test_enabled_column_is_authoritative(rules_db: Session) -> None:
    rule = synthetic_rule()
    repository.create_rule(rules_db, rule)
    rules_db.get(RuleRow, rule.id).enabled = False
    rules_db.commit()
    assert repository.get_rule(rules_db, rule.id).enabled is False


def test_commit_failure_rolls_back(rules_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    commit = rules_db.commit
    monkeypatch.setattr(rules_db, "commit", Mock(side_effect=SQLAlchemyError("synthetic")))
    with pytest.raises(SQLAlchemyError, match="synthetic"):
        repository.create_rule(rules_db, synthetic_rule())
    assert not rules_db.new
    assert rules_db.get(RuleRow, "SYNTHETIC_RULE") is None
    monkeypatch.setattr(rules_db, "commit", commit)
    repository.create_rule(rules_db, synthetic_rule())
    assert repository.get_rule(rules_db, "SYNTHETIC_RULE") == synthetic_rule()
