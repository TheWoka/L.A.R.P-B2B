import type { DocPurpose, Overall, Report } from "../api/types";

export interface HistoryItem {
  report_id: string;
  kind: "file" | "text";
  ext: string | null;
  purpose: DocPurpose;
  overall: Overall;
  created_at: string;
  red: number;
  yellow: number;
}

const STORAGE_KEY = "grif.history.v1";
const MAX_ITEMS = 50;
let storageUnavailable = false;

function isHistoryItem(value: unknown): value is HistoryItem {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.report_id === "string" && item.report_id.length > 0
    && (item.kind === "file" || item.kind === "text")
    && (item.ext === null || item.ext === ".docx" || item.ext === ".pdf" || item.ext === ".xlsx")
    && (item.kind !== "text" || item.ext === null)
    && (item.purpose === "internal" || item.purpose === "publication" || item.purpose === "contract" || item.purpose === "international")
    && (item.overall === "red" || item.overall === "yellow" || item.overall === "green")
    && typeof item.created_at === "string" && Number.isFinite(Date.parse(item.created_at))
    && typeof item.red === "number" && Number.isSafeInteger(item.red) && item.red >= 0
    && typeof item.yellow === "number" && Number.isSafeInteger(item.yellow) && item.yellow >= 0;
}

export function listHistory(): HistoryItem[] {
  if (storageUnavailable) return [];
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(stored)) return [];
    const seen = new Set<string>();
    return stored.filter(isHistoryItem).filter((item) => {
      if (seen.has(item.report_id)) return false;
      seen.add(item.report_id);
      return true;
    }).slice(0, MAX_ITEMS).map(({ report_id, kind, ext, purpose, overall, created_at, red, yellow }) => (
      { report_id, kind, ext, purpose, overall, created_at, red, yellow }
    ));
  } catch {
    return [];
  }
}

function saveHistory(items: HistoryItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    storageUnavailable = false;
  } catch {
    // При запрете хранения или переполнении история недоступна до следующей успешной записи.
    storageUnavailable = true;
  }
}

export function addToHistory(report: Report): void {
  // Из имени берём только разрешённое расширение: в самом имени могут быть ПДн.
  const ext = report.filename?.match(/\.(docx|pdf|xlsx)$/i)?.[0].toLowerCase() ?? null;
  const item: HistoryItem = {
    report_id: report.report_id,
    kind: report.filename === null ? "text" : "file",
    ext,
    purpose: report.purpose,
    overall: report.overall,
    created_at: report.created_at,
    red: report.violations.filter((violation) => violation.severity === "red").length,
    yellow: report.violations.filter((violation) => violation.severity === "yellow").length,
  };
  saveHistory([item, ...listHistory().filter((entry) => entry.report_id !== item.report_id)].slice(0, MAX_ITEMS));
}

export function removeFromHistory(id: string): void {
  saveHistory(listHistory().filter((item) => item.report_id !== id));
}

export function clearHistory(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
    storageUnavailable = false;
  } catch {
    storageUnavailable = true;
  }
}
