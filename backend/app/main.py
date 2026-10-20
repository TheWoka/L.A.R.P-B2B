"""Точка входа FastAPI: `uvicorn app.main:app`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, health, metrics, reports, rules, tasks
from app.core.logging import setup_logging
from app.core.version import APP_VERSION
from app.db.session import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    try:
        init_db()
    except Exception as exc:
        # API всё равно стартует: /api/health покажет db=false, а не упадёт весь сервис.
        logger.error("Не удалось инициализировать БД: %s", type(exc).__name__)
    yield


app = FastAPI(
    title="ИИ-аудитор безопасности документов",
    description="Хакатон B2B-РТС «AI Procurement Copilot», задача 7. Контракты: docs/contracts.md",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (health, documents, tasks, reports, rules, metrics):
    app.include_router(module.router, prefix="/api")
