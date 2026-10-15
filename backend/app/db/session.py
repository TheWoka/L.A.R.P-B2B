"""Подключение к PostgreSQL и инициализация схемы."""

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Создание engine не открывает соединение — импорт модуля безопасен без запущенной БД.
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: одна сессия на запрос."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Включает расширение pgvector и создаёт таблицы, если их ещё нет.

    Миграций (Alembic) пока нет: на хакатоне схема создаётся заново.
    """
    from app.db.models import Base  # локальный импорт, чтобы не было циклов

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)
    logger.info("База данных инициализирована")
