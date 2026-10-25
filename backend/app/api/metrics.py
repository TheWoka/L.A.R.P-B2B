"""Готовые метрики качества детекции; расчёт выполняется отдельно через run_eval."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.schemas import Metrics

router = APIRouter(tags=["metrics"])

METRICS_PATHS = (
    Path("/eval/results/metrics.json"),
    Path(__file__).resolve().parents[3] / "eval/results/metrics.json",
)


@router.get(
    "/metrics",
    response_model=Metrics,
    summary="Метрики качества на выбранном корпусе",
    responses={404: {"description": "Метрики ещё не посчитаны"}},
)
def get_metrics(
    corpus: str | None = Query(
        None, pattern=r"^[a-z0-9_-]{1,32}$",
        description="Имя корпуса; без параметра — метрики эталонного набора",
    ),
) -> Metrics:
    """Читает первый доступный файл; ошибки не раскрывают его содержимое."""
    for default_path in METRICS_PATHS:
        try:
            root = default_path.parent.resolve()
            path = root / corpus / "metrics.json" if corpus is not None else default_path
            path = path.resolve()
            if not path.is_relative_to(root):
                raise HTTPException(status_code=404, detail="Метрики ещё не посчитаны")
            return Metrics.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            raise HTTPException(status_code=500, detail="Не удалось прочитать метрики") from None
    raise HTTPException(status_code=404, detail="Метрики ещё не посчитаны")
