import { Card, Typography } from "antd";
import { useParams } from "react-router-dom";

// TODO T-006 (Алиса): опрос getTask() раз в 1–2 с, прогресс по этапам (Stage),
// при status="done" — переход на /reports/:reportId, при "failed" — понятное сообщение.
export default function TaskPage() {
  const { taskId } = useParams<{ taskId: string }>();

  return (
    <Card>
      <Typography.Title level={2}>Документ проверяется</Typography.Title>
      <Typography.Paragraph type="secondary">Номер проверки: {taskId}</Typography.Paragraph>
    </Card>
  );
}
