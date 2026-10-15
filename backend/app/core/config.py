"""Настройки приложения из переменных окружения (.env).

Список переменных — раздел 5 docs/contracts.md. Новая переменная добавляется
одновременно в .env.example и сюда.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Прочее ---
    embedding_model: str = "intfloat/multilingual-e5-small"
    max_upload_mb: int = 50
    report_ttl_days: int = 7


@lru_cache
def get_settings() -> Settings:
    """Возвращает настройки; читаются из окружения один раз за процесс."""
    return Settings()
