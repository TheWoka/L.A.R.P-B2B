import { useState } from "react";
import { Button, Popconfirm } from "antd";
import { useNavigate } from "react-router-dom";

import { PURPOSE_LABELS } from "../components/labels";
import { OVERALL } from "../components/TrafficLight";
import { clearHistory, listHistory, removeFromHistory } from "../lib/history";

export default function HistoryPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState(listHistory);

  return (
    <div className="page-container history-page">
      <header className="page-heading">
        <h1>История проверок</h1>
        <p>История хранится только в этом браузере. Сами отчёты сервер удаляет через срок хранения (по умолчанию 7 дней) — после этого запись останется, но открыть отчёт будет нельзя.</p>
      </header>

      {items.length > 0 ? (
        <>
          <div className="history-toolbar">
            <span>Сохранено: {items.length} из 50</span>
            <Popconfirm
              title="Очистить историю проверок?"
              description="Все записи в этом браузере будут убраны из истории."
              okText="Очистить"
              cancelText="Отмена"
              onConfirm={() => { clearHistory(); setItems(listHistory()); }}
            >
              <Button>Очистить историю</Button>
            </Popconfirm>
          </div>
          <ul className="history-list">
            {items.map((item) => (
              <li key={item.report_id} className="history-card">
                <span className="history-status" style={{ background: OVERALL[item.overall].color }} role="img" aria-label={OVERALL[item.overall].label} title={OVERALL[item.overall].label} />
                <div className="history-details">
                  <h2>{item.kind === "text" ? "Текстовый запрос" : `Документ${item.ext ? ` ${item.ext}` : ""}`}</h2>
                  <p>{PURPOSE_LABELS[item.purpose]}</p>
                  <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("ru-RU", { dateStyle: "long", timeStyle: "short" })}</time>
                  <div className="history-counts">серьёзных: {item.red}, замечаний: {item.yellow}</div>
                </div>
                <div className="history-actions">
                  <Button type="primary" onClick={() => navigate(`/reports/${encodeURIComponent(item.report_id)}`)}>Открыть</Button>
                  <Button type="text" onClick={() => { removeFromHistory(item.report_id); setItems(listHistory()); }}>Убрать из истории</Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div className="history-empty">
          <span className="history-empty-symbol" aria-hidden="true">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
          </span>
          <h2>Здесь появятся ваши проверки</h2>
          <p>Проверьте документ или текст, чтобы вернуться к результату позже.</p>
          <Button type="primary" size="large" onClick={() => navigate("/")}>Проверить документ</Button>
        </div>
      )}
    </div>
  );
}
