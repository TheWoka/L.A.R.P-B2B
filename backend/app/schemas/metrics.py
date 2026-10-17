"""Метрики качества детекции на эталонном корпусе (contracts.md, раздел 1).

Ответ GET /api/metrics. Источник — eval/results/metrics.json (пишет eval/run_eval.py).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TypeMetrics(BaseModel):
    type: str                        # значение EntityType
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    support: int = Field(ge=0)       # число эталонных сущностей


class Metrics(BaseModel):
    corpus: str                      # название/версия корпуса
    documents: int
    created_at: datetime
    by_type: list[TypeMetrics]
    pd_recall: float = Field(ge=0, le=1)            # по всем ПДн — критерий ≥ 0.90
    trade_secret_recall: float = Field(ge=0, le=1)  # критерий ≥ 0.85
    matching: str                    # правило совпадения: "overlap ≥ 50% и тот же type"
