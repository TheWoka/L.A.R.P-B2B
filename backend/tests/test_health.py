"""GET /api/health: проверки БД и Redis подменяются, контейнеры не нужны."""

import pytest
from fastapi.testclient import TestClient

from app.api import health


def test_health_all_up(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "check_db", lambda: True)
    monkeypatch.setattr(health, "check_redis", lambda: True)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True, "redis": True}


@pytest.mark.parametrize(("db", "redis_ok"), [(False, False), (False, True), (True, False)])
def test_health_degraded_when_dependency_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db: bool, redis_ok: bool
) -> None:
    """Граничный случай: хотя бы одна зависимость недоступна — degraded, но HTTP 200."""
    monkeypatch.setattr(health, "check_db", lambda: db)
    monkeypatch.setattr(health, "check_redis", lambda: redis_ok)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "db": db, "redis": redis_ok}


def test_check_redis_returns_false_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Реальная функция проверки не бросает исключение при недоступном Redis."""
    monkeypatch.setattr(
        health, "get_settings", lambda: type("S", (), {"redis_url": "redis://127.0.0.1:1/0"})()
    )
    assert health.check_redis() is False
