"""Детектор ПДн на регулярных выражениях с валидацией. Задача T-007, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.

Принцип: регулярка находит только кандидата, решение принимает проверка —
контрольная сумма (СНИЛС, ИНН, карта, ключ счёта по БИК), формат (серия паспорта,
код телефона, реальная дата) или обязательное слово-контекст рядом. Реквизиты
организаций (ИНН из 10 цифр, счета 40702…, ИКЗ, ОГРН и т.п.) — не ПДн и не возвращаются.

Контакты организации — тоже не ПДн и сущностью не становятся вовсе (T-019a): бесплатные
линии 8-800 / 8-804, ролевые ящики (info@, sales@, zakupki@ …), телефон с меткой «приёмная»,
«отдел», «служба», «горячая линия» слева или сразу после ролевого ящика. Раньше они шли
с уверенностью 0.5, но confidence нигде не фильтруется — и любое ТЗ с телефоном поддержки
получало ложное PD_IN_PUBLICATION.

Текст не нормализуется: смещения совпадают с исходным doc.text. Исходные значения
нигде не сохраняются и не логируются — в Entity только value_masked и координаты.
"""

import re
from bisect import bisect_left, bisect_right, insort
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection import validators as v
from app.services.masking.masker import mask_value

# ---------- Уверенность ----------
CONF_CHECKSUM = 0.98        # прошла контрольная сумма
CONF_CONTEXT = 0.95         # формат + обязательный контекст
CONF_FORMAT = 0.9           # только формат (телефон с кодом страны)
CONF_PHONE_LOCAL = 0.85     # телефон без кода страны, рядом метка "тел."
CONF_ACCOUNT_NO_BIK = 0.8   # счёт физлица, рядом нет БИК для проверки ключа
CONF_ACCOUNT_LABELED = 0.8  # счёт физлица, ключ по БИК не сошёлся, но слева «счёт» (T-019b)
CONF_EMAIL = 0.95

# При пересечении остаётся сущность с меньшим рангом; при равенстве — более длинная.
PRIORITY: dict[EntityType, int] = {
    EntityType.SNILS: 0,
    EntityType.INN: 1,
    EntityType.CARD: 2,
    EntityType.BANK_ACCOUNT: 3,
    EntityType.PASSPORT: 4,
    EntityType.BIRTH_DATE: 5,
    EntityType.PHONE: 6,
    EntityType.EMAIL: 7,
}

# Балансовые счета физлиц (ПДн), включая 40802 — счёт ИП (ИП — физлицо, 152-ФЗ).
# Счета организаций (40702, 40602, 30101 …) — не ПДн.
PERSONAL_ACCOUNT_PREFIXES = ("40817", "40820", "40802", "423", "426")
# Ролевые ящики организации: не ПДн, сущность не создаётся. Сравнивается локальная часть
# без цифр в конце и её первое слово до «.», «_», «-»: «zakupki.tomsk@», «info2@».
ROLE_MAILBOXES = frozenset({
    "info", "mail", "post", "contact", "contacts", "hello", "feedback", "question",
    "zakupki", "zakupka", "zakup", "tender", "tenders", "torgi", "goszakaz",
    "office", "priem", "priemnaya", "reception", "secretary", "sekretar", "kanc", "doc", "docs",
    "sales", "sale", "zakaz", "order", "orders", "shop", "market", "marketing", "pr", "press",
    "support", "help", "helpdesk", "service", "servis", "hotline",
    "buh", "buhgalteria", "accounting", "finance", "fin", "hr", "kadry", "job", "jobs",
    "admin", "noreply", "no-reply", "postmaster", "webmaster", "security",
})

# ---------- Окна контекста (символов) ----------
WIN_SNILS = 40
WIN_PASSPORT = 60
WIN_CARD = 40
WIN_ACCOUNT = 60            # «Личный счёт в АО «Банк …», БИК …: 40817…»
WIN_BIRTH = 40
WIN_BIRTH_RIGHT = 15        # "01.02.1990 г.р." — сокращение стоит после даты
WIN_BIK = 120
WIN_LABEL = 30
WIN_PHONE_LABEL = 30
WIN_ORG_CONTEXT = 30        # «приёмная / отдел / служба …» не дальше 30 символов слева
SERIES_MAX_GAP = 3          # "серия 4506 …": между словом и числом не больше 3 символов

# ---------- Регулярные выражения (компилируются один раз) ----------
S = r"[   ‐‑–—-]"   # разделитель внутри номера

SNILS_RE = re.compile(rf"(?<!\d)(?:\d{{3}}{S}\d{{3}}{S}\d{{3}}{S}\d{{2}}|\d{{11}})(?!\d)")
INN_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
CARD_RE = re.compile(
    rf"(?<!\d)(?<!\d{{4}}{S})"
    rf"(?:\d{{4}}(?:{S}\d{{4}}){{3}}(?:{S}\d{{3}})?|\d{{13,19}})"
    rf"(?!\d)(?!{S}\d{{4}})"
)
ACCOUNT_RE = re.compile(
    rf"(?<!\d)(?<!\d{S})"
    rf"(?:\d{{5}}{S}?\d{{3}}{S}?\d{S}?\d{{4}}{S}?\d{{7}}|\d{{4}}(?:{S}\d{{4}}){{4}})"
    rf"(?!\d)(?!{S}\d)"
)
BIK_RE = re.compile(r"(?<!\d)04\d{7}(?!\d)")
PASSPORT_RE = re.compile(
    rf"(?<!\d)(?P<series>\d{{2}}{S}?\d{{2}})"
    rf"(?:[  ]*(?:№|N|(?i:номер))[  ]*|{S}{{1,2}})?"
    rf"(?P<num>\d{{6}})(?!\d)"
)
PHONE_RE = re.compile(
    rf"(?<![\d+])(?<!\d{{3}}{S})(?:\+7|[78]){S}?"
    rf"(?:\({S}?\d{{3,5}}{S}?\)|\d{{3,5}}){S}?"
    rf"\d{{1,3}}(?:{S}?\d{{2}}){{2}}"
    rf"(?!\d)(?!{S}\d{{3}})"
)
# Без кода страны: "(912) 345-67-89", "912 345-67-89", "(3822) 12-34-56" — 10 цифр.
PHONE_LOCAL_RE = re.compile(
    rf"(?<![\d+])(?<!\d{S})"
    rf"(?:\({S}?\d{{3,5}}{S}?\)|\d{{3,5}}){S}?"
    rf"\d{{1,3}}(?:{S}?\d{{2}}){{2}}"
    rf"(?!\d)(?!{S}\d{{3}})"
)
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")
# Контекст телефона организации слева (нормализованное окно): «телефон приёмной: …»,
# «Отдел продаж: …», «горячая линия …». «Служебный» сюда не попадает: «служеб» ≠ «служб».
ORG_PHONE_CONTEXT = re.compile(
    r"(?<!\w)(?:приемн\w*|отдел\w*|служб\w*|горяч\w*\s+лини\w*|диспетчер\w*|канцеляри\w*"
    r"|колл-?центр\w*|контакт-?центр\w*|секретариат\w*|reception|hotline|call-?cent\w*)"
)
# Ящик организации слева от адреса почты (T-019b): «общий ящик», «общий адрес лаборатории»,
# «общий контакт заказчика», а также те же службы, что у телефонов («приёмная», «отдел» …).
ORG_EMAIL_CONTEXT = re.compile(
    r"(?<!\w)общ(?:ий|его|ему|ая|ей|ую)\s+(?:[\w-]+\s+)?(?:адрес\w*|ящик\w*|почт\w*"
    r"|контакт\w*|e-?mail|электронн\w*)"
    rf"|{ORG_PHONE_CONTEXT.pattern}"
)
# «личный», «мне» между меткой организации и адресом — почта человека.
PERSONAL_MARK_RE = re.compile(r"личн\w*|(?<!\w)(?:мне|мой|моя|мою|мо[её]й)(?!\w)")
# ФИО между меткой и телефоном: «Отдел ВЭД: Кулагин М.Ю., тел. …» — телефон сотрудника.
NAME_IN_GAP_RE = re.compile(
    r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.|[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?[А-ЯЁ][а-яё]+"
    r"|[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+"
)
# Ролевой ящик прямо перед телефоном: «sales@example.ru, тел. 8 (3822) …».
EMAIL_BEFORE_PHONE_RE = re.compile(
    r"(?P<local>[\w.+-]+)@[\w.-]+\s*[,;]?\s*(?:(?:тел|моб|факс|phone)\w*\.?)?\s*:?\s*$",
    re.IGNORECASE,
)

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
DATE_NUM_RE = re.compile(
    r"(?<![\d.])(?P<d>\d{1,2})(?P<sep>[./])(?P<m>\d{1,2})(?P=sep)(?P<y>\d{4})(?!\d)"
)
DATE_ISO_RE = re.compile(r"(?<!\d)(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])")
DATE_TEXT_RE = re.compile(
    rf"(?<!\d)(?P<d>\d{{1,2}})[  ]+(?P<mon>{'|'.join(MONTHS)})[  ]+(?P<y>\d{{4}})(?!\d)",
    re.IGNORECASE,
)

# Контекст ищется в нормализованном окне: нижний регистр, "ё" → "е".
KW_SNILS = re.compile(r"снилс|страхов")
KW_PASSPORT = re.compile(r"паспорт")
KW_ISSUED = re.compile(r"выдан")
KW_SERIES_BEFORE = re.compile(rf"сери[яи]\W{{0,{SERIES_MAX_GAP}}}$")
PHONE_LABEL = re.compile(r"(?<!\w)(?:тел|моб|факс|phone)")
KW_CARD = re.compile(r"карт|card")
# Слово «счёт» слева от счёта физлица (T-019b). Префикс 40817/40820/40802/423/426 по плану
# счетов Банка России уже говорит «счёт физлица или ИП»; ключ по БИК отсекает случайные
# 20 цифр, слово «счёт» рядом — то же самое. Ключ не сошёлся при слове «счёт» — опечатка или
# БИК другого банка рядом, а сведения о счёте человека остаются ПДн.
KW_ACCOUNT = re.compile(r"(?<!\w)(?:сч[её]т\w*|сч\.|р/с|л/с|account)")
KW_BIRTH = re.compile(r"рожд|родил(?:ся|ась)|г\.\s?р\.|д\.\s?р\.")
KW_BIRTH_RIGHT = re.compile(r"^[\s,]*(?:г\.\s?р\.|д\.\s?р\.|года\s+рождения|г\.\s*рожд)")
YEAR_RE = re.compile(r"\d{4}")
# Метка реквизита организации прямо перед числом: "ИНН 7707…", "ОКТМО: 7970…".
NON_PD_LABEL = re.compile(
    r"(?:инн|кпп|огрнип|огрн|окпо|октмо|окато|оквэд|бик|икз|р/с|к/с|кор\.?\s*сч\w*)"
    r"\s*[:№]?\s*$"
)


@dataclass(frozen=True, slots=True)
class Candidate:
    type: EntityType
    start: int
    end: int
    confidence: float


@dataclass(frozen=True, slots=True)
class _Ctx:
    """Текст документа и границы сегментов для окон контекста."""

    text: str
    seg_starts: list[int]
    seg_ends: list[int]

    def segment_bounds(self, pos: int) -> tuple[int, int]:
        i = bisect_right(self.seg_starts, pos) - 1
        if i < 0:
            return 0, len(self.text)
        return self.seg_starts[i], self.seg_ends[i]


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def left_window(text: str, start: int, width: int) -> str:
    """До width символов слева от start в пределах текущей строки, нормализованные."""
    lo = max(0, start - width)
    nl = text.rfind("\n", lo, start)
    return _norm(text[(nl + 1 if nl != -1 else lo):start])


def right_window(text: str, end: int, width: int) -> str:
    """До width символов справа от end в пределах текущей строки, нормализованные."""
    hi = min(len(text), end + width)
    nl = text.find("\n", end, hi)
    return _norm(text[end:(nl if nl != -1 else hi)])


def _has_non_pd_label(text: str, start: int) -> bool:
    return NON_PD_LABEL.search(left_window(text, start, WIN_LABEL)) is not None


def _clean_gap(gap: str) -> bool:
    """Между словом-контекстом и кандидатом нет другого числа (≥ 4 цифр) и метки телефона."""
    return YEAR_RE.search(gap) is None and PHONE_LABEL.search(gap) is None


def keyword_near(text: str, start: int, end: int, kw: re.Pattern[str], width: int) -> bool:
    """Слово kw в окне ±width (в пределах строки) и «чистый» промежуток до кандидата.

    Одно слово-контекст не засчитывается нескольким числам: ближайшее вхождение
    слева/справа должно отделяться от кандидата только текстом без других чисел.
    """
    left = left_window(text, start, width)
    hits = list(kw.finditer(left))
    if hits and _clean_gap(left[hits[-1].end():]):
        return True
    right = right_window(text, end, width)
    hit = kw.search(right)
    return hit is not None and _clean_gap(right[:hit.start()])


# ---------- Поиск по типам ----------
def _find_snils(ctx: _Ctx) -> Iterator[Candidate]:
    text = ctx.text
    for m in SNILS_RE.finditer(text):
        value = m.group()
        if not v.is_valid_snils(value):
            continue
        if value.isdigit():  # 11 цифр подряд — может быть телефоном, нужен контекст
            if not KW_SNILS.search(left_window(text, m.start(), WIN_SNILS)):
                continue
            if _has_non_pd_label(text, m.start()):
                continue
        yield Candidate(EntityType.SNILS, m.start(), m.end(), CONF_CHECKSUM)


def _find_inn(ctx: _Ctx) -> Iterator[Candidate]:
    # Только 12 цифр (физлицо). ИНН юрлица (10 цифр) — не ПДн.
    for m in INN_RE.finditer(ctx.text):
        if v.is_valid_inn(m.group()):
            yield Candidate(EntityType.INN, m.start(), m.end(), CONF_CHECKSUM)


def _find_card(ctx: _Ctx) -> Iterator[Candidate]:
    text = ctx.text
    for m in CARD_RE.finditer(text):
        start, end, value = m.start(), m.end(), m.group()
        if _has_non_pd_label(text, start):
            continue
        if value.isdigit():  # сплошные цифры — только рядом со словом "карта"
            if not KW_CARD.search(left_window(text, start, WIN_CARD)):
                continue
        elif not v.is_valid_card(value) and len(value) > 19:
            end, value = start + 19, value[:19]  # 16 цифр + чужая группа из 3 цифр
        if v.is_valid_card(value) or _is_labeled_card(text, start, value):
            yield Candidate(EntityType.CARD, start, end, CONF_CHECKSUM)


def _is_labeled_card(text: str, start: int, value: str) -> bool:
    """Номер по ISO/IEC 7812 (16–19 цифр, алгоритм Луна) с любой первой цифрой 1–9, если слева
    слово «карта» (T-019b). Первая цифра 2–6 — платёжные системы (Мир, Visa, Mastercard);
    1, 7–9 — отраслевые и национальные карты, тоже номер карты физлица."""
    digits = v.only_digits(value) or ""
    if not 16 <= len(digits) <= 19 or digits[0] == "0" or not v.luhn_ok(digits):
        return False
    return KW_CARD.search(left_window(text, start, WIN_CARD)) is not None


def _has_passport_context(text: str, start: int, end: int) -> bool:
    """«паспорт» рядом, ИЛИ «серия/серии» прямо перед числом и «выдан» рядом.

    Само слово «серия» контекстом не считается: «серия поставки», «серия препарата».
    """
    if keyword_near(text, start, end, KW_PASSPORT, WIN_PASSPORT):
        return True
    series_before = KW_SERIES_BEFORE.search(left_window(text, start, WIN_PASSPORT))
    return series_before is not None and keyword_near(
        text, start, end, KW_ISSUED, WIN_PASSPORT
    )


def _find_passport(ctx: _Ctx) -> Iterator[Candidate]:
    text = ctx.text
    for m in PASSPORT_RE.finditer(text):
        if not v.is_valid_passport_series(m.group("series")):
            continue
        if _has_passport_context(text, m.start(), m.end()) and not _has_non_pd_label(
            text, m.start()
        ):
            yield Candidate(EntityType.PASSPORT, m.start(), m.end(), CONF_CONTEXT)


def is_role_mailbox(local: str) -> bool:
    """Локальная часть адреса — ролевой ящик организации: «info2», «zakupki.tomsk»."""
    local = local.lower().rstrip("0123456789")
    first = re.split(r"[._-]", local, maxsplit=1)[0].rstrip("0123456789")
    return local in ROLE_MAILBOXES or first in ROLE_MAILBOXES


def _raw_left(text: str, start: int, width: int) -> str:
    """Как left_window, но без нормализации — для проверок по регистру (ФИО)."""
    lo = max(0, start - width)
    nl = text.rfind("\n", lo, start)
    return text[(nl + 1 if nl != -1 else lo):start]


def is_org_phone(text: str, start: int, national: str) -> bool:
    """Телефон организации, а не физлица: такой сущности не создаём.

    8-800 / 8-804; метка «приёмная / отдел / служба / горячая линия …» в 30 символах слева
    без ФИО между меткой и номером; ролевой ящик сразу перед номером.
    """
    if v.is_toll_free(national):
        return True
    raw = _raw_left(text, start, WIN_ORG_CONTEXT)
    hits = list(ORG_PHONE_CONTEXT.finditer(_norm(raw)))  # _norm не меняет длину
    if hits and NAME_IN_GAP_RE.search(raw[hits[-1].end():]) is None:
        return True
    email = EMAIL_BEFORE_PHONE_RE.search(_raw_left(text, start, WIN_ORG_CONTEXT + 40))
    return email is not None and is_role_mailbox(email.group("local"))


def _find_phone(ctx: _Ctx) -> Iterator[Candidate]:
    text = ctx.text
    for m in PHONE_RE.finditer(text):
        national = v.ru_phone_national(m.group())
        if national is None or not v.is_valid_ru_phone(m.group()):
            continue
        if _has_non_pd_label(text, m.start()) or is_org_phone(text, m.start(), national):
            continue
        yield Candidate(EntityType.PHONE, m.start(), m.end(), CONF_FORMAT)


def _find_phone_local(ctx: _Ctx) -> Iterator[Candidate]:
    """10 цифр без +7/8 — только с меткой «тел/моб/факс/phone» в 30 символах слева."""
    text = ctx.text
    for m in PHONE_LOCAL_RE.finditer(text):
        national = v.ru_phone_national(m.group())
        if national is None or v.is_valid_ru_phone(m.group()):  # нужно ровно 10 цифр
            continue
        if not PHONE_LABEL.search(left_window(text, m.start(), WIN_PHONE_LABEL)):
            continue
        if _has_non_pd_label(text, m.start()) or is_org_phone(text, m.start(), national):
            continue
        yield Candidate(EntityType.PHONE, m.start(), m.end(), CONF_PHONE_LOCAL)


def is_org_email(text: str, start: int) -> bool:
    """Слева в той же строке — ящик организации («общий ящик», «приёмная» …) и между меткой
    и адресом нет ФИО. «Отдел ВЭД: Кулагин М.Ю., kulagin@…» — почта сотрудника (T-019b)."""
    raw = _raw_left(text, start, WIN_ORG_CONTEXT + 10)
    hits = list(ORG_EMAIL_CONTEXT.finditer(_norm(raw)))  # _norm не меняет длину
    if not hits:
        return False
    gap = raw[hits[-1].end():]
    return NAME_IN_GAP_RE.search(gap) is None and PERSONAL_MARK_RE.search(_norm(gap)) is None


def _find_email(ctx: _Ctx) -> Iterator[Candidate]:
    """Email физлица; ролевые ящики (info@, sales@ …) и ящики с меткой организации слева
    («общий ящик», «приёмная», T-019b) пропускаются: это не ПДн (152-ФЗ, ст. 3 п. 1)."""
    for m in EMAIL_RE.finditer(ctx.text):
        if is_role_mailbox(m.group().split("@", 1)[0]) or is_org_email(ctx.text, m.start()):
            continue
        yield Candidate(EntityType.EMAIL, m.start(), m.end(), CONF_EMAIL)


def _date_parts(m: re.Match[str]) -> tuple[int, int, int]:
    month = m.group("mon") if "mon" in m.re.groupindex else None
    month_no = MONTHS[month.lower()] if month else int(m.group("m"))
    return int(m.group("d")), month_no, int(m.group("y"))


def _has_birth_context(text: str, start: int, end: int) -> bool:
    left = left_window(text, start, WIN_BIRTH)
    keywords = list(KW_BIRTH.finditer(left))
    # между словом-контекстом и датой не должно быть другой даты (иначе контекст чужой)
    if keywords and not YEAR_RE.search(left[keywords[-1].end():]):
        return True
    return KW_BIRTH_RIGHT.search(right_window(text, end, WIN_BIRTH_RIGHT)) is not None


def _find_birth_date(ctx: _Ctx) -> Iterator[Candidate]:
    text = ctx.text
    for pattern in (DATE_NUM_RE, DATE_ISO_RE, DATE_TEXT_RE):
        for m in pattern.finditer(text):
            if not v.is_valid_birth_date(*_date_parts(m)):
                continue
            if _has_birth_context(text, m.start(), m.end()):
                yield Candidate(EntityType.BIRTH_DATE, m.start(), m.end(), CONF_CONTEXT)


def _find_accounts(ctx: _Ctx) -> tuple[list[Candidate], list[tuple[int, int]]]:
    """Счета физлиц + «блокеры»: счета организаций и счета с неверным ключом без слова
    «счёт» слева (T-019b: со словом — счёт физлица, CONF_ACCOUNT_LABELED).

    Блокеры не возвращаются, но занимают свой диапазон, чтобы внутри реквизитов
    организации не нашлись «телефон» или «карта».
    """
    text = ctx.text
    biks = [(m.start(), m.end(), m.group()) for m in BIK_RE.finditer(text)]
    bik_starts = [b[0] for b in biks]
    found: list[Candidate] = []
    blockers: list[tuple[int, int]] = []
    for m in ACCOUNT_RE.finditer(text):
        digits = v.only_digits(m.group()) or ""
        if not digits.startswith(PERSONAL_ACCOUNT_PREFIXES):
            blockers.append((m.start(), m.end()))
            continue
        seg_lo, seg_hi = ctx.segment_bounds(m.start())
        lo, hi = max(seg_lo, m.start() - WIN_BIK), min(seg_hi, m.end() + WIN_BIK)
        window = biks[bisect_left(bik_starts, lo):bisect_right(bik_starts, hi)]
        near = [b[2] for b in window if b[1] <= hi]
        if not near:
            conf = CONF_ACCOUNT_NO_BIK
        elif any(v.is_valid_account_key(digits, bik) for bik in near):
            conf = CONF_CHECKSUM
        elif KW_ACCOUNT.search(left_window(text, m.start(), WIN_ACCOUNT)):
            conf = CONF_ACCOUNT_LABELED            # T-019b: см. KW_ACCOUNT
        else:
            blockers.append((m.start(), m.end()))
            continue
        found.append(Candidate(EntityType.BANK_ACCOUNT, m.start(), m.end(), conf))
    return found, blockers


_FINDERS: tuple[Callable[[_Ctx], Iterator[Candidate]], ...] = (
    _find_snils, _find_inn, _find_card, _find_passport,
    _find_birth_date, _find_phone, _find_phone_local, _find_email,
)


# ---------- Пересечения ----------
class _SpanSet:
    """Непересекающиеся полуинтервалы [start, end) с проверкой пересечения через bisect."""

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []

    def overlaps(self, start: int, end: int) -> bool:
        i = bisect_right(self._starts, start)
        if i > 0 and self._ends[i - 1] > start:
            return True
        return i < len(self._starts) and self._starts[i] < end

    def add(self, start: int, end: int) -> None:
        i = bisect_right(self._starts, start)
        self._starts.insert(i, start)
        self._ends.insert(i, end)


def resolve_overlaps(
    candidates: list[Candidate], blockers: list[tuple[int, int]] | None = None
) -> list[Candidate]:
    """Убирает пересечения: выигрывает больший приоритет типа, затем бо́льшая длина."""
    taken = _SpanSet()
    for start, end in sorted(blockers or []):
        if not taken.overlaps(start, end):
            taken.add(start, end)
    kept: list[Candidate] = []
    order = sorted(candidates, key=lambda c: (PRIORITY[c.type], c.start - c.end, c.start))
    for cand in order:
        if not taken.overlaps(cand.start, cand.end):
            taken.add(cand.start, cand.end)
            insort(kept, cand, key=lambda c: c.start)
    return kept


def _to_entity(text: str, cand: Candidate) -> Entity:
    return Entity(
        type=cand.type,
        category=EntityCategory.pd,
        start=cand.start,
        end=cand.end,
        value_masked=mask_value(text[cand.start:cand.end]),
        confidence=cand.confidence,
        detector="regex",
    )


def detect_regex(doc: ExtractedDocument) -> list[Entity]:
    """Ищет СНИЛС, ИНН физлица, карту, счёт физлица, паспорт, дату рождения, телефон, email.

    Все сущности — category=pd, detector="regex", без пересечений, по возрастанию start.
    page/section не заполняются — это делает detect.assign_location.
    """
    text = doc.text
    if not text:
        return []
    segments = sorted(doc.segments, key=lambda s: s.offset)
    ctx = _Ctx(
        text=text,
        seg_starts=[s.offset for s in segments],
        seg_ends=[s.offset + len(s.text) for s in segments],
    )
    candidates, blockers = _find_accounts(ctx)
    for finder in _FINDERS:
        candidates.extend(finder(ctx))
    return [_to_entity(text, c) for c in resolve_overlaps(candidates, blockers)]
