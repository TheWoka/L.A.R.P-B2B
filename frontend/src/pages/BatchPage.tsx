import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Progress, Spin, Tabs, Tag } from "antd";
import { Link, useLocation } from "react-router-dom";

import { ApiError, getReport, getTask } from "../api/client";
import type { Report, TaskInfo } from "../api/types";
import { addToHistory } from "../lib/history";
import { parseBatchTasks } from "../lib/batch";
import { ReportView } from "./ReportPage";

export default function BatchPage() {
  const { search } = useLocation();
  const ids = useMemo(() => parseBatchTasks(search), [search]);
  const [tasks, setTasks] = useState<Record<string, TaskInfo>>({});
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [activeId, setActiveId] = useState(ids[0] ?? "");
  const [pollError, setPollError] = useState<string | null>(null);
  const selectedFirstSevere = useRef(false);
  const metadata = useMemo(() => {
    try { return JSON.parse(sessionStorage.getItem(`grif-batch-${ids.join(",")}`) ?? "{}") as { names?: Record<string, string>; failed?: { name: string; reason: string }[] }; }
    catch { return {}; }
  }, [ids]);
  const names = metadata.names ?? {};
  const failedUploads = metadata.failed ?? [];

  useEffect(() => {
    if (ids.length === 0) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let errors = 0;
    const snapshot: Record<string, TaskInfo> = {};
    async function poll() {
      const pending = ids.filter((id) => !snapshot[id] || !["done", "failed"].includes(snapshot[id].status));
      if (pending.length === 0) return;
      const results = await Promise.allSettled(pending.map((id) => getTask(id)));
      if (!active) return;
      let failedRequests = 0;
      results.forEach((result, index) => {
        const id = pending[index];
        if (result.status === "fulfilled") snapshot[id] = result.value;
        else if (result.reason instanceof ApiError && result.reason.status === 404) {
          snapshot[id] = { task_id: id, status: "failed", stage: null, progress: 0, report_id: null, error: "Проверка не найдена. Возможно, ссылка устарела." };
        } else failedRequests += 1;
      });
      setTasks({ ...snapshot });
      errors = failedRequests > 0 ? errors + 1 : 0;
      setPollError(errors > 0 ? "Не удалось обновить часть проверок. Повторяем попытку." : null);
      if (errors >= 5) {
        setPollError("Нет связи с сервером. Обновите страницу, чтобы повторить попытку.");
        return;
      }
      if (ids.some((id) => !snapshot[id] || !["done", "failed"].includes(snapshot[id].status))) timer = setTimeout(poll, 1500);
    }
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [ids]);

  useEffect(() => {
    const available = ids.filter((id) => tasks[id]?.status === "done" && tasks[id].report_id && !reports[id]);
    if (available.length === 0) return;
    let active = true;
    void Promise.allSettled(available.map((id) => getReport(tasks[id].report_id!))).then((results) => {
      if (!active) return;
      const next: Record<string, Report> = {};
      results.forEach((result, index) => {
        if (result.status === "fulfilled") {
          next[available[index]] = result.value;
          addToHistory(result.value);
        }
      });
      if (Object.keys(next).length > 0) setReports((current) => ({ ...current, ...next }));
    });
    return () => { active = false; };
  }, [ids, tasks, reports]);

  const complete = ids.length > 0 && ids.every((id) => tasks[id]?.status === "done" || tasks[id]?.status === "failed");
  useEffect(() => {
    if (!complete || selectedFirstSevere.current || ids.some((id) => tasks[id]?.status === "done" && !reports[id])) return;
    const severe = ids.find((id) => reports[id]?.overall === "red");
    if (severe) setActiveId(severe);
    selectedFirstSevere.current = true;
  }, [complete, ids, reports, tasks]);

  if (ids.length === 0) return <div className="page-container"><Alert type="warning" title="Не найдены проверки" description="Откройте пакетную проверку после выбора нескольких файлов." action={<Link to="/">Загрузить документы</Link>} /></div>;

  const ready = ids.filter((id) => tasks[id]?.status === "done").length;
  const failed = ids.filter((id) => tasks[id]?.status === "failed").length;
  const serious = Object.values(reports).reduce((sum, report) => sum + report.violations.filter((item) => item.severity === "red").length, 0);

  return <div className="page-container batch-page">
    <header className="page-heading"><h1>Проверка документов</h1><p>{complete ? "Проверка завершена. Выберите файл, чтобы увидеть подробный отчёт." : "Файлы проверяются по очереди. Результаты появляются здесь автоматически."}</p></header>
    <div className="batch-summary">
      <Card><strong>{ready} из {ids.length}</strong><span>Отчётов готово</span></Card>
      <Card><strong>{serious}</strong><span>Серьёзных нарушений</span></Card>
      <Card><strong>{failed + failedUploads.length}</strong><span>Файлов с ошибкой</span></Card>
    </div>
    {failedUploads.length > 0 && <Alert className="batch-alert" type="warning" showIcon title="Не все файлы удалось отправить" description={<ul>{failedUploads.map((entry, index) => <li key={`${entry.name}-${index}`}>{entry.name}: {entry.reason}</li>)}</ul>} />}
    {pollError && <Alert className="batch-alert" type="warning" showIcon title={pollError} action={pollError.startsWith("Нет связи") ? <Button onClick={() => window.location.reload()}>Обновить</Button> : undefined} />}
    <Tabs className="batch-tabs" activeKey={activeId} onChange={setActiveId} items={ids.map((id, index) => {
      const task = tasks[id];
      const label = <span className="batch-tab-label"><span className={`batch-status is-${task?.status ?? "queued"}`} aria-hidden="true" />{names[id] ?? `Файл ${index + 1}`}</span>;
      let children;
      if (task?.status === "failed") children = <Alert type="error" showIcon title="Не удалось проверить файл" description={task.error ?? "Попробуйте загрузить его снова."} />;
      else if (task?.status === "done" && task.report_id) children = <div className="batch-report"><ReportView key={task.report_id} reportId={task.report_id} /></div>;
      else children = <Card className="batch-progress"><Spin size="large" /><h2>{task?.stage === "reporting" ? "Подготавливаем отчёт" : "Документ проверяется"}</h2><p>Обычно это занимает меньше минуты. Большие документы могут обрабатываться дольше.</p><Progress percent={task?.progress ?? 0} status="active" /><Tag>{task?.status === "queued" || !task ? "В очереди" : "Проверяется"}</Tag></Card>;
      return { key: id, label, children };
    })} />
    <Button type="primary" className="batch-new-check" onClick={() => { window.location.href = "/"; }}>Проверить другие документы</Button>
  </div>;
}
