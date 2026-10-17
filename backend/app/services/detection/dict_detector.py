"""Словарный детектор коммерческой тайны и финансовых данных. Задача T-013a, исполнитель — Данила.

Внутренний интерфейс модуля detection — не контракт.

Принцип: коммерческая тайна и финансы — это связка «ключевое слово + значение»
(«себестоимость … 38 400 руб.», «12% — наценка»). Само слово «маржа» без числа
сущностью не считается — это кандидат на уточнение LLM в T-013. Слова без значения
(«ноу-хау», «рецептура») — сущность сразу, но с меньшей уверенностью.

Цены и суммы из документации закупки (НМЦК, цена контракта, обеспечение, аванс)
публикуются в ЕИС (44-ФЗ) и коммерческой тайной не являются — исключаются.

Словари — data/rules/dictionaries.yaml (Алина расширяет без кода). Расчётные счета
организаций (407/406/405/40802, корсчёт 30101) ищутся кодом с проверкой ключа по БИК.
Счета физлиц — ПДн, их находит regex_detector (T-007).

value_masked для trade_secret / financial — исходный фрагмент: маскируются только ПДн
(решение 17.10). Фрагменты текста в логи не пишутся (AGENTS.md, правило 4).
"""

import logging
import re
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.schemas import Entity, EntityCategory, EntityType, ExtractedDocument
from app.services.detection import validators as v
from app.services.detection.regex_detector import ACCOUNT_RE, BIK_RE

logger = logging.getLogger(__name__)

# ---------- Уверенность по умолчанию ----------
CONF_WITH_VALUE = 0.9       # ключевое слово + значение
CONF_KEYWORD_ONLY = 0.75    # needs_value: false
CONF_ACCOUNT_BIK = 0.95     # счёт организации, ключ проверен по БИК рядом
CONF_ACCOUNT_NO_BIK = 0.8   # счёт организации, БИК рядом нет

# ---------- Окна (символов) ----------
WIN_VALUE_AFTER = 80        # значение не дальше 80 символов после ключевого слова
WIN_VALUE_BEFORE = 40       # … или 40 символов перед ним: «12% — наценка»
WIN_EXCLUDE = 60            # окно исключений слева от слова и от значения
WIN_VAT = 12                # «НДС 20%» — значение налога, а не показатель
WIN_BIK = 120
MAX_ENTITY_LEN = 160

ACCOUNT_GROUP_ID = "org_account"
ORG_ACCOUNT_PREFIXES = ("405", "406", "407", "40802", "30101")
ALLOWED_TYPES = {EntityType.TRADE_SECRET, EntityType.FINANCIAL}
CATEGORY = {
    EntityType.TRADE_SECRET: EntityCategory.trade_secret,
    EntityType.FINANCIAL: EntityCategory.financial,
}

# ---------- Значение: сумма, процент, число с единицей ----------
_NUM = r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_SCALE = r"(?:тыс|млн|млрд|трлн)\.?"
_CURRENCY = r"(?:руб(?:л[а-я]*)?\.?|р\.|₽|rub|долл[а-я]*\.?|usd|евро|eur|€|\$)"
_PERCENT = r"(?:%|процент[а-я]*|проц\.)"
_PER_UNIT = r"(?:\s?/\s?(?:ед|шт|мес|год|час|сут|кг|т|м2|м)\.?)"
VALUE_RE = re.compile(
    rf"(?<!\d[  ])(?<![\w.,])"
    rf"(?:[$€]\s?(?:{_NUM})"
    rf"|(?:{_NUM})(?:\s?{_SCALE})?\s?(?:{_CURRENCY}|{_PERCENT}){_PER_UNIT}?)"
    rf"(?!\w)",
    re.IGNORECASE,
)

# Граница фразы: «;», перевод строки, «!», «?» или точка в конце предложения.
# Точка внутри «ед.», «руб. за единицу», «1.5» фразу не обрывает.
PHRASE_END_RE = re.compile(r"[;\n!?]|\.(?=\s+[A-ZА-ЯЕ«\"]|\s*$)")

# Исключения: публичные цены и суммы закупки (44-ФЗ, ЕИС). Ищутся в нормализованном тексте.
EXCLUDE_LEFT_RE = re.compile(
    r"нмцк|начальн\w+\s+\(?(?:максимальн\w+\)?\s+)?цен\w*|цен\w*\s+(?:контракта|договора)"
    r"|стоимост\w*\s+контракта|обеспечени\w+\s+(?:заявки|исполнения)|размер\w*\s+аванса",
    re.IGNORECASE,
)
NOT_SECRET_RE = re.compile(
    r"не\s+(?:явля\w+|составля\w+|относ\w+)\s+(?:к\s+)?коммерческ\w+\s+тайн\w*",
    re.IGNORECASE,
)
VAT_RE = re.compile(r"ндс", re.IGNORECASE)
# Названия законов и статей: «№ 98-ФЗ «О коммерческой тайне»», «ст. 1465 ГК РФ «…»».
LAW_TITLE_RE = re.compile(
    r"(?:закон\w*|кодекс\w*|\bгк\b|\d+-фз|\bст\.|стать\w+|положени\w+)[^«\n]{0,40}«[^»\n]{0,200}»"
    r"|\d+-фз",
    re.IGNORECASE,
)
_EDGE_JUNK = " \t ,:;—–-"


@dataclass(frozen=True, slots=True)
class DictGroup:
    """Группа словаря: одна объединённая регулярка на все шаблоны группы."""

    id: str
    type: EntityType
    regex: re.Pattern[str]
    needs_value: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class _Candidate:
    type: EntityType
    start: int
    end: int
    anchor_start: int       # часть, которая обязана уцелеть при обрезке по ПДн:
    anchor_end: int         # значение, а для needs_value: false — ключевое слово
    confidence: float
    group_id: str


# ---------- Загрузка словарей ----------
def _default_path() -> Path:
    """/data/rules (контейнер) или <корень репозитория>/data/rules (локально, CI)."""
    repo_root = Path(__file__).resolve().parents[4]
    candidates = (
        Path("/data/rules/dictionaries.yaml"),
        repo_root / "data" / "rules" / "dictionaries.yaml",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("dictionaries.yaml не найден в /data/rules и data/rules")


def _compile_group(item: object, position: int, seen: set[str]) -> DictGroup:
    """Проверяет одну группу словаря; ошибка формата — ValueError с id группы."""
    raw_id = item.get("id") if isinstance(item, dict) else None
    label = raw_id if isinstance(raw_id, str) and raw_id else f"№{position}"
    if not isinstance(item, dict):
        raise ValueError(f"Группа {label}: ожидается словарь")
    extra = set(item) - {"id", "type", "patterns", "needs_value", "confidence", "description"}
    if extra:
        raise ValueError(f"Группа {label}: неизвестные поля {sorted(extra)}")
    if not isinstance(raw_id, str) or not raw_id or raw_id in seen:
        raise ValueError(f"Группа {label}: id пустой или повторяется")
    try:
        etype = EntityType(item.get("type"))
    except ValueError:
        raise ValueError(f"Группа {label}: type должен быть TRADE_SECRET или FINANCIAL") from None
    if etype not in ALLOWED_TYPES:
        raise ValueError(f"Группа {label}: type должен быть TRADE_SECRET или FINANCIAL")
    needs_value = item.get("needs_value")
    if not isinstance(needs_value, bool):
        raise ValueError(f"Группа {label}: needs_value должен быть true или false")
    default_conf = CONF_WITH_VALUE if needs_value else CONF_KEYWORD_ONLY
    confidence = item.get("confidence", default_conf)
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise ValueError(f"Группа {label}: confidence должен быть числом")
    if not 0 <= confidence <= 1:
        raise ValueError(f"Группа {label}: confidence вне диапазона [0, 1]")
    regex = _compile_patterns(item.get("patterns"), label)
    return DictGroup(raw_id, etype, regex, needs_value, float(confidence))


def _compile_patterns(patterns: object, label: str) -> re.Pattern[str]:
    """Объединяет шаблоны группы в одну регулярку с границами слова."""
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"Группа {label}: patterns должен быть непустым списком")
    parts: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"Группа {label}: шаблон должен быть непустой строкой")
        normalized = re.sub(r" +", r"\\s+", _norm(pattern.strip()))
        try:
            re.compile(normalized)
        except re.error as exc:
            raise ValueError(f"Группа {label}: некорректный шаблон ({exc.msg})") from None
        parts.append(f"(?:{normalized})")
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=4)
def load_dictionaries(path: Path | None = None) -> tuple[DictGroup, ...]:
    """Загружает и компилирует словари один раз на процесс (lru_cache).

    path=None — первый существующий из /data/rules/dictionaries.yaml и
    <корень репозитория>/data/rules/dictionaries.yaml. Ошибка формата → ValueError с id группы.
    """
    source = path or _default_path()
    try:
        with source.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = "" if mark is None else f": строка {mark.line + 1}"
        raise ValueError(f"Некорректный YAML словарей{where}") from None
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ValueError("Файл словарей должен содержать список групп")
    groups: list[DictGroup] = []
    seen: set[str] = set()
    for position, item in enumerate(data, start=1):
        group = _compile_group(item, position, seen)
        seen.add(group.id)
        groups.append(group)
    return tuple(groups)


# ---------- Текст: нормализация, фразы, исключения ----------
def _norm(s: str) -> str:
    """«ё» → «е» с сохранением длины: смещения в нормализованном тексте совпадают."""
    return s.replace("ё", "е").replace("Ё", "Е")


@dataclass(frozen=True, slots=True)
class _Text:
    norm: str
    phrase_ends: list[int]          # позиции сразу после границы фразы
    law_starts: list[int]
    law_ends: list[int]

    @classmethod
    def build(cls, text: str) -> "_Text":
        norm = _norm(text)
        ends = [m.end() for m in PHRASE_END_RE.finditer(norm)]
        laws = [(m.start(), m.end()) for m in LAW_TITLE_RE.finditer(norm)]
        return cls(norm, ends, [a for a, _ in laws], [b for _, b in laws])

    def phrase(self, pos: int) -> tuple[int, int]:
        """Границы фразы [lo, hi), в которую попадает pos; hi включает точку «руб.»."""
        i = bisect_right(self.phrase_ends, pos)
        lo = self.phrase_ends[i - 1] if i > 0 else 0
        hi = self.phrase_ends[i] if i < len(self.phrase_ends) else len(self.norm)
        return lo, hi

    def in_law_title(self, start: int, end: int) -> bool:
        i = bisect_right(self.law_starts, start) - 1
        return i >= 0 and self.law_ends[i] >= end

    def excluded(self, pos: int, phrase_lo: int) -> bool:
        """Слева от pos (в пределах фразы, ≤ 60 символов) — публичная цена закупки."""
        window = self.norm[max(phrase_lo, pos - WIN_EXCLUDE):pos]
        return EXCLUDE_LEFT_RE.search(window) is not None

    def is_vat(self, value_start: int) -> bool:
        return VAT_RE.search(self.norm[max(0, value_start - WIN_VAT):value_start]) is not None


# ---------- Поиск значений ----------
def _value_after(t: _Text, kw_end: int, hi: int, used: set[int]) -> tuple[int, int] | None:
    """Первое значение после слова в пределах фразы, начало ≤ 80 символов от слова."""
    for m in VALUE_RE.finditer(t.norm, kw_end, hi):
        if m.start() - kw_end > WIN_VALUE_AFTER:
            return None
        if m.start() not in used and not t.is_vat(m.start()):
            return m.start(), m.end()
    return None


def _value_before(t: _Text, kw_start: int, lo: int, used: set[int]) -> tuple[int, int] | None:
    """Ближайшее значение перед словом: «12% — наценка». Запятая между ними — чужая фраза."""
    best: tuple[int, int] | None = None
    for m in VALUE_RE.finditer(t.norm, max(lo, kw_start - WIN_VALUE_BEFORE - 40), kw_start):
        gap = t.norm[m.end():kw_start]
        if len(gap) <= WIN_VALUE_BEFORE and "," not in gap and m.start() not in used:
            if not t.is_vat(m.start()):
                best = (m.start(), m.end())
    return best


def _keyword_hits(t: _Text, groups: Sequence[DictGroup]) -> Iterator[tuple[DictGroup, int, int]]:
    for group in groups:
        for m in group.regex.finditer(t.norm):
            if not t.in_law_title(m.start(), m.end()):
                yield group, m.start(), m.end()


def _pair(group: DictGroup, ks: int, ke: int, value: tuple[int, int]) -> _Candidate:
    vs, ve = value
    start, end = min(ks, vs), max(ke, ve)
    return _Candidate(group.type, start, end, vs, ve, group.confidence, group.id)


def _keyword_candidates(t: _Text, groups: Sequence[DictGroup]) -> list[_Candidate]:
    """Связки «слово + значение» и слова без значения, с учётом исключений.

    Сначала каждое слово берёт значение после себя; значения, уже занятые словом слева,
    второму слову «перед ним» не достаются («ретро-бонус 3% от оборота» — одна сущность).
    """
    found: list[_Candidate] = []
    used: set[int] = set()
    pending: list[tuple[DictGroup, int, int, int]] = []
    for group, ks, ke in sorted(_keyword_hits(t, groups), key=lambda h: h[1]):
        lo, hi = t.phrase(ks)
        if t.excluded(ks, lo) or NOT_SECRET_RE.search(t.norm, lo, hi):
            continue
        if not group.needs_value:
            found.append(_Candidate(group.type, ks, ke, ks, ke, group.confidence, group.id))
            continue
        value = _value_after(t, ke, hi, used)
        if value is None or t.excluded(value[0], lo):
            pending.append((group, ks, ke, lo))
            continue
        used.add(value[0])
        found.append(_pair(group, ks, ke, value))
    for group, ks, ke, lo in pending:
        value = _value_before(t, ks, lo, used)
        if value is not None and not t.excluded(value[0], lo):
            used.add(value[0])
            found.append(_pair(group, ks, ke, value))
    return [c for c in found if c.end - c.start <= MAX_ENTITY_LEN]


# ---------- Счета организаций ----------
def _account_candidates(text: str) -> list[_Candidate]:
    """Расчётные счета организаций и корсчета: 20 цифр с балансовым префиксом.

    БИК рядом (±120 символов) и ключ сошёлся — 0.95; БИК рядом нет — 0.8;
    БИК есть, но ключ не сошёлся ни с одним — не счёт (как в regex_detector).
    """
    biks = [(m.start(), m.group()) for m in BIK_RE.finditer(text)]
    bik_starts = [b[0] for b in biks]
    found: list[_Candidate] = []
    for m in ACCOUNT_RE.finditer(text):
        digits = v.only_digits(m.group()) or ""
        if not digits.startswith(ORG_ACCOUNT_PREFIXES):
            continue
        lo, hi = m.start() - WIN_BIK, m.end() + WIN_BIK
        near = [b for _, b in biks[bisect_left(bik_starts, lo):bisect_right(bik_starts, hi)]]
        if not near:
            conf = CONF_ACCOUNT_NO_BIK
        elif any(v.is_valid_account_key(digits, bik) for bik in near):
            conf = CONF_ACCOUNT_BIK
        else:
            continue
        found.append(_Candidate(
            EntityType.FINANCIAL, m.start(), m.end(), m.start(), m.end(), conf, ACCOUNT_GROUP_ID
        ))
    return found


# ---------- Обрезка по ПДн и пересечения ----------
@dataclass(frozen=True, slots=True)
class _PdIndex:
    """Спаны ПДн, отсортированные по start, для поиска пересечений через bisect."""

    starts: list[int]
    ends: list[int]
    max_len: int

    @classmethod
    def build(cls, entities: Sequence[Entity]) -> "_PdIndex":
        spans = sorted((e.start, e.end) for e in entities)
        max_len = max((b - a for a, b in spans), default=0)
        return cls([a for a, _ in spans], [b for _, b in spans], max_len)

    def overlapping(self, start: int, end: int) -> list[tuple[int, int]]:
        lo = bisect_left(self.starts, start - self.max_len)
        hi = bisect_left(self.starts, end)
        return [(a, b) for a, b in zip(self.starts[lo:hi], self.ends[lo:hi], strict=True)
                if b > start]


def _trim_to_free(text: str, cand: _Candidate, pd: _PdIndex) -> _Candidate | None:
    """Сужает спан до куска без ПДн, содержащего якорь (значение или слово).

    «Наценка ИП Смирнова А.А. составляет 12%» → «составляет 12%». Если ПДн задевает
    сам якорь — None. Края куска очищаются от пробелов и знаков препинания.
    """
    start, end = cand.start, cand.end
    for ps, pe in pd.overlapping(cand.start, cand.end):
        if ps < cand.anchor_end and pe > cand.anchor_start:
            return None
        if pe <= cand.anchor_start:
            start = max(start, pe)
        else:
            end = min(end, ps)
    while start < cand.anchor_start and text[start] in _EDGE_JUNK:
        start += 1
    while end > cand.anchor_end and text[end - 1] in _EDGE_JUNK:
        end -= 1
    if (start, end) == (cand.start, cand.end):
        return cand
    return _Candidate(
        cand.type, start, end, cand.anchor_start, cand.anchor_end, cand.confidence, cand.group_id
    )


def _resolve_overlaps(cands: list[_Candidate]) -> list[_Candidate]:
    """Без пересечений: выше уверенность, затем длиннее, затем левее. По возрастанию start."""
    kept: list[_Candidate] = []       # по возрастанию start, без пересечений
    for cand in sorted(cands, key=lambda c: (-c.confidence, c.start - c.end, c.start)):
        i = bisect_left(kept, cand.start, key=lambda c: c.start)
        if i > 0 and kept[i - 1].end > cand.start:
            continue
        if i < len(kept) and kept[i].start < cand.end:
            continue
        kept.insert(i, cand)
    return kept


def _to_entity(text: str, cand: _Candidate) -> Entity:
    return Entity(
        type=cand.type,
        category=CATEGORY[cand.type],
        start=cand.start,
        end=cand.end,
        value_masked=text[cand.start:cand.end],   # не ПДн — без маскирования (решение 17.10)
        confidence=cand.confidence,
        detector="dict",
    )


def find_candidates(
    doc: ExtractedDocument,
    pd_entities: Sequence[Entity] = (),
    groups: Sequence[DictGroup] | None = None,
) -> list[_Candidate]:
    """Кандидаты с id группы — для detect_dict и для метрик в тестах."""
    text = doc.text
    if not text:
        return []
    groups = load_dictionaries() if groups is None else groups
    t = _Text.build(text)
    pd = _PdIndex.build(pd_entities)
    raw = _keyword_candidates(t, groups) + _account_candidates(text)
    trimmed = [c for c in (_trim_to_free(text, c, pd) for c in raw) if c is not None]
    return _resolve_overlaps(trimmed)


def detect_dict(
    doc: ExtractedDocument,
    pd_entities: Sequence[Entity] = (),
    groups: Sequence[DictGroup] | None = None,
) -> list[Entity]:
    """Ищет TRADE_SECRET и FINANCIAL по словарям и шаблонам, без LLM.

    detector="dict", координаты — в исходном doc.text, value_masked == doc.text[start:end].
    pd_entities — уже найденные ПДн (regex + NER): спаны обрезаются так, чтобы не
    пересекаться с ними, иначе merge_entities выкинет одну из сущностей, а mask() упадёт.
    Результат без пересечений, по возрастанию start; page/section ставит detect.assign_location.
    """
    started = time.perf_counter()
    entities = [_to_entity(doc.text, c) for c in find_candidates(doc, pd_entities, groups)]
    counts = Counter(e.type.value for e in entities)
    # В лог — только количество по типам и время (AGENTS.md, правило 4).
    logger.info(
        "detect_dict: entities=%d by_type=%s elapsed_ms=%.1f",
        len(entities), dict(sorted(counts.items())), (time.perf_counter() - started) * 1000,
    )
    return entities
