"""Русские ошибки 422, безопасный ответ и границы текстового запроса (T-052)."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import documents
from app.db.models import Base, DocumentRow, TaskRow
from app.db.session import get_db
from app.main import app
from app.services import storage

PURPOSE_MESSAGE = (
    "Выберите назначение документа: внутренний, для публикации, договор с контрагентом "
    "или международный контракт."
)
LONG_TEXT_MESSAGE = "Текст слишком длинный: не больше 200 000 символов. Загрузите его файлом."
SYNTHETIC_NAME = "Примеров Синтетик Тестович"
RULE_FIELDS = [
    ("id", "идентификатор"), ("title", "название"), ("description", "описание"),
    ("severity", "уровень риска"), ("law_refs", "ссылки на законы"),
    ("applies_to", "назначения документов"), ("condition", "условие"),
    ("recommendation", "рекомендация"),
]


@pytest.fixture(autouse=True)
def validation_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Session, Mock]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    delay = Mock()
    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(documents.process_document, "delay", delay)
    with Session(engine, expire_on_commit=False) as session:
        def override_db() -> Iterator[Session]:
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            yield session, delay
        finally:
            app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def assert_validation_error(response: Response, message: str) -> None:
    assert response.status_code == 422
    assert response.json() == {"detail": message}
    assert isinstance(response.json()["detail"], str)


def rule_payload() -> dict[str, object]:
    return {
        "id": "SYNTHETIC_TEST_RULE", "title": "Синтетическое правило",
        "description": "Пример для проверки API", "severity": "yellow",
        "law_refs": [{"law": "Политика ИБ компании", "article": "п. 1"}],
        "applies_to": ["publication"],
        "condition": {"type": "purpose_is", "purposes": ["publication"]},
        "recommendation": "Проверьте документ.",
    }


@pytest.mark.parametrize("purpose", ["xxx", "", None])
def test_invalid_query_purpose(client: TestClient, purpose: object) -> None:
    response = client.post("/api/queries", json={"text": "Синтетический текст", "purpose": purpose})
    assert_validation_error(response, PURPOSE_MESSAGE)


def test_missing_query_purpose(client: TestClient) -> None:
    assert_validation_error(client.post("/api/queries", json={"text": "Текст"}), PURPOSE_MESSAGE)


@pytest.mark.parametrize("data", [{}, {"purpose": "xxx"}])
def test_missing_or_invalid_upload_purpose(client: TestClient, data: dict[str, str]) -> None:
    response = client.post(
        "/api/documents", data=data, files={"file": ("synthetic.pdf", b"%PDF-test")},
    )
    assert_validation_error(response, PURPOSE_MESSAGE)


@pytest.mark.parametrize(
    "text", ["", " ", "\t\n\r", "\u00a0\u2003", " " * 200_001],
    ids=["empty", "space", "newlines", "unicode-spaces", "long-spaces"],
)
def test_empty_and_whitespace_text(
    client: TestClient, text: str, validation_db: tuple[Session, Mock], tmp_path: Path,
) -> None:
    response = client.post("/api/queries", json={"text": text, "purpose": "publication"})
    assert_validation_error(response, "Введите текст для проверки.")
    assert validation_db[0].scalar(select(TaskRow)) is None
    validation_db[1].assert_not_called()
    assert not list(tmp_path.iterdir())


def test_missing_text(client: TestClient) -> None:
    response = client.post("/api/queries", json={"purpose": "publication"})
    assert_validation_error(response, "Введите текст для проверки.")


@pytest.mark.parametrize("text", [None, 123, ["Синтетический текст"]])
def test_wrong_text_type(client: TestClient, text: object) -> None:
    response = client.post("/api/queries", json={"text": text, "purpose": "publication"})
    assert_validation_error(response, "Неверный запрос: проверьте поле «text».")


@pytest.mark.parametrize("text", ["А" * 200_001, "🧪" * 200_001], ids=["cyrillic", "emoji"])
def test_text_over_limit(client: TestClient, text: str) -> None:
    response = client.post("/api/queries", json={"text": text, "purpose": "publication"})
    assert_validation_error(response, LONG_TEXT_MESSAGE)


@pytest.mark.parametrize("purpose", ["internal", "publication", "contract", "international"])
@pytest.mark.parametrize("text", ["Я", "\n" + "Я" * 199_997 + "🧪 "], ids=["min", "max"])
def test_valid_text_boundaries_and_purposes(
    client: TestClient, validation_db: tuple[Session, Mock], purpose: str, text: str,
) -> None:
    response = client.post("/api/queries", json={"text": text, "purpose": purpose})
    assert response.status_code == 202
    document = validation_db[0].get(DocumentRow, response.json()["document_id"])
    assert document is not None
    assert Path(document.stored_path).read_text(encoding="utf-8") == text
    validation_db[1].assert_called_once_with(response.json()["task_id"])


def test_missing_file(client: TestClient) -> None:
    response = client.post("/api/documents", data={"purpose": "publication"})
    assert_validation_error(response, "Выберите файл для проверки.")


@pytest.mark.parametrize("corpus", ["../x", "BLIND", "", "a" * 33])
def test_invalid_metrics_corpus(client: TestClient, corpus: str) -> None:
    response = client.get("/api/metrics", params={"corpus": corpus})
    assert_validation_error(response, "Неверное имя набора данных.")


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(
    ("field", "name"), [item for item in RULE_FIELDS if item[0] != "applies_to"],
)
def test_missing_rule_field(client: TestClient, method: str, field: str, name: str) -> None:
    payload = rule_payload()
    del payload[field]
    path = "/api/rules" if method == "POST" else "/api/rules/SYNTHETIC_TEST_RULE"
    response = client.request(method, path, json=payload)
    assert_validation_error(response, f"Правило: не заполнено поле «{name}».")


@pytest.mark.parametrize("method", ["POST", "PUT"])
@pytest.mark.parametrize(("field", "name"), RULE_FIELDS)
def test_invalid_rule_field(client: TestClient, method: str, field: str, name: str) -> None:
    payload = {**rule_payload(), field: None}
    path = "/api/rules" if method == "POST" else "/api/rules/SYNTHETIC_TEST_RULE"
    response = client.request(method, path, json=payload)
    assert_validation_error(response, f"Правило: неверное значение поля «{name}».")


def test_invalid_nested_rule_field(client: TestClient) -> None:
    payload = {**rule_payload(), "applies_to": ["xxx"]}
    response = client.post("/api/rules", json=payload)
    assert_validation_error(response, "Правило: неверное значение поля «назначения документов».")


def test_invalid_rule_severity_value(client: TestClient) -> None:
    response = client.post("/api/rules", json={**rule_payload(), "severity": "green"})
    assert_validation_error(response, "Правило: неверное значение поля «уровень риска».")


def test_missing_nested_law_field(client: TestClient) -> None:
    payload = {**rule_payload(), "law_refs": [{"article": "п. 1"}]}
    response = client.post("/api/rules", json=payload)
    assert_validation_error(response, "Правило: не заполнено поле «ссылки на законы».")


def test_valid_rule_still_creates_and_updates(client: TestClient) -> None:
    payload = rule_payload()
    assert client.post("/api/rules", json=payload).status_code == 201
    response = client.put("/api/rules/SYNTHETIC_TEST_RULE", json=payload)
    assert response.status_code == 200
    assert response.json()["title"] == payload["title"]


def test_first_error_is_used(client: TestClient) -> None:
    response = client.post("/api/queries", json={"text": "", "purpose": "xxx"})
    assert_validation_error(response, "Введите текст для проверки.")


@pytest.mark.parametrize("too_long", [False, True])
def test_validation_does_not_echo_document_or_log_it(
    client: TestClient, caplog: pytest.LogCaptureFixture, too_long: bool,
) -> None:
    text = SYNTHETIC_NAME + ("А" * 200_000 if too_long else " — синтетический документ")
    response = client.post("/api/queries", json={"text": text, "purpose": "xxx"})
    assert_validation_error(response, LONG_TEXT_MESSAGE if too_long else PURPOSE_MESSAGE)
    assert SYNTHETIC_NAME not in response.text + caplog.text
    assert text not in response.text + caplog.text
    assert set(response.json()) == {"detail"}


def test_generic_validation_error(client: TestClient) -> None:
    response = client.get("/api/reports/synthetic/export", params={"format": "xxx"})
    assert_validation_error(response, "Неверный запрос: проверьте поле «format».")


def test_malformed_json_does_not_echo_input(client: TestClient) -> None:
    response = client.post(
        "/api/queries", content='{"text": "' + SYNTHETIC_NAME,
        headers={"Content-Type": "application/json"},
    )
    assert_validation_error(response, "Неверный запрос: проверьте поле «запрос».")
    assert SYNTHETIC_NAME not in response.text


def test_swagger_and_openapi_match_validation_contract(client: TestClient) -> None:
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    schema = client.get("/openapi.json").json()
    for operations in schema["paths"].values():
        for operation in operations.values():
            error = operation["responses"]["422"]["content"]["application/json"]["schema"]
            assert error["properties"]["detail"]["type"] == "string"
    text = schema["components"]["schemas"]["QueryRequest"]["properties"]["text"]
    assert text["minLength"] == 1
    assert text["maxLength"] == 200_000
