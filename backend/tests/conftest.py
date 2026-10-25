"""Общие фикстуры тестов. Тесты не требуют запущенных PostgreSQL и Redis."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# До импорта приложения: тесты всегда в режиме mock, без ключей (AGENTS.md, правило 7).
# Присваивание, а не setdefault: docker-compose run подставляет .env с реальным провайдером,
# и тесты не должны уходить во внешний API (деньги, лимиты, данные наружу).
os.environ["LLM_PROVIDER"] = "mock"
for _name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ.pop(_name, None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

# backend/tests/conftest.py -> корень репозитория
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient без запуска lifespan: init_db() не вызывается, БД не нужна."""
    yield TestClient(app)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Классификатор grif-cls выключен (T-036b): тесты LLM-шагов проверяют их отдельно от модели."""
    from app.services.detection import model_detector

    monkeypatch.setattr(model_detector, "load_model", lambda: None)
