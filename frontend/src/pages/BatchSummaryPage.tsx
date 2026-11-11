import { useEffect, useState } from "react";
import { Alert, Button, Empty, Spin, Tag } from "antd";
import { Link, useParams } from "react-router-dom";
import { ApiError, batchExportUrl, getBatch } from "../api/client";
import type { BatchDocument, BatchSummary } from "../api/types";
import TrafficLight, { OVERALL } from "../components/TrafficLight";
import { PURPOSE_LABELS } from "../components/labels";
import { addBatchToHistory } from "../lib/history";

function DocumentResult({ item }: { item: BatchDocument }) {
  if (item.status === "failed") return <span className="batch-document-error">{item.error || "Не удалось проверить документ"}</span>;
  if (item.status !== "done") return <span>{item.status === "queued" ? "В очереди" : "Проверяется"}</span>;
  return <>{item.overall && <span className="batch-verdict"><i style={{ background: OVERALL[item.overall].color }} />{OVERALL[item.overall].label}</span>}
    <span className="batch-document-counts">Серьёзных: {item.violations_red} · Замечаний: {item.violations_yellow}</span></>;
}

export default function BatchSummaryPage() {
  const { batchId = "" } = useParams();
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setSummary(null);
    setError(null);
    async function refresh() {
      try {
        const result = await getBatch(batchId);
        if (stopped) return;
        setSummary(result);
        setError(null);
        addBatchToHistory(result);
        if (!result.complete) timer = setTimeout(() => { void refresh(); }, 2000);
      } catch (failure: unknown) {
        if (stopped) return;
        setError(failure instanceof ApiError && failure.status === 404
          ? "Комплект не найден или удалён по сроку хранения"
          : "Не удалось обновить сводный отчёт. Проверьте соединение и повторите попытку.");
      }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, [batchId, attempt]);

  return <div className="page-container batch-summary-page">
    <header className="page-heading"><h1>Сводный отчёт по комплекту</h1><p>{summary ? summary.name || `Комплект от ${new Date(summary.created_at).toLocaleDateString("ru-RU")}` : "Общий итог проверки документов"}</p></header>
    {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => setAttempt((value) => value + 1)}>Повторить</Button>} />}
    {!summary && !error && <div className="batch-loading" role="status"><Spin /><p>Загружаем сводный отчёт…</p></div>}
    {summary && <>
      <section className="batch-overview" aria-label="Общий итог">
        <div>{summary.overall ? <TrafficLight value={summary.overall} /> : <Tag color="blue">{summary.complete ? "Нет готовых отчётов" : "Проверка идёт"}</Tag>}
          <h2>{summary.overall ? OVERALL[summary.overall].label : summary.complete ? "Нет результатов для общего итога" : "Документы проверяются"}</h2>
          <p>Общий итог определяется по документу с наиболее серьёзными нарушениями.</p></div>
        <div className="batch-exports" aria-label="Скачать сводный отчёт">{(["pdf", "md", "json"] as const).map((format) => <Button key={format} type={format === "pdf" ? "primary" : "default"} href={batchExportUrl(batchId, format)}>{format === "pdf" ? "Скачать PDF" : format === "md" ? "Markdown" : "JSON"}</Button>)}</div>
      </section>
      <div className="batch-summary-stats">
        <div><span>Документов</span><strong>{summary.documents_done} <small>из {summary.documents_total}</small></strong><span>Ошибок проверки: {summary.documents_failed}</span></div>
        <div><span>Серьёзных нарушений</span><strong className="count-red">{summary.violations_red}</strong><span>Исправьте в первую очередь</span></div>
        <div><span>Замечаний</span><strong className="count-amber">{summary.violations_yellow}</strong><span>Требуют внимания</span></div>
      </div>
      {!summary.complete && <Alert type="info" showIcon title={`Проверено ${summary.documents_done} из ${summary.documents_total} — страница обновится сама`} />}
      {summary.documents.some((item) => item.ocr) && <Alert type="warning" showIcon title="В комплекте есть сканы" description="Текст распознан автоматически. Возможны ошибки: проверьте находки по исходникам." />}
      <section className="batch-panel"><h2>Что исправить в первую очередь</h2>
        {summary.rules.length ? <ul className="batch-priorities">{summary.rules.map((rule) => <li key={rule.rule_id}><Tag color={rule.severity === "red" ? "red" : "gold"}>{rule.severity === "red" ? "Серьёзное" : "Замечание"}</Tag><strong>{rule.title}</strong><span>Документов: {rule.documents}</span></li>)}</ul>
          : <p>{summary.complete ? "В готовых отчётах нарушений не найдено. Проверьте, нет ли документов с ошибкой обработки." : "Приоритеты появятся после проверки документов."}</p>}
      </section>
      <section className="batch-panel"><h2>Документы комплекта</h2>
        {!summary.documents.length ? <Empty description="В комплект ещё не загружены документы" /> : <div className="batch-documents">
          <div className="batch-document-head"><span>Документ / назначение</span><span>Результат</span><span>Отчёт</span></div>
          {summary.documents.map((item, index) => <article className="batch-document" key={item.document_id}>
            <div><h3>{item.filename || `Документ ${index + 1}`}</h3><p>{PURPOSE_LABELS[item.purpose]}</p>{item.ocr && <Tag>Скан (OCR)</Tag>}</div>
            <div className="batch-document-result"><DocumentResult item={item} /></div>
            <div>{item.status === "done" && item.report_id ? <Link aria-label={`Открыть отчёт: ${item.filename || `документ ${index + 1}`}`} to={`/reports/${encodeURIComponent(item.report_id)}`}>Открыть отчёт</Link> : <span className="batch-waiting">{item.status === "failed" ? "Отчёт недоступен" : "Ожидаем результат"}</span>}</div>
          </article>)}
        </div>}
      </section>
    </>}
    <div className="batch-summary-footer"><Link to="/">Проверить другие документы</Link><Link to="/history">История проверок</Link></div>
  </div>;
}
