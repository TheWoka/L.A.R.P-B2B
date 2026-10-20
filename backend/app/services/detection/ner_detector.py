"""NER-детектор ФИО и адресов физлиц на Natasha. Задача T-008, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.

PERSON: спаны PER из NewsNERTagger, только из ≥ 2 слов или с инициалами
(«Иванов И.И.», «И.И. Иванов»). Одно слово без инициалов отбрасывается: Natasha
путает фамилии с названиями улиц и организаций («Ленина», «Пушкина»).

ADDRESS: AddrExtractor (медленный) запускается только на предложениях с маркером
улицы или жилья. Соседние части адреса склеиваются в одну сущность. Адрес — ПДн,
только если он похож на адрес физлица (см. _is_personal_address). Юридический адрес
организации ПДн не является и не возвращается.

ИП — физлицо: ФИО после «ИП» / «индивидуальный предприниматель» возвращается как PERSON,
даже если Natasha разметила всю конструкцию как ORG.

Правила поверх Natasha (T-019a): полное ФИО с отчеством в любом падеже («Выдана
Пономарёву Артёму Вадимовичу» — Natasha дательный падеж пропускает) и имя держателя
карты латиницей («держатель IVAN PETROV», «Cardholder: Ivan Petrov»).

ФИО ЗАГЛАВНЫМИ: Natasha работает на копии текста той же длины, где «ИВАНОВ» → «Иванов»
(аббревиатуры ООО, ИНН, НДС … не меняются). Смещения в копии и в doc.text совпадают;
value_masked и проверки PERSON строятся по исходному doc.text.
"""

import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from razdel import sentenize

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.masking.masker import mask_value

logger = logging.getLogger(__name__)

PERSON_CONFIDENCE = 0.85
PERSON_INITIALS_CONFIDENCE = 0.9
ADDRESS_CONFIDENCE = 0.8

# Инициалы: «И.И.», «И. И.», «А.С», «И.» — второй инициал не может быть началом слова.
_INITIALS = r"[А-ЯЁ]\.\s?(?:[А-ЯЁ](?![А-Яа-яЁё])\.?)?"
# Фамилия: «Иванов», «ИВАНОВ», «Салтыков-Щедрин».
_CAP_WORD = r"[А-ЯЁ](?:[а-яё]+|[А-ЯЁ]+)"
_SURNAME = rf"{_CAP_WORD}(?:-{_CAP_WORD})?"
_INITIALS_AFTER_RE = re.compile(rf"\s{{1,2}}({_INITIALS})")          # «Иванов» + « И.И.»
_INITIALS_BEFORE_RE = re.compile(rf"({_INITIALS})\s{{1,2}}$")       # «И.И. » + «Иванов»
_INITIALS_FORMAT_RE = re.compile(
    rf"^(?:{_SURNAME}\s+{_INITIALS}|{_INITIALS}\s*{_SURNAME})$"
)
_ONLY_INITIALS_RE = re.compile(rf"^{_INITIALS}$")

# ИП — физлицо, его ФИО — ПДн (152-ФЗ), даже если Natasha разметила «ИП …» как ORG.
_IP_PREFIX = r"(?:ИП|И\.\s?П\.|индивидуальн\w*\s+предпринимател\w*)"
_IP_PREFIX_RE = re.compile(rf"{_IP_PREFIX}\s+", re.IGNORECASE)   # .match(pos): в начале спана
_IP_IN_TEXT_RE = re.compile(rf"(?<!\w){_IP_PREFIX}\s+", re.IGNORECASE)   # в тексте
# Форма ФИО после префикса ИП (ищется по копии текста с исправленным регистром).
_TITLE_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_NAME_SHAPE_RE = re.compile(
    rf"{_TITLE_WORD}(?:\s+{_TITLE_WORD}){{1,2}}(?![А-Яа-яЁё])"
    rf"|{_TITLE_WORD}\s+{_INITIALS}|{_INITIALS}\s*{_TITLE_WORD}"
)
IP_PERSON_CONFIDENCE = 0.9

# Полное ФИО «Фамилия Имя Отчество» в любом падеже: третье слово — отчество
# («-ович/-евич/-ич», «-овна/-евна/-ична/-инична» + падежное окончание).
_PATRONYMIC = r"[А-ЯЁ][а-яё]+(?:(?:ович|евич|ич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой))"
_FULL_NAME_RE = re.compile(
    rf"(?<![А-Яа-яЁё-]){_TITLE_WORD}\s+[А-ЯЁ][а-яё]+\s+{_PATRONYMIC}(?![А-Яа-яЁё])"
)
FULL_NAME_CONFIDENCE = 0.85
# Держатель карты латиницей: два слова с заглавной после метки.
_LATIN_WORD = r"[A-Z](?:[a-z]+|[A-Z]+)(?:-[A-Z](?:[a-z]+|[A-Z]+))?"
_CARDHOLDER_RE = re.compile(
    r"(?:(?i:держател\w*)(?:\s+(?i:карты))?|(?i:card\s?holder)|(?i:имя\s+на\s+карте))"
    rf"\s*[:—–-]?\s*({_LATIN_WORD}\s+{_LATIN_WORD})(?![A-Za-z])"
)
CARDHOLDER_CONFIDENCE = 0.9
# «держатель карты Visa Gold» — платёжная система и класс карты, а не имя.
_CARD_WORDS = frozenset({
    "VISA", "MASTERCARD", "MAESTRO", "MIR", "UNIONPAY", "JCB", "AMEX", "ELECTRON",
    "CLASSIC", "GOLD", "PLATINUM", "STANDARD", "WORLD", "BLACK", "INFINITE", "SIGNATURE",
    "PREMIUM", "BUSINESS", "CORPORATE", "DEBIT", "CREDIT", "CARD", "BANK",
})

# ФИО ЗАГЛАВНЫМИ: Natasha обучена на обычном регистре и «ИВАНОВ ИВАН» не видит.
_CAPS_WORD_RE = re.compile(r"(?<![^\W\d_])[А-ЯЁ]{2,}(?![^\W\d_])")
_VOWELS = frozenset("АЕЁИОУЫЭЮЯ")
_ABBREVIATIONS = frozenset({
    "АО", "ООО", "ОАО", "ЗАО", "ПАО", "НАО", "ИП", "ГУП", "МУП", "ФГУП", "ГБУ", "МБУ",
    "АНО", "НКО", "ИНН", "КПП", "ОГРН", "ОГРНИП", "БИК", "ОКПО", "ОКВЭД", "ОКТМО", "ОКАТО",
    "КБК", "СНИЛС", "ЕГРЮЛ", "ЕГРИП", "ФНС", "ИФНС", "УФНС", "МВД", "УФМС", "ФМС", "ФИО",
    "НДС", "НДФЛ", "РФ", "ФЗ", "ГК", "НК", "ТК", "КОАП", "ГОСТ", "ЕИС", "ИКЗ", "НМЦК",
    "РТС", "ПДН", "ИСПДН", "КИИ", "ФСТЭК", "ФСБ", "ТЗ", "ДСП", "СССР", "ЕАЭС",
})

# Маркер улицы или жилья, за которым идёт название или номер: «ул. Ленина», «д. 12».
# Требование названия/номера отсекает «т.д.», «дней», «домашний» — AddrExtractor
# медленный (~0,3 мс на символ), поэтому на него идут только участки с маркером.
_NAME_OR_NUMBER = r"(?-i:[А-ЯЁ0-9])"
_ADDRESS_HINT_RE = re.compile(
    rf"(?<!\w)(?:(?:ул|пер|бул|ш)\.\s?{_NAME_OR_NUMBER}"
    rf"|(?:улиц|проспект|переул)\w*\s+{_NAME_OR_NUMBER}"
    rf"|пр-кт\.?\s?{_NAME_OR_NUMBER}"
    r"|(?:д|кв)\.\s?№?\s?\d|(?:дом|квартир)\w*\s+№?\s?\d)",
    re.IGNORECASE,
)
# Окно вокруг маркера: индекс, регион и город стоят левее улицы, дом и квартира — правее.
WINDOW_LEFT = 80
WINDOW_RIGHT = 40
# Между частями одного адреса — только запятые и пробелы.
_ADDRESS_GLUE_RE = re.compile(r"^[,\s]*$")
# Хотя бы одна часть адреса ниже уровня города, иначе это просто «г. Томск».
_STREET_LEVEL_PARTS = frozenset({
    "улица", "проспект", "переулок", "площадь", "шоссе", "бульвар", "набережная",
    "проезд", "дом", "корпус", "строение", "квартира", "офис",
})
_PERSONAL_HINT_RE = re.compile(
    r"проживающ|зарегистрирован|адрес\w* регистрации|мест\w* жительства"
    r"|адрес\w* проживания|паспорт",
    re.IGNORECASE,
)
_LEGAL_HINT_RE = re.compile(
    r"юридическ\w* адрес|юр\.\s?адрес|мест\w* нахождения|почтов\w* адрес",
    re.IGNORECASE,
)
PERSONAL_WINDOW = 60   # окно слева для признаков адреса физлица и расстояние до PERSON
LEGAL_WINDOW = 40      # окно слева для признаков адреса организации


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


@dataclass(frozen=True)
class _AddressPart:
    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class _Models:
    segmenter: Any
    ner_tagger: Any
    addr_extractor: Any


@lru_cache(maxsize=1)
def _load_models() -> _Models:
    """Загружает модели Natasha один раз на процесс (~1 с). Не вызывается при импорте."""
    # pymorphy2 ищет словари через pkg_resources (setuptools), которого может не быть
    # в окружении Python 3.11+. Явный путь к словарям убирает эту зависимость.
    import pymorphy2_dicts_ru

    os.environ.setdefault("PYMORPHY2_DICT_PATH", pymorphy2_dicts_ru.get_path())
    from natasha import AddrExtractor, MorphVocab, NewsEmbedding, NewsNERTagger, Segmenter

    started = time.perf_counter()
    models = _Models(
        segmenter=Segmenter(),
        ner_tagger=NewsNERTagger(NewsEmbedding()),
        addr_extractor=AddrExtractor(MorphVocab()),
    )
    logger.info("ner: models loaded in %.2f s", time.perf_counter() - started)
    return models


def _is_abbreviation(word: str) -> bool:
    return word in _ABBREVIATIONS or (len(word) <= 4 and not _VOWELS & set(word))


def _title_caps(text: str) -> str:
    """Копия текста той же длины: «ИВАНОВ ИВАН» → «Иванов Иван», аббревиатуры не трогаем.

    Регистр букв меняется на месте, поэтому все смещения в копии совпадают с исходными.
    На копии работают Natasha и поиск ФИО после «ИП»; значения и проверки — по исходному.
    """
    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        return word if _is_abbreviation(word) else word[0] + word[1:].lower()

    return _CAPS_WORD_RE.sub(fix, text)  # str.lower() у кириллицы не меняет длину


def _ner_spans(text: str, models: _Models) -> tuple[list[_Span], list[_Span]]:
    """Спаны PER и ORG из NewsNERTagger, отсортированные по start."""
    from natasha import Doc

    doc = Doc(text)
    doc.segment(models.segmenter)
    doc.tag_ner(models.ner_tagger)
    per = [_Span(s.start, s.stop) for s in doc.spans if s.type == "PER"]
    org = [_Span(s.start, s.stop) for s in doc.spans if s.type == "ORG"]
    return per, org


# ---------- PERSON ----------
def _attach_initials(text: str, span: _Span) -> _Span:
    """Присоединяет инициалы, которые Natasha выделила отдельно: «Петров» + « И.И.»."""
    start, end = span.start, span.end
    after = _INITIALS_AFTER_RE.match(text, end)
    if after:
        end = after.end(1)
    else:
        before = _INITIALS_BEFORE_RE.search(text, max(0, start - 6), start)
        if before:
            start = before.start(1)
    return _Span(start, end)


def _extend_initial_dot(text: str, span: _Span) -> _Span:
    """«Петровой А.С» + «.» → захватить точку последнего инициала."""
    value = text[span.start:span.end]
    if text[span.end:span.end + 1] == "." and re.search(r"(?:^|[\s.])[А-ЯЁ]$", value):
        return _Span(span.start, span.end + 1)
    return span


def _person_span(text: str, raw: _Span) -> tuple[_Span, bool] | None:
    """Правила PERSON по исходному тексту: (спан, есть ли инициалы) или None.

    ≥ 2 слов или формат с инициалами; одно слово без инициалов («Ленина»), спан со
    строчной буквы и одни инициалы отбрасываются.
    """
    value = text[raw.start:raw.end]
    if not value or not value[0].isupper() or _ONLY_INITIALS_RE.match(value):
        return None
    span = raw if len(value.split()) >= 2 else _attach_initials(text, raw)
    span = _extend_initial_dot(text, span)
    value = text[span.start:span.end]
    initials = bool(_INITIALS_FORMAT_RE.match(value))
    if len(value.split()) < 2 and not initials:
        return None
    return span, initials


def _person_entities(text: str, per_spans: list[_Span]) -> list[Entity]:
    entities: list[Entity] = []
    for raw in per_spans:
        checked = _person_span(text, raw)
        if checked:
            span, initials = checked
            confidence = PERSON_INITIALS_CONFIDENCE if initials else PERSON_CONFIDENCE
            entities.append(_entity(text, span, EntityType.PERSON, confidence))
    return entities


def _ip_name_candidates(ner_text: str, prefixed_spans: list[_Span]) -> list[_Span]:
    """Спаны ФИО индивидуальных предпринимателей.

    1. Спан Natasha начинается с «ИП» / «И.П.» / «индивидуальный предприниматель» →
       остаток спана после префикса. Обычно это ORG, но «И.П. Смирнова А.С.» Natasha
       размечает как PER, принимая «И.П.» за инициалы, — такой PER тоже идёт сюда.
    2. Тот же префикс в тексте, за которым идёт ФИО по форме, — на случай, когда Natasha
       не размечает такое ФИО вовсе («Исполнитель: ИП Смирнова Анна Сергеевна, ИНН …»).
    """
    candidates: list[_Span] = []
    for span in prefixed_spans:
        prefix = _IP_PREFIX_RE.match(ner_text, span.start, span.end)
        if prefix and prefix.end() < span.end:
            candidates.append(_Span(prefix.end(), span.end))
    for prefix in _IP_IN_TEXT_RE.finditer(ner_text):
        name = _NAME_SHAPE_RE.match(ner_text, prefix.end())
        if name:
            candidates.append(_Span(name.start(), name.end()))
    return candidates


def _starts_with_ip(ner_text: str, span: _Span) -> bool:
    return bool(_IP_PREFIX_RE.match(ner_text, span.start, span.end))


def _ip_person_entities(
    text: str, ner_text: str, prefixed_spans: list[_Span]
) -> list[Entity]:
    entities: list[Entity] = []
    for raw in _ip_name_candidates(ner_text, prefixed_spans):
        checked = _person_span(text, raw)
        if checked:
            entities.append(_entity(text, checked[0], EntityType.PERSON, IP_PERSON_CONFIDENCE))
    return entities


def _rule_person_entities(text: str, ner_text: str) -> list[Entity]:
    """ФИО по правилам: полное ФИО с отчеством (по копии с исправленным регистром)
    и держатель карты латиницей (по исходному тексту)."""
    entities = [
        _entity(text, _Span(m.start(), m.end()), EntityType.PERSON, FULL_NAME_CONFIDENCE)
        for m in _FULL_NAME_RE.finditer(ner_text)
    ]
    entities += [
        _entity(text, _Span(m.start(1), m.end(1)), EntityType.PERSON, CARDHOLDER_CONFIDENCE)
        for m in _CARDHOLDER_RE.finditer(text)
        if not _CARD_WORDS & set(m.group(1).upper().split())
    ]
    return entities


def _add_missing(persons: list[Entity], extra: list[Entity]) -> list[Entity]:
    """Добавляет сущности правил: новые — как есть, а если правило целиком накрывает
    неполный спан Natasha («Гусевой Мариной» + « Олеговной») — заменяет его.
    Частичные пересечения не трогаем: доверяем Natasha."""
    kept = list(persons)
    for e in extra:
        overlap = [p for p in kept if p.start < e.end and e.start < p.end]
        same = any((p.start, p.end) == (e.start, e.end) for p in overlap)
        if not same and all(e.start <= p.start and p.end <= e.end for p in overlap):
            kept = [p for p in kept if p not in overlap] + [e]
    return sorted(kept, key=lambda e: (e.start, e.end))


def _unique_persons(persons: list[Entity], ip_persons: list[Entity]) -> list[Entity]:
    """Одинаковые спаны схлопываются; у ИП остаётся его confidence 0.9."""
    by_span = {(e.start, e.end): e for e in persons}
    by_span.update({(e.start, e.end): e for e in ip_persons})
    return sorted(by_span.values(), key=lambda e: (e.start, e.end))


# ---------- ADDRESS ----------
def _snap(text: str, start: int, end: int, lo: int, hi: int) -> _Span:
    """Сдвигает границы окна к границам слов, чтобы не резать слово пополам."""
    while lo < start < end and text[start - 1].isalnum() and text[start].isalnum():
        start += 1
    while start < end < hi and text[end - 1].isalnum() and text[end].isalnum():
        end -= 1
    return _Span(start, end)


def _candidate_chunks(text: str) -> list[_Span]:
    """Участки для AddrExtractor: окна вокруг маркеров адреса внутри предложений razdel.

    Перекрывающиеся окна одного предложения объединяются. Если razdel разрезал адрес
    по сокращению («обл.», «д.»), части из соседних участков склеит _glue_parts.
    """
    chunks: list[_Span] = []
    for sent in sentenize(text):
        for hint in _ADDRESS_HINT_RE.finditer(sent.text):
            lo = max(sent.start, sent.start + hint.start() - WINDOW_LEFT)
            hi = min(sent.stop, sent.start + hint.end() + WINDOW_RIGHT)
            window = _snap(text, lo, hi, sent.start, sent.stop)
            if chunks and window.start <= chunks[-1].end:
                chunks[-1] = _Span(chunks[-1].start, max(chunks[-1].end, window.end))
            else:
                chunks.append(window)
    return chunks


def _address_parts(text: str, models: _Models) -> list[_AddressPart]:
    """Части адреса с типом («улица», «дом» …) в координатах doc.text.

    Одинаковые участки (колонтитулы, повторяющиеся реквизиты) разбираются один раз:
    кэш живёт только внутри одного вызова и не хранит ПДн между документами.
    """
    cache: dict[str, list[tuple[int, int, str]]] = {}
    parts: list[_AddressPart] = []
    for chunk in _candidate_chunks(text):
        piece = text[chunk.start:chunk.end]
        if piece not in cache:
            cache[piece] = [
                (m.start, m.stop, m.fact.type)
                for m in models.addr_extractor(piece)
                if getattr(m.fact, "type", None)  # без типа — обычно слово с заглавной
            ]
        parts.extend(
            _AddressPart(chunk.start + s, chunk.start + e, kind) for s, e, kind in cache[piece]
        )
    return sorted(parts, key=lambda p: p.start)


def _glue_parts(text: str, parts: list[_AddressPart]) -> list[list[_AddressPart]]:
    """Склеивает части, разделённые только запятыми и пробелами, в адреса."""
    groups: list[list[_AddressPart]] = []
    for part in parts:
        if groups and _ADDRESS_GLUE_RE.match(text[groups[-1][-1].end:part.start]):
            groups[-1].append(part)
        else:
            groups.append([part])
    return [g for g in groups if any(p.kind in _STREET_LEVEL_PARTS for p in g)]


def _distance(a: _Span, b: _Span) -> int:
    return max(a.start - b.end, b.start - a.end, 0)


def _is_personal_address(
    text: str, addr: _Span, kinds: set[str], persons: list[_Span], orgs: list[_Span]
) -> bool:
    """Адрес физлица? Порядок проверок — от сильного признака к слабому.

    1. «проживающий», «зарегистрирован», «паспорт» … в 60 символах слева → ПДн.
    2. «юридический адрес», «место нахождения» … в 40 символах слева → не ПДн.
    3. ORG-спан Natasha заканчивается в 40 символах слева → не ПДн.
    4. Есть квартира → ПДн.
    5. PERSON на расстоянии ≤ 60 символов → ПДн.
    """
    if _PERSONAL_HINT_RE.search(text, max(0, addr.start - PERSONAL_WINDOW), addr.start):
        return True
    if _LEGAL_HINT_RE.search(text, max(0, addr.start - LEGAL_WINDOW), addr.start):
        return False
    if any(addr.start - LEGAL_WINDOW <= o.end <= addr.start for o in orgs):
        return False
    if "квартира" in kinds:
        return True
    return any(_distance(addr, p) <= PERSONAL_WINDOW for p in persons)


def _address_entities(
    text: str, ner_text: str, models: _Models, persons: list[_Span], orgs: list[_Span]
) -> list[Entity]:
    entities: list[Entity] = []
    for group in _glue_parts(text, _address_parts(ner_text, models)):
        span = _Span(group[0].start, group[-1].end)
        if any(p.start < span.end and span.start < p.end for p in persons):
            continue  # «Петров П.» Natasha читает как «переулок» — это ФИО, а не адрес
        kinds = {p.kind for p in group}
        if _is_personal_address(text, span, kinds, persons, orgs):
            entities.append(_entity(text, span, EntityType.ADDRESS, ADDRESS_CONFIDENCE))
    return entities


# ---------- общее ----------
def _entity(text: str, span: _Span, etype: EntityType, confidence: float) -> Entity:
    return Entity(
        type=etype,
        category=EntityCategory.pd,
        start=span.start,
        end=span.end,
        value_masked=mask_value(text[span.start:span.end]),
        confidence=confidence,
        detector="ner",
    )


def detect_ner(doc: ExtractedDocument) -> list[Entity]:
    """Ищет ФИО и адреса физлиц моделью Natasha; detector="ner", category=pd.

    Модель загружается один раз на процесс (лениво), не при импорте модуля.
    Результат отсортирован по start; PERSON и ADDRESS могут пересекаться между
    собой — пересечения убирает merge_entities в detect().
    Задача T-008, исполнитель — Данила.
    """
    text = doc.text
    if not text.strip():
        return []
    started = time.perf_counter()
    models = _load_models()
    ner_text = _title_caps(text)   # та же длина и те же смещения, что у text
    per_spans, org_spans = _ner_spans(ner_text, models)
    plain_per = [p for p in per_spans if not _starts_with_ip(ner_text, p)]
    prefixed = [p for p in per_spans + org_spans if _starts_with_ip(ner_text, p)]
    persons = _unique_persons(
        _person_entities(text, plain_per), _ip_person_entities(text, ner_text, prefixed)
    )
    persons = _add_missing(persons, _rule_person_entities(text, ner_text))
    person_spans = [_Span(e.start, e.end) for e in persons]
    addresses = _address_entities(text, ner_text, models, person_spans, org_spans)
    entities = sorted(persons + addresses, key=lambda e: (e.start, e.end))
    # В лог — только количество по типам и время, никаких значений (AGENTS.md, правило 4).
    counts = Counter(e.type.value for e in entities)
    logger.info(
        "detect_ner: document_id=%s entities=%d by_type=%s elapsed=%.2fs",
        doc.document_id, len(entities), dict(sorted(counts.items())),
        time.perf_counter() - started,
    )
    return entities
