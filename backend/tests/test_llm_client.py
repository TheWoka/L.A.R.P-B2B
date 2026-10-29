"""Тесты LLM-клиента (T-002). Без сети, без Redis и без ключа API."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from enum import Enum
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings, get_settings
from app.services.llm import client as llm_client
from app.services.llm.cache import CACHE_KEY_PREFIX, CACHE_TTL_S, RedisCache, make_cache_key
from app.services.llm.client import (
    LLMBudgetExceeded,
    LLMError,
    LLMRequestError,
    LLMResponseError,
    complete_json,
    is_llm_enabled,
    llm_call_budget,
    reset_mock_responses,
    run_complete_json,
    set_mock_response,
)
from app.services.llm.providers import MAX_ANSWER_TOKENS

FAKE_KEY = "sk-test-NOT-A-REAL-KEY-12345"
USER_TEXT = "Договор с [PERSON_1], СНИЛС [SNILS_1], секретная фраза zebra-42"
SYSTEM = "Ты аудитор документов закупок."


# ---------------------------------------------------------------- схемы ---


class Severity(str, Enum):
    low = "low"
    high = "high"


class Inner(BaseModel):
    flag: bool
    note: str


class Answer(BaseModel):
    found: bool
    quote: str
    count: int
    score: float
    items: list[str]
    maybe: str | None
    severity: Severity
    kind: Literal["none", "violation"]
    inner: Inner
    with_default: str = "по умолчанию"
    tags: list[str] = Field(default_factory=lambda: ["t"])


class Verdict(BaseModel):
    answer: bool
    quote: str


# -------------------------------------------------------------- фикстуры ---


class MemoryCache:
    """Кэш в памяти по протоколу Cache."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self.data[key] = value
        self.ttls[key] = ttl


class FakeLLM:
    """httpx.MockTransport с очередью ответов и журналом запросов."""

    def __init__(self, responses: list[httpx.Response | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else ok({"answer": False, "quote": ""})
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def ok(payload: dict | str, usage: dict | None = None) -> httpx.Response:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    data = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return httpx.Response(200, json=data)


def real_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "llm_provider": "openai_compatible",
        "llm_base_url": "http://llm.test/v1",
        "llm_api_key": FAKE_KEY,
        "llm_model": "test-model",
        "redis_url": "redis://localhost:1/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    reset_mock_responses()
    llm_client._fallback_warned = False
    yield
    reset_mock_responses()


@pytest.fixture
def no_sleep() -> list[float]:
    return []


def call(
    fake: FakeLLM,
    cache: MemoryCache | RedisCache,
    sleeps: list[float],
    user: str = USER_TEXT,
    schema: type[BaseModel] = Verdict,
    settings: Settings | None = None,
) -> BaseModel:
    return run_complete_json(
        SYSTEM,
        user,
        schema,
        settings=settings or real_settings(),
        cache=cache,
        transport=fake.transport,
        sleep=sleeps.append,
    )


# ------------------------------------------------------------------ mock ---


def test_mock_builds_minimal_safe_instance() -> None:
    result = complete_json(SYSTEM, USER_TEXT, Answer)
    assert isinstance(result, Answer)
    assert result.found is False
    assert result.quote == ""
    assert result.count == 0 and result.score == 0
    assert result.items == []
    assert result.maybe is None
    assert result.severity is Severity.low
    assert result.kind == "none"
    assert result.inner == Inner(flag=False, note="")
    assert result.with_default == "по умолчанию"
    assert result.tags == ["t"]


def test_mock_is_deterministic() -> None:
    assert complete_json(SYSTEM, "a", Answer) == complete_json(SYSTEM, "b", Answer)


def test_mock_works_without_key_and_redis() -> None:
    settings = Settings(
        _env_file=None, llm_provider="mock", llm_api_key="", redis_url="redis://localhost:1/0"
    )  # type: ignore[call-arg]
    fake = FakeLLM()
    result = run_complete_json(
        SYSTEM, USER_TEXT, Verdict, settings=settings, transport=fake.transport
    )
    assert result == Verdict(answer=False, quote="")
    assert fake.requests == []
    assert is_llm_enabled(settings) is False


def test_mock_override_and_reset() -> None:
    set_mock_response(Verdict, lambda: Verdict(answer=True, quote="[PERSON_1]"))
    assert complete_json(SYSTEM, USER_TEXT, Verdict) == Verdict(answer=True, quote="[PERSON_1]")
    reset_mock_responses()
    assert complete_json(SYSTEM, USER_TEXT, Verdict) == Verdict(answer=False, quote="")


def test_mock_is_not_cached() -> None:
    cache = MemoryCache()
    settings = Settings(_env_file=None, llm_provider="mock")  # type: ignore[call-arg]
    run_complete_json(SYSTEM, USER_TEXT, Verdict, settings=settings, cache=cache)
    assert cache.data == {}


def test_fallback_to_mock_without_base_url(caplog: pytest.LogCaptureFixture) -> None:
    settings = real_settings(llm_base_url="")
    fake = FakeLLM()
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            result = run_complete_json(
                SYSTEM, USER_TEXT, Verdict, settings=settings, transport=fake.transport
            )
    assert result == Verdict(answer=False, quote="")
    assert fake.requests == []
    assert is_llm_enabled(settings) is False
    assert is_llm_enabled(real_settings(llm_model="")) is False
    assert is_llm_enabled(real_settings()) is True
    assert sum("работаем в режиме mock" in r.getMessage() for r in caplog.records) == 1


# ----------------------------------------------------- openai_compatible ---


def test_openai_request_format(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok({"answer": True, "quote": "[PERSON_1]"})])
    result = call(fake, MemoryCache(), no_sleep)
    assert result == Verdict(answer=True, quote="[PERSON_1]")
    request = fake.requests[0]
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    body = fake.body()
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    system_msg, user_msg = body["messages"]
    assert system_msg["role"] == "system" and system_msg["content"].startswith(SYSTEM)
    assert '"quote"' in system_msg["content"] and "только JSON" in system_msg["content"]
    assert user_msg == {"role": "user", "content": USER_TEXT}


def test_openai_parses_fenced_json(no_sleep: list[float]) -> None:
    fenced = 'Вот ответ:\n```json\n{"answer": true, "quote": "x"}\n```'
    fake = FakeLLM([ok(fenced)])
    assert call(fake, MemoryCache(), no_sleep) == Verdict(answer=True, quote="x")


# ----------------------------------------------------------------- кэш ---


def test_cache_hit_skips_transport(no_sleep: list[float]) -> None:
    cache = MemoryCache()
    fake = FakeLLM([ok({"answer": True, "quote": "q"})])
    first = call(fake, cache, no_sleep)
    second = call(fake, cache, no_sleep)
    assert first == second == Verdict(answer=True, quote="q")
    assert len(fake.requests) == 1
    (key,) = cache.data
    assert key.startswith(CACHE_KEY_PREFIX) and len(key) == len(CACHE_KEY_PREFIX) + 64
    assert cache.ttls[key] == CACHE_TTL_S == 7 * 24 * 3600


def test_cache_miss_on_different_user(no_sleep: list[float]) -> None:
    cache = MemoryCache()
    fake = FakeLLM()
    call(fake, cache, no_sleep, user="текст 1")
    call(fake, cache, no_sleep, user="текст 2")
    assert len(fake.requests) == 2
    assert len(cache.data) == 2


def test_cache_key_depends_on_all_parts() -> None:
    base = make_cache_key("p", "m", "s", "u", Verdict)
    assert base == make_cache_key("p", "m", "s", "u", Verdict)
    assert base != make_cache_key("p", "m2", "s", "u", Verdict)
    assert base != make_cache_key("p", "m", "s2", "u", Verdict)
    assert base != make_cache_key("p", "m", "s", "u", Inner)


def test_failed_response_is_not_cached(no_sleep: list[float]) -> None:
    cache = MemoryCache()
    fake = FakeLLM([ok("не json"), ok("снова не json")])
    with pytest.raises(LLMResponseError):
        call(fake, cache, no_sleep)
    assert cache.data == {}


def test_redis_unavailable_works_without_cache(
    no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    cache = RedisCache("redis://localhost:1/0", timeout_s=0.2)
    fake = FakeLLM()
    with caplog.at_level(logging.WARNING):
        call(fake, cache, no_sleep)
        call(fake, cache, no_sleep)
    assert len(fake.requests) == 2
    assert cache.disabled is True
    assert sum("Redis недоступен" in r.getMessage() for r in caplog.records) == 1
    assert "localhost:1" not in repr(cache)


# ------------------------------------------------- невалидный JSON ---


def test_invalid_json_then_valid(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok({"answer": "может быть"}), ok({"answer": True, "quote": "q"})])
    assert call(fake, MemoryCache(), no_sleep) == Verdict(answer=True, quote="q")
    assert len(fake.requests) == 2
    retry_messages = fake.body(1)["messages"]
    assert [m["role"] for m in retry_messages] == ["system", "user", "assistant", "user"]
    assert "quote" in retry_messages[-1]["content"]  # текст ошибки валидации


def test_invalid_json_twice_raises(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok("```json\n{oops}\n```"), ok({"answer": "?"})])
    with pytest.raises(LLMResponseError) as exc_info:
        call(fake, MemoryCache(), no_sleep)
    assert len(fake.requests) == 2
    assert isinstance(exc_info.value, LLMError)


# ------------------------------------------------- HTTP-ошибки ---


def test_429_then_200_retries_without_sleeping(no_sleep: list[float]) -> None:
    fake = FakeLLM([httpx.Response(429), ok({"answer": True, "quote": ""})])
    assert call(fake, MemoryCache(), no_sleep) == Verdict(answer=True, quote="")
    assert len(fake.requests) == 2
    assert no_sleep == [1.0]


def test_5xx_and_network_errors_exhaust_attempts(no_sleep: list[float]) -> None:
    fake = FakeLLM([httpx.Response(503), httpx.ConnectError("boom"), httpx.Response(500)])
    with pytest.raises(LLMRequestError):
        call(fake, MemoryCache(), no_sleep)
    assert len(fake.requests) == 3
    assert no_sleep == [1.0, 3.0]


def test_400_raises_without_retry(no_sleep: list[float]) -> None:
    fake = FakeLLM([httpx.Response(400, json={"error": "bad"})])
    with pytest.raises(LLMRequestError) as exc_info:
        call(fake, MemoryCache(), no_sleep)
    assert len(fake.requests) == 1
    assert no_sleep == []
    assert "400" in str(exc_info.value)
    assert FAKE_KEY not in str(exc_info.value) and USER_TEXT not in str(exc_info.value)


# ------------------------------------------------- лимит вызовов ---


def test_budget_exceeded_on_third_real_call(no_sleep: list[float]) -> None:
    fake = FakeLLM()
    with llm_call_budget(2) as budget:
        call(fake, MemoryCache(), no_sleep, user="1")
        call(fake, MemoryCache(), no_sleep, user="2")
        with pytest.raises(LLMBudgetExceeded):
            call(fake, MemoryCache(), no_sleep, user="3")
    assert budget.used == 2
    assert len(fake.requests) == 2


def test_budget_ignores_cache_hits_and_mock(no_sleep: list[float]) -> None:
    cache = MemoryCache()
    fake = FakeLLM()
    with llm_call_budget(1) as budget:
        for _ in range(3):
            call(fake, cache, no_sleep)
        complete_json(SYSTEM, USER_TEXT, Verdict)  # mock из окружения тестов
    assert budget.used == 1
    assert len(fake.requests) == 1


def test_budget_counts_json_repair_as_call(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok("не json"), ok({"answer": True, "quote": ""})])
    with llm_call_budget(1):
        with pytest.raises(LLMBudgetExceeded):
            call(fake, MemoryCache(), no_sleep)
    assert len(fake.requests) == 1


def test_budget_nested_contexts(no_sleep: list[float]) -> None:
    fake = FakeLLM()
    with llm_call_budget(1) as outer:
        call(fake, MemoryCache(), no_sleep, user="outer")
        with llm_call_budget(2) as inner:
            call(fake, MemoryCache(), no_sleep, user="inner-1")
            call(fake, MemoryCache(), no_sleep, user="inner-2")
        with pytest.raises(LLMBudgetExceeded):
            call(fake, MemoryCache(), no_sleep, user="outer-2")
    assert (outer.used, inner.used) == (1, 2)


def test_budget_parallel_threads_are_isolated() -> None:
    errors: list[BaseException] = []
    used: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        fake = FakeLLM()
        try:
            with llm_call_budget(2) as budget:
                for i in range(2):
                    barrier.wait(timeout=5)
                    call(fake, MemoryCache(), [], user=f"{name}-{i}")
                used[name] = budget.used
        except BaseException as exc:  # noqa: BLE001 — передаём в основной поток
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("doc-a", "doc-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert used == {"doc-a": 2, "doc-b": 2}


def test_no_budget_outside_context(no_sleep: list[float]) -> None:
    fake = FakeLLM()
    for i in range(5):
        call(fake, MemoryCache(), no_sleep, user=str(i))
    assert len(fake.requests) == 5


def test_budget_default_from_settings() -> None:
    with llm_call_budget() as budget:
        assert budget.limit == get_settings().llm_max_calls_per_doc
    with pytest.raises(ValueError):
        with llm_call_budget(-1):
            pass


# ------------------------------------------------- безопасность логов ---


def test_logs_contain_no_prompt_answer_or_key(
    no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    answer_quote = "ответ-с-уникальной-цитатой-777"
    fake = FakeLLM(
        [
            httpx.Response(500),
            ok("мусор"),
            ok({"answer": True, "quote": answer_quote}),
        ]
    )
    cache = MemoryCache()
    with caplog.at_level(logging.DEBUG):
        call(fake, cache, no_sleep)
        call(fake, cache, no_sleep)  # попадание в кэш тоже логируется
    text = caplog.text
    assert "LLM вызов" in text and "cache_hit=True" in text and "total_tokens=15" in text
    for secret in (USER_TEXT, "zebra-42", SYSTEM, FAKE_KEY, answer_quote, "мусор"):
        assert secret not in text


# ------------------------------------------- таймаут LLM_TIMEOUT_S (T-029b) ---


def _timeouts(request: httpx.Request) -> dict[str, float | None]:
    """Таймауты httpx, с которыми ушёл запрос (connect/read/write/pool)."""
    return dict(request.extensions["timeout"])


def test_timeout_default_is_request_timeout_constant(no_sleep: list[float]) -> None:
    from app.services.llm.providers import REQUEST_TIMEOUT_S

    fake = FakeLLM([ok({"answer": False, "quote": ""})])
    call(fake, MemoryCache(), no_sleep)
    assert REQUEST_TIMEOUT_S == 30.0
    assert set(_timeouts(fake.requests[0]).values()) == {REQUEST_TIMEOUT_S}


def test_timeout_from_env_reaches_httpx_client(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float],
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", "120")
    settings = real_settings()  # llm_timeout_s не передаём — значение только из окружения
    assert settings.llm_timeout_s == 120.0
    fake = FakeLLM([ok({"answer": False, "quote": ""})])
    call(fake, MemoryCache(), no_sleep, settings=settings)
    assert set(_timeouts(fake.requests[0]).values()) == {120.0}


def test_timeout_fractional_value_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", "0.5")
    assert real_settings().llm_timeout_s == 0.5


@pytest.mark.parametrize("value", ["0", "-1", "-0.5", "abc", "inf", "nan"])
def test_timeout_invalid_value_is_settings_error(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", value)
    with pytest.raises(ValidationError):
        real_settings()


def test_timeout_exhausts_attempts_and_is_not_cached(no_sleep: list[float]) -> None:
    fake = FakeLLM([httpx.ReadTimeout("t")] * 3)
    cache = MemoryCache()
    with pytest.raises(LLMRequestError, match="таймаут"):
        call(fake, cache, no_sleep, settings=real_settings(llm_timeout_s=0.01))
    assert len(fake.requests) == 3 and no_sleep == [1.0, 3.0]
    assert cache.data == {}


def test_timeout_in_condition_gives_unknown_without_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.llm import checks, providers

    settings = real_settings(llm_timeout_s=0.01)
    fake = FakeLLM([httpx.ReadTimeout("t")] * 3)

    def request_chat(s: Settings, messages: list[dict[str, str]], **_: object) -> object:
        return providers.request_chat(s, messages, transport=fake.transport, sleep=lambda _: None)

    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "get_default_cache", lambda url: MemoryCache())
    monkeypatch.setattr(llm_client, "request_chat", request_chat)
    text = "Исполнитель принимает необходимые меры для защиты сведений."
    with llm_call_budget() as budget:
        outcome = checks.ask_condition("Меры описаны конкретно?", text, [], "R1", report_text=text)
    assert outcome.verdict == "unknown" and outcome.evidence is None
    assert [c.verdict for c in outcome.calls] == ["unknown"]
    assert budget.used == 1 and len(fake.requests) == 3


# ---------------------------------------- T-019b: потолок длины ответа ---


def truncated(content: str = '{"items": [{"quote": "наценка 1') -> httpx.Response:
    """Ответ, обрезанный на max_tokens: finish_reason="length", JSON неполный."""
    data = {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 400, "total_tokens": 410}}
    return httpx.Response(200, json=data)


def test_request_body_limits_answer_length(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok({"answer": True, "quote": "x"})])
    call(fake, MemoryCache(), no_sleep)
    assert fake.body()["max_tokens"] == MAX_ANSWER_TOKENS == 400


def test_truncated_answer_fails_without_repair_and_cache(no_sleep: list[float]) -> None:
    fake = FakeLLM([truncated(), ok({"answer": True, "quote": "x"})])
    cache = MemoryCache()
    with pytest.raises(LLMResponseError, match="обрезан"):
        call(fake, cache, no_sleep)
    assert len(fake.requests) == 1                 # повтор с «исправь JSON» не тратится
    assert cache.data == {}


def test_finish_reason_stop_is_parsed_as_usual(no_sleep: list[float]) -> None:
    data = {"choices": [{"message": {"content": '{"answer": true, "quote": "x"}'},
                         "finish_reason": "stop"}]}
    fake = FakeLLM([httpx.Response(200, json=data)])
    assert call(fake, MemoryCache(), no_sleep) == Verdict(answer=True, quote="x")


# ------------------------------- T-066: reasoning_effort для моделей с рассуждениями ---


def test_reasoning_effort_absent_by_default(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float],
) -> None:
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    settings = real_settings()
    assert settings.llm_reasoning_effort is None
    fake = FakeLLM([ok({"answer": True, "quote": "x"})])
    call(fake, MemoryCache(), no_sleep, settings=settings)
    assert "reasoning_effort" not in fake.body()      # облако получает прежнее тело


def test_reasoning_effort_from_env_reaches_request(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float],
) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", "none")
    settings = real_settings()  # значение только из окружения, как в docker-compose
    assert settings.llm_reasoning_effort == "none"
    fake = FakeLLM([ok({"answer": True, "quote": "x"})])
    call(fake, MemoryCache(), no_sleep, settings=settings)
    body = fake.body()
    assert body["reasoning_effort"] == "none"
    assert body["max_tokens"] == MAX_ANSWER_TOKENS     # потолок ответа не меняется


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_reasoning_effort_blank_is_not_sent(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float], value: str,
) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", value)  # строка LLM_REASONING_EFFORT= в .env
    settings = real_settings()
    assert settings.llm_reasoning_effort is None
    fake = FakeLLM([ok({"answer": True, "quote": "x"})])
    call(fake, MemoryCache(), no_sleep, settings=settings)
    assert "reasoning_effort" not in fake.body()


def test_reasoning_effort_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", "  low ")
    assert real_settings().llm_reasoning_effort == "low"


def test_reasoning_effort_sent_on_json_repair_too(no_sleep: list[float]) -> None:
    fake = FakeLLM([ok("не json"), ok({"answer": True, "quote": "x"})])
    call(fake, MemoryCache(), no_sleep, settings=real_settings(llm_reasoning_effort="none"))
    assert len(fake.requests) == 2
    assert [fake.body(i)["reasoning_effort"] for i in range(2)] == ["none", "none"]


def test_reasoning_effort_mock_makes_no_http_requests() -> None:
    settings = Settings(
        _env_file=None, llm_provider="mock", llm_reasoning_effort="none",
        redis_url="redis://localhost:1/0",
    )  # type: ignore[call-arg]
    fake = FakeLLM()
    result = run_complete_json(
        SYSTEM, USER_TEXT, Verdict, settings=settings, transport=fake.transport
    )
    assert result == Verdict(answer=False, quote="")
    assert fake.requests == []
