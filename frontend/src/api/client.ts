// Клиент REST API (contracts.md, раздел 4). Все пути относительные: /api/...
// В dev их проксирует Vite, в docker-compose — nginx.

import type {
  DocPurpose,
  QueryRequest,
  Report,
  ReportText,
  Rule,
  TaskCreated,
  TaskInfo,
} from "./types";

const BASE_URL = "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function hasStringDetail(body: unknown): body is { detail: string } {
  return (
    typeof body === "object" &&
    body !== null &&
    "detail" in body &&
    typeof (body as { detail: unknown }).detail === "string"
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, init);
  if (!response.ok) {
    let message = `Ошибка сервера (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (hasStringDetail(body)) message = body.detail;
    } catch {
      // тело не JSON — оставляем общее сообщение
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function uploadDocument(file: File, purpose: DocPurpose): Promise<TaskCreated> {
  const form = new FormData();
  form.append("file", file);
  form.append("purpose", purpose);
  return request<TaskCreated>("/documents", { method: "POST", body: form });
}

export function submitQuery(query: QueryRequest): Promise<TaskCreated> {
  return request<TaskCreated>("/queries", jsonInit("POST", query));
}

export function getTask(taskId: string): Promise<TaskInfo> {
  return request<TaskInfo>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function getReport(reportId: string): Promise<Report> {
  return request<Report>(`/reports/${encodeURIComponent(reportId)}`);
}

export function getReportText(reportId: string): Promise<ReportText> {
  return request<ReportText>(`/reports/${encodeURIComponent(reportId)}/text`);
}

export function listRules(): Promise<Rule[]> {
  return request<Rule[]>("/rules");
}

export function createRule(rule: Rule): Promise<Rule> {
  return request<Rule>("/rules", jsonInit("POST", rule));
}

export function updateRule(id: string, rule: Rule): Promise<Rule> {
  return request<Rule>(`/rules/${encodeURIComponent(id)}`, jsonInit("PUT", rule));
}

export function deleteRule(id: string): Promise<void> {
  return request<void>(`/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
}
