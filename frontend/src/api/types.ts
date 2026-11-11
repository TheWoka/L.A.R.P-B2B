// TypeScript-зеркало docs/contracts.md (разделы 1 и 4.1).
// Менять только вместе с контрактом и backend/app/schemas/ — решение тимлида.

// ---------- Назначение документа ----------
export type DocPurpose = "internal" | "publication" | "contract" | "international";

// ---------- Извлечение текста ----------
export interface TextSegment {
  id: number;
  text: string;
  offset: number;
  page: number | null;
  section: string | null;
}

// ---------- Распознавание (OCR), контракт v3 ----------
export interface OcrPage {
  page: number; // ≥ 1
  confidence: number; // 0..1, средняя уверенность слов страницы
}

export interface OcrInfo {
  engine: string; // "tesseract-5.3.0 rus+eng"
  pages: OcrPage[]; // только распознанные страницы
  mean_confidence: number; // 0..1, по всем распознанным словам
  low_confidence_pages: number[]; // confidence < 0.75
  /** [start, end) слов с уверенностью < 0.6, координаты полного текста. */
  low_confidence_spans: [number, number][];
}

export interface ExtractedDocument {
  document_id: string;
  filename: string;
  text: string;
  segments: TextSegment[];
  pages: number | null;
  ocr: OcrInfo | null; // null — весь текст из текстового слоя
}

// ---------- Найденные сущности ----------
export type EntityType =
  | "PERSON"
  | "PASSPORT"
  | "SNILS"
  | "INN"
  | "PHONE"
  | "EMAIL"
  | "ADDRESS"
  | "BIRTH_DATE"
  | "CARD"
  | "BANK_ACCOUNT"
  | "SPECIAL_CATEGORY"
  | "TRADE_SECRET"
  | "FINANCIAL";

export type EntityCategory = "pd" | "pd_special" | "trade_secret" | "financial";

export type Detector = "regex" | "ner" | "dict" | "llm" | "model";

export interface Entity {
  type: EntityType;
  category: EntityCategory;
  start: number;
  end: number;
  page: number | null;
  section: string | null;
  /** Для ПДн — маскирование с сохранением длины (contracts.md, 4.1): "И█████ И███". */
  value_masked: string;
  confidence: number;
  detector: Detector;
}

// ---------- Маркеры чек-листа ----------
export type MarkerType =
  | "approval_sheet"
  | "classification_mark"
  | "retention_period"
  | "consent_clause"
  | "processing_assignment"
  | "kii_mention";

export interface Evidence {
  /** Цитата, ПДн скрыты с сохранением длины: quote === ReportText.text.slice(start, end). */
  quote: string;
  start: number;
  end: number;
  page: number | null;
  section: string | null;
}

export interface Marker {
  type: MarkerType;
  present: boolean;
  evidence: Evidence | null;
}

// ---------- Правила и нарушения ----------
export interface LawRef {
  law: string;
  article: string;
  title: string | null;
  url: string | null;
}

export type Severity = "red" | "yellow";

// Дерево условия правила (contracts.md, раздел 2). Форма проверяется на бэкенде.
export type RuleCondition = Record<string, unknown>;

export interface Rule {
  id: string;
  title: string;
  description: string;
  severity: Severity;
  law_refs: LawRef[];
  applies_to: DocPurpose[];
  condition: RuleCondition;
  recommendation: string;
  enabled: boolean;
}

export interface Violation {
  rule_id: string;
  title: string;
  severity: Severity;
  law_refs: LawRef[];
  evidence: Evidence[];
  recommendation: string;
  source: "rule" | "llm";
}

// ---------- Отчёт ----------
export type ChecklistCategory = "approvals" | "classification" | "retention";
export type ChecklistStatus = "ok" | "missing" | "not_required";
export type Overall = "red" | "yellow" | "green";

export interface ChecklistItem {
  category: ChecklistCategory;
  status: ChecklistStatus;
  comment: string;
  evidence: Evidence | null;
}

// След проверки (контракт v2): по нему отчёт можно воспроизвести.
export type LlmProvider = "mock" | "openai_compatible";

// Режим нейросети (контракт v3): без нейросети / локальная Ollama / облачный API.
export type LlmMode = "mock" | "local" | "cloud";

export interface AuditInfo {
  document_sha256: string; // sha256 исходного файла (для запроса — текста в UTF-8)
  ruleset_version: string; // первые 12 hex sha256 канонического JSON включённых правил
  dictionaries_version: string; // то же для data/rules/dictionaries.yaml
  app_version: string;
  detectors: string[]; // например ["regex", "ner:natasha-1.6.0", "dict"]
  llm_provider: LlmProvider;
  llm_model: string | null; // null в режиме mock
  llm_calls: number; // реальных вызовов LLM (без кэша и mock)
  llm_mode: LlmMode; // режим нейросети проверки; в старых отчётах — "mock"
}

export interface Report {
  report_id: string;
  document_id: string;
  filename: string | null;
  purpose: DocPurpose;
  overall: Overall;
  created_at: string; // ISO 8601
  processing_ms: number;
  stats: Record<string, number>;
  entities: Entity[];
  checklist: ChecklistItem[]; // ровно 3 элемента
  violations: Violation[];
  llm_used: boolean;
  audit: AuditInfo;
  redacted_available: boolean; // есть ли обезличенная копия (redactedUrl)
  ocr: OcrInfo | null; // копия ExtractedDocument.ocr; null — OCR не применялся
}

// ---------- «Что видела нейросеть»: журнал вызовов LLM ----------
export type LlmPurpose = "rule_condition" | "trade_secret" | "special_category";
export type LlmVerdict = "yes" | "no" | "unknown";

export interface LlmCall {
  purpose: LlmPurpose;
  rule_id: string | null; // для purpose="rule_condition", иначе null
  question: string;
  /** РОВНО тот текст, что ушёл в LLM: только замаскированный ([PERSON_1] …). */
  masked_fragment: string;
  verdict: LlmVerdict;
  quote: string | null; // цитата из ответа LLM (в координатах masked_fragment)
  cached: boolean; // ответ взят из кэша Redis
}

// ---------- Метрики качества ----------
export interface TypeMetrics {
  type: string; // значение EntityType
  precision: number; // 0..1
  recall: number; // 0..1
  f1: number; // 0..1
  support: number; // число эталонных сущностей
}

export interface Metrics {
  corpus: string;
  documents: number;
  created_at: string; // ISO 8601
  by_type: TypeMetrics[];
  pd_recall: number; // по всем ПДн — критерий ≥ 0.90
  trade_secret_recall: number; // критерий ≥ 0.85
  matching: string; // правило совпадения, например "overlap ≥ 50% и тот же type"
}

// ---------- Задачи ----------
export type TaskStatus = "queued" | "processing" | "done" | "failed";
export type Stage = "extracting" | "detecting" | "masking" | "checking" | "reporting";

export interface TaskInfo {
  task_id: string;
  status: TaskStatus;
  stage: Stage | null;
  progress: number;
  report_id: string | null;
  error: string | null;
}

// ---------- Модели API (contracts.md, раздел 4.1) ----------
export interface TaskCreated {
  task_id: string;
  document_id: string;
}

export interface QueryRequest {
  /** Не больше 200 000 символов (contracts.md, раздел 4.1). */
  text: string;
  purpose: DocPurpose;
  /** Не указан или null — режим по умолчанию сервера. */
  llm_mode?: LlmMode | null;
}

// Все смещения в Report и ReportText.segments — в координатах ИСХОДНОГО текста.
// text замаскирован с сохранением длины ("Иванов Иван" → "И█████ И███"),
// поэтому Entity.start/end и Evidence.start/end можно применять к нему напрямую.
export interface ReportText {
  text: string;
  segments: TextSegment[];
}

export interface HealthStatus {
  status: "ok" | "degraded"; // degraded — db или redis недоступны
  db: boolean;
  redis: boolean;
}

// ---------- Комплект документов и сводный отчёт (контракт v3) ----------
export interface BatchCreate {
  name?: string | null; // до 120 символов
  purpose: DocPurpose;
}

export interface BatchCreated {
  batch_id: string;
}

export type BatchDocumentStatus = "queued" | "processing" | "done" | "failed";

export interface BatchDocument {
  document_id: string;
  task_id: string;
  filename: string | null; // безопасное имя: ПДн в имени заменены нейтральным
  purpose: DocPurpose;
  status: BatchDocumentStatus;
  report_id: string | null;
  overall: Overall | null;
  violations_red: number;
  violations_yellow: number;
  ocr: boolean; // хотя бы одна страница распознана OCR
  error: string | null;
}

export interface BatchRuleSummary {
  rule_id: string;
  title: string;
  severity: Severity;
  documents: number; // ≥ 1: в скольких документах сработало
}

export interface BatchSummary {
  batch_id: string;
  name: string | null;
  purpose: DocPurpose;
  created_at: string; // ISO 8601
  complete: boolean; // нет документов queued/processing
  overall: Overall | null; // худший среди готовых; null — готовых нет
  documents_total: number;
  documents_done: number;
  documents_failed: number;
  violations_red: number;
  violations_yellow: number;
  rules: BatchRuleSummary[]; // red раньше yellow, затем по убыванию documents, затем rule_id
  documents: BatchDocument[]; // red → yellow → green → processing/queued → failed
}

// ---------- Режимы нейросети (контракт v3) ----------
export interface LlmModeInfo {
  mode: LlmMode;
  available: boolean;
  model: string | null;
  reason: string | null; // почему недоступен: «Ollama не запущена», «Нет ключа в .env»
}

export interface LlmModesResponse {
  default: LlmMode;
  modes: LlmModeInfo[]; // всегда три: mock, local, cloud
}
