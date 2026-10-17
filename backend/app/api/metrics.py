"""Метрики качества детекции. Заглушка по контракту, реализация — T-028."""

from fastapi import APIRouter, HTTPException

from app.schemas import Metrics

router = APIRouter(tags=["metrics"])

NOT_IMPLEMENTED = "Будет реализовано в T-028"


@router.get(
    "/metrics",
    response_model=Metrics,
    summary="Метрики качества на эталонном корпусе",
    responses={404: {"description": "Метрики ещё не посчитаны"}},
)
def get_metrics() -> Metrics:
    """Источник — eval/results/metrics.json (пишет eval/run_eval.py, T-014a).

    Нет файла → 404 {"detail": "Метрики ещё не посчитаны"}.
    """
    raise HTTPException(status_code=501, detail=NOT_IMPLEMENTED)
