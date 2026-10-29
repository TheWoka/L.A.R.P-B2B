"""Провайдеры LLM: mock и openai_compatible. Внутренний модуль T-002, не контракт.

Публичный вход — `client.complete_json`. Здесь только механика: построение
ответа mock, HTTP-запрос к OpenAI-совместимому API и разбор JSON-ответа.

Безопасность (AGENTS.md, правила 3–5): тексты промптов, ответов и ключ API
не попадают ни в логи, ни в сообщения исключений.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import DEFAULT_LLM_TIMEOUT_S, Settings

logger = logging.getLogger(__name__)

# Значение по умолчанию; фактический таймаут — Settings.llm_timeout_s (LLM_TIMEOUT_S, T-029b).
REQUEST_TIMEOUT_S = DEFAULT_LLM_TIMEOUT_S
MAX_HTTP_ATTEMPTS = 3
# Потолок длины ответа (T-019b). Наши ответы — короткий JSON ({"verdict": …} или до ~10 цитат
# по 3–15 слов): 400 токенов хватает с запасом. Без потолка локальная модель (T-lite, T-029b)
# могла «зацикливаться» и генерировать до таймаута.
MAX_ANSWER_TOKENS = 400
RETRY_DELAYS_S: tuple[float, ...] = (1.0, 3.0)
_MAX_MOCK_DEPTH = 20

SleepFn = Callable[[float], None]
MockFactory = Callable[[], BaseModel]


# ---------------------------------------------------------------- ошибки ---


class LLMError(Exception):
    """Базовая ошибка LLM-клиента. Сообщение никогда не содержит промпт, ответ или ключ."""


class LLMRequestError(LLMError):
    """Запрос к провайдеру не удался: сеть, таймаут, HTTP 4xx/5xx."""


class LLMResponseError(LLMError):
    """Провайдер ответил, но ответ не удалось разобрать или провалидировать схемой.

    `repair_hint` — краткое описание ошибок валидации (без входных значений),
    оно уходит в повторный запрос к LLM, но не в str() исключения и не в логи.
    """

    def __init__(self, message: str, repair_hint: str = "") -> None:
        super().__init__(message)
        self.repair_hint = repair_hint


class LLMBudgetExceeded(LLMError):
    """Исчерпан лимит вызовов LLM на документ (llm_call_budget)."""


# ------------------------------------------------------------------ mock ---

_mock_overrides: dict[type[BaseModel], MockFactory] = {}


def set_mock_response(schema: type[BaseModel], factory: MockFactory) -> None:
    """Подменяет ответ mock для схемы schema (для тестов и демо)."""
    _mock_overrides[schema] = factory


def reset_mock_responses() -> None:
    """Сбрасывает все подмены mock-ответов."""
    _mock_overrides.clear()


def mock_complete(schema: type[BaseModel]) -> BaseModel:
    """Ответ mock: подмена, если задана, иначе минимальный безопасный экземпляр схемы."""
    factory = _mock_overrides.get(schema)
    if factory is not None:
        return schema.model_validate(factory())
    return build_default_instance(schema)


def build_default_instance(schema: type[BaseModel]) -> BaseModel:
    """Строит детерминированный минимальный валидный экземпляр по JSON-схеме модели.

    bool → False, str → "", int/float → 0 (или minimum), list → [], Optional → None,
    Enum/Literal → первое значение, вложенные модели — рекурсивно. Необязательные
    поля опускаются, и Pydantic подставляет их значения по умолчанию.
    Ответ «нет / ничего не найдено» — mock не создаёт ложных нарушений.
    """
    json_schema = schema.model_json_schema()
    defs: dict[str, Any] = json_schema.get("$defs", {})
    return schema.model_validate(_default_for(json_schema, defs, 0))


def _default_for(node: dict[str, Any], defs: dict[str, Any], depth: int) -> Any:
    """Значение по умолчанию для узла JSON-схемы."""
    if depth > _MAX_MOCK_DEPTH:
        return None
    if "$ref" in node:
        return _default_for(defs[node["$ref"].split("/")[-1]], defs, depth + 1)
    if "default" in node:
        return node["default"]
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in node:
            options: list[dict[str, Any]] = node[key]
            if any(opt.get("type") == "null" for opt in options):
                return None
            return _default_for(options[0], defs, depth + 1)
    if "allOf" in node:
        return _default_for(node["allOf"][0], defs, depth + 1)
    return _default_for_type(node, defs, depth)


def _default_for_type(node: dict[str, Any], defs: dict[str, Any], depth: int) -> Any:
    """Значение по полю `type` узла схемы."""
    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = "null" if "null" in node_type else node_type[0]
    if node_type == "object":
        required: list[str] = node.get("required", [])
        props: dict[str, Any] = node.get("properties", {})
        return {name: _default_for(props[name], defs, depth + 1) for name in required}
    if node_type == "array":
        return []
    if node_type == "boolean":
        return False
    if node_type in ("integer", "number"):
        return node.get("minimum", 0)
    if node_type == "string":
        return _default_string(node)
    return None


_STRING_FORMATS: dict[str, str] = {
    "date-time": "1970-01-01T00:00:00Z",
    "date": "1970-01-01",
    "time": "00:00:00",
    "uuid": "00000000-0000-0000-0000-000000000000",
}


def _default_string(node: dict[str, Any]) -> str:
    """Пустая строка, либо минимальная строка нужного формата/длины."""
    fmt = node.get("format")
    if isinstance(fmt, str) and fmt in _STRING_FORMATS:
        return _STRING_FORMATS[fmt]
    return "x" * int(node.get("minLength", 0))


# ----------------------------------------------------- openai_compatible ---


@dataclass
class ChatResult:
    """Сырой ответ провайдера: текст сообщения и usage."""

    content: str
    usage: dict[str, int] = field(default_factory=dict)


def build_messages(system: str, user: str, schema: type[BaseModel]) -> list[dict[str, str]]:
    """Сообщения для chat/completions: в system дописываем JSON-схему ответа."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    system_full = (
        f"{system}\n\n"
        "Ответ — только JSON-объект, без пояснений и без markdown. "
        f"JSON-схема ответа:\n{schema_json}"
    )
    return [{"role": "system", "content": system_full}, {"role": "user", "content": user}]


def add_repair_turn(
    messages: list[dict[str, str]], bad_content: str, hint: str
) -> list[dict[str, str]]:
    """Добавляет к диалогу невалидный ответ и просьбу исправить его по ошибкам валидации."""
    repair = (
        "Ответ не прошёл проверку по JSON-схеме. Ошибки:\n"
        f"{hint}\n"
        "Верни только исправленный JSON-объект."
    )
    return [
        *messages,
        {"role": "assistant", "content": bad_content},
        {"role": "user", "content": repair},
    ]


def request_chat(
    settings: Settings,
    messages: list[dict[str, str]],
    transport: httpx.BaseTransport | None = None,
    sleep: SleepFn = time.sleep,
) -> ChatResult:
    """POST {LLM_BASE_URL}/chat/completions с повторами при 429/5xx и сетевых ошибках.

    Не больше MAX_HTTP_ATTEMPTS попыток, паузы RETRY_DELAYS_S. Другие 4xx — сразу
    LLMRequestError. Таймаут каждой попытки — settings.llm_timeout_s (LLM_TIMEOUT_S).
    Длина ответа ограничена MAX_ANSWER_TOKENS; ответ, обрезанный по длине
    (finish_reason="length"), — LLMResponseError без повтора (см. _parse_envelope).
    Возвращает текст ответа и usage.
    """
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    body = build_request_body(settings, messages)
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    with httpx.Client(transport=transport, timeout=settings.llm_timeout_s) as http:
        for attempt in range(1, MAX_HTTP_ATTEMPTS + 1):
            status = _send_once(http, url, body, headers, attempt)
            if isinstance(status, httpx.Response):
                return _parse_envelope(status)
            if attempt < MAX_HTTP_ATTEMPTS:
                sleep(RETRY_DELAYS_S[min(attempt - 1, len(RETRY_DELAYS_S) - 1)])
    raise LLMRequestError(
        f"LLM недоступна: {MAX_HTTP_ATTEMPTS} попытки не удались (последняя причина: {status})"
    )


def build_request_body(settings: Settings, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Тело запроса chat/completions.

    reasoning_effort (T-066) добавляется, только если задан LLM_REASONING_EFFORT: модели
    с рассуждениями (Qwen3.5, Gemma 4 в Ollama) иначе тратят весь MAX_ANSWER_TOKENS на
    рассуждения и получают finish_reason="length". Без настройки тело прежнее — облачные
    провайдеры не получают незнакомого поля.
    """
    body: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": MAX_ANSWER_TOKENS,
        "response_format": {"type": "json_object"},
    }
    if settings.llm_reasoning_effort is not None:
        body["reasoning_effort"] = settings.llm_reasoning_effort
    return body


def _send_once(
    http: httpx.Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    attempt: int,
) -> httpx.Response | str:
    """Одна HTTP-попытка. Успех → Response; повторяемая ошибка → краткая причина строкой."""
    try:
        response = http.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        logger.warning("LLM: таймаут запроса, попытка %d", attempt)
        return "таймаут"
    except httpx.TransportError as exc:
        logger.warning("LLM: сетевая ошибка %s, попытка %d", type(exc).__name__, attempt)
        return f"сетевая ошибка {type(exc).__name__}"
    code = response.status_code
    if code == 429 or code >= 500:
        logger.warning("LLM: HTTP %d, попытка %d", code, attempt)
        return f"HTTP {code}"
    if code >= 400:
        raise LLMRequestError(f"LLM отклонила запрос: HTTP {code}")
    return response


def _parse_envelope(response: httpx.Response) -> ChatResult:
    """Достаёт choices[0].message.content и usage из ответа chat/completions.

    finish_reason="length" — ответ обрезан на MAX_ANSWER_TOKENS, JSON неполный: сразу
    LLMResponseError. Повтор с просьбой исправить JSON упёрся бы в тот же потолок и только
    потратил бы вызов; вызывающий шаг пишет в журнал verdict="unknown".
    """
    try:
        data = response.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMResponseError("LLM вернула ответ не в формате chat/completions") from None
    if isinstance(choice, dict) and choice.get("finish_reason") == "length":
        logger.warning("LLM: ответ обрезан по длине (max_tokens=%d)", MAX_ANSWER_TOKENS)
        raise LLMResponseError("LLM: ответ обрезан по длине (max_tokens)")
    if not isinstance(content, str):
        raise LLMResponseError("LLM вернула пустой ответ")
    usage_raw = data.get("usage") or {}
    usage = {k: int(v) for k, v in usage_raw.items() if isinstance(v, int)}
    return ChatResult(content=content, usage=usage)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> str:
    """Убирает обёртку ```json … ``` и вырезает JSON-объект от первой { до последней }."""
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise LLMResponseError(
            "В ответе LLM нет JSON-объекта", repair_hint="Ответ не содержит JSON-объекта."
        )
    return text[start : end + 1]


def parse_json_response(text: str, schema: type[BaseModel]) -> BaseModel:
    """Разбирает текст ответа LLM и валидирует его схемой schema."""
    raw = extract_json_object(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(
            "LLM вернула некорректный JSON",
            repair_hint=f"Некорректный JSON: {exc.msg} (позиция {exc.pos}).",
        ) from None
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise LLMResponseError(
            f"Ответ LLM не соответствует схеме {schema.__name__}",
            repair_hint=_validation_hint(exc),
        ) from None


def _validation_hint(exc: ValidationError) -> str:
    """Ошибки валидации без входных значений: путь поля и сообщение."""
    lines = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(part) for part in err["loc"]) or "<корень>"
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)
