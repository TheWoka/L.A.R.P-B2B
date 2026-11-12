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


class OcrPage(BaseModel):            # контракт v3 (schemas/document.py)
    page: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)  # средняя уверенность слов страницы


class OcrInfo(BaseModel):            # контракт v3: распознавание страниц без текстового слоя
    engine: str                      # "tesseract-5.3.0 rus+eng"
    pages: list[OcrPage]             # только распознанные страницы
    mean_confidence: float = Field(ge=0, le=1)  # по всем распознанным словам
    low_confidence_pages: list[int] = []        # confidence < 0.75 (OCR_LOW_PAGE_CONF)
    low_confidence_spans: list[tuple[int, int]] = []  # [start, end) слов с conf < 0.6
                                     # (OCR_LOW_WORD_CONF), координаты полного текста


class ExtractedDocument(BaseModel):
    document_id: str
    filename: str
    text: str                        # полный текст; сегменты склеены через "\n"
    segments: list[TextSegment]
    pages: int | None = None
    ocr: OcrInfo | None = None       # None — весь текст из текстового слоя (контракт v3)


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
    value_masked: str                # для ПДн — маскирование с сохранением длины (раздел 4.1): "И█████ И███", "███-███-███ ██"
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
    quote: str                       # цитата исходного текста, ПДн замаскированы с сохранением длины:
                                     # quote == ReportText.text[start:end], len(quote) == end - start
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


# ---------- Режимы нейросети (schemas/llm.py, контракт v3) ----------
LlmMode = Literal["mock", "local", "cloud"]  # без нейросети / локальная Ollama / облачный API


class LlmModeInfo(BaseModel):
    mode: LlmMode
    available: bool
    model: str | None = None
    reason: str | None = None        # почему недоступен: «Ollama не запущена», «Нет ключа в .env»


class LlmModesResponse(BaseModel):   # ответ GET /api/llm/modes; ключ API никогда не возвращается
    default: LlmMode
    modes: list[LlmModeInfo]         # всегда три: mock, local, cloud


# ---------- Отчёт ----------
class AuditInfo(BaseModel):          # след проверки: отчёт можно воспроизвести и проверить
    document_sha256: str             # sha256 исходного файла (для текстового запроса — его текста в UTF-8)
    ruleset_version: str             # первые 12 hex sha256 канонического JSON включённых правил
    dictionaries_version: str        # то же для data/rules/dictionaries.yaml
    app_version: str                 # версия приложения (FastAPI app.version)
    detectors: list[str]             # например ["regex", "ner:natasha-1.6.0", "dict"]
    llm_provider: Literal["mock", "openai_compatible"]
    llm_model: str | None            # None в режиме mock
    llm_calls: int = Field(0, ge=0)  # реальных вызовов LLM (без кэша и mock)
    llm_mode: LlmMode = "mock"       # режим нейросети проверки (контракт v3); старые отчёты — "mock"


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
    audit: AuditInfo                 # след проверки (контракт v2)
    redacted_available: bool = False # есть ли обезличенная копия (GET /api/reports/{id}/redacted)
    ocr: OcrInfo | None = None       # копия ExtractedDocument.ocr (контракт v3)


# ---------- Комплект документов и сводный отчёт (schemas/batch.py, контракт v3) ----------
class BatchCreate(BaseModel):        # тело POST /api/batches
    name: str | None = Field(None, max_length=120)
    purpose: DocPurpose


class BatchCreated(BaseModel):       # ответ 201 POST /api/batches
    batch_id: str


class BatchDocument(BaseModel):
    document_id: str
    task_id: str
    filename: str | None             # безопасное имя: ПДн в имени -> нейтральное
    purpose: DocPurpose
    status: Literal["queued", "processing", "done", "failed"]
    report_id: str | None = None
    overall: Literal["red", "yellow", "green"] | None = None
    violations_red: int = Field(0, ge=0)
    violations_yellow: int = Field(0, ge=0)
    ocr: bool = False                # хотя бы одна страница распознана OCR
    error: str | None = None         # понятный пользователю текст, без технических деталей


class BatchRuleSummary(BaseModel):
    rule_id: str
    title: str
    severity: Literal["red", "yellow"]
    documents: int = Field(ge=1)     # в скольких документах сработало


class BatchSummary(BaseModel):       # ответ GET /api/batches/{batch_id}
    batch_id: str
    name: str | None
    purpose: DocPurpose
    created_at: datetime
    complete: bool                   # нет queued/processing
    overall: Literal["red", "yellow", "green"] | None  # худший среди готовых; None — готовых нет
    documents_total: int
    documents_done: int
    documents_failed: int
    violations_red: int
    violations_yellow: int
    rules: list[BatchRuleSummary]    # red раньше yellow, затем по убыванию documents, затем rule_id
    documents: list[BatchDocument]   # red -> yellow -> green -> processing/queued -> failed


# ---------- «Что видела нейросеть»: журнал вызовов LLM (schemas/llm_log.py) ----------
class LlmCall(BaseModel):
    purpose: Literal["rule_condition", "trade_secret", "special_category"]
    rule_id: str | None              # для purpose="rule_condition", иначе None
    question: str
    masked_fragment: str             # РОВНО тот текст, что ушёл в LLM: только замаскированный ([PERSON_1] …)
    verdict: Literal["yes", "no", "unknown"]
    quote: str | None                # цитата из ответа LLM (в координатах masked_fragment), если есть
    cached: bool                     # ответ взят из кэша Redis


# ---------- Метрики качества (schemas/metrics.py) ----------
class TypeMetrics(BaseModel):
    type: str                        # значение EntityType
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    support: int = Field(ge=0)       # число эталонных сущностей


class Metrics(BaseModel):
    corpus: str                      # название/версия корпуса
    documents: int
    created_at: datetime
    by_type: list[TypeMetrics]
    pd_recall: float = Field(ge=0, le=1)            # по всем ПДн — критерий ≥ 0.90
    trade_secret_recall: float = Field(ge=0, le=1)  # критерий ≥ 0.85
    matching: str                    # правило совпадения, например "overlap ≥ 50% и тот же type"


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

**След проверки (`Report.audit`).** Тот же документ + те же правила и словари + та же версия
приложения = тот же результат. `ruleset_version` и `dictionaries_version` — первые 12 hex
sha256 канонического JSON (ключи отсортированы, без пробелов) включённых правил и содержимого
`data/rules/dictionaries.yaml`. Значения ПДн в `audit` не попадают. `llm_mode` — режим, в котором
реально шла проверка (контракт v3); у отчётов, сохранённых до v3, — значение по умолчанию `"mock"`.

**Совместимость v3.** Все поля контракта v3 в существующих моделях (`ExtractedDocument.ocr`,
`Report.ocr`, `AuditInfo.llm_mode`, `QueryRequest.llm_mode`) необязательные и со значениями по
умолчанию: отчёты из БД, сохранённые до v3, и `report.example.json` валидируются без изменений.

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

Ограничение: условие `llm` нельзя ставить внутрь `not` (на любой глубине). Ответ LLM «не знаю» (`unknown`) или сбой LLM при отрицании превратились бы в нарушение без доказательства. Такое правило отклоняется при загрузке (`validate_condition` → `ValueError`); проверять отсутствие чего-либо через LLM нужно позитивным вопросом с цитатой.

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
# контракт v3: возвращает и ExtractedDocument.ocr. Форматы: .pdf .docx .xlsx .xml
# и изображения .png .jpg .jpeg .tif .tiff (каждый кадр TIFF — страница).
# OCR — только для страниц без текстового слоя (PDF-скан, изображение); страницы с текстовым
# слоем читаются как раньше. Ни одна страница не распознавалась → ocr = None.

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
# пересчёт смещения из masked_text в координаты исходного текста — для цитат из ответа LLM.
# Вне плейсхолдеров — сдвиг на разницу длин замен левее; строго внутри плейсхолдера → orig_start;
# ровно на masked_end → orig_end. Перед пересчётом границы цитаты LLM расширяются до целых
# плейсхолдеров (T-013), поэтому значение ПДн попадает в цитату целиком.
def mask_preserving_length(text: str, entities: list[Entity]) -> str: ...
# маскирование с сохранением длины (раздел 4.1) — для ReportText.text, Evidence.quote, Entity.value_masked

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
    mask_spans: list[MaskSpan] = []  # MaskResult.spans — перевод цитат LLM в исходные координаты
def evaluate(rules: list[Rule], ctx: RuleContext) -> list[Violation]: ...

# services/report/builder.py
def build_report(ctx: RuleContext, violations: list[Violation], processing_ms: int, llm_used: bool, audit: AuditInfo) -> Report: ...
```

XML (выгрузки ЕИС, T-064a): сегмент = одно поле (лист или ФИО из lastName/firstName/middleName), section = путь элементов; атрибуты не читаются. Подробнее: лист — элемент без дочерних элементов с непустым текстом; ≥ 2 листа ФИО у одного родителя склеиваются в «Фамилия Имя Отчество»; `section` — локальные имена (без пространств имён) от корня (без него) до родителя поля через `/`, не длиннее 120 символов; `page = None`. Разбор только через defusedxml, DTD и сущности запрещены. Обезличенная копия .xml — раздел 4.1 (T-064b).

## 4. REST API

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| POST | `/api/documents` | multipart: `file` (.pdf / .docx / .xlsx / .xml / .png / .jpg / .jpeg / .tif / .tiff), `purpose`; необязательные `batch_id`, `llm_mode` (v3) | `202 TaskCreated`; `batch_id` неизвестен → `404`; в комплекте уже `BATCH_MAX_DOCUMENTS` документов → `400`; `llm_mode` недоступен → `400` |
| POST | `/api/queries` | JSON `QueryRequest` (необязательное `llm_mode`, v3) | `202 TaskCreated`; `llm_mode` недоступен → `400` |
| POST | `/api/batches` | JSON `BatchCreate` | `201 BatchCreated` |
| GET | `/api/batches/{batch_id}` | — | `BatchSummary`; нет комплекта → `404` |
| GET | `/api/batches/{batch_id}/export?format=json\|md\|pdf` | — | файл `batch_<id>.<ext>` (`Content-Disposition: attachment`); нет комплекта → `404` |
| GET | `/api/llm/modes` | — | `LlmModesResponse`; ключ API никогда не возвращается |
| GET | `/api/tasks/{task_id}` | — | `TaskInfo` |
| GET | `/api/reports/{report_id}` | — | `Report` |
| GET | `/api/reports/{report_id}/text` | — | `ReportText` — текст с **замаскированными** ПДн для подсветки |
| GET | `/api/reports/{report_id}/export?format=json\|pdf\|md` | — | файл отчёта |
| GET | `/api/reports/{report_id}/llm-log` | — | `list[LlmCall]` — «что видела нейросеть»; в режиме mock — `[]` или mock-вызовы с `verdict: "unknown"` |
| GET | `/api/reports/{report_id}/redacted` | — | обезличенная копия в формате исходника (`.pdf` / `.docx` / `.xlsx` / `.xml`, для текстового запроса — `.txt`; `.xml` — `Content-Type: application/xml`), `Content-Disposition: attachment; filename="<имя>_обезличено.<ext>"`; копии нет → `404 {detail: "Обезличенная копия недоступна"}` |
| GET | `/api/metrics?corpus=<имя>` | `corpus` необязателен, только `^[a-z0-9_-]{1,32}$`, иначе `422` | `Metrics` из `eval/results/<имя>/metrics.json`; без параметра — `eval/results/metrics.json` (пишет `eval/run_eval.py`); файла нет → `404 {detail: "Метрики ещё не посчитаны"}` |
| GET | `/api/rules` | — | `list[Rule]` |
| POST | `/api/rules` | `Rule` | `201 Rule` |
| PUT | `/api/rules/{id}` | `Rule` | `Rule` |
| DELETE | `/api/rules/{id}` | — | `204` |
| GET | `/api/health` | — | `HealthStatus` |

Ограничения: файл до 50 МБ; форматы .pdf, .docx, .xlsx, .xml (у .xml после UTF-8 BOM и пробелов первым должен идти `<`) и изображения (v3) с проверкой сигнатуры: .png — `89 50 4E 47`, .jpg / .jpeg — `FF D8 FF`, .tif / .tiff — `49 49 2A 00` или `4D 4D 00 2A`; иначе `400 {detail: "понятное сообщение"}`.
Режим нейросети (v3): `llm_mode` не указан → `LLM_DEFAULT_MODE`; режим есть в `GET /api/llm/modes`, но `available=false` → `400` с `reason` в `detail`.
Ошибки валидации запроса: `422 {"detail": "строка по-русски"}`; ответ не содержит введённых значений.
Исходные файлы удаляются после обработки; отчёты хранятся `REPORT_TTL_DAYS` дней (по умолчанию 7) — система сама соблюдает сроки хранения, которые проверяет.
Обезличенная копия создаётся в конвейере **до** удаления исходника и хранится тот же срок, что отчёт (`REPORT_TTL_DAYS`); правила копии — в разделе 4.1.

### 4.1. Модели запросов и ответов API (Pydantic v2)

```python
# schemas/task.py
class TaskCreated(BaseModel):        # ответ POST /api/documents и POST /api/queries
    task_id: str
    document_id: str


class QueryRequest(BaseModel):       # тело POST /api/queries
    text: str = Field(min_length=1, max_length=200_000)
    purpose: DocPurpose
    llm_mode: LlmMode | None = None  # None — режим по умолчанию сервера (контракт v3)


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

`ReportText.text` — текст с маскированием, сохраняющим длину (`mask_preserving_length`). Поэтому
`len(ReportText.text)` равна длине исходного текста и смещения совпадают с исходными — фронтенд
подсвечивает находки по `start/end` без пересчёта. Тем же правилом строятся `Evidence.quote`
(`quote == ReportText.text[start:end]`) и `Entity.value_masked`.

**Правило маскирования с сохранением длины.** Применяется к значению каждой сущности ПДн
(категории `pd`, `pd_special`). «Слово» — непрерывная последовательность букв и цифр (`\w+`).
Слово начинается с буквы — первая буква остаётся, остальные символы слова → `█`; слово начинается
с цифры — все символы слова → `█`. Прочие символы (пробелы, `-`, `(`, `)`, `+`, `@`, `.`) остаются.

| Исходное значение | После маскирования |
|---|---|
| `Иванов Иван` | `И█████ И███` |
| `123-456-789 00` | `███-███-███ ██` |
| `+7 (912) 345-67-89` | `+█ (███) ███-██-██` |
| `ivan.petrov@mail.ru` | `i███.p█████@m███.r█` |

**`value_masked` у коммерческой тайны и финансов.** Маскирование применяется только к ПДн.
У сущностей категорий `trade_secret` и `financial` `value_masked` — исходный фрагмент без
маскирования (решение 17.10): юристу нужно видеть, что именно найдено.

**Обезличенная копия (`GET /api/reports/{report_id}/redacted`).**
- Создаётся в конвейере до удаления исходника; хранится `REPORT_TTL_DAYS`, как отчёт. Нет копии → `Report.redacted_available = false` и эндпоинт отвечает `404`.
- ПДн (категории `pd`, `pd_special`) удаляются **физически**: в PDF — редакция (redaction annotations + применение), а не закраска поверх текста; в .docx / .xlsx / .txt / .xml — замена значения. Скопировать ПДн из копии нельзя.
- .xml (T-064b): поля — те же, что видел детектор (`iter_xml_fields`); поле ФИО (lastName/firstName/middleName одного родителя), в котором найдено хоть одно значение ПДн, целиком становится «[ФИО]» в каждом элементе; найденные значения заменяются и в атрибутах и tail-тексте. Префиксы пространств имён сохраняются, вывод — UTF-8 с XML-декларацией; комментарии и инструкции обработки в копию не попадают.
- Коммерческая тайна и финансы не трогаются (решение 17.10): маскируются только ПДн.

**Комплект и сводный отчёт (контракт v3).**
- `BatchSummary` не хранится, а **вычисляется при чтении** из статусов задач и отчётов документов комплекта; значений ПДн не содержит (только счётчики, `rule_id`, `title`, безопасные имена файлов).
- `filename` в `BatchDocument` — безопасное имя: если в имени файла найдены ПДн, показывается нейтральное имя («Документ_3.pdf»).
- `complete = true`, когда в комплекте нет документов `queued`/`processing`. `overall` — худший светофор среди готовых (`done`) документов; готовых нет → `None`.
- Порядок `rules`: `red` раньше `yellow`, затем по убыванию `documents`, затем по `rule_id`. Порядок `documents`: `red` → `yellow` → `green` → `processing`/`queued` → `failed`.
- Комплект удаляется retention'ом, когда в нём не осталось документов (отчёты удалены по `REPORT_TTL_DAYS`).

**OCR (контракт v3).** `Report.ocr` — копия `ExtractedDocument.ocr`; `None`, если весь текст взят из текстового слоя. Смещения в `low_confidence_spans` — в координатах полного исходного текста, как и все смещения отчёта.
Обезличенная копия OCR-документа: пиксели под ПДн **закрашиваются** на изображении страницы (в PDF-скане и в файле-изображении); если закрасить не удалось — копии нет и `redacted_available = false`.

**Журнал LLM (`LlmCall`).** `masked_fragment` — ровно то, что ушло в LLM, поэтому в журнале
только плейсхолдеры (`[PERSON_1]`). Исходный текст в журнал не пишется и при показе не
восстанавливается (AGENTS.md, правило 5).

Текст для LLM (`MaskResult.masked_text`, плейсхолдеры `[PERSON_1]`) имеет другую длину. Цитаты из
ответа LLM переводятся в исходные координаты через `to_original_offset(masked_offset, spans)`.

## 5. Переменные окружения (`.env.example`)

```
POSTGRES_USER=auditor
POSTGRES_PASSWORD=auditor
POSTGRES_DB=auditor
DATABASE_URL=postgresql+psycopg://auditor:auditor@postgres:5432/auditor
REDIS_URL=redis://redis:6379/0

LLM_PROVIDER=mock                 # mock | openai_compatible (старые переменные — см. ниже)
LLM_BASE_URL=                     # например https://<сервис>/v1 или http://ollama:11434/v1
LLM_API_KEY=
LLM_MODEL=

# Режимы нейросети (контракт v3). Старые LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY/LLM_MODEL
# продолжают работать и задают режим по умолчанию.
LLM_DEFAULT_MODE=mock             # mock | local | cloud
LLM_LOCAL_BASE_URL=http://ollama:11434/v1
LLM_LOCAL_MODEL=
LLM_CLOUD_BASE_URL=
LLM_CLOUD_API_KEY=                # только в .env; в API и логи не попадает
LLM_CLOUD_MODEL=
LLM_MAX_CALLS_PER_DOC=30
LLM_TIMEOUT_S=30                  # таймаут одного запроса к LLM, с; float > 0; локально на CPU — 120
LLM_REASONING_EFFORT=             # необязательно; "none" — выключить рассуждения локальной модели (Ollama: Qwen3.5, Gemma 4); облаку не задавать

MAX_UPLOAD_MB=50
REPORT_TTL_DAYS=7

# OCR (контракт v3)
OCR_LANG=rus+eng
OCR_DPI=300
OCR_MAX_PAGES=100
OCR_LOW_WORD_CONF=0.6             # слово с уверенностью ниже — в low_confidence_spans
OCR_LOW_PAGE_CONF=0.75            # страница со средней уверенностью ниже — в low_confidence_pages

# Комплект документов (контракт v3)
BATCH_MAX_DOCUMENTS=100
```

## История изменений

| Дата | Что изменилось | Кто решил |
|---|---|---|
| 2026-11-12 | Раздел 5: `BATCH_MAX_DOCUMENTS` по умолчанию 20 → 100 (требование организаторов этапа 2), T-088. Схемы и эндпоинты не менялись | Данила (тимлид) |
| 2026-11-11 | **Контракт v3: комплект и сводный отчёт, OCR, режимы нейросети (решение Данилы)**, T-070. Раздел 1: `OcrPage`, `OcrInfo`, `ExtractedDocument.ocr`, `Report.ocr`, `AuditInfo.llm_mode`, `LlmMode`, `LlmModeInfo`, `LlmModesResponse`, `BatchCreate`, `BatchCreated`, `BatchDocument`, `BatchRuleSummary`, `BatchSummary`; все новые поля старых моделей необязательные. Раздел 3: `extract` возвращает `ocr`, форматы изображений. Раздел 4: `/api/batches`, `/api/llm/modes`, поля `batch_id`/`llm_mode` в `POST /api/documents`, `llm_mode` в `QueryRequest`. Раздел 5: `LLM_DEFAULT_MODE`, `LLM_LOCAL_*`, `LLM_CLOUD_*`, `OCR_*`, `BATCH_MAX_DOCUMENTS` | Данила (тимлид) |
| 2026-10-31 | Раздел 5: убран EMBEDDING_MODEL — модуль RAG (заглушка T-014) удалён; статьи законов открываются из data/legal | Данила (тимлид) |
| 2026-10-30 | обезличенная копия .xml | Данила (тимлид) |
| 2026-10-29 | .xml (выгрузки ЕИС) в POST /api/documents; обезличенная копия .xml — T-064b | Данила (тимлид) |
| 2026-10-29 | Раздел 5: LLM_REASONING_EFFORT — режим рассуждений локальных моделей (T-066) | Данила (тимлид) |
| 2026-10-27 | export?format=md (T-060) | Данила (тимлид) |
| 2026-10-25 | 422 — {detail: строка}; QueryRequest.text ≤ 200 000 (T-052) | Данила (тимлид) |
| 2026-10-24 | GET /api/metrics?corpus= — метрики других корпусов (слепой набор) | Данила (тимлид) |
| 2026-10-24 | Раздел 5: LLM_TIMEOUT_S — таймаут запроса к LLM (T-029b) | Данила (тимлид) |
| 2026-10-24 | LlmCall.purpose += special_category (T-013e) | Данила (тимлид) |
| 2026-10-22 | Раздел 2: зафиксирован запрет условия `llm` внутри `not` (уже реализован в T-013b, `validate_condition`). Поведение кода не меняется. | Данила (тимлид) |
| 2026-10-21 | T-013b: RuleContext.mask_spans для перевода цитат LLM | Данила (тимлид) |
| 2026-10-17 | T-009b: build_report принимает audit: AuditInfo (собирает конвейер: хэш исходника, версии правил и словарей, счётчик LLM) | Данила (тимлид) |
| 2026-10-17 | **Контракт v2** (T-030). Раздел 1: `AuditInfo`, в `Report` — `audit` (обязательное) и `redacted_available` (по умолчанию `false`); `LlmCall` (журнал «что видела нейросеть»); `Metrics`, `TypeMetrics`. Раздел 4: `GET /api/reports/{id}/llm-log`, `GET /api/reports/{id}/redacted`, `GET /api/metrics`; обезличенная копия создаётся до удаления исходника и хранится `REPORT_TTL_DAYS`. Раздел 4.1: правила копии (ПДн удаляются физически, в PDF — редакция; КТ не трогается); `value_masked` у `trade_secret`/`financial` — исходный фрагмент без маскирования. Реализация: T-016 (audit), T-013/T-027b (llm-log), T-026 (redacted), T-028 (metrics). | Данила (тимлид) |
| 2026-10-16 | Ревью T-001: `Evidence.quote` и `Entity.value_masked` — маскирование с сохранением длины, `quote == ReportText.text[start:end]`. Определено правило маскирования (слово = `\w+`; буквенное слово — первая буква видна; цифровое — скрыто целиком; разделители видны). В раздел 3 добавлена `mask_preserving_length(text, entities) -> str` (T-008). Уточнена `to_original_offset` внутри плейсхолдера; границы цитат LLM расширяются до целых плейсхолдеров (T-013). | Данила (тимлид) |
| 2026-10-15 | T-001 (правка 2): единая система координат — все смещения в `Report` и `ReportText.segments` в координатах исходного текста; `ReportText.text` — маскирование с сохранением длины (`█`, кроме первой буквы слова). В `MaskResult` добавлено `spans: list[MaskSpan]`, добавлена функция `to_original_offset` (реализация — T-008). Health: `status = "degraded"`, если `db` или `redis` = false (HTTP 200). | Данила (тимлид) |
| 2026-10-15 | T-001: `GET /api/health` возвращает `HealthStatus {status, db, redis}` вместо `{status}`. Добавлен раздел 4.1 с моделями `TaskCreated`, `QueryRequest`, `ReportText`, `HealthStatus` — раньше эти формы были описаны только в таблице раздела 4 без имён. Поля и смысл ответов не изменились, кроме двух новых полей health. | Данила (тимлид) |
