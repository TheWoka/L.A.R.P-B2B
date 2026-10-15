# Контракты данных и API

Единственный источник правды для всех модулей. Python-версия схем лежит в `backend/app/schemas/`,
TypeScript-версия — в `frontend/src/api/types.ts`. Менять — только через тимлида.

Все смещения (`start`, `end`, `offset`) — индексы символов в **полном тексте документа** (`ExtractedDocument.text`), `end` не включается.

## 1. Схемы данных (Pydantic v2)

```python
from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


# ---------- Назначение документа ----------
class DocPurpose(str, Enum):
    internal = "internal"            # внутренний документ компании
    publication = "publication"      # будет опубликован (ЕИС, сайт) — открытый доступ
    contract = "contract"            # проект контракта / договор с контрагентом
    international = "international"  # международный контракт (проверяем ещё и GDPR)


# ---------- Извлечение текста ----------
class TextSegment(BaseModel):
    id: int
    text: str
    offset: int                      # начало сегмента в полном тексте
    page: int | None = None          # номер страницы (pdf), иначе None
    section: str | None = None       # "3.2. Обязанности сторон" или "Лист1!B2"


class ExtractedDocument(BaseModel):
    document_id: str
    filename: str
    text: str                        # полный текст; сегменты склеены через "\n"
    segments: list[TextSegment]
    pages: int | None = None


# ---------- Найденные сущности ----------
class EntityType(str, Enum):
    PERSON = "PERSON"                # ФИО
    PASSPORT = "PASSPORT"
    SNILS = "SNILS"
    INN = "INN"                      # ИНН физлица (12 цифр); ИНН юрлица — не ПДн
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    ADDRESS = "ADDRESS"
    BIRTH_DATE = "BIRTH_DATE"
    CARD = "CARD"                    # номер банковской карты
    BANK_ACCOUNT = "BANK_ACCOUNT"    # расчётный / лицевой счёт
    SPECIAL_CATEGORY = "SPECIAL_CATEGORY"  # здоровье, убеждения и т.п. (ст. 10 152-ФЗ)
    TRADE_SECRET = "TRADE_SECRET"    # себестоимость, маржа, ноу-хау, условия скидок
    FINANCIAL = "FINANCIAL"          # финансовые показатели, суммы, реквизиты


class EntityCategory(str, Enum):
    pd = "pd"
    pd_special = "pd_special"
    trade_secret = "trade_secret"
    financial = "financial"


class Entity(BaseModel):
    type: EntityType
    category: EntityCategory
    start: int
    end: int
    page: int | None = None
    section: str | None = None
    value_masked: str                # для показа: "Иванов И***", "***-***-*** 45"
    confidence: float = Field(ge=0, le=1)
    detector: Literal["regex", "ner", "dict", "llm", "model"]


# ---------- Маркеры чек-листа ----------
class MarkerType(str, Enum):
    approval_sheet = "approval_sheet"          # лист согласования / подписи / визы
    classification_mark = "classification_mark"  # гриф "Конфиденциально", "Коммерческая тайна"
    retention_period = "retention_period"      # указан срок хранения / уничтожения
    consent_clause = "consent_clause"          # согласие на обработку ПДн
    processing_assignment = "processing_assignment"  # условия поручения обработки ПДн (ч.3 ст.6 152-ФЗ)
    kii_mention = "kii_mention"                # сведения о значимых объектах КИИ


class Evidence(BaseModel):
    quote: str                       # дословная цитата (для ПДн — уже замаскированная)
    start: int
    end: int
    page: int | None = None
    section: str | None = None


class Marker(BaseModel):
    type: MarkerType
    present: bool
    evidence: Evidence | None = None


# ---------- Правила и нарушения ----------
class LawRef(BaseModel):
    law: str                         # "152-ФЗ", "187-ФЗ", "GDPR", "98-ФЗ", "Политика ИБ компании"
    article: str                     # "ст. 6 ч. 3", "Art. 28"
    title: str | None = None
    url: str | None = None


class Severity(str, Enum):
    red = "red"
    yellow = "yellow"


class Rule(BaseModel):
    id: str                          # "PD_IN_PUBLICATION"
    title: str
    description: str
    severity: Severity
    law_refs: list[LawRef]
    applies_to: list[DocPurpose] = [] # пусто = ко всем документам
    condition: dict                  # см. раздел 2
    recommendation: str
    enabled: bool = True


class Violation(BaseModel):
    rule_id: str
    title: str
    severity: Severity
    law_refs: list[LawRef]
    evidence: list[Evidence]
    recommendation: str
    source: Literal["rule", "llm"]


# ---------- Отчёт ----------
class ChecklistItem(BaseModel):
    category: Literal["approvals", "classification", "retention"]
    status: Literal["ok", "missing", "not_required"]
    comment: str
    evidence: Evidence | None = None


class Report(BaseModel):
    report_id: str
    document_id: str
    filename: str | None             # None для текстового запроса
    purpose: DocPurpose
    overall: Literal["red", "yellow", "green"]
    created_at: datetime
    processing_ms: int
    stats: dict[str, int]            # {"PERSON": 12, "SNILS": 3, ...}
    entities: list[Entity]
    checklist: list[ChecklistItem]   # ровно 3 элемента
    violations: list[Violation]
    llm_used: bool                   # False, если работали в режиме mock / без ключа


# ---------- Задачи ----------
class TaskStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class Stage(str, Enum):
    extracting = "extracting"
    detecting = "detecting"
    masking = "masking"
    checking = "checking"
    reporting = "reporting"


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    stage: Stage | None = None
    progress: int = Field(0, ge=0, le=100)
    report_id: str | None = None
    error: str | None = None         # понятный пользователю текст, без технических деталей
```

**Правило итогового светофора:** `red`, если есть хотя бы одно нарушение `red`; иначе `yellow`, если есть хотя бы одно `yellow`; иначе `green`.

## 2. Формат условия правила (`Rule.condition`)

Условия — JSON/YAML-деревья. Новое правило добавляется без изменения кода.

| Тип | Пример | Истинно, если |
|---|---|---|
| `entity_present` | `{type: entity_present, categories: [pd], types: [PASSPORT], min_count: 1}` | найдено ≥ `min_count` сущностей указанных категорий/типов (`types` необязателен) |
| `marker_present` | `{type: marker_present, marker: consent_clause}` | маркер найден |
| `marker_absent` | `{type: marker_absent, marker: classification_mark}` | маркер не найден |
| `purpose_is` | `{type: purpose_is, purposes: [publication]}` | назначение документа из списка |
| `llm` | `{type: llm, question: "Передаются ли ПДн третьему лицу без указания цели?"}` | LLM ответила «да» по **замаскированному** тексту и вернула дословную цитату, найденную в тексте |
| `all` | `{all: [cond1, cond2]}` | все истинны |
| `any` | `{any: [cond1, cond2]}` | хотя бы одно истинно |
| `not` | `{not: cond}` | условие ложно |

Пример правила (`data/rules/rules.yaml`):

```yaml
- id: PD_IN_PUBLICATION
  title: Персональные данные в документе для публикации
  description: Документ предназначен для открытого доступа, но содержит персональные данные физлиц.
  severity: red
  law_refs:
    - {law: 152-ФЗ, article: ст. 7}
    - {law: 152-ФЗ, article: ст. 10.1}
  applies_to: [publication]
  condition: {type: entity_present, categories: [pd, pd_special], min_count: 1}
  recommendation: Удалите или обезличьте персональные данные перед публикацией либо получите согласие на распространение.

- id: TRADE_SECRET_WITHOUT_MARK
  title: Коммерческая тайна без грифа
  description: В документе есть сведения, составляющие коммерческую тайну, но нет грифа.
  severity: red
  law_refs: [{law: 98-ФЗ, article: ст. 10}]
  condition:
    all:
      - {type: entity_present, categories: [trade_secret]}
      - {type: marker_absent, marker: classification_mark}
  recommendation: Нанесите гриф «Коммерческая тайна» с указанием обладателя информации.
```

## 3. Интерфейсы сервисов (Python)

```python
# services/extraction/extract.py
def extract(path: Path, filename: str, document_id: str) -> ExtractedDocument: ...

# services/detection/detect.py
def detect(doc: ExtractedDocument) -> list[Entity]: ...          # regex + NER + словари

# services/markers/find_markers.py
def find_markers(doc: ExtractedDocument) -> list[Marker]: ...    # все MarkerType, present=True/False

# services/masking/masker.py
class MaskSpan(BaseModel):           # одна замена: где плейсхолдер в masked_text и где значение в исходном тексте
    masked_start: int
    masked_end: int
    orig_start: int
    orig_end: int

class MaskResult(BaseModel):
    masked_text: str                 # текст с плейсхолдерами [PERSON_1] — только он уходит в LLM
    mapping: dict[str, str]          # "[PERSON_1]" -> исходное значение (НЕ логировать, НЕ сохранять)
    spans: list[MaskSpan]            # по одному на каждую замену, по возрастанию masked_start
def mask(text: str, entities: list[Entity]) -> MaskResult: ...
def unmask(text: str, mapping: dict[str, str]) -> str: ...
def to_original_offset(masked_offset: int, spans: list[MaskSpan]) -> int: ...
# пересчёт смещения из masked_text в координаты исходного текста — для цитат из ответа LLM

# services/llm/client.py
def complete_json(system: str, user: str, schema: type[BaseModel]) -> BaseModel: ...
# провайдер из env; mock возвращает детерминированный ответ; кэш в Redis по sha256(запроса)

# services/rules/engine.py
class RuleContext(BaseModel):
    doc: ExtractedDocument
    masked_text: str
    entities: list[Entity]
    markers: list[Marker]
    purpose: DocPurpose
def evaluate(rules: list[Rule], ctx: RuleContext) -> list[Violation]: ...

# services/report/builder.py
def build_report(ctx: RuleContext, violations: list[Violation], processing_ms: int, llm_used: bool) -> Report: ...
```

## 4. REST API

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| POST | `/api/documents` | multipart: `file`, `purpose` | `202 TaskCreated` |
| POST | `/api/queries` | JSON `QueryRequest` | `202 TaskCreated` |
| GET | `/api/tasks/{task_id}` | — | `TaskInfo` |
| GET | `/api/reports/{report_id}` | — | `Report` |
| GET | `/api/reports/{report_id}/text` | — | `ReportText` — текст с **замаскированными** ПДн для подсветки |
| GET | `/api/reports/{report_id}/export?format=json\|pdf` | — | файл отчёта |
| GET | `/api/rules` | — | `list[Rule]` |
| POST | `/api/rules` | `Rule` | `201 Rule` |
| PUT | `/api/rules/{id}` | `Rule` | `Rule` |
| DELETE | `/api/rules/{id}` | — | `204` |
| GET | `/api/health` | — | `HealthStatus` |

Ограничения: файл до 50 МБ; форматы .pdf, .docx, .xlsx; иначе `400 {detail: "понятное сообщение"}`.
Исходные файлы удаляются после обработки; отчёты хранятся `REPORT_TTL_DAYS` дней (по умолчанию 7) — система сама соблюдает сроки хранения, которые проверяет.

### 4.1. Модели запросов и ответов API (Pydantic v2)

```python
# schemas/task.py
class TaskCreated(BaseModel):        # ответ POST /api/documents и POST /api/queries
    task_id: str
    document_id: str


class QueryRequest(BaseModel):       # тело POST /api/queries
    text: str = Field(min_length=1)
    purpose: DocPurpose


# schemas/report.py
class ReportText(BaseModel):         # ответ GET /api/reports/{report_id}/text
    text: str                        # маскирование С СОХРАНЕНИЕМ ДЛИНЫ (см. ниже)
    segments: list[TextSegment]      # offset — в координатах исходного полного текста


# schemas/health.py
class HealthStatus(BaseModel):       # ответ GET /api/health
    status: Literal["ok", "degraded"]  # "ok" — db и redis true; "degraded" — хотя бы одно false
    db: bool                         # PostgreSQL отвечает на SELECT 1
    redis: bool                      # Redis отвечает на PING
```

`GET /api/health` всегда отвечает HTTP 200: сам API жив, состояние зависимостей — в `status`, `db`, `redis`.

**Единая система координат.** Все смещения в `Report` (`Entity.start/end`, `Evidence.start/end`)
и в `ReportText.segments` считаются в координатах **исходного** полного текста (`ExtractedDocument.text`).

`ReportText.text` — текст с маскированием, сохраняющим длину: каждый символ значения ПДн заменяется
на `█`, кроме первой буквы каждого слова (`"Иванов Иван"` → `"И█████ И███"`). Поэтому
`len(ReportText.text)` равна длине исходного текста и смещения совпадают с исходными — фронтенд
подсвечивает находки по `start/end` без пересчёта.

Текст для LLM (`MaskResult.masked_text`, плейсхолдеры `[PERSON_1]`) имеет другую длину. Цитаты из
ответа LLM переводятся в исходные координаты через `to_original_offset(masked_offset, spans)`.

## 5. Переменные окружения (`.env.example`)

```
POSTGRES_USER=auditor
POSTGRES_PASSWORD=auditor
POSTGRES_DB=auditor
DATABASE_URL=postgresql+psycopg://auditor:auditor@postgres:5432/auditor
REDIS_URL=redis://redis:6379/0

LLM_PROVIDER=mock                 # mock | openai_compatible
LLM_BASE_URL=                     # например https://<сервис>/v1 или http://ollama:11434/v1
LLM_API_KEY=
LLM_MODEL=
LLM_MAX_CALLS_PER_DOC=30

EMBEDDING_MODEL=intfloat/multilingual-e5-small
MAX_UPLOAD_MB=50
REPORT_TTL_DAYS=7
```

## История изменений

| Дата | Что изменилось | Кто решил |
|---|---|---|
| 2026-10-15 | T-001 (правка 2): единая система координат — все смещения в `Report` и `ReportText.segments` в координатах исходного текста; `ReportText.text` — маскирование с сохранением длины (`█`, кроме первой буквы слова). В `MaskResult` добавлено `spans: list[MaskSpan]`, добавлена функция `to_original_offset` (реализация — T-008). Health: `status = "degraded"`, если `db` или `redis` = false (HTTP 200). | Данила (тимлид) |
| 2026-10-15 | T-001: `GET /api/health` возвращает `HealthStatus {status, db, redis}` вместо `{status}`. Добавлен раздел 4.1 с моделями `TaskCreated`, `QueryRequest`, `ReportText`, `HealthStatus` — раньше эти формы были описаны только в таблице раздела 4 без имён. Поля и смысл ответов не изменились, кроме двух новых полей health. | Данила (тимлид) |
