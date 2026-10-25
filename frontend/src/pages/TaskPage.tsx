import { useEffect, useState } from "react";
import { Alert, Button, Card, Progress, Result, Steps, Typography } from "antd";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, getTask } from "../api/client";
import type { Stage, TaskInfo } from "../api/types";

const STAGES: { stage: Stage; title: string }[] = [
  { stage: "extracting", title: "Извлечение текста" },
  { stage: "detecting", title: "Поиск данных" },
  { stage: "masking", title: "Маскирование" },
  { stage: "checking", title: "Проверка по правилам" },
  { stage: "reporting", title: "Сборка отчёта" },
];

export default function TaskPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [missing, setMissing] = useState(false);
  const [networkErrors, setNetworkErrors] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!taskId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    async function poll() {
      try {
        const next = await getTask(taskId!);
        if (!active) return;
        failures = 0;
        setNetworkErrors(0);
        setErrorMessage(null);
        setTask(next);
        if (next.status === "done" && next.report_id) {
          navigate(`/reports/${encodeURIComponent(next.report_id)}`, { replace: true });
          return;
        }
        if (next.status === "failed") return;
      } catch (error: unknown) {
        if (!active) return;
        if (error instanceof ApiError && error.status === 404) {
          setMissing(true);
          return;
        }
        if (error instanceof ApiError) {
          setErrorMessage(error.message);
        } else {
          failures += 1;
          setNetworkErrors(failures);
          setErrorMessage("Нет соединения с сервером. Пробуем подключиться снова.");
          if (failures >= 5) return;
        }
      }
      if (active) timer = setTimeout(poll, 1500);
    }

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [taskId, navigate]);

  if (missing || !taskId) {
    return <Result status="404" title="Проверка не найдена" extra={<Button onClick={() => navigate("/")}>На главную</Button>} />;
  }

  const current = task?.stage ? STAGES.findIndex((item) => item.stage === task.stage) : 0;
  return (
    <div className="page-container">
      <div className="page-eyebrow">Гриф / Проверка документа</div>
      <header className="page-heading">
        <h1>{task?.status === "failed" ? "Не удалось проверить документ" : "Документ проверяется"}</h1>
        <p>Номер проверки: {taskId}</p>
      </header>
      <Card>
      {task?.status !== "failed" && (
        <Typography.Paragraph>Этапы обновляются автоматически. Можно оставить эту страницу открытой до появления отчёта.</Typography.Paragraph>
      )}
      <Steps current={Math.max(0, current)} items={STAGES.map(({ title }) => ({ title }))} responsive />
      <Progress percent={task?.progress ?? 0} status={task?.status === "failed" ? "exception" : "active"} style={{ marginTop: 24 }} />
      {task?.status === "failed" && (
        <Alert type="error" showIcon title="Проверка не завершена" description={task.error ?? "Попробуйте ещё раз."} action={<Button onClick={() => navigate("/")}>Проверить другой документ</Button>} />
      )}
      {errorMessage && task?.status !== "failed" && (
        <Alert type="warning" showIcon title={errorMessage} description={networkErrors >= 5 ? "Не удалось восстановить соединение. Обновите страницу, чтобы повторить попытку." : undefined} style={{ marginTop: 16 }} />
      )}
      {networkErrors >= 5 && <Button style={{ marginTop: 12 }} onClick={() => window.location.reload()}>Обновить страницу</Button>}
      </Card>
    </div>
  );
}
