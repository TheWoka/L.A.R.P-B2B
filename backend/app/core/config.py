"""Настройки приложения из переменных окружения (.env).

Список переменных — раздел 5 docs/contracts.md. Новая переменная добавляется
одновременно в .env.example и сюда.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Таймаут одного запроса к LLM по умолчанию, секунды (LLM_TIMEOUT_S, T-029b).
# providers.REQUEST_TIMEOUT_S ссылается на это же значение.
DEFAULT_LLM_TIMEOUT_S = 30.0


class Settings(BaseSettings):
    # Ищем .env в текущей папке и на уровень выше (корень репо при запуске из backend/).
    # В docker-compose переменные приходят через env_file, файл не нужен.
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- PostgreSQL ---
    postgres_user: str = "auditor"
    postgres_password: str = "auditor"
    postgres_db: str = "auditor"
    database_url: str = "postgresql+psycopg://auditor:auditor@postgres:5432/auditor"

    # --- Redis ---
    redis_url: str = "redis://redis:6379/0"

    # --- LLM ---
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_max_calls_per_doc: int = 30
    # Таймаут одного HTTP-запроса к LLM, секунды; > 0, конечное число.
    # Для локальной модели на CPU — 120 (docs/local-llm.md).
    llm_timeout_s: float = Field(default=DEFAULT_LLM_TIMEOUT_S, gt=0, allow_inf_nan=False)
    # Поле reasoning_effort в запросе к LLM (T-066), например "none" — выключить рассуждения
    # у Qwen3.5 / Gemma 4. Только для Ollama и других серверов, принимающих reasoning_effort;
    # облачной gpt-4o-mini не задавать. None (по умолчанию) — поля в запросе нет.
    llm_reasoning_effort: str | None = None

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _blank_reasoning_effort_is_none(cls, value: object) -> object:
        """Пустая строка или пробелы (LLM_REASONING_EFFORT= в .env) — как «не задано»."""
        if isinstance(value, str):
            return value.strip() or None
        return value

    # --- Прочее ---
    max_upload_mb: int = 50
    report_ttl_days: int = 7


@lru_cache
def get_settings() -> Settings:
    """Возвращает настройки; читаются из окружения один раз за процесс."""
    return Settings()
