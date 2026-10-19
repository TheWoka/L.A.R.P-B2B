"""Поиск шести маркеров по ключевым фразам без раскрытия текста в логах."""

import logging
import re
from bisect import bisect_left, bisect_right
from itertools import islice

from app.schemas import Evidence, ExtractedDocument, Marker, MarkerType, TextSegment

logger = logging.getLogger(__name__)

PHRASES: dict[MarkerType, tuple[str, ...]] = {
    MarkerType.approval_sheet: (
        "лист согласования", "согласовано", "утверждаю", "визы", "лист ознакомления",
    ),
    MarkerType.classification_mark: (
        "коммерческая тайна", "конфиденциально", "для служебного пользования", "ДСП",
    ),
    MarkerType.retention_period: (
        "срок хранения", "хранится в течение", "подлежит уничтожению",
        "уничтожается по истечении", "срок хранения документа",
    ),
    MarkerType.consent_clause: (
        "согласие на обработку персональных данных", "даю согласие на обработку",
        "согласие субъекта персональных данных",
    ),
    MarkerType.processing_assignment: (
        "по поручению оператора", "поручение обработки",
        "поручает обработку персональных данных",
    ),
    MarkerType.kii_mention: (
        "критической информационной инфраструктуры", "объект КИИ", "значимый объект",
        "187-ФЗ", "КИИ",
    ),
}
APPROVAL_LINE_START = frozenset({"согласовано", "утверждаю", "визы"})
RETENTION_GOODS_PREFIXES = ("товар", "продукц", "издели", "груз", "материал", "образц")
WORD_PATTERN = re.compile(r"\w+")
MARGIN_SECTIONS = frozenset({"Верхний колонтитул", "Нижний колонтитул"})
CLASSIFICATION_MAX_LENGTH = 80
LAW_REFERENCE_DISTANCE = 80  # Максимальное расстояние между краями статьи и 152-ФЗ.
LABEL_DECORATION = " \t\r\n\v\f\u00a0«»\"'“”„:;,.()[]‐‑–—-"
ARTICLE_REFERENCE = r"(?P<article>ч\.\s*3\s*ст\.\s*6|части\s+3\s+статьи\s+6)(?![.,]\d)"
LAW_152_RE = re.compile(r"(?<!\w)152[‐‑–—-]ФЗ(?!\w)", re.IGNORECASE)


def _compile_phrases(phrases: tuple[str, ...], *, include_article: bool) -> re.Pattern[str]:
    """Ищет в исходном тексте: регистр и е/ё не меняют координаты совпадения."""
    alternatives = [
        r"\s+".join(re.escape(word).replace("е", "[её]") for word in phrase.split())
        for phrase in sorted(phrases, key=len, reverse=True)
    ]
    if include_article:
        alternatives.append(ARTICLE_REFERENCE)
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


PHRASE_PATTERNS = {
    kind: _compile_phrases(phrases, include_article=kind == MarkerType.processing_assignment)
    for kind, phrases in PHRASES.items()
}


def find_markers(doc: ExtractedDocument) -> list[Marker]:
    """Возвращает по одному маркеру на каждый MarkerType, в порядке перечисления."""
    segments = sorted(doc.segments, key=lambda segment: segment.offset)
    offsets = [segment.offset for segment in segments]
    law_spans = [match.span() for match in LAW_152_RE.finditer(doc.text)]
    markers: list[Marker] = []
    for kind in MarkerType:
        evidence = _find_evidence(doc, kind, segments, offsets, law_spans)
        markers.append(Marker(type=kind, present=evidence is not None, evidence=evidence))
    logger.info("Найдено маркеров: %d", sum(marker.present for marker in markers))
    return markers


def _find_evidence(
    doc: ExtractedDocument, kind: MarkerType, segments: list[TextSegment],
    offsets: list[int], law_spans: list[tuple[int, int]],
) -> Evidence | None:
    """Берёт первое допустимое вхождение и цитирует только саму ключевую фразу."""
    for match in PHRASE_PATTERNS[kind].finditer(doc.text):
        segment = _segment_at(match.start(), segments, offsets)
        if not _accept_match(doc.text, kind, match, segment, law_spans):
            continue
        return Evidence(
            quote=doc.text[match.start():match.end()], start=match.start(), end=match.end(),
            page=segment.page if segment is not None else None,
            section=segment.section if segment is not None else None,
        )
    return None


def _segment_at(
    start: int, segments: list[TextSegment], offsets: list[int],
) -> TextSegment | None:
    """На границе выбирает новый сегмент; в промежутках метаданных нет."""
    index = bisect_right(offsets, start) - 1
    if index < 0:
        return None
    segment = segments[index]
    return segment if start < segment.offset + len(segment.text) else None


def _accept_match(
    text: str, kind: MarkerType, match: re.Match[str], segment: TextSegment | None,
    law_spans: list[tuple[int, int]],
) -> bool:
    """Отличает реквизиты и грифы от упоминаний, проверяет контекст статьи."""
    phrase = " ".join(match.group().lower().replace("ё", "е").split())
    if kind == MarkerType.approval_sheet:
        if phrase in APPROVAL_LINE_START:
            return _is_approval_label(text, match)
    if kind == MarkerType.classification_mark:
        return _is_classification(text, match, segment)
    if kind == MarkerType.retention_period and phrase == "срок хранения":
        following_words = WORD_PATTERN.finditer(text, match.end())
        return not any(
            word.group().lower().startswith(RETENTION_GOODS_PREFIXES)
            for word in islice(following_words, 3)
        )
    if match.lastgroup == "article":
        return _has_nearby_law(match.start(), match.end(), law_spans)
    return True


def _is_approval_label(text: str, match: re.Match[str]) -> bool:
    """Реквизит начинается со строки и имеет пустое либо допустимое продолжение."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    if text[line_start:match.start()].strip():
        return False
    line_end = text.find("\n", match.end())
    suffix = text[match.end():line_end if line_end >= 0 else len(text)].lstrip()
    return not suffix or suffix[0] in ":,/-—" or suffix[0].isupper()


def _is_classification(text: str, match: re.Match[str], segment: TextSegment | None) -> bool:
    """Гриф — короткая плашка, колонтитул или фраза из нескольких слов заглавными."""
    quote = match.group()
    if (quote.isupper() and len(quote.split()) >= 2) or (
        segment is not None and segment.section in MARGIN_SECTIONS
    ):
        return True
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    units = [text[line_start:line_end if line_end >= 0 else len(text)]]
    if segment is not None:
        units.append(segment.text)
    return any(
        len(unit.strip()) <= CLASSIFICATION_MAX_LENGTH
        and unit.strip().strip(LABEL_DECORATION).strip() == quote
        for unit in units
    )


def _has_nearby_law(start: int, end: int, law_spans: list[tuple[int, int]]) -> bool:
    """Проверяет ближайшую ссылку на 152-ФЗ с любой стороны от статьи."""
    index = bisect_left(law_spans, (start,))
    return any(
        max(start - law_end, law_start - end, 0) <= LAW_REFERENCE_DISTANCE
        for law_start, law_end in law_spans[max(0, index - 1):index + 1]
    )
