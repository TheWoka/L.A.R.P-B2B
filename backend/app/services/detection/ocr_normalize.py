"""Цифровой вид OCR-текста для regex-детектора. Задача T-077c, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.

OCR путает в номерах цифры с похожими буквами: «1l2-233-445 9S» вместо «112-233-445 95».
Контрольная сумма СНИЛС, ИНН, карты такой номер отвергает, и ПДн в скане пропускается.
digits_view() возвращает копию текста ТОЙ ЖЕ длины, где в «числовых токенах» путаемые буквы
заменены цифрами. Длина не меняется (замена 1:1), поэтому координаты находки в копии — это
координаты в исходном тексте: value_masked и start/end берутся из doc.text без пересчёта.
Тот же приём, что у ЗАГЛАВНЫХ ФИО в ner_detector (копия текста той же длины).

Числовой токен — последовательность из цифр, пробелов, разделителей «- ( ) + . /» и путаемых
букв, которая:
- не примыкает к другим буквам («Обь», «ОГРН», «Строка» не трогаются — буква «О» в них
  стоит рядом с «б», «Г», «т»);
- без крайних разделителей длиной ≥ 4;
- состоит из цифр хотя бы на 60% среди значащих символов (цифры + путаемые буквы;
  разделители не считаются, иначе «+7 (9l2) 345-67-89» не прошёл бы порог).
Внутри токена буквы заменяются только в частях между пробелами, где уже есть цифра:
«ООО 12345678» остаётся «ООО», а «1l2-233-445 9S» → «112-233-445 95».

Используется только для документов с doc.ocr is not None; обычный текст не меняется.
"""

import re

# Путаемый символ → цифра. Только 1:1 по длине.
CONFUSABLE_DIGITS: dict[str, str] = {
    "О": "0", "о": "0", "O": "0", "o": "0",   # кириллица и латиница
    "З": "3", "з": "3",
    "l": "1", "I": "1", "|": "1", "!": "1",
    "Б": "6", "б": "6",
    "S": "5", "s": "5",
    "B": "8",
}
_SEPARATORS = "-()+./"
_SPACES = "   "  # пробел, неразрывный, узкий неразрывный, тонкий
_DASHES = "‐‑–—"
MIN_TOKEN_LEN = 4
MIN_DIGIT_SHARE = 0.6

_CONFUSABLE = "".join(CONFUSABLE_DIGITS)
_ALLOWED = re.escape(_SEPARATORS + _SPACES + _DASHES + _CONFUSABLE)
# Буква — \w без цифр и «_». Токен не начинается сразу после буквы и не кончается перед буквой.
_LETTER = r"[^\W\d_]"
_TOKEN_RE = re.compile(rf"(?<!{_LETTER})[\d{_ALLOWED}]+(?!{_LETTER})")
_EDGE = _SEPARATORS + _SPACES + _DASHES + "|!"  # «|» и «!» на краю — не цифры, а разметка
_CHUNK_RE = re.compile(rf"[^{re.escape(_SPACES)}]+")


def _fix_token(token: str) -> str:
    """Токен без крайних разделителей → тот же токен с цифрами вместо путаемых букв."""
    digits = sum(ch.isdigit() for ch in token)
    letters = sum(ch in CONFUSABLE_DIGITS for ch in token)
    if len(token) < MIN_TOKEN_LEN or digits == 0 or digits < MIN_DIGIT_SHARE * (digits + letters):
        return token
    parts: list[str] = []
    pos = 0
    for chunk in _CHUNK_RE.finditer(token):
        parts.append(token[pos:chunk.start()])
        part = chunk.group()
        if any(ch.isdigit() for ch in part):
            part = "".join(CONFUSABLE_DIGITS.get(ch, ch) for ch in part)
        parts.append(part)
        pos = chunk.end()
    parts.append(token[pos:])
    return "".join(parts)


def digits_view(text: str) -> str:
    """Копия text той же длины: в числовых токенах путаемые буквы заменены цифрами.

    «СНИЛС 1l2-233-445 9S» → «СНИЛС 112-233-445 95»; «Обь» и «ООО» не меняются.
    """
    chunks: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        raw = m.group()
        core = raw.strip(_EDGE)
        if not core:
            continue
        lead = raw.index(core)
        start = m.start() + lead
        fixed = _fix_token(core)
        if fixed != core:
            chunks.append(text[pos:start])
            chunks.append(fixed)
            pos = start + len(core)
    chunks.append(text[pos:])
    return "".join(chunks)  # замены 1:1 — длина и координаты те же, что у text
