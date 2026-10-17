"""Маскирование ПДн перед отправкой в LLM. Задача T-008, исполнитель — Данила.

mapping содержит исходные значения: НЕ логировать, НЕ сохранять в БД (AGENTS.md, правила 4–5).
"""

import re
from bisect import bisect_right
from collections import Counter

from app.schemas import Entity, EntityCategory, EntityType, MaskResult, MaskSpan

MASK_CHAR = "█"
_WORD_RE = re.compile(r"\w+")
PLACEHOLDER_RE = re.compile(r"\[[A-Z_]+_\d+\]")

# Маскируются только ПДн. Коммерческая тайна и финансовые данные остаются в тексте:
# LLM должна их видеть, чтобы классифицировать (T-013), а 152-ФЗ к ним не относится.
MASKED_CATEGORIES = frozenset({EntityCategory.pd, EntityCategory.pd_special})
# Типы, у которых одинаковость значения определяется только цифрами.
NUMERIC_TYPES = frozenset({
    EntityType.PASSPORT, EntityType.SNILS, EntityType.INN, EntityType.PHONE,
    EntityType.CARD, EntityType.BANK_ACCOUNT, EntityType.BIRTH_DATE,
})
_SPACES_RE = re.compile(r"\s+")


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


def _pd_entities(text: str, entities: list[Entity]) -> list[Entity]:
    """Сущности ПДн по возрастанию start; проверяет границы и отсутствие пересечений.

    Пересечения проверяются по всем сущностям: detect() возвращает их без пересечений,
    поэтому пересечение — ошибка выше по конвейеру, а не повод молча что-то выбрать.
    """
    ordered = sorted(entities, key=lambda e: (e.start, e.end))
    prev_end = 0
    for e in ordered:
        if not 0 <= e.start < e.end <= len(text):
            raise ValueError(f"entity {e.type.value} [{e.start}, {e.end}) вне текста")
        if e.start < prev_end:
            raise ValueError(f"entities overlap at {e.start}")
        prev_end = e.end
    return [e for e in ordered if e.category in MASKED_CATEGORIES]


def _normalize(etype: EntityType, value: str) -> str:
    """Ключ «одинаковости» значения: цифры для числовых типов, иначе текст без регистра."""
    if etype in NUMERIC_TYPES:
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            return digits
    return _SPACES_RE.sub(" ", value.casefold().replace("ё", "е")).strip()


class _PlaceholderRegistry:
    """Выдаёт плейсхолдеры [TYPE_N]: своя нумерация на тип, одно значение — один номер."""

    def __init__(self, reserved: set[str]) -> None:
        self._reserved = reserved            # плейсхолдеры, которые уже есть в исходном тексте
        self._counters: Counter[str] = Counter()
        self._by_key: dict[tuple[str, str], str] = {}
        self.mapping: dict[str, str] = {}

    def get(self, etype: EntityType, value: str) -> str:
        key = (etype.value, _normalize(etype, value))
        if key not in self._by_key:
            placeholder = self._next(etype.value)
            self._by_key[key] = placeholder
            self.mapping[placeholder] = value   # первое встреченное написание
        return self._by_key[key]

    def _next(self, type_name: str) -> str:
        while True:
            self._counters[type_name] += 1
            candidate = f"[{type_name}_{self._counters[type_name]}]"
            if candidate not in self._reserved:
                return candidate


def mask(text: str, entities: list[Entity]) -> MaskResult:
    """Заменяет ПДн (категории pd, pd_special) на плейсхолдеры [PERSON_1], [SNILS_1] ...

    trade_secret и financial остаются в тексте. Нумерация своя для каждого типа, с 1,
    в порядке первого появления. Одинаковые значения (PERSON, ADDRESS, EMAIL — без учёта
    регистра, «ё», лишних пробелов; числовые типы — по цифрам) получают один плейсхолдер,
    чтобы LLM понимала, что это один и тот же человек. mapping хранит первое написание.
    Для каждой замены — MaskSpan, spans по возрастанию masked_start.

    Если в исходном тексте уже есть строка вида [PERSON_1], она остаётся как есть, а такой
    номер не выдаётся: нумерация его пропускает ([PERSON_2] …). Поэтому unmask не заменит
    исходную строку чужим значением. Пересечения сущностей и выход за текст → ValueError.

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    registry = _PlaceholderRegistry(set(PLACEHOLDER_RE.findall(text)))
    pieces: list[str] = []
    spans: list[MaskSpan] = []
    orig_pos = masked_pos = 0
    for e in _pd_entities(text, entities):
        pieces.append(text[orig_pos:e.start])
        masked_pos += e.start - orig_pos
        placeholder = registry.get(e.type, text[e.start:e.end])
        pieces.append(placeholder)
        spans.append(MaskSpan(
            masked_start=masked_pos, masked_end=masked_pos + len(placeholder),
            orig_start=e.start, orig_end=e.end,
        ))
        masked_pos += len(placeholder)
        orig_pos = e.end
    pieces.append(text[orig_pos:])
    return MaskResult(masked_text="".join(pieces), mapping=registry.mapping, spans=spans)


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Обратная замена плейсхолдеров на исходные значения по mapping.

    Заменяются все вхождения плейсхолдеров из mapping за один проход (значение, похожее
    на плейсхолдер, повторно не раскрывается). Неизвестные плейсхолдеры не трогаются.
    Для повторов с разным написанием («Иванов И.И.» / «ИВАНОВ И.И.») восстанавливается
    первое написание — у них один плейсхолдер.

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    return PLACEHOLDER_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def to_original_offset(masked_offset: int, spans: list[MaskSpan]) -> int:
    """Переводит смещение в masked_text в координаты исходного текста.

    Нужна, чтобы цитату из ответа LLM (найденную в masked_text) показать в отчёте
    в исходных координатах. Вне плейсхолдеров: masked_offset плюс суммарная разница длин
    (orig_end - orig_start) - (masked_end - masked_start) всех замен левее.
    Строго внутри плейсхолдера и ровно на masked_start → orig_start; ровно на masked_end →
    orig_end. Поиск — bisect по spans (они идут по возрастанию masked_start), O(log n).
    Отрицательное смещение → ValueError; смещение больше len(masked_text) без текста не
    проверить — это ответственность вызывающего.
    Границы цитаты LLM до вызова расширяются до целых плейсхолдеров (expand_to_placeholders).

    Контракт: docs/contracts.md, раздел 3. Задача T-008, исполнитель — Данила.
    """
    if masked_offset < 0:
        raise ValueError(f"masked_offset < 0: {masked_offset}")
    i = bisect_right(spans, masked_offset, key=lambda s: s.masked_start) - 1
    if i < 0:
        return masked_offset                  # левее первой замены сдвига нет
    span = spans[i]
    if masked_offset < span.masked_end:
        return span.orig_start                # на masked_start или внутри плейсхолдера
    # Между заменами текст совпадает посимвольно: отсчитываем от конца последней замены.
    return span.orig_end + (masked_offset - span.masked_end)


def expand_to_placeholders(start: int, end: int, spans: list[MaskSpan]) -> tuple[int, int]:
    """Расширяет границы цитаты в masked_text до целых плейсхолдеров (для T-013).

    Если start или end попадает строго внутрь плейсхолдера, граница сдвигается к его
    началу / концу. Тогда to_original_offset переведёт цитату так, что значение ПДн
    попадёт в неё целиком. Внутренняя функция модуля, не контракт.
    """
    if start < 0 or end < start:
        raise ValueError(f"bad range [{start}, {end})")
    i = bisect_right(spans, start, key=lambda s: s.masked_start) - 1
    if i >= 0 and spans[i].masked_start < start < spans[i].masked_end:
        start = spans[i].masked_start
    j = bisect_right(spans, end, key=lambda s: s.masked_start) - 1
    if j >= 0 and spans[j].masked_start < end < spans[j].masked_end:
        end = spans[j].masked_end
    return start, end


def mask_preserving_length(text: str, entities: list[Entity]) -> str:
    """Маскирование с сохранением длины для ReportText.text, Evidence.quote, Entity.value_masked.

    Только сущности категорий pd и pd_special: text[start:end] → mask_value(...), остальное
    не меняется. Слово = \\w+: начинается с буквы — первая буква остаётся, остальное → "█";
    начинается с цифры — всё → "█". Прочие символы не меняются.
    "Иванов Иван" → "И█████ И███", "123-456-789 00" → "███-███-███ ██".
    Длина результата равна len(text). Пересечения сущностей → ValueError.

    Контракт: docs/contracts.md, разделы 3 и 4.1. Задача T-008, исполнитель — Данила.
    """
    pieces: list[str] = []
    pos = 0
    for e in _pd_entities(text, entities):
        pieces.append(text[pos:e.start])
        pieces.append(mask_value(text[e.start:e.end]))
        pos = e.end
    pieces.append(text[pos:])
    return "".join(pieces)
