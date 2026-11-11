import type { LlmMode, LlmModeInfo } from "../api/types";

export const LABELS: Record<LlmMode, string> = {
  mock: "Без нейросети",
  local: "Локально (Ollama)",
  cloud: "Облако",
};

const STORAGE_KEY = "grif-llm-mode";

export const MOCK_MODES: LlmModeInfo[] = [
  { mode: "mock", available: true, model: null, reason: null },
];

export function loadSavedMode(): LlmMode | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === "mock" || saved === "local" || saved === "cloud" ? saved : null;
  } catch {
    return null;
  }
}

export function saveMode(mode: LlmMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Выбор работает и при запрете хранилища браузера.
  }
}

export function pickInitialMode(
  modes: readonly LlmModeInfo[], saved: LlmMode | null, def: LlmMode,
): LlmMode {
  const available = (mode: LlmMode | null): boolean => modes.some((item) => item.mode === mode && item.available);
  if (saved !== null && available(saved)) return saved;
  return available(def) ? def : "mock";
}
