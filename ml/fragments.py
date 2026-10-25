"""Нарезка текста на фрагменты для классификатора КТ и особых категорий. Задача T-036a.

Фрагмент — предложение; длинное (> MAX_FRAGMENT_CHARS символов) режется дальше по «;»,
по запятой перед союзом и по тире, но куски не короче MIN_PIECE_WORDS слов.
Строка документа (таблица, пункт списка) — всегда граница фрагмента.

Та же функция пойдёт в конвейер (T-036b), поэтому модуль зависит только от `re`.
Работает и на исходном, и на замаскированном тексте ([PERSON_1] не содержит точек).
"""

import re

MAX_FRAGMENT_CHARS = 200
MIN_PIECE_WORDS = 4      # минимальная длина куска при разрезании длинного предложения
MIN_FRAGMENT_WORDS = 2   # «Маржа 18%» — уже фрагмент; одно слово («Итого») — нет

_WORD_RE = re.compile(r"\w+")
# Конец предложения: . ! ? … (возможно, несколько) + закрывающие кавычки/скобки + пробел.
_SENT_END_RE = re.compile(r"[.!?…]+[»\"')\]]*\s+")
# После точки не режем, если перед ней сокращение, за которым обычно идёт имя или число.
_NO_SPLIT_ABBR = frozenset({
    "ул", "д", "кв", "им", "т", "тел", "ст", "ч", "п", "пп", "пр", "просп", "см", "рис",
    "табл", "стр", "гр", "обл", "пос", "с", "р", "ген", "зам", "нач", "проф", "акад",
    "тов", "г-н", "г-жа", "гос", "мкр", "корп", "пер", "наб", "пл", "е", "др",
    "вх", "исх", "прил", "разд", "абз", "подп", "напр", "ср",
})
_YEAR_RE = re.compile(r"(?:19|20)\d\d\s*$")
# Внутренние границы длинного предложения: «;», «, <союз>», тире с пробелами.
_CONJUNCTIONS = (
    "и", "а", "но", "однако", "причём", "причем", "при этом", "что", "чтобы", "который",
    "которая", "которое", "которые", "поскольку", "так как", "если", "хотя", "либо",
    "или", "где", "когда", "потому что", "в том числе", "включая",
)
_INNER_SPLIT_RE = re.compile(
    r";\s+|,\s+(?=(?:" + "|".join(re.escape(c) for c in _CONJUNCTIONS) + r")\b)"
    r"|\s+[—–-]\s+",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _is_false_end(line: str, dot_pos: int) -> bool:
    """True, если точка в позиции dot_pos — не конец предложения (инициал, сокращение)."""
    if line[dot_pos] != ".":
        return False
    m = re.search(r"([\w-]+)$", line[:dot_pos])
    if m is None:
        return False
    token = m.group(1)
    if len(token) == 1 and token.isalpha() and token.isupper():
        return True                        # инициал: «И. П. Иванов», «Иванов И.»
    low = token.lower()
    if low == "г":                         # «2025 г. Компания» — конец; «г. Томск» — нет
        return _YEAR_RE.search(line[:m.start()]) is None
    return low in _NO_SPLIT_ABBR


def _starts_sentence(ch: str) -> bool:
    return ch.isupper() or ch.isdigit() or ch in "«\"[—–-•("


def _sentences(line: str) -> list[tuple[int, int]]:
    """Предложения внутри одной строки: отрезки [start, end) в координатах строки."""
    bounds: list[tuple[int, int]] = []
    start = 0
    for m in _SENT_END_RE.finditer(line):
        nxt = m.end()
        if nxt >= len(line) or not _starts_sentence(line[nxt]):
            continue
        punct_end = m.start() + len(m.group(0).rstrip())
        if _is_false_end(line, m.start()):
            continue
        bounds.append((start, punct_end))
        start = nxt
    bounds.append((start, len(line)))
    return bounds


def _merge_short(pieces: list[tuple[int, int]], text: str) -> list[tuple[int, int]]:
    """Склеивает куски короче MIN_PIECE_WORDS слов с соседом (предпочтительно — левым)."""
    merged: list[tuple[int, int]] = []
    for s, e in pieces:
        if merged and _word_count(text[s:e]) < MIN_PIECE_WORDS:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    if len(merged) > 1 and _word_count(text[merged[0][0]:merged[0][1]]) < MIN_PIECE_WORDS:
        merged[1] = (merged[0][0], merged[1][1])
        merged.pop(0)
    return merged


def _split_long(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Длинное предложение → куски по «;», «, <союз>», тире; короткие куски склеиваются."""
    if end - start <= MAX_FRAGMENT_CHARS:
        return [(start, end)]
    pieces: list[tuple[int, int]] = []
    pos = start
    for m in _INNER_SPLIT_RE.finditer(text, start, end):
        cut = m.start() + (1 if text[m.start()] in ";," else 0)  # знак остаётся слева
        if cut > pos:
            pieces.append((pos, cut))
        pos = m.end()
    if pos < end:
        pieces.append((pos, end))
    return _merge_short(pieces, text) if len(pieces) > 1 else [(start, end)]


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def split_fragments(text: str) -> list[tuple[int, int]]:
    """Текст → фрагменты [(start, end)] в координатах text, по возрастанию, без пересечений.

    Границы: перевод строки, конец предложения (кроме инициалов и сокращений вроде «ул.»,
    «т.», «г. Томск»), а внутри предложения длиннее MAX_FRAGMENT_CHARS — «;», запятая
    перед союзом и тире. Пробелы по краям отрезаются. Фрагменты короче
    MIN_FRAGMENT_WORDS слов (\\w+) отбрасываются.
    """
    result: list[tuple[int, int]] = []
    line_start = 0
    for line in text.split("\n"):
        for s, e in _sentences(line):
            for ps, pe in _split_long(text, line_start + s, line_start + e):
                ts, te = _trim(text, ps, pe)
                if te > ts and _word_count(text[ts:te]) >= MIN_FRAGMENT_WORDS:
                    result.append((ts, te))
        line_start += len(line) + 1
    return result
