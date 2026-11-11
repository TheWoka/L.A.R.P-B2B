"""Режимы нейросети: mock / локальная / облачная (contracts.md, разделы 1 и 4, контракт v3).

Ответ GET /api/llm/modes. Ключ API в ответ никогда не попадает (AGENTS.md, правило 3).
"""

from typing import Literal

from pydantic import BaseModel

# mock — без нейросети; local — Ollama в Docker; cloud — OpenAI-совместимый API по ключу из .env
LlmMode = Literal["mock", "local", "cloud"]


class LlmModeInfo(BaseModel):
    mode: LlmMode
    available: bool
    model: str | None = None
    reason: str | None = None        # почему недоступен: «Ollama не запущена», «Нет ключа в .env»


class LlmModesResponse(BaseModel):
    default: LlmMode
    modes: list[LlmModeInfo]         # всегда три: mock, local, cloud
