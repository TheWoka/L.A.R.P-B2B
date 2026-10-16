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
