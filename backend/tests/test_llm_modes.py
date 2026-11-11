"""T-073: режимы нейросети mock / local / cloud — настройки, доступность, API, конвейер.

Без сети, без Ollama и без настоящего ключа: HTTP подменяется httpx.MockTransport.
"""

import json
import logging
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import documents
from app.core.config import Settings
from app.db.models import Base, DocumentRow, ReportRow, TaskRow
from app.db.session import get_db
from app.main import app
from app.schemas import Report
from app.services import pipeline, storage
from app.services.llm import client as llm_client
from app.services.llm import modes, providers
from app.services.llm.client import (
    LLMRequestError,
    complete_json,
    current_mode,
    effective_settings,
    llm_call_budget,
    use_llm_mode,
)
from app.worker import tasks
from tests.test_pipeline import make_report

FAKE_KEY = "sk-test-NOT-A-REAL-KEY"
LEGACY_KEY = "sk-test-LEGACY-NOT-REAL"
LOCAL_URL = "http://ollama:11434/v1"
CLOUD_URL = "https://api.example.com/v1"
LOCAL_MODEL = "qwen-synthetic:4b"
CLOUD_MODEL = "cloud-synthetic-mini"
SYSTEM = "Ответь по схеме."
USER = "Исполнитель [PERSON_1] обязуется хранить сведения."  # уже замаскировано


class Verdict(BaseModel):
    answer: bool


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, redis_url="redis://localhost:1/0", **values)  # type: ignore[call-arg]


def all_modes(**values: object) -> Settings:
    """Заданы все три режима; по умолчанию — mock."""
    base: dict[str, object] = {
        "llm_default_mode": "mock",
        "llm_local_base_url": LOCAL_URL, "llm_local_model": LOCAL_MODEL,
        "llm_cloud_base_url": CLOUD_URL, "llm_cloud_api_key": FAKE_KEY,
        "llm_cloud_model": CLOUD_MODEL, "llm_reasoning_effort": "none",
    }
    base.update(values)
    return make_settings(**base)


def models_response(*ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": i} for i in ids]})


def chat_ok() -> httpx.Response:
    content = json.dumps({"answer": True})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class Recorder:
    """MockTransport с журналом запросов: handler решает, что ответить."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self.data[key] = value


@pytest.fixture(autouse=True)
def _fresh_modes_cache() -> Iterator[None]:
    modes.reset_modes_cache()
    yield
    modes.reset_modes_cache()


def use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """Одни и те же настройки для режимов (API, воркер) и для клиента LLM."""
    monkeypatch.setattr(modes, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)


# --------------------------------------------- старый .env и resolve_mode_configs ---


@pytest.mark.parametrize(("provider", "base_url", "expected"), [
    ("mock", "", "mock"),
    ("mock", CLOUD_URL, "mock"),
    ("openai_compatible", "http://ollama:11434/v1", "local"),
    ("openai_compatible", "http://localhost:11434/v1", "local"),
    ("openai_compatible", "http://host.docker.internal:11434/v1", "local"),
    ("openai_compatible", "https://api.example.com/v1", "cloud"),
    ("openai_compatible", "", "cloud"),
])
def test_legacy_env_gives_default_mode(provider: str, base_url: str, expected: str) -> None:
    settings = make_settings(llm_provider=provider, llm_base_url=base_url)
    assert settings.llm_default_mode is None
    assert modes.default_mode(settings) == expected


def test_explicit_default_mode_wins_over_legacy() -> None:
    settings = make_settings(
        llm_default_mode="mock", llm_provider="openai_compatible", llm_base_url=CLOUD_URL,
    )
    assert modes.default_mode(settings) == "mock"


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_default_mode_is_not_set(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LLM_DEFAULT_MODE", value)  # строка LLM_DEFAULT_MODE= в .env
    assert make_settings().llm_default_mode is None


def test_unknown_default_mode_is_settings_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_DEFAULT_MODE", "gpt")
    with pytest.raises(ValueError):
        make_settings()


def test_legacy_local_fills_local_config_only() -> None:
    settings = make_settings(
        llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama", llm_model=LOCAL_MODEL,
    )
    configs = modes.resolve_mode_configs(settings)
    local, cloud = configs["local"], configs["cloud"]
    assert (local.provider, local.base_url, local.model) == (
        "openai_compatible", "http://localhost:11434/v1", LOCAL_MODEL,
    )
    assert local.api_key == "ollama"
    assert (cloud.base_url, cloud.api_key, cloud.model) == ("", "", "")
    assert configs["mock"].provider == "mock"


def test_legacy_cloud_fills_cloud_config_only() -> None:
    settings = make_settings(
        llm_provider="openai_compatible", llm_base_url=CLOUD_URL,
        llm_api_key=LEGACY_KEY, llm_model=CLOUD_MODEL,
    )
    configs = modes.resolve_mode_configs(settings)
    assert (configs["cloud"].base_url, configs["cloud"].api_key, configs["cloud"].model) == (
        CLOUD_URL, LEGACY_KEY, CLOUD_MODEL,
    )
    assert (configs["local"].base_url, configs["local"].model) == (LOCAL_URL, "")
    assert configs["local"].api_key == ""      # облачный ключ в Ollama не уходит


def test_new_variables_win_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_LOCAL_BASE_URL", "http://ollama:11434/v1")
    settings = make_settings(
        llm_provider="openai_compatible", llm_base_url="http://localhost:11434/v1",
        llm_model="old-model", llm_local_model=LOCAL_MODEL,
    )
    local = modes.config_for("local", settings)
    assert (local.base_url, local.model) == ("http://ollama:11434/v1", LOCAL_MODEL)


def test_new_variables_without_legacy() -> None:
    configs = modes.resolve_mode_configs(all_modes())
    assert (configs["local"].base_url, configs["local"].model) == (LOCAL_URL, LOCAL_MODEL)
    assert (configs["cloud"].api_key, configs["cloud"].model) == (FAKE_KEY, CLOUD_MODEL)


def test_mode_config_repr_hides_key() -> None:
    assert FAKE_KEY not in repr(modes.config_for("cloud", all_modes()))


# ------------------------------------------------------------ доступность ---


def test_local_unavailable_without_ollama() -> None:
    probe = Recorder(refuse)
    result = modes.compute_modes(all_modes(), probe.transport)
    local = result.modes[1]
    assert (local.mode, local.available, local.reason) == ("local", False, "Ollama не запущена")
    assert [str(r.url) for r in probe.requests] == [f"{LOCAL_URL}/models"]


def test_local_unavailable_on_http_error() -> None:
    probe = Recorder(lambda request: httpx.Response(500))
    local = modes.compute_modes(all_modes(), probe.transport).modes[1]
    assert (local.available, local.reason) == (False, "Ollama не запущена")


def test_local_model_not_pulled() -> None:
    probe = Recorder(lambda request: models_response("other-model:latest"))
    local = modes.compute_modes(all_modes(), probe.transport).modes[1]
    assert local.available is False
    assert local.reason == f"Модель {LOCAL_MODEL} не скачана: ollama pull {LOCAL_MODEL}"
    assert local.model == LOCAL_MODEL


def test_local_model_without_tag_matches_latest() -> None:
    probe = Recorder(lambda request: models_response("gemma-synthetic:latest"))
    settings = all_modes(llm_local_model="gemma-synthetic")
    local = modes.compute_modes(settings, probe.transport).modes[1]
    assert (local.available, local.reason) == (True, None)


def test_local_available_when_model_present() -> None:
    probe = Recorder(lambda request: models_response(LOCAL_MODEL))
    result = modes.compute_modes(all_modes(), probe.transport)
    assert [(m.mode, m.available) for m in result.modes] == [
        ("mock", True), ("local", True), ("cloud", True),
    ]


def test_local_without_model_makes_no_request() -> None:
    probe = Recorder(refuse)
    local = modes.compute_modes(all_modes(llm_local_model=""), probe.transport).modes[1]
    assert (local.available, local.reason) == (False, "Не задана LLM_LOCAL_MODEL")
    assert probe.requests == []


@pytest.mark.parametrize("missing", ["llm_cloud_api_key", "llm_cloud_model", "llm_cloud_base_url"])
def test_cloud_unavailable_without_key_or_model(missing: str) -> None:
    probe = Recorder(lambda request: models_response(LOCAL_MODEL))
    cloud = modes.compute_modes(all_modes(**{missing: ""}), probe.transport).modes[2]
    assert (cloud.available, cloud.reason) == (False, "Нет ключа или модели в .env")
    assert all("api.example.com" not in str(r.url) for r in probe.requests)  # наружу не ходим


def test_modes_cached_for_ten_seconds() -> None:
    probe = Recorder(lambda request: models_response(LOCAL_MODEL))
    now = [100.0]
    settings = all_modes()
    first = modes.available_modes(settings, probe.transport, clock=lambda: now[0])
    now[0] = 109.9
    assert modes.available_modes(settings, probe.transport, clock=lambda: now[0]) is first
    assert len(probe.requests) == 1
    now[0] = 110.1
    modes.available_modes(settings, probe.transport, clock=lambda: now[0])
    assert len(probe.requests) == 2


def test_mock_mode_info_does_not_probe_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modes, "compute_modes", Mock(side_effect=AssertionError("probe")))
    assert modes.mode_info("mock").available is True


# --------------------------------------------------------- GET /api/llm/modes ---


def test_modes_endpoint_never_returns_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    use_settings(monkeypatch, all_modes(llm_local_model="", llm_api_key=LEGACY_KEY))
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/llm/modes")
    assert response.status_code == 200
    assert FAKE_KEY not in response.text and LEGACY_KEY not in response.text
    assert FAKE_KEY not in caplog.text
    body = response.json()
    assert body["default"] == "mock"
    assert [(m["mode"], m["available"]) for m in body["modes"]] == [
        ("mock", True), ("local", False), ("cloud", True),
    ]
    assert body["modes"][2]["model"] == CLOUD_MODEL
    assert set(body["modes"][2]) == {"mode", "available", "model", "reason"}


def test_modes_endpoint_with_default_settings(client: TestClient) -> None:
    body = client.get("/api/llm/modes").json()   # тестовое окружение: mock, ключей нет
    assert body["default"] == "mock"
    assert [(m["mode"], m["available"]) for m in body["modes"]] == [
        ("mock", True), ("local", False), ("cloud", False),
    ]


# ------------------------------------------------ POST /api/documents, /queries ---


@pytest.fixture
def api_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Session, Mock]]:
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


def docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("Синтетический документ закупки")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def upload(client: TestClient, **data: str) -> httpx.Response:
    return client.post(
        "/api/documents", files={"file": ("synthetic.docx", docx_bytes())},
        data={"purpose": "internal", **data},
    )


def test_upload_cloud_without_key_is_400(
    client: TestClient, api_db: tuple[Session, Mock], tmp_path: Path,
) -> None:
    session, delay = api_db
    response = upload(client, llm_mode="cloud")
    assert response.status_code == 400
    assert response.json() == {"detail": "Режим недоступен: Нет ключа или модели в .env"}
    assert session.scalars(select(DocumentRow)).all() == []
    assert list(tmp_path.iterdir()) == []
    delay.assert_not_called()


def test_upload_mock_is_saved_in_document(
    client: TestClient, api_db: tuple[Session, Mock],
) -> None:
    session, delay = api_db
    response = upload(client, llm_mode="mock")
    assert response.status_code == 202
    assert session.get(DocumentRow, response.json()["document_id"]).llm_mode == "mock"
    delay.assert_called_once()


def test_upload_without_mode_uses_default(
    client: TestClient, api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = api_db
    monkeypatch.setattr(modes, "compute_modes", Mock(side_effect=AssertionError("no probe")))
    use_settings(monkeypatch, all_modes(llm_default_mode="local"))
    response = upload(client)
    assert response.status_code == 202      # режим по умолчанию при загрузке не проверяется
    assert session.get(DocumentRow, response.json()["document_id"]).llm_mode == "local"


def test_upload_cloud_with_key_is_accepted(
    client: TestClient, api_db: tuple[Session, Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _ = api_db
    use_settings(monkeypatch, all_modes(llm_local_model=""))
    response = upload(client, llm_mode="cloud")
    assert response.status_code == 202
    assert session.get(DocumentRow, response.json()["document_id"]).llm_mode == "cloud"
    assert FAKE_KEY not in response.text


def test_upload_unknown_mode_is_422(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    response = upload(client, llm_mode="gpt")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_query_modes(client: TestClient, api_db: tuple[Session, Mock]) -> None:
    session, delay = api_db
    denied = client.post(
        "/api/queries", json={"text": "Запрос", "purpose": "internal", "llm_mode": "local"},
    )
    assert denied.status_code == 400
    assert denied.json()["detail"] == "Режим недоступен: Не задана LLM_LOCAL_MODEL"
    accepted = client.post(
        "/api/queries", json={"text": "Запрос", "purpose": "internal", "llm_mode": "mock"},
    )
    assert accepted.status_code == 202
    assert session.get(DocumentRow, accepted.json()["document_id"]).llm_mode == "mock"
    delay.assert_called_once()


# ------------------------------------------------ use_llm_mode и complete_json ---


@pytest.fixture
def llm_http(monkeypatch: pytest.MonkeyPatch) -> tuple[Recorder, MemoryCache]:
    """Настоящий complete_json, HTTP — через MockTransport, кэш — в памяти."""
    fake = Recorder(lambda request: chat_ok())
    cache = MemoryCache()

    def request_chat(settings: Settings, messages: list[dict[str, str]], **_: object) -> object:
        return providers.request_chat(settings, messages, transport=fake.transport,
                                      sleep=lambda _: None)

    monkeypatch.setattr(llm_client, "request_chat", request_chat)
    monkeypatch.setattr(llm_client, "get_default_cache", lambda url: cache)
    use_settings(monkeypatch, all_modes())
    return fake, cache


def test_local_mode_goes_to_local_url_and_model(llm_http: tuple[Recorder, MemoryCache]) -> None:
    fake, _ = llm_http
    with use_llm_mode("local"):
        assert complete_json(SYSTEM, USER, Verdict) == Verdict(answer=True)
    request = fake.requests[0]
    body = json.loads(request.content)
    assert str(request.url) == f"{LOCAL_URL}/chat/completions"
    assert body["model"] == LOCAL_MODEL
    assert body["reasoning_effort"] == "none"
    assert FAKE_KEY not in request.headers["Authorization"]


def test_cloud_mode_uses_cloud_key_without_reasoning_effort(
    llm_http: tuple[Recorder, MemoryCache],
) -> None:
    fake, _ = llm_http
    with use_llm_mode("cloud"):
        complete_json(SYSTEM, USER, Verdict)
    request = fake.requests[0]
    body = json.loads(request.content)
    assert str(request.url) == f"{CLOUD_URL}/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert body["model"] == CLOUD_MODEL
    assert "reasoning_effort" not in body


def test_mock_mode_makes_no_requests(llm_http: tuple[Recorder, MemoryCache]) -> None:
    fake, cache = llm_http
    with use_llm_mode("mock"):
        assert complete_json(SYSTEM, USER, Verdict) == Verdict(answer=False)
    assert fake.requests == [] and cache.data == {}


def test_default_mode_outside_block(llm_http: tuple[Recorder, MemoryCache]) -> None:
    fake, _ = llm_http
    assert current_mode() == "mock"            # LLM_DEFAULT_MODE=mock
    complete_json(SYSTEM, USER, Verdict)
    assert fake.requests == []


def test_use_llm_mode_nests_and_restores(llm_http: tuple[Recorder, MemoryCache]) -> None:
    with use_llm_mode("cloud"):
        with use_llm_mode("local"):
            assert current_mode() == "local"
            assert effective_settings().llm_model == LOCAL_MODEL
        assert current_mode() == "cloud"
        assert effective_settings().llm_api_key == FAKE_KEY
    assert current_mode() == "mock"
    assert effective_settings().llm_provider == "mock"


def test_cache_keys_differ_between_local_and_cloud(
    llm_http: tuple[Recorder, MemoryCache], monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, cache = llm_http
    use_settings(monkeypatch, all_modes(llm_cloud_model=LOCAL_MODEL))  # даже одна и та же модель
    with use_llm_mode("local"):
        complete_json(SYSTEM, USER, Verdict)
    with use_llm_mode("cloud"):
        complete_json(SYSTEM, USER, Verdict)
    with use_llm_mode("mock"):
        complete_json(SYSTEM, USER, Verdict)
    assert len(fake.requests) == 2 and len(cache.data) == 2   # mock не кэшируется
    with use_llm_mode("cloud"):
        complete_json(SYSTEM, USER, Verdict)
    assert len(fake.requests) == 2                             # повтор — из кэша своего режима


def test_unavailable_mode_fails_fast_without_budget(
    llm_http: tuple[Recorder, MemoryCache],
) -> None:
    fake, _ = llm_http
    with llm_call_budget() as budget, use_llm_mode("local", available=False):
        with pytest.raises(LLMRequestError, match="недоступен"):
            complete_json(SYSTEM, USER, Verdict)
    assert fake.requests == [] and budget.used == 0


def test_unavailable_mode_gives_unknown_condition(
    llm_http: tuple[Recorder, MemoryCache],
) -> None:
    from app.services.llm import checks

    fake, _ = llm_http
    text = "Исполнитель принимает необходимые меры для защиты сведений."
    with llm_call_budget(), use_llm_mode("local", available=False):
        outcome = checks.ask_condition("Меры описаны конкретно?", text, [], "R1", report_text=text)
    assert outcome.verdict == "unknown" and fake.requests == []


# ------------------------------------------------- воркер и след проверки ---


@pytest.fixture
def worker_db() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def run_text_task(session: Session, tmp_path: Path, llm_mode: str | None) -> Report:
    path = tmp_path / "synthetic.txt"
    path.write_text("Синтетический запрос без персональных данных.", encoding="utf-8")
    session.add(DocumentRow(
        id="synthetic-document", filename=None, purpose="internal",
        stored_path=str(path), llm_mode=llm_mode,
    ))
    session.flush()
    session.add(TaskRow(id="synthetic-task", document_id="synthetic-document", status="queued"))
    session.commit()
    tasks.run_pipeline("synthetic-task", session)
    task = session.get(TaskRow, "synthetic-task")
    assert task.status == "done", task.error
    return Report.model_validate(session.get(ReportRow, task.report_id).data)


@pytest.mark.parametrize(("stored", "expected"), [("mock", "mock"), (None, "mock")])
def test_report_audit_has_document_mode(
    worker_db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    stored: str | None, expected: str,
) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)
    report = run_text_task(worker_db, tmp_path, stored)
    assert report.audit.llm_mode == expected
    assert report.audit.llm_provider == "mock" and report.audit.llm_model is None
    assert report.llm_used is False


def test_unavailable_local_mode_does_not_fail_check(
    worker_db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(pipeline, "build_report", make_report)
    use_settings(monkeypatch, all_modes())
    monkeypatch.setattr(modes, "compute_modes", _down_modes)
    with caplog.at_level(logging.INFO):
        report = run_text_task(worker_db, tmp_path, "local")
    assert report.audit.llm_mode == "local"
    assert report.audit.llm_calls == 0 and report.llm_used is False
    assert "режим local недоступен" in caplog.text
    assert LOCAL_URL not in caplog.text and FAKE_KEY not in caplog.text


def _down_modes(settings: Settings) -> modes.LlmModesResponse:
    """compute_modes без Ollama: соединение отклонено."""
    configs = modes.resolve_mode_configs(settings)
    return modes.LlmModesResponse(
        default=modes.default_mode(settings),
        modes=[
            modes.LlmModeInfo(mode="mock", available=True),
            modes.probe_local(configs["local"], Recorder(refuse).transport),
            modes.check_cloud(configs["cloud"]),
        ],
    )


def test_cloud_mode_in_audit_trail(
    worker_db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    llm_http: tuple[Recorder, MemoryCache],
) -> None:
    fake, _ = llm_http                       # наружу не уходит: HTTP подменён
    monkeypatch.setattr(pipeline, "build_report", make_report)
    use_settings(monkeypatch, all_modes(llm_local_model=""))
    report = run_text_task(worker_db, tmp_path, "cloud")
    assert all(str(r.url).startswith(CLOUD_URL) for r in fake.requests)
    assert report.audit.llm_mode == "cloud"
    assert report.audit.llm_provider == "openai_compatible"
    assert report.audit.llm_model == CLOUD_MODEL
    assert FAKE_KEY not in json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
