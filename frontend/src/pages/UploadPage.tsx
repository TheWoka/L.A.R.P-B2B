import { Card, Typography } from "antd";
import { Link } from "react-router-dom";

// TODO T-006 (Алиса): форма загрузки файла (.pdf, .docx, .xlsx до 50 МБ) и выбор назначения
// документа → uploadDocument() → переход на /tasks/:taskId. Вторая вкладка — проверка текста
// через submitQuery().
export default function UploadPage() {
  return (
    <Card>
      <Typography.Title level={2}>Проверка документа</Typography.Title>
      <Typography.Paragraph>
        Загрузите документ закупки — система найдёт персональные данные и коммерческую тайну,
        проверит грифы, согласования и сроки хранения и покажет, что нужно исправить.
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        Форма загрузки появится в задаче T-006. Пока можно открыть{" "}
        <Link to="/reports/example">пример отчёта</Link>.
      </Typography.Paragraph>
    </Card>
  );
}
