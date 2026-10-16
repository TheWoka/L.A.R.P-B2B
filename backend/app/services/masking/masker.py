"""Маскирование ПДн перед отправкой в LLM. Задача T-008, исполнитель — Данила.

mapping содержит исходные значения: НЕ логировать, НЕ сохранять в БД (AGENTS.md, правила 4–5).
"""

import re

from app.schemas import Entity, MaskResult, MaskSpan

MASK_CHAR = "█"
_WORD_RE = re.compile(r"\w+")


def _mask_word(match: re.Match[str]) -> str:
    word = match.group(0)
    if word[0].isalpha():
        return word[0] + MASK_CHAR * (len(word) - 1)
    return MASK_CHAR * len(word)


def mask_value(value: str) -> str:
    """Маскирование одной строки с сохранением длины (contracts.md, раздел 4.1).

    Слово = \\w+: начинается с буквы — первая буква видна, остальное → "█";
    иначе (цифра, "_") — всё слово → "█". Прочие символы не меняются.
    "Иванов Иван" → "И█████ И███", "+7 (912) 345-67-89" → "+█ (███) ███-██-██".
    Длина результата всегда равна len(value). Переиспользуется в mask_preserving_length (T-008).
    """
    return _WORD_RE.sub(_mask_word, value)


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
    Строго внутри плейсхолдера → orig_start; ровно на masked_end → orig_end.
    Границы цитаты LLM до вызова расширяются до целых плейсхолдеров (T-013).

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")


def mask_preserving_length(text: str, entities: list[Entity]) -> str:
    """Маскирование с сохранением длины для ReportText.text, Evidence.quote, Entity.value_masked.

    Только сущности категорий pd и pd_special. Слово = \\w+: начинается с буквы — первая буква
    остаётся, остальное → "█"; начинается с цифры — всё → "█". Прочие символы не меняются.
    "Иванов Иван" → "И█████ И███", "123-456-789 00" → "███-███-███ ██".
    Длина результата равна len(text).

    Контракт: docs/contracts.md, разделы 3 и 4.1. Задача T-008, исполнитель — Данила.
    """
    raise NotImplementedError("T-008 (Данила)")
