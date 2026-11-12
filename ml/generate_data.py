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

Набор v3 (T-080, gpt-4.1-mini): --plan v3 --out /train/v3 --price-in 0.4 --price-out 1.6;
фрагменты, совпавшие с v1 и v2 (dedup_key), отбрасываются.
"""

import argparse
import json
import logging
import random
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    ban_ts_keywords: bool = False     # v3: TRADE_SECRET со словом-ярлыком отбрасывается


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

# ---------- набор v3 (T-080: КТ без слов-ярлыков, особые категории, трудные NONE) ----------
# Темы заданы тимлидом своими словами в постановке T-080; тексты eval/* не читались.
DOC_TYPES.update({                    # после ALL_DOCS: планы v1 и v2 не меняются
    "tz": "техническое задание",
    "notice": "извещение и документация о закупке",
    "price_list": "прайс-лист или тарифы",
    "order": "приказ или распоряжение по персоналу",
})
TS_PLAIN_THEMES = (
    "по какой цене берём товар у поставщика (разговорно: «берём у них по …»)",
    "скидка, которую поставщик даёт только нам", "отсрочка платежа, которую дали только нам",
    "бесплатная доставка или хранение как личная договорённость с поставщиком",
    "сколько времени закладываем на операцию (внутренний норматив)",
    "сколько материала уходит у нас на единицу продукции (внутренний норматив)",
    "сколько зарабатываем на сделке или на единице товара, сумма словами",
    "сколько остаётся после всех расходов по проекту, сумма или доля словами",
    "сколько и чего собираемся закупить в следующем периоде",
    "сколько собираемся продать и кому в следующем периоде",
    "эксклюзив: поставщик работает в регионе только через нас, срок и условия",
    "особая цена, которую мы дали одному покупателю", "до какой цены готовы опуститься на торгах",
    "сколько платим своему подрядчику за работу",
)
SC_V3_THEMES = (
    "причина больничного", "диагноз простыми словами, как говорят коллеги",
    "группа инвалидности и её причина", "беременность сотрудницы и связанные с ней ограничения",
    "вероисповедание как поле анкеты или причина просьбы", "национальность как поле анкеты",
    "членство в профсоюзе и профсоюзная должность", "судимость в характеристике или анкете",
    "операция или лечение как причина отсутствия", "ограничение по здоровью как причина перевода",
)
SC_FORM_V3_THEMES = (
    "состояние здоровья и диагноз", "инвалидность: группа и причина", "вероисповедание",
    "национальность", "членство в профсоюзе", "сведения о судимости с конкретным значением",
    "семейное положение и состав семьи", "гражданство и знание языков", "образование и стаж",
    "дисциплинарные взыскания и поощрения", "воинский учёт и категория запаса",
)
TECH_NONE_THEMES = (
    "плотность, толщина, формат бумаги и упаковки (г/м², мкм, мм)",
    "мощность, напряжение, производительность оборудования", "габариты, масса, объём, вместимость",
    "характеристики компьютерной техники: память, частота, диагональ",
    "показатели материалов: прочность, марка бетона, класс, сорт",
    "температура хранения и эксплуатации из паспорта изделия", "срок службы, гарантия, ресурс",
    "комплектность, количество в упаковке, фасовка",
    "нормы из ГОСТ, СанПиН, технического регламента", "расход из инструкции производителя",
    "требования к транспорту: грузоподъёмность, объём кузова, класс экологичности",
)
PUBLIC_NONE_THEMES = (
    "начальная (максимальная) цена контракта из извещения", "цена заключённого контракта",
    "цена единицы товара из опубликованного контракта", "публичный прайс-лист поставщика",
    "тарифы на перевозку, связь, коммунальные услуги", "ставки НДС, налога на прибыль, взносов",
    "цена для заказчика в коммерческом предложении", "обеспечение заявки и исполнения контракта",
    "штраф, пеня, неустойка по договору", "аванс и порядок оплаты по контракту",
    "экономия по итогам торгов, снижение от начальной цены в протоколе",
    "выручка и прибыль из опубликованной отчётности",
)
PEOPLE_NONE_THEMES = (
    "семейное положение, дети, смена фамилии, развод",
    "дисциплинарное взыскание: выговор, замечание, лишение премии",
    "требование предоставить медицинские документы или справку (без сведений о человеке)",
    "закупка лекарств и медицинских изделий без пациентов",
    "требование «отсутствие судимости» к участникам закупки или кандидатам вообще",
    "медосмотр, диспансеризация, вакцинация как мероприятие для всех",
    "больничный, отгул, отпуск без причины", "охрана труда и общие противопоказания к профессии",
    "страхование сотрудников (ДМС) как условие договора",
    "поле анкеты про здоровье, веру, судимость без значения или с прочерком",
    "профсоюз и партия как организации, без членства конкретного человека",
)
TS_V3_DOCS = ("letter", "chat", "memo", "minutes", "costing", "procurement_report")
SC_V3_DOCS = ("letter", "chat", "memo", "form", "certificate", "reference", "order")
TECH_DOCS = ("tz", "notice", "offer", "contract", "price_list", "letter", "costing")
PUBLIC_DOCS = ("notice", "contract", "offer", "price_list", "procurement_report", "minutes",
               "letter")
PEOPLE_DOCS = ("form", "order", "reference", "memo", "letter", "notice", "tz", "chat")

CALL_KINDS_V3 = (
    CallKind("ts_plain", "все фрагменты класса TRADE_SECRET и все — БЕЗ слов-ярлыков из "
             "правила 7: смысл передан обычной речью. Примерно в каждом третьем фрагменте "
             "сумма, доля или срок записаны словами, а не цифрами.",
             TS_V3_DOCS, TS_PLAIN_THEMES, 40, ban_ts_keywords=True),
    CallKind("contrast_ts_plain", "10 пар подряд: фрагмент TRADE_SECRET без слов-ярлыков из "
             "правила 7 и сразу за ним похожий по словам и числам фрагмент NONE (то же число "
             "или товар, но это открытые сведения: паспорт изделия, опубликованная цена, "
             "прайс, тариф, цена для заказчика).",
             TS_V3_DOCS, TS_PLAIN_THEMES + TECH_NONE_THEMES[:6] + PUBLIC_NONE_THEMES[:7], 10,
             ban_ts_keywords=True),
    CallKind("sc_person", "все фрагменты класса SPECIAL_CATEGORY о конкретном человеке. "
             "Чередуй три конструкции поровну: строка «Поле: значение»; фраза письма, записки "
             "или характеристики; объяснение, почему человек отсутствует или переведён. "
             "В половине фрагментов человек назван [PERSON_1], в половине — словами или никак "
             "(строка его анкеты).", SC_V3_DOCS, SC_V3_THEMES, 30),
    CallKind("sc_form_v3", "строки анкет, личных карточек и справок вида «Поле: значение» без "
             "имени человека: 10 SPECIAL_CATEGORY (значение — конкретная болезнь, группа и "
             "причина инвалидности, вера, национальность, профсоюз, судимость со статьёй или "
             "сроком) и 10 NONE (семейное положение, гражданство, образование, взыскания, "
             "воинский учёт, а также поля особых категорий с прочерком, «нет», «не имеет»).",
             ("form", "certificate", "reference", "order"), SC_FORM_V3_THEMES, 12),
    CallKind("none_tech", "все фрагменты класса NONE — технические характеристики и требования "
             "с числами и единицами измерения, как в техническом задании и паспорте изделия.",
             TECH_DOCS, TECH_NONE_THEMES, 22),
    CallKind("none_public", "все фрагменты класса NONE — открытые денежные сведения закупки и "
             "рынка: суммы, цены, ставки, проценты, которые публикуются или сообщаются "
             "заказчику.", PUBLIC_DOCS, PUBLIC_NONE_THEMES, 18),
    CallKind("none_people", "все фрагменты класса NONE — про людей, медицину, судимость и "
             "кадровые дела, но без сведений особых категорий о конкретном человеке. В трети "
             "фрагментов есть [PERSON_1].", PEOPLE_DOCS, PEOPLE_NONE_THEMES, 18),
)
# Слова-ярлыки: во фрагментах TRADE_SECRET видов с ban_ts_keywords их быть не должно
# (v1 и v2 уже научили модель этим словам; v3 учит смыслу без них).
_TS_KEYWORD = re.compile(
    r"себестоим|наценк|надбавк|марж|рентабельн|закупочн|ретро|ноу-хау|конфиденц|секрет"
    r"|внутренн\w+ (?:цен|план|норматив)|только для внутренн", re.IGNORECASE,
)


@dataclass(frozen=True)
class Plan:
    name: str
    kinds: tuple[CallKind, ...]
    prompt: str                       # файл в ml/prompts/
    checkpoint: str                   # файл в ml/out/
    prefix: str                       # префикс call_id
    previous: tuple[str, ...] = ()    # наборы (относительно родителя --out) для дедупликации


PLANS = {
    "v1": Plan("v1", CALL_KINDS, "generate.md", "gen_raw.jsonl", ""),
    "v2": Plan("v2", CALL_KINDS_V2, "generate_v2.md", "gen_raw_v2.jsonl", "v2-"),
    "v3": Plan("v3", CALL_KINDS_V3, "generate_v3.md", "gen_raw_v3.jsonl", "v3-",
               previous=("fragments.jsonl", "v2/fragments.jsonl")),
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


def previous_keys(paths: list[Path]) -> set[str]:
    """Ключи дедупликации фрагментов прошлых наборов (v3 не повторяет v1 и v2)."""
    keys: set[str] = set()
    for path in paths:
        if not path.is_file():
            logger.warning("Нет прошлого набора %s — дедупликация с ним пропущена", path)
            continue
        with path.open(encoding="utf-8") as f:
            keys.update(dedup_key(json.loads(line)["text"]) for line in f if line.strip())
    return keys


def _banned_keyword(batch: dict, label: str, text: str) -> bool:
    """TRADE_SECRET со словом-ярлыком в виде вызова, где ярлыки запрещены (v3)."""
    if label != "TRADE_SECRET":
        return False
    kind = next((k for k in ACTIVE.kinds if k.name == batch["meta"].get("kind")), None)
    return bool(kind and kind.ban_ts_keywords and _TS_KEYWORD.search(text))


def postprocess(raw: list[dict], masker: Callable[[str], str],
                known: set[str] | None = None) -> tuple[list[dict], dict]:
    """Сырые ответы → чистые фрагменты: метка, длина, маскирование, дедупликация, конфликты.

    known — ключи фрагментов прошлых наборов: совпавшие отбрасываются (duplicate_previous).
    """
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
            if _banned_keyword(batch, label, text):
                drops["ts_keyword"] += 1
                continue
            masked = masker(text)
            drops["masked_pd"] += masked != text
            key = dedup_key(masked)
            if known and key in known:
                drops["duplicate_previous"] += 1
                continue
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
                               "none_requisites", "sc_form", "contrast_ts_plain", "sc_form_v3",
                               "none_tech", "none_public", "none_people") for r in rows),
        "dropped": drops, "length": length_stats(rows),
        "llm": {**{k: v for k, v in asdict(usage).items() if k != "by_kind"},
                "usd": round(usage.cost(*prices), 4)},
    }


def write_review_sample(rows: list[dict], path: Path, n: int = REVIEW_SAMPLE) -> None:
    """300 случайных фрагментов (seed фиксирован) — для оценки шума разметки глазами."""
    sample = random.Random(SEED).sample(rows, k=min(n, len(rows)))
    lines = [
        f"# Выборка для проверки разметки (набор {ACTIVE.name})", "",
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


GEN_ANSWER_TOKENS = 4000        # 20 фрагментов по ~150 символов ≈ 1 500 токенов; запас ×2,5


@contextmanager
def long_answers(limit: int = GEN_ANSWER_TOKENS) -> Iterator[None]:
    """На время генерации поднимает потолок длины ответа клиента LLM.

    В конвейере ответ — короткий JSON, и providers.MAX_ANSWER_TOKENS = 400 (T-019b) защищает
    от зацикливания локальной модели. Пакет из 20 фрагментов в 400 токенов не помещается,
    поэтому потолок меняется только внутри этого блока и только в процессе генерации.
    """
    from app.services.llm import providers

    old = providers.MAX_ANSWER_TOKENS
    providers.MAX_ANSWER_TOKENS = max(old, limit)
    try:
        yield
    finally:
        providers.MAX_ANSWER_TOKENS = old


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
    with long_answers(), llm_call_budget(limit=args.max_calls), \
            CHECKPOINT_FILE.open("a", encoding="utf-8") as f:
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


def generator_model() -> str | None:
    """Модель режима по умолчанию из .env; None — режим mock (генерировать нечем).

    Ключ не читается и не печатается: берём только имя провайдера и модели.
    """
    from common import ensure_app_importable
    ensure_app_importable()
    from app.services.llm.client import effective_settings, resolve_provider

    settings = effective_settings()
    if resolve_provider(settings) == "mock":
        return None
    return settings.llm_model


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Генерация фрагментов для классификатора (T-036a)")
    p.add_argument("--out", type=Path, default=TRAIN_DIR)
    p.add_argument("--plan", choices=sorted(PLANS), default="v1",
                   help="v1 — первая генерация; v2 — добор отрицаний и длинных фраз; "
                        "v3 — КТ без слов-ярлыков, особые категории, трудные NONE (T-080)")
    p.add_argument(
        "--scale", type=float, default=1.1, help="множитель числа вызовов (запас на дубли)",
    )
    p.add_argument("--max-calls", type=int, default=400)
    p.add_argument("--max-usd", type=float, default=2.0)
    p.add_argument("--price-in", type=float, default=0.15, help="$ за 1M входных токенов")
    p.add_argument("--price-out", type=float, default=0.60, help="$ за 1M выходных токенов")
    p.add_argument("--dry-run", action="store_true", help="только план и оценка стоимости")
    p.add_argument("--postprocess-only", action="store_true", help="пересобрать из checkpoint")
    p.add_argument("--dedup-against", type=Path, nargs="*", default=None,
                   help="fragments.jsonl прошлых наборов (по умолчанию — из плана, рядом с --out)")
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
    model = None if args.postprocess_only else generator_model()
    if model is None and not args.postprocess_only:
        logger.error("Режим нейросети — mock: задай облачную модель и ключ в .env "
                     "(LLM_CLOUD_* или LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)")
        return 4
    if model:
        logger.info("Генератор: %s (цены --price-in/--price-out должны быть от этой модели)",
                    model)
    usage = Usage()
    client_logger = logging.getLogger("app.services.llm.client")
    client_logger.setLevel(logging.INFO)
    client_logger.addHandler(UsageHandler(usage))
    done = read_checkpoint() if args.postprocess_only else run_calls(calls, args, usage)
    previous = (args.dedup_against if args.dedup_against is not None
                else [args.out.parent / name for name in ACTIVE.previous])
    known = previous_keys(previous)
    rows, drops = postprocess(list(done.values()), make_person_masker(), known)
    stats = build_stats(rows, drops, usage, (args.price_in, args.price_out))
    stats["llm"]["model"] = model
    old_stats = args.out / "stats.json"
    if args.postprocess_only and old_stats.is_file():   # вызовы были в прошлом запуске
        stats["llm"] = json.loads(old_stats.read_text(encoding="utf-8")).get("llm", stats["llm"])
    write_outputs(rows, stats, args.out)
    logger.info("Фрагментов: %d %s. Реальных вызовов: %d (из кэша %d, ошибок %d), "
                "токенов %d+%d, ≈ %.3f $", len(rows), stats["by_label"], usage.calls,
                usage.cache_hits, usage.failed, usage.prompt_tokens, usage.completion_tokens,
                usage.cost(args.price_in, args.price_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
