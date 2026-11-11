"""Режимы нейросети mock / local / cloud (контракт v3, T-073). Внутренний модуль.

Режим выбирается для каждой проверки: `POST /api/documents` и `POST /api/queries` принимают
`llm_mode`, документ хранит его в `DocumentRow.llm_mode`, воркер запускает конвейер внутри
`client.use_llm_mode(...)`. Здесь — только конфигурация режимов и проверка доступности.

Совместимость со старым .env (у жюри он может остаться): LLM_PROVIDER / LLM_BASE_URL /
LLM_API_KEY / LLM_MODEL задают режим по умолчанию и заполняют пустые LLM_LOCAL_* /
LLM_CLOUD_* этого режима — см. `resolve_mode_configs`.

Безопасность (AGENTS.md, правило 3): ключ облака живёт только в `ModeConfig.api_key`
(repr скрыт) и в заголовке запроса к LLM; в `LlmModesResponse` и в логи он не попадает.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings, get_settings
from app.schemas import LlmMode, LlmModeInfo, LlmModesResponse

logger = logging.getLogger(__name__)

ProviderName = Literal["mock", "openai_compatible"]
ALL_MODES: tuple[LlmMode, ...] = ("mock", "local", "cloud")

OLLAMA_PORT = 11434
DEFAULT_LOCAL_BASE_URL = "http://ollama:11434/v1"
PROBE_TIMEOUT_S = 1.0
MODES_CACHE_TTL_S = 10.0

REASON_OLLAMA_DOWN = "Ollama не запущена"
REASON_NO_LOCAL_MODEL = "Не задана LLM_LOCAL_MODEL"
REASON_NO_CLOUD = "Нет ключа или модели в .env"


@dataclass(frozen=True)
class ModeConfig:
    """Куда и с какой моделью ходит режим. api_key не печатается в repr (логи, трейсбеки)."""

    mode: LlmMode
    provider: ProviderName
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    model: str = ""


# ------------------------------------------------- совместимость со старым .env ---


def is_ollama_url(base_url: str) -> bool:
    """Адрес похож на Ollama: хост `ollama` (сервис docker-compose) или порт 11434."""
    try:
        parts = urlsplit(base_url.strip())
        port = parts.port
    except ValueError:
        return False
    return (parts.hostname or "").lower() == "ollama" or port == OLLAMA_PORT


def legacy_mode(settings: Settings) -> LlmMode:
    """Режим, который описывают старые переменные LLM_PROVIDER / LLM_BASE_URL.

    mock → mock; openai_compatible → local, если адрес — Ollama, иначе cloud.
    """
    if settings.llm_provider != "openai_compatible":
        return "mock"
    return "local" if is_ollama_url(settings.llm_base_url) else "cloud"


def default_mode(settings: Settings | None = None) -> LlmMode:
    """LLM_DEFAULT_MODE, а если он не задан — режим из старых переменных."""
    current = settings or get_settings()
    return current.llm_default_mode or legacy_mode(current)


def resolve_mode_configs(settings: Settings) -> dict[LlmMode, ModeConfig]:
    """Конфигурации всех трёх режимов с учётом старых переменных.

    Старые LLM_BASE_URL / LLM_API_KEY / LLM_MODEL заполняют пустые поля только того режима,
    который они описывают (`legacy_mode`). LLM_LOCAL_BASE_URL имеет значение по умолчанию,
    поэтому «пустым» считается, если его нет в окружении (model_fields_set).
    """
    legacy = legacy_mode(settings)
    local_legacy = legacy == "local"
    cloud_legacy = legacy == "cloud"
    local_base = settings.llm_local_base_url.strip()
    if "llm_local_base_url" not in settings.model_fields_set and local_legacy:
        local_base = settings.llm_base_url.strip()
    return {
        "mock": ModeConfig(mode="mock", provider="mock"),
        "local": ModeConfig(
            mode="local", provider="openai_compatible",
            base_url=local_base or DEFAULT_LOCAL_BASE_URL,
            api_key=settings.llm_api_key if local_legacy else "",
            model=_pick(settings.llm_local_model, settings.llm_model, local_legacy),
        ),
        "cloud": ModeConfig(
            mode="cloud", provider="openai_compatible",
            base_url=_pick(settings.llm_cloud_base_url, settings.llm_base_url, cloud_legacy),
            api_key=_pick(settings.llm_cloud_api_key, settings.llm_api_key, cloud_legacy),
            model=_pick(settings.llm_cloud_model, settings.llm_model, cloud_legacy),
        ),
    }


def _pick(new_value: str, legacy_value: str, use_legacy: bool) -> str:
    """Новая переменная, а если она пуста и режим описан старыми — старая."""
    value = new_value.strip()
    if value or not use_legacy:
        return value
    return legacy_value.strip()


def config_for(mode: LlmMode, settings: Settings | None = None) -> ModeConfig:
    """Конфигурация режима mode."""
    return resolve_mode_configs(settings or get_settings())[mode]


# ------------------------------------------------------------ доступность ---


def _model_ids(payload: object) -> set[str]:
    """data[].id из ответа GET /models (формат OpenAI, его же отдаёт Ollama)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return set()
    return {item["id"] for item in payload["data"] if isinstance(item, dict)
            and isinstance(item.get("id"), str)}


def _model_present(model: str, ids: set[str]) -> bool:
    """Модель есть в списке. Ollama называет модель без тега как `<имя>:latest`."""
    return model in ids or (":" not in model and f"{model}:latest" in ids)


def probe_local(
    config: ModeConfig, transport: httpx.BaseTransport | None = None,
) -> LlmModeInfo:
    """GET {base}/models с таймаутом 1 с: Ollama отвечает и модель скачана."""
    model = config.model or None
    if not config.model:
        return LlmModeInfo(mode="local", available=False, model=None, reason=REASON_NO_LOCAL_MODEL)
    url = config.base_url.rstrip("/") + "/models"
    try:
        with httpx.Client(transport=transport, timeout=PROBE_TIMEOUT_S) as http:
            response = http.get(url)
        response.raise_for_status()
        ids = _model_ids(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Режим local недоступен: %s", type(exc).__name__)
        return LlmModeInfo(mode="local", available=False, model=model, reason=REASON_OLLAMA_DOWN)
    if not _model_present(config.model, ids):
        reason = f"Модель {config.model} не скачана: ollama pull {config.model}"
        return LlmModeInfo(mode="local", available=False, model=model, reason=reason)
    return LlmModeInfo(mode="local", available=True, model=model)


def check_cloud(config: ModeConfig) -> LlmModeInfo:
    """Облако доступно, если заданы адрес, модель и ключ. Наружу не ходим: это деньги и время."""
    if config.base_url and config.model and config.api_key:
        return LlmModeInfo(mode="cloud", available=True, model=config.model)
    return LlmModeInfo(
        mode="cloud", available=False, model=config.model or None, reason=REASON_NO_CLOUD,
    )


def compute_modes(
    settings: Settings, transport: httpx.BaseTransport | None = None,
) -> LlmModesResponse:
    """Доступность всех трёх режимов без кэша."""
    configs = resolve_mode_configs(settings)
    return LlmModesResponse(
        default=default_mode(settings),
        modes=[
            LlmModeInfo(mode="mock", available=True, model=None),
            probe_local(configs["local"], transport),
            check_cloud(configs["cloud"]),
        ],
    )


@dataclass
class _ModesCache:
    value: LlmModesResponse | None = None
    expires_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


_cache = _ModesCache()


def available_modes(
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LlmModesResponse:
    """Ответ GET /api/llm/modes. Кэш на MODES_CACHE_TTL_S, чтобы интерфейс не дёргал Ollama."""
    now = clock()
    with _cache.lock:
        if _cache.value is not None and now < _cache.expires_at:
            return _cache.value
    value = compute_modes(settings or get_settings(), transport)
    with _cache.lock:
        _cache.value, _cache.expires_at = value, now + MODES_CACHE_TTL_S
    return value


def reset_modes_cache() -> None:
    """Сбрасывает кэш доступности (тесты, смена настроек)."""
    with _cache.lock:
        _cache.value, _cache.expires_at = None, 0.0


def mode_info(mode: LlmMode, settings: Settings | None = None) -> LlmModeInfo:
    """Доступность одного режима (из кэша available_modes). mock — без обращения к Ollama."""
    if mode == "mock":
        return LlmModeInfo(mode="mock", available=True, model=None)
    return next(info for info in available_modes(settings).modes if info.mode == mode)
