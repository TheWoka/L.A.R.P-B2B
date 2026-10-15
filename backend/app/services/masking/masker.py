"""Маскирование ПДн перед отправкой в LLM. Задача T-008, исполнитель — Данила.

mapping содержит исходные значения: НЕ логировать, НЕ сохранять в БД (AGENTS.md, правила 4–5).
"""

from app.schemas import Entity, MaskResult, MaskSpan


def mask(text: str, entities: list[Entity]) -> MaskResult:
    """Заменяет сущности на плейсхолдеры [PERSON_1], [SNILS_1] ...

    Одинаковые значения получают одинаковый плейсхолдер. Для каждой замены записывает
    MaskSpan (где плейсхолдер в masked_text и где значение в исходном text).

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Обратная замена плейсхолдеров на исходные значения по mapping.

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")


def to_original_offset(masked_offset: int, spans: list[MaskSpan]) -> int:
    """Переводит смещение в masked_text в координаты исходного текста.

    Нужна, чтобы цитату из ответа LLM (найденную в masked_text) показать в отчёте
    в исходных координатах. Вне плейсхолдеров: masked_offset плюс суммарная разница длин
    (orig_end - orig_start) - (masked_end - masked_start) всех замен левее.

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")
