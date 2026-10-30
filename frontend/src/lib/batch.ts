import type { DocPurpose, TaskCreated } from "../api/types";

export const MAX_BATCH_FILES = 10;
const MAX_FILE_SIZE = 50 * 1024 * 1024;
const EXTENSIONS = [".pdf", ".docx", ".xlsx", ".xml"];

export function documentCountLabel(count: number): string {
  const lastTwo = count % 100;
  const last = count % 10;
  const noun = lastTwo >= 11 && lastTwo <= 14 ? "документов" : last === 1 ? "документ" : last >= 2 && last <= 4 ? "документа" : "документов";
  return `${count} ${noun}`;
}

export function validateBatchFile(file: Pick<File, "name" | "size">): string | null {
  if (!EXTENSIONS.some((extension) => file.name.toLowerCase().endsWith(extension))) return "Поддерживаются PDF, DOCX, XLSX или XML";
  if (file.size === 0) return "Файл пустой";
  if (file.size > MAX_FILE_SIZE) return "Файл больше 50 МБ";
  return null;
}

export function parseBatchTasks(search: string): string[] {
  const raw = new URLSearchParams(search).get("tasks") ?? "";
  return [...new Set(raw.split(",").filter((id) => /^[a-zA-Z0-9_-]{1,128}$/.test(id)))].slice(0, MAX_BATCH_FILES);
}

/** purpose — одно назначение для всех файлов или функция «файл → назначение» (своё у каждого файла). */
export async function submitBatchFiles(
  files: File[],
  purpose: DocPurpose | ((file: File, index: number) => DocPurpose),
  upload: (file: File, purpose: DocPurpose) => Promise<TaskCreated>,
): Promise<{ created: { id: string; name: string }[]; failed: { name: string; reason: string }[] }> {
  const created: { id: string; name: string }[] = [];
  const failed: { name: string; reason: string }[] = [];
  for (const [index, file] of files.entries()) {
    try {
      const task = await upload(file, typeof purpose === "function" ? purpose(file, index) : purpose);
      created.push({ id: task.task_id, name: file.name });
    } catch (error: unknown) {
      failed.push({ name: file.name, reason: error instanceof Error ? error.message : "Не удалось отправить файл" });
    }
  }
  return { created, failed };
}
