"""GET /api/health — единственный полностью рабочий эндпоинт каркаса."""

import logging

import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.schemas import HealthStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def check_db() -> bool:
    """True, если PostgreSQL отвечает на SELECT 1 (таймаут подключения 3 с)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # health не должен падать
        logger.warning("Проверка БД не прошла: %s", type(exc).__name__)
        return False


def check_redis() -> bool:
    """True, если Redis отвечает на PING (таймаут 1 с)."""
    try:
        client = redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception as exc:
        logger.warning("Проверка Redis не прошла: %s", type(exc).__name__)
        return False


@router.get("/health", response_model=HealthStatus, summary="Состояние сервиса")
def health() -> HealthStatus:
    """Всегда HTTP 200. status="degraded", если БД или Redis недоступны (contracts.md, 4.1)."""
    db_ok, redis_ok = check_db(), check_redis()
    status = "ok" if db_ok and redis_ok else "degraded"
    return HealthStatus(status=status, db=db_ok, redis=redis_ok)
