"""Поиск особых категорий ПДн нейросетью с проверкой цитат. Задача T-013e, исполнитель — Данила.

Внутренний интерфейс модуля llm — не контракт. Промпт — prompts/special_category.md.
Схема та же, что у коммерческой тайны (T-013d), общие функции — в quotes.py.

Как устроено:
1. Кандидаты без LLM — куски masked_text (строки; длинные — по предложениям), где есть привязка
   к конкретному человеку (has_person_context, общая с классификатором T-036b): плейсхолдер
   человека ([PERSON_n], [SNILS_n], [PASSPORT_n], [BIRTH_DATE_n]) в этом или соседнем куске
   (±1) или слово-лицо («работник», «кандидат», «он», «у неё» …) в самом куске; либо корень
   особой категории («инфаркт», «судим», «церк» …) и слово-лицо в соседнем куске. Без человека
   кусок не берётся: «Поставка инсулина для городской больницы № 3» — не ПДн. Куски, целиком
   покрытые уже найденной SPECIAL_CATEGORY, куски без текста вне плейсхолдеров и куски
   «Поле: [МЕТКА]» отбрасываются.
2. Пакеты ≤ MAX_BATCH_CHARS, не больше MAX_SPECIAL_BATCHES; если не помещается всё —
   первыми идут куски с корнем особой категории.
3. LLM только ПРЕДЛАГАЕТ цитаты. Засчитывает код: цитата дословно есть в пакете, длина
   6–200 символов, без плейсхолдеров ПДн (имени в цитате нет — только сами сведения), нет
   пересечения ни с одной найденной сущностью (иначе повторная маска невозможна), слева и внутри
   нет признака требования («отсутствие судимости», «не должен иметь»), цитата — не название
   поля анкеты перед уже скрытым значением («Национальность: [SPECIAL_CATEGORY_1]»).
4. Сущность — SPECIAL_CATEGORY (pd_special), detector="llm"; value_masked — маскирование
   с сохранением длины (contracts.md, раздел 4.1): это ПДн. Конвейер после находки заново
   маскирует текст, и следующие вызовы LLM (КТ, условия правил) сведений уже не видят.
5. Каждый пакет — LlmCall(purpose="special_category") в журнале документа.

Что видит модель: текст, где имена, телефоны, документы и уже найденные особые категории
заменены плейсхолдерами, а ещё не найденные сведения о здоровье, убеждениях и т. п. видны —
иначе их не найти. Без имени это обезличенные сведения. mock → шаг ничего не делает.
В лог — только счётчики (AGENTS.md, правило 4).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.schemas import (
    Entity,
    EntityCategory,
    EntityType,
    ExtractedDocument,
    LlmCall,
    MaskResult,
    MaskSpan,
)
from app.services.detection.detect import assign_location
from app.services.detection.dict_detector import SPECIAL_EXCLUDE_LEFT_RE
from app.services.llm import client
from app.services.llm.checks import Verdict, defuse, record_llm_call
from app.services.llm.quotes import (
    Batch,
    Candidate,
    LocatedQuote,
    covered_by,
    locate_quote,
    overlaps,
    pack_batches,
    pieces,
    request_quotes,
)
from app.services.masking.masker import PLACEHOLDER_RE, mask_value

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "special_category.md"
QUESTION = "Какие сведения о человеке относятся к особым категориям?"
MAX_SPECIAL_BATCHES = 2       # пакетов на документ (бюджет общий с КТ и условиями правил)
MIN_QUOTE_CHARS = 6
MAX_QUOTE_CHARS = 200
EXCLUDE_LEFT_CHARS = 40       # окно слева от цитаты для признака требования
LLM_CONFIDENCE = 0.7

# Плейсхолдеры, которые всегда обозначают конкретного человека (ФИО и его документы).
# Телефон, e-mail, адрес и ИНН бывают и у организации — привязкой к человеку не считаются.
PERSON_PLACEHOLDER_RE = re.compile(r"\[(?:PERSON|SNILS|PASSPORT|BIRTH_DATE)_\d+\]")
PERSON_WORD_RE = re.compile(
    r"(?<!\w)(?:работни\w*|сотрудни\w*|кандидат\w*|заявител\w*|участник\w*|исполнител\w*"
    r"|специалист\w*|гражданин\w*|гражданк\w*|он|она|его|е[её]|ему|ей|у\s+него|у\s+н[её]е)(?!\w)",
    re.IGNORECASE,
)
SPECIAL_STEMS = (
    "болез", "болен", "больн", "диагноз", "инвалид", "беремен", "лечени", "госпитал", "операци",
    "инфаркт", "инсульт", "онколог", "психиатр", "нарколог", "судим", "осужд", "привлекал",
    "религи", "верующ", "церк", "мечет", "конфесс", "партии", "политическ", "профсоюз",
    "национальн", "этническ", "расов", "интимн",
)
_HIV_RE = re.compile(r"(?<!\w)ВИЧ(?!\w)")  # с учётом регистра: «вич» есть в отчествах
# «Национальность: [SPECIAL_CATEGORY_1]» — название поля, значение которого уже скрыто.
_LABEL_ONLY_RE = re.compile(r"[^:\[\]\n]{1,60}:(?:\s*\[[A-Z_]+_\d+\][\s,;.]*)+")
_BEFORE_PLACEHOLDER_RE = re.compile(r"\s*:\s*\[[A-Z_]+_\d+\]")


class SpecialCategoryItem(BaseModel):
    quote: str


class SpecialCategoryAnswer(BaseModel):
    """Ответ LLM. Пустой список — безопасный ответ mock: ни одной находки."""

    items: list[SpecialCategoryItem] = []


@dataclass
class _Stats:
    accepted: int = 0
    rejected: int = 0


# ---------------------------------------------------------------- вход ---


def detect_special_llm(
    doc: ExtractedDocument, masked: MaskResult, entities: list[Entity],
) -> list[Entity]:
    """Дополняет entities особыми категориями от LLM; новый список, отсортированный по start.

    masked — MaskResult того же документа по тем же entities (в LLM уходит только
    masked.masked_text). Вызовы — в текущий llm_call_budget и журнал llm_call_log.
    mock → entities без изменений, ни одного вызова и записи журнала. Ошибки LLM наружу не выходят.
    Если список вырос, вызывающий обязан заново выполнить mask / mask_preserving_length.
    """
    if not client.is_llm_enabled():
        return list(entities)
    candidates = select_candidates(masked.masked_text, masked.spans, entities)
    batches = select_batches(candidates)
    kept = list(entities)
    stats = _Stats()
    sent = 0
    for batch in batches:
        call = _ask_batch(batch, doc, masked.spans, kept, stats)
        if call is None:
            logger.warning("Особые категории через LLM: лимит вызовов исчерпан, пакетов %d", sent)
            break
        sent += 1
        record_llm_call(call)
    logger.info(
        "Особые категории через LLM: document_id=%s кандидатов=%d пакетов=%d отправлено=%d "
        "принято=%d отброшено=%d",
        doc.document_id, len(candidates), len(batches), sent, stats.accepted, stats.rejected,
    )
    return sorted(kept, key=lambda e: (e.start, e.end))


# ----------------------------------------------------------- кандидаты ---


def select_candidates(
    masked_text: str, spans: list[MaskSpan], entities: list[Entity],
) -> list[Candidate]:
    """Куски с привязкой к конкретному человеку (см. docstring модуля), по порядку текста."""
    special = [e for e in entities if e.type == EntityType.SPECIAL_CATEGORY]
    bounds = pieces(masked_text)
    texts = [masked_text[start:end] for start, end in bounds]
    bound = has_person_context(texts, word_in_neighbors=False)
    bound_near = has_person_context(texts, word_in_neighbors=True)
    result: list[Candidate] = []
    for i, (start, end) in enumerate(bounds):
        if not bound[i] and not (has_special_stem(texts[i]) and bound_near[i]):
            continue
        if len(PLACEHOLDER_RE.sub("", texts[i]).strip()) < MIN_QUOTE_CHARS:
            continue
        if _LABEL_ONLY_RE.fullmatch(texts[i]):
            continue
        if covered_by(start, end, spans, special):
            continue
        result.append(Candidate(start, end, defuse(texts[i])))
    return result


def has_person_context(texts: Sequence[str], *, word_in_neighbors: bool) -> list[bool]:
    """Для каждого куска masked_text: есть ли привязка к конкретному человеку (ст. 10 152-ФЗ).

    Привязка — плейсхолдер человека (PERSON_PLACEHOLDER_RE) в этом или соседнем куске (±1)
    либо слово-лицо (PERSON_WORD_RE, вне плейсхолдеров) в самом куске, а при
    word_in_neighbors=True — и в соседнем. Общая проверка для LLM-шага (отбор кандидатов,
    T-013e) и классификатора (model_detector, T-036b): «Поставка препаратов для лечения
    онкологических заболеваний для больницы № 3» — без человека, это не ПДн.
    """
    has_placeholder = [PERSON_PLACEHOLDER_RE.search(t) is not None for t in texts]
    has_word = [PERSON_WORD_RE.search(PLACEHOLDER_RE.sub(" ", t)) is not None for t in texts]
    result: list[bool] = []
    for i in range(len(texts)):
        near = range(max(i - 1, 0), min(i + 2, len(texts)))
        word = any(has_word[j] for j in near) if word_in_neighbors else has_word[i]
        result.append(word or any(has_placeholder[j] for j in near))
    return result


def has_special_stem(text: str) -> bool:
    """Есть корень особой категории вне плейсхолдеров."""
    plain = PLACEHOLDER_RE.sub(" ", text)
    lowered = plain.lower()
    return _HIV_RE.search(plain) is not None or any(stem in lowered for stem in SPECIAL_STEMS)


def select_batches(candidates: list[Candidate]) -> list[Batch]:
    """Не больше MAX_SPECIAL_BATCHES пакетов. Не помещается всё — первыми куски с корнем
    особой категории; внутри пакета куски идут по порядку текста."""
    batches = pack_batches(candidates)
    if len(batches) > MAX_SPECIAL_BATCHES:
        ordered = sorted(candidates, key=lambda c: (not has_special_stem(c.text), c.start))
        batches = pack_batches(ordered)
    return [
        Batch(sorted(batch.candidates, key=lambda c: c.start))
        for batch in batches[:MAX_SPECIAL_BATCHES]
    ]


# ---------------------------------------------------------- вызов LLM ---


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _ask_batch(
    batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan], kept: list[Entity],
    stats: _Stats,
) -> LlmCall | None:
    """Один пакет: вызов, проверка цитат, обновление kept. None — бюджет исчерпан."""
    fragment = batch.text
    reply = request_quotes(
        _system_prompt(), QUESTION, fragment, SpecialCategoryAnswer, "Особые категории через LLM",
    )
    if reply is None:
        return None
    if reply.failed:
        return _call(fragment, "unknown", None, cached=False)
    first_quote: str | None = None
    for quote in reply.quotes:
        accepted = accept_quote(quote, batch, doc, spans, kept)
        if accepted is None:
            stats.rejected += 1
            continue
        stats.accepted += 1
        first_quote = first_quote if first_quote is not None else accepted
    verdict: Verdict = "yes" if first_quote is not None else "no"
    return _call(fragment, verdict, first_quote, cached=reply.cached)


def _call(fragment: str, verdict: Verdict, quote: str | None, *, cached: bool) -> LlmCall:
    return LlmCall(
        purpose="special_category", rule_id=None, question=QUESTION, masked_fragment=fragment,
        verdict=verdict, quote=quote, cached=cached,
    )


# ------------------------------------------------------ проверка цитат ---


def accept_quote(
    quote: str, batch: Batch, doc: ExtractedDocument, spans: list[MaskSpan], kept: list[Entity],
) -> str | None:
    """Засчитывает цитату: добавляет Entity в kept и возвращает цитату пакета, иначе None."""
    located = locate_quote(quote, batch, spans, MIN_QUOTE_CHARS, MAX_QUOTE_CHARS)
    if located is None or _is_requirement(located, batch) or _is_field_label(located, batch):
        return None
    if any(overlaps(e, located.start, located.end) for e in kept):
        return None
    value = doc.text[located.start:located.end]
    entity = Entity(
        type=EntityType.SPECIAL_CATEGORY, category=EntityCategory.pd_special,
        start=located.start, end=located.end, value_masked=mask_value(value),
        confidence=LLM_CONFIDENCE, detector="llm",
    )
    kept.append(assign_location([entity], doc)[0])
    return located.shown


def _is_requirement(located: LocatedQuote, batch: Batch) -> bool:
    """«Отсутствие судимости», «не должен иметь…» в цитате или слева в том же куске."""
    for candidate in batch.candidates:
        if candidate.start <= located.masked_start < candidate.end:
            left = max(located.masked_start - EXCLUDE_LEFT_CHARS, candidate.start)
            context = candidate.text[left - candidate.start:located.masked_end - candidate.start]
            return SPECIAL_EXCLUDE_LEFT_RE.search(context) is not None
    return False


def _is_field_label(located: LocatedQuote, batch: Batch) -> bool:
    """Цитата — название поля перед уже скрытым значением: «Национальность» в
    «Национальность: [SPECIAL_CATEGORY_1]». Сведений в ней нет."""
    for candidate in batch.candidates:
        if candidate.start <= located.masked_start < candidate.end:
            tail = candidate.text[located.masked_end - candidate.start:]
            return _BEFORE_PLACEHOLDER_RE.match(tail) is not None
    return False
