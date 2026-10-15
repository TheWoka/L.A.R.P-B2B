"""Ответ GET /api/health (contracts.md, раздел 4.1)."""

from typing import Literal

from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]  # "ok" — db и redis true; "degraded" — хотя бы одно false
    db: bool                         # PostgreSQL отвечает на SELECT 1
    redis: bool                      # Redis отвечает на PING
