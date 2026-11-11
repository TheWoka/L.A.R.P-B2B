"""Режимы нейросети: GET /api/llm/modes (контракт v3, T-073).

Ключ API в ответ не попадает: LlmModeInfo содержит только режим, доступность, имя модели
и причину недоступности (AGENTS.md, правило 3).
"""

from fastapi import APIRouter, HTTPException

from app.schemas import LlmMode, LlmModesResponse
from app.services.llm import modes

router = APIRouter(tags=["llm"])


@router.get(
    "/llm/modes", response_model=LlmModesResponse,
    summary="Какие режимы нейросети доступны",
)
def get_llm_modes() -> LlmModesResponse:
    """mock — всегда; local — если Ollama отвечает и модель скачана; cloud — если в .env есть
    адрес, модель и ключ. Результат кэшируется на 10 секунд."""
    return modes.available_modes()


def resolve_requested_mode(requested: LlmMode | None) -> LlmMode:
    """Режим для новой проверки: None → режим по умолчанию; недоступный → 400 с причиной.

    Режим по умолчанию не проверяется при загрузке: если он недоступен в момент обработки,
    воркер не падает, а LLM-шаги дают unknown.
    """
    if requested is None:
        return modes.default_mode()
    info = modes.mode_info(requested)
    if not info.available:
        raise HTTPException(status_code=400, detail=f"Режим недоступен: {info.reason}")
    return requested
