import type { ChecklistCategory, ChecklistStatus, DocPurpose, EntityType, Severity } from "../api/types";

export const ENTITY_TYPE_LABELS: Record<EntityType, string> = {
  PERSON: "ФИО",
  PASSPORT: "Паспорт",
  SNILS: "СНИЛС",
  INN: "ИНН",
  PHONE: "Телефон",
  EMAIL: "Электронная почта",
  ADDRESS: "Адрес",
  BIRTH_DATE: "Дата рождения",
  CARD: "Номер карты",
  BANK_ACCOUNT: "Банковский счёт",
  SPECIAL_CATEGORY: "Особые категории данных",
  TRADE_SECRET: "Коммерческая тайна",
  FINANCIAL: "Финансовые данные",
};

export const CHECKLIST_CATEGORY_LABELS: Record<ChecklistCategory, string> = {
  approvals: "Согласования",
  classification: "Гриф конфиденциальности",
  retention: "Срок хранения",
};

export const CHECKLIST_STATUS_LABELS: Record<ChecklistStatus, string> = {
  ok: "Есть",
  missing: "Отсутствует",
  not_required: "Не требуется",
};

export const PURPOSE_LABELS: Record<DocPurpose, string> = {
  internal: "Внутренний документ",
  publication: "Для публикации (ЕИС, сайт)",
  contract: "Договор с контрагентом",
  international: "Международный контракт",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  red: "Серьёзное",
  yellow: "Замечание",
};
