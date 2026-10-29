import type { ChecklistCategory, ChecklistStatus, DocPurpose, EntityType, LlmPurpose, LlmVerdict, Severity } from "../api/types";

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

export const LLM_VERDICT_LABELS: Record<LlmPurpose, Record<LlmVerdict, { label: string; color: string }>> = {
  rule_condition: {
    yes: { label: "Да — нарушение", color: "red" },
    no: { label: "Нет", color: "green" },
    unknown: { label: "Не удалось определить", color: "default" },
  },
  trade_secret: {
    yes: { label: "Найдена коммерческая тайна", color: "blue" },
    no: { label: "Не найдено", color: "default" },
    unknown: { label: "Не удалось проверить", color: "default" },
  },
  special_category: {
    yes: { label: "Найдены особые категории ПДн", color: "blue" },
    no: { label: "Не найдено", color: "default" },
    unknown: { label: "Не удалось проверить", color: "default" },
  },
};

export function llmPurposeTitle(purpose: LlmPurpose, ruleId: string | null): string {
  switch (purpose) {
    case "rule_condition": return ruleId ? `Проверка условия правила ${ruleId}` : "Проверка условия правила";
    case "trade_secret": return "Поиск коммерческой тайны";
    case "special_category": return "Поиск особых категорий ПДн";
  }
}

export function detectorLabel(name: string): string {
  if (name === "regex") return "Шаблоны и контрольные суммы";
  if (name.startsWith("ner:")) return "Распознавание имён Natasha";
  if (name === "dict") return "Словари";
  if (name.startsWith("llm:")) return `Генеративная нейросеть ${name.slice(4) || "(модель не указана)"}`;
  if (name.startsWith("model:")) return "Классификатор grif-cls (нейросеть, локально)";
  return name;
}
