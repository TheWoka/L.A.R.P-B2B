import { Card, Typography } from "antd";

// TODO T-006 (Алиса): таблица правил из listRules(), включение/выключение,
// добавление и редактирование (createRule, updateRule, deleteRule).
export default function RulesPage() {
  return (
    <Card>
      <Typography.Title level={2}>Правила проверки</Typography.Title>
      <Typography.Paragraph type="secondary">
        Здесь будет список правил: какие законы и внутренние политики проверяются и насколько
        серьёзно каждое нарушение.
      </Typography.Paragraph>
    </Card>
  );
}
