"""Генерация обучающих фрагментов для классификатора КТ и особых категорий. Задача T-036a.

Дешёвая облачная модель (gpt-4o-mini через app.services.llm.client.complete_json, кэш Redis)
пишет короткие фрагменты документов закупок с меткой TRADE_SECRET / SPECIAL_CATEGORY / NONE.
Разнообразие — перебором «сеток»: тип документа × стиль × отрасль × задание × набор тем.

Честность метрики: ни одной строки из eval/* в промпт не попадает; промпт — ml/prompts/generate.md.

Запуск (из корня репозитория, PowerShell, нужен ключ в .env):
    docker-compose up -d redis
    docker-compose run --rm -e LLM_TIMEOUT_S=90 -v "${PWD}/ml:/ml" -v "${PWD}/data/train:/train" \
        api python /ml/generate_data.py --out /train --dry-run      # план и оценка стоимости
    ... то же без --dry-run                                           # генерация

Повторный запуск продолжает с места остановки (ml/out/gen_raw.jsonl) и берёт ответы из кэша.
"""

import argparse
import json
import logging
import random
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from itertools import product
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    LABELS,
    ML_DIR,
    OUT_DIR,
    SEED,
    TRAIN_DIR,
    dedup_key,
    setup_logging,
    write_json,
)  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logger = logging.getLogger("generate_data")

PROMPT_FILE = ML_DIR / "prompts" / "generate.md"
CHECKPOINT_FILE = OUT_DIR / "gen_raw.jsonl"
ITEMS_PER_CALL = 20
MIN_CHARS, MAX_CHARS = 15, 300
REVIEW_SAMPLE = 300
CHARS_PER_TOKEN = 2.6          # консервативно для русского текста (для оценки стоимости)

DOC_TYPES = {
    "letter": "деловое письмо",
    "chat": "переписка в рабочем чате",
    "memo": "служебная записка",
    "minutes": "протокол совещания",
    "offer": "коммерческое предложение",
    "costing": "калькуляция (таблица)",
    "form": "анкета кандидата или сотрудника",
    "certificate": "справка",
    "reference": "характеристика на сотрудника",
    "contract": "договор",
    "procurement_report": "отчёт о закупке",
}
REGISTERS = {
    "official": "официальный, канцелярский",
    "casual": "разговорный, как пишут коллеги друг другу",
    "abbrev": "сжатый, с сокращениями и обрывками фраз, как в заметках и таблицах",
}
INDUSTRIES = {
    "construction": "строительство", "it": "IT и программное обеспечение",
    "medicine": "медицина и фармацевтика", "catering": "общественное питание",
    "furniture": "производство мебели", "logistics": "логистика и перевозки",
    "fuel": "ГСМ и топливо",
}
TS_DOCS = ("letter", "chat", "memo", "minutes", "offer", "costing", "contract",
           "procurement_report")
SC_DOCS = ("letter", "chat", "memo", "minutes", "form", "certificate", "reference")
ALL_DOCS = tuple(DOC_TYPES)

TS_THEMES = (
    "себестоимость единицы товара и её статьи", "закупочная цена у завода или дистрибьютора",
    "скидка производителя или дилерская скидка нам", "наценка и торговая надбавка",
    "маржа и рентабельность конкретной сделки", "особая цена или скидка одному клиенту",
    "ретро-бонус за объём от производителя", "минимальная цена на торгах, ниже которой не идём",
    "на сколько процентов готовы снизиться на аукционе", "внутренний план продаж на период",
    "список ключевых клиентов с объёмами закупок", "ставки субподрядчиков и их расценки",
    "рецептура и пропорции компонентов", "технологический режим: температура, время, давление",
    "нормы расхода материалов на единицу продукции", "транспортная составляющая в калькуляции",
    "фонд оплаты труда в калькуляции проекта", "условия отсрочки, полученные от поставщика",
)
SC_THEMES = (
    "диагноз или хроническая болезнь сотрудника", "перенесённая операция или госпитализация",
    "группа инвалидности", "беременность сотрудницы", "больничный с названной причиной",
    "медицинские противопоказания к работе", "учёт у нарколога или психиатра",
    "травма и её последствия", "судимость или привлечение к ответственности",
    "национальность человека", "вероисповедание и религиозные обряды",
    "политические взгляды или членство в партии", "членство в профсоюзе",
    "аллергия или особая диета по болезни", "лечение и регулярные визиты к врачу",
    "сведения о личной жизни в сдержанной форме",
)
HARD_THEMES = (
    "НМЦК и цена контракта", "обеспечение заявки и исполнения", "цены из публичного прайс-листа",
    "цена для заказчика в коммерческом предложении", "НДС и другие налоги",
    "реквизиты организации", "штрафы и пени по договору",
    "закупка лекарств без конкретного человека",
    "требование «отсутствие судимости» к участникам", "должность и стаж сотрудника",
    "«член комиссии» и «член совета директоров»", "больничный или отпуск без причины",
    "медосмотр пройден, допуск к работе", "выручка и прибыль из публичной отчётности",
    "пустые поля анкеты и ссылки на законы", "количество товара и сроки поставки",
)
PLAIN_THEMES = (
    "приветствие и вежливые формулы", "порядок приёмки товара", "адрес доставки на склад",
    "требования технического задания", "график работ и этапы", "контактное лицо по вопросам",
    "условия гарантии", "порядок документооборота", "упаковка и маркировка", "повестка совещания",
    "обучение персонала заказчика", "техника безопасности на объекте",
)


class GenItem(BaseModel):
    """Один фрагмент в ответе LLM. Метка — str: неизвестные значения отсеивает postprocess."""

    text: str
    label: str


class GenBatch(BaseModel):
    items: list[GenItem]


@dataclass(frozen=True)
class CallKind:
    name: str
    task: str
    docs: tuple[str, ...]
    themes: tuple[str, ...]
    calls: int
    items: int = ITEMS_PER_CALL
    chars: int = 110                  # ожидаемая длина фрагмента (для оценки стоимости)


CALL_KINDS = (
    CallKind("trade_secret", "все фрагменты класса TRADE_SECRET.", TS_DOCS, TS_THEMES, 60),
    CallKind("special", "все фрагменты класса SPECIAL_CATEGORY.", SC_DOCS, SC_THEMES, 45),
    CallKind("contrast_ts", "10 пар подряд: фрагмент TRADE_SECRET и сразу за ним похожий по "
             "словам и теме фрагмент NONE (публичные сведения, нет внутренней экономики).",
             TS_DOCS, TS_THEMES + HARD_THEMES[:8], 30),
    CallKind("contrast_sc", "10 пар подряд: фрагмент SPECIAL_CATEGORY и сразу за ним похожий по "
             "словам фрагмент NONE (нет конкретного человека, нет причины или нет сведений).",
             SC_DOCS, SC_THEMES + HARD_THEMES[7:], 30),
    CallKind("hard_none", "все фрагменты класса NONE — «трудные отрицания» с цифрами, деньгами, "
             "медициной или людьми.", ALL_DOCS, HARD_THEMES, 55),
    CallKind("plain_none", "все фрагменты класса NONE — обычные фразы такого документа.",
             ALL_DOCS, PLAIN_THEMES, 80),
)

# ---------- набор v2 (после первой оценки на dev) ----------
# Причины взяты из статистики обучающих данных v1, а не из текстов eval/*:
# [PERSON] был в 95% фрагментов SPECIAL_CATEGORY и в 9% NONE (ярлык «человек → особые»);
# плейсхолдеров [CARD]/[BANK_ACCOUNT]/[INN]/[SNILS]/[PASSPORT] в данных не было вовсе;
# фрагменты короче настоящих предложений (максимум 162 символа).
PERSON_NONE_THEMES = (
    "должность и подразделение", "контактное лицо и способ связи", "командировка и её цель",
    "отпуск или больничный без причины", "подпись, виза, согласование документа",
    "ответственный за этап или поставку", "член комиссии и его роль", "стаж и опыт работы",
    "образование и повышение квалификации", "замещение на время отсутствия",
    "приём на работу, перевод, график смен", "участие в совещании и поручения",
    "награда, благодарность, оценка работы", "доверенность и полномочия",
    "гражданство и место работы", "семейное положение, дети (без медицины)",
)
REQUISITE_THEMES = (
    "банковская карта сотрудника для выплат", "лицевой или расчётный счёт физлица",
    "ИНН и СНИЛС в анкете", "паспортные данные в доверенности", "контакты и адрес в заявлении",
    "дата рождения в карточке сотрудника", "реквизиты ИП в договоре", "реестр выплат",
)
SC_FORM_THEMES = (
    "диагноз или группа инвалидности в анкете", "национальность в анкете",
    "вероисповедание в анкете", "сведения о судимости с конкретным значением",
    "партийность или профсоюз в анкете", "причина больничного в справке",
    "противопоказания в медицинском заключении", "обычные поля анкеты: образование, стаж",
    "обычные поля: семейное положение, гражданство, языки", "поля справки: место работы, оклад",
)
FORM_DOCS = ("form", "certificate", "reference", "memo", "letter")
REQ_DOCS = ("form", "contract", "letter", "certificate", "procurement_report", "memo")

CALL_KINDS_V2 = (
    *(CallKind(k.name, k.task, k.docs, k.themes, round(k.calls * 0.55)) for k in CALL_KINDS),
    CallKind("none_person", "все фрагменты класса NONE; в каждом есть [PERSON_1] или "
             "[PERSON_2] (иногда оба), но сведения о человеке обычные — никакого здоровья, "
             "убеждений, судимости, национальности, профсоюза и партии.",
             ALL_DOCS, PERSON_NONE_THEMES, 55),
    CallKind("none_requisites", "все фрагменты класса NONE с плейсхолдерами реквизитов и "
             "контактов: [CARD_1], [BANK_ACCOUNT_1], [INN_1], [SNILS_1], [PASSPORT_1], "
             "[PHONE_1], [EMAIL_1], [ADDRESS_1], [BIRTH_DATE_1] — в каждом хотя бы один.",
             REQ_DOCS, REQUISITE_THEMES, 30),
    CallKind("sc_form", "строки анкет и справок вида «Поле: значение» без имени человека: "
             "половина — SPECIAL_CATEGORY (значение поля — конкретная болезнь, группа "
             "инвалидности, национальность, вера, судимость, партия, профсоюз), половина — "
             "NONE (обычные поля). Не используй пустые и отрицательные значения у полей "
             "особых категорий.", FORM_DOCS, SC_FORM_THEMES, 25),
    CallKind("long_mix", "длинные сложные предложения настоящих документов (150–300 "
             "символов) с перечислениями, оговорками и цифрами: 5 TRADE_SECRET, "
             "4 SPECIAL_CATEGORY, 6 NONE (из них 3 «трудных»). Человек может быть назван "
             "словами («сотрудник», «кандидатка»), не только плейсхолдером.",
             ALL_DOCS, TS_THEMES + SC_THEMES + HARD_THEMES, 45, items=15, chars=220),
)


@dataclass(frozen=True)
class Plan:
    name: str
    kinds: tuple[CallKind, ...]
    prompt: str                       # файл в ml/prompts/
    checkpoint: str                   # файл в ml/out/
    prefix: str                       # префикс call_id


PLANS = {
    "v1": Plan("v1", CALL_KINDS, "generate.md", "gen_raw.jsonl", ""),
    "v2": Plan("v2", CALL_KINDS_V2, "generate_v2.md", "gen_raw_v2.jsonl", "v2-"),
}
ACTIVE = PLANS["v1"]


def use_plan(name: str) -> Plan:
    """Выбирает набор вызовов, промпт и checkpoint (v1 — первая генерация, v2 — добор)."""
    global ACTIVE, PROMPT_FILE, CHECKPOINT_FILE
    ACTIVE = PLANS[name]
    PROMPT_FILE = ML_DIR / "prompts" / ACTIVE.prompt
    CHECKPOINT_FILE = OUT_DIR / ACTIVE.checkpoint
    return ACTIVE


@dataclass(frozen=True)
class GenCall:
    call_id: str
    kind: str
    doc_type: str
    register: str
    industry: str
    themes: tuple[str, ...]


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failed: int = 0
    by_kind: Counter = field(default_factory=Counter)

    def cost(self, price_in: float, price_out: float) -> float:
        return (self.prompt_tokens * price_in + self.completion_tokens * price_out) / 1e6


def plan_calls(scale: float = 1.0, seed: int = SEED) -> list[GenCall]:
    """Детерминированный план вызовов: сетка тип × стиль × отрасль обходится равномерно."""
    rng = random.Random(seed)
    calls: list[GenCall] = []
    for kind in ACTIVE.kinds:
        grid = list(product(kind.docs, REGISTERS, INDUSTRIES))
        rng.shuffle(grid)
        for i in range(round(kind.calls * scale)):
            doc, reg, ind = grid[i % len(grid)]
            themes = tuple(rng.sample(kind.themes, k=min(4, len(kind.themes))))
            calls.append(GenCall(f"{ACTIVE.prefix}{kind.name}-{i:03d}", kind.name, doc, reg,
                                 ind, themes))
    return calls


def load_prompt() -> tuple[str, Template]:
    """Системное сообщение и шаблон пользовательского (без HTML-комментария в начале)."""
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    raw = re.sub(r"^<!--.*?-->\s*", "", raw, flags=re.DOTALL)
    system, user = raw.split("=== USER ===", 1)
    return system.strip(), Template(user.strip())


def _kind(name: str) -> CallKind:
    return next(k for k in ACTIVE.kinds if k.name == name)


def build_user(call: GenCall, template: Template) -> str:
    kind = _kind(call.kind)
    return template.substitute(
        variant=call.call_id, doc_type=DOC_TYPES[call.doc_type], register=REGISTERS[call.register],
        industry=INDUSTRIES[call.industry], count=kind.items, task=kind.task,
        themes="; ".join(call.themes),
    )


def estimate_cost(calls: list[GenCall], price_in: float, price_out: float) -> dict:
    """Оценка сверху: входные символы промпта + ~20 фрагментов по 110 символов в ответе."""
    system, template = load_prompt()
    in_chars = sum(len(system) + len(build_user(c, template)) + 600 for c in calls)  # +схема
    out_chars = sum(_kind(c.kind).items * (_kind(c.kind).chars + 30) for c in calls)
    tok_in, tok_out = in_chars / CHARS_PER_TOKEN, out_chars / CHARS_PER_TOKEN
    return {
        "calls": len(calls), "prompt_tokens": round(tok_in), "completion_tokens": round(tok_out),
        "usd": round((tok_in * price_in + tok_out * price_out) / 1e6, 3),
    }


# ---------- постобработка ----------

_SPACES = re.compile(r"\s+")
_BULLET = re.compile(r"^(?:[-–—•*]\s+|\d{1,2}[.)]\s+)")
# Плейсхолдеры, которые выдаёт mask(); выдуманных моделью ([CLIENT_4]) в работе не будет.
_KNOWN_PLACEHOLDERS = frozenset({
    "PERSON", "PHONE", "EMAIL", "ADDRESS", "BIRTH_DATE", "SNILS", "PASSPORT", "INN", "CARD",
    "BANK_ACCOUNT",
})
_BRACKET_TOKEN = re.compile(r"\[[A-Z][A-Z0-9_]*\]")       # любой [ВЕРХНИЙ_РЕГИСТР]
_PLACEHOLDER = re.compile(r"\[([A-Z_]+?)_\d+\]")


def has_unknown_placeholder(text: str) -> bool:
    """[CLIENT_4], [PERSON_17_NATIONALITY] и т.п. — не то, что выдаёт mask()."""
    for token in _BRACKET_TOKEN.findall(text):
        m = _PLACEHOLDER.fullmatch(token)
        if m is None or m.group(1) not in _KNOWN_PLACEHOLDERS:
            return True
    return False
# Пересказ инструкции внутри фрагмента («без указания конкретного пациента») — подсказка-ярлык,
# которой нет в настоящих документах: модель выучила бы её вместо смысла.
_META_PHRASE = re.compile(
    r"без (?:указания|конкретного|привязки|упоминания)|не является|не относится"
    r"|конкретн\w+ (?:человек|пациент|лиц)|публичн\w+ (?:сведени|информац)"
    r"|коммерческ\w+ тайн|персональн\w+ данн|особ\w+ категори",
    re.IGNORECASE,
)
_FALLBACK_NAME = re.compile(
    r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё]+"
)


def clean_text(text: str) -> str:
    """Схлопывает пробелы, убирает маркер списка в начале."""
    return _BULLET.sub("", _SPACES.sub(" ", text).strip())


def make_person_masker() -> Callable[[str], str]:
    """Маскирование ПДн так же, как в конвейере (detect → mask, только категория pd).

    Особые категории (pd_special) не маскируем — это и есть положительные примеры.
    Без пакета app (запуск вне контейнера) — запасное правило для «Фамилия И.О.».
    """
    from common import ensure_app_importable
    try:
        ensure_app_importable()
    except SystemExit:
        logger.warning("Пакет app недоступен: ФИО ищем запасным правилом")
        return lambda text: _FALLBACK_NAME.sub("[PERSON_9]", text)
    from app.evaluation.corpus import text_document
    from app.schemas import EntityCategory
    from app.services.detection.detect import detect
    from app.services.masking.masker import mask

    def masker(text: str) -> str:
        ents = [e for e in detect(text_document(text, "gen.txt"))
                if e.category == EntityCategory.pd]
        return mask(text, ents).masked_text if ents else text

    return masker


def postprocess(raw: list[dict], masker: Callable[[str], str]) -> tuple[list[dict], dict]:
    """Сырые ответы → чистые фрагменты: метка, длина, маскирование, дедупликация, конфликты."""
    drops: Counter = Counter()
    by_key: dict[str, dict] = {}
    labels_by_key: dict[str, set[str]] = {}
    for batch in raw:
        for item in batch["items"]:
            label = str(item.get("label", "")).strip().upper()
            text = clean_text(str(item.get("text", "")))
            if label not in LABELS:
                drops["bad_label"] += 1
                continue
            if not MIN_CHARS <= len(text) <= MAX_CHARS:
                drops["length"] += 1
                continue
            if _META_PHRASE.search(text):
                drops["meta_phrase"] += 1
                continue
            if has_unknown_placeholder(text):
                drops["unknown_placeholder"] += 1
                continue
            masked = masker(text)
            drops["masked_pd"] += masked != text
            key = dedup_key(masked)
            labels_by_key.setdefault(key, set()).add(label)
            if key in by_key:
                drops["duplicate"] += 1
                continue
            by_key[key] = {"text": masked, "label": label, "meta": batch["meta"]}
    conflicts = {k for k, labs in labels_by_key.items() if len(labs) > 1}
    drops["label_conflict"] = len(conflicts)
    rows = [r for k, r in by_key.items() if k not in conflicts]
    return rows, dict(drops)


def length_stats(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for label in LABELS:
        lens = [len(r["text"]) for r in rows if r["label"] == label]
        if lens:
            out[label] = {"n": len(lens), "min": min(lens), "median": statistics.median(lens),
                          "mean": round(statistics.mean(lens), 1), "max": max(lens)}
    return out


def build_stats(rows: list[dict], drops: dict, usage: Usage, prices: tuple[float, float]) -> dict:
    def count(key: str) -> dict:
        return dict(sorted(Counter(r["meta"][key] for r in rows).items()))

    return {
        "fragments": len(rows),
        "by_label": dict(Counter(r["label"] for r in rows)),
        "by_kind": count("kind"), "by_doc_type": count("doc_type"),
        "by_register": count("register"), "by_industry": count("industry"),
        "hard_negatives": sum(r["label"] == "NONE" and r["meta"]["kind"] in
                              ("hard_none", "contrast_ts", "contrast_sc", "none_person",
                               "none_requisites", "sc_form") for r in rows),
        "dropped": drops, "length": length_stats(rows),
        "llm": {**{k: v for k, v in asdict(usage).items() if k != "by_kind"},
                "usd": round(usage.cost(*prices), 4)},
    }


def write_review_sample(rows: list[dict], path: Path, n: int = REVIEW_SAMPLE) -> None:
    """300 случайных фрагментов (seed фиксирован) — для оценки шума разметки глазами."""
    sample = random.Random(SEED).sample(rows, k=min(n, len(rows)))
    lines = [
        "# Выборка для проверки разметки (T-036a)", "",
        "Отметь номера, где метка неверна (или поставь ✗ в последнем столбце). "
        "Доля ошибок = оценка шума обучающих данных.", "",
        "| № | Метка | Фрагмент | ✗ |", "|---|---|---|---|",
    ]
    for i, r in enumerate(sample, start=1):
        text = r["text"].replace("|", "\\|")
        lines.append(f"| {i} | {r['label']} | {text} | |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(rows: list[dict], stats: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["meta"]["call_id"], r["text"]))
    with (out / "fragments.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_json(out / "stats.json", stats)
    write_review_sample(rows, out / "sample_for_review.md")


# ---------- вызовы LLM ----------

class UsageHandler(logging.Handler):
    """Считает токены по записи «LLM вызов: …» клиента (текст промпта там не пишется)."""

    def __init__(self, usage: Usage) -> None:
        super().__init__(logging.INFO)
        self.usage = usage

    def emit(self, record: logging.LogRecord) -> None:
        if not str(record.msg).startswith("LLM вызов") or len(record.args or ()) < 9:
            return
        args = record.args
        if args[7]:                      # cache_hit
            self.usage.cache_hits += 1
            return
        self.usage.calls += 1
        self.usage.prompt_tokens += args[4] if isinstance(args[4], int) else 0
        self.usage.completion_tokens += args[5] if isinstance(args[5], int) else 0


def read_checkpoint() -> dict[str, dict]:
    if not CHECKPOINT_FILE.is_file():
        return {}
    with CHECKPOINT_FILE.open(encoding="utf-8") as f:
        return {b["meta"]["call_id"]: b for b in map(json.loads, f) if b}


def run_calls(calls: list[GenCall], args: argparse.Namespace, usage: Usage) -> dict[str, dict]:
    """Выполняет недостающие вызовы; каждый ответ сразу дописывается в checkpoint."""
    from common import ensure_app_importable
    ensure_app_importable()
    from app.services.llm.client import (
        LLMBudgetExceeded,
        LLMError,
        complete_json,
        llm_call_budget,
    )

    system, template = load_prompt()
    done = read_checkpoint()
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with llm_call_budget(limit=args.max_calls), CHECKPOINT_FILE.open("a", encoding="utf-8") as f:
        for n, call in enumerate(calls, start=1):
            if call.call_id in done:
                continue
            if usage.cost(args.price_in, args.price_out) > args.max_usd:
                logger.error("Достигнут лимит %.2f $ — остановка", args.max_usd)
                break
            try:
                batch = complete_json(system, build_user(call, template), GenBatch)
            except LLMBudgetExceeded:
                logger.error("Исчерпан лимит --max-calls=%d — остановка", args.max_calls)
                break
            except LLMError as exc:
                usage.failed += 1
                logger.warning("%s: ошибка LLM (%s: %s)", call.call_id, type(exc).__name__, exc)
                if usage.failed >= 5 and usage.calls == 0:
                    logger.error("5 ошибок подряд без единого ответа — проверь LLM_MODEL; стоп")
                    break
                continue
            record = {"meta": {**asdict(call), "call_id": call.call_id},
                      "items": [i.model_dump() for i in batch.items]}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            done[call.call_id] = record
            if n % 20 == 0:
                logger.info("вызовов %d/%d, токенов %d+%d", n, len(calls),
                            usage.prompt_tokens, usage.completion_tokens)
    return done


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Генерация фрагментов для классификатора (T-036a)")
    p.add_argument("--out", type=Path, default=TRAIN_DIR)
    p.add_argument("--plan", choices=sorted(PLANS), default="v1",
                   help="v1 — первая генерация; v2 — добор отрицаний и длинных фраз")
    p.add_argument(
        "--scale", type=float, default=1.1, help="множитель числа вызовов (запас на дубли)",
    )
    p.add_argument("--max-calls", type=int, default=400)
    p.add_argument("--max-usd", type=float, default=2.0)
    p.add_argument("--price-in", type=float, default=0.15, help="$ за 1M входных токенов")
    p.add_argument("--price-out", type=float, default=0.60, help="$ за 1M выходных токенов")
    p.add_argument("--dry-run", action="store_true", help="только план и оценка стоимости")
    p.add_argument("--postprocess-only", action="store_true", help="пересобрать из checkpoint")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging()
    use_plan(args.plan)
    calls = plan_calls(args.scale)
    est = estimate_cost(calls, args.price_in, args.price_out)
    logger.info("План %s: %d вызовов; оценка: %s", ACTIVE.name, len(calls), est)
    if len(calls) > args.max_calls or est["usd"] > args.max_usd:
        logger.error("План превышает лимит (%d вызовов / %.2f $): уточни у тимлида",
                     args.max_calls, args.max_usd)
        return 3
    if args.dry_run:
        return 0
    usage = Usage()
    client_logger = logging.getLogger("app.services.llm.client")
    client_logger.setLevel(logging.INFO)
    client_logger.addHandler(UsageHandler(usage))
    done = read_checkpoint() if args.postprocess_only else run_calls(calls, args, usage)
    rows, drops = postprocess(list(done.values()), make_person_masker())
    stats = build_stats(rows, drops, usage, (args.price_in, args.price_out))
    previous = args.out / "stats.json"
    if args.postprocess_only and previous.is_file():   # вызовы были в прошлом запуске
        stats["llm"] = json.loads(previous.read_text(encoding="utf-8")).get("llm", stats["llm"])
    write_outputs(rows, stats, args.out)
    logger.info("Фрагментов: %d %s. Реальных вызовов: %d (из кэша %d, ошибок %d), "
                "токенов %d+%d, ≈ %.3f $", len(rows), stats["by_label"], usage.calls,
                usage.cache_hits, usage.failed, usage.prompt_tokens, usage.completion_tokens,
                usage.cost(args.price_in, args.price_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
