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

export interface ExtractedDocument {
  document_id: string;
  filename: string;
  text: string;
  segments: TextSegment[];
  pages: number | null;
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

export interface AuditInfo {
  document_sha256: string; // sha256 исходного файла (для запроса — текста в UTF-8)
  ruleset_version: string; // первые 12 hex sha256 канонического JSON включённых правил
  dictionaries_version: string; // то же для data/rules/dictionaries.yaml
  app_version: string;
  detectors: string[]; // например ["regex", "ner:natasha-1.6.0", "dict"]
  llm_provider: LlmProvider;
  llm_model: string | null; // null в режиме mock
  llm_calls: number; // реальных вызовов LLM (без кэша и mock)
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
}

// ---------- «Что видела нейросеть»: журнал вызовов LLM ----------
export type LlmPurpose = "rule_condition" | "trade_secret";
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
  text: string;
  purpose: DocPurpose;
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
