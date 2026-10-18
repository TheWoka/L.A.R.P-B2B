import { Alert, Card, Col, Descriptions, Row, Space, Statistic, Tag, Typography } from "antd";
import { useParams } from "react-router-dom";

import type { DocPurpose, Overall, Report } from "../api/types";
import Checklist from "../components/Checklist";
import EntitiesTable from "../components/EntitiesTable";
import ViolationsTable from "../components/ViolationsTable";
import { PURPOSE_LABELS } from "../components/labels";
import mockReport from "../mocks/report.example.json";

// В T-006 страница использует демонстрационный отчёт; подключение GET /reports будет позже.
const report = mockReport as Report;

const OVERALL: Record<Overall, { color: string; label: string; description: string }> = {
  red: {
    color: "#cf1322",
    label: "Есть серьёзные нарушения",
    description: "Перед использованием документа необходимо устранить серьёзные нарушения.",
  },
  yellow: {
    color: "#d48806",
    label: "Есть замечания",
    description: "Проверьте замечания и рекомендации перед использованием документа.",
  },
  green: {
    color: "#389e0d",
    label: "Нарушений не найдено",
    description: "По результатам проверки нарушений не обнаружено.",
  },
};

function TrafficLight({ value }: { value: Overall }) {
  const lamps: Overall[] = ["red", "yellow", "green"];
  const colors: Record<Overall, string> = {
    red: "#cf1322",
    yellow: "#d48806",
    green: "#389e0d",
  };

  return (
    <Space size="middle" aria-label={`Итог проверки: ${OVERALL[value].label}`}>
      {lamps.map((lamp) => (
        <span
          key={lamp}
          title={lamp === value ? OVERALL[lamp].label : undefined}
          style={{
            display: "inline-block",
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: colors[lamp],
            opacity: lamp === value ? 1 : 0.16,
            boxShadow: lamp === value ? `0 0 0 4px ${colors[lamp]}22` : "none",
          }}
        />
      ))}
    </Space>
  );
}

export default function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const redCount = report.violations.filter((violation) => violation.severity === "red").length;
  const yellowCount = report.violations.filter((violation) => violation.severity === "yellow").length;
  const entityCount = Object.values(report.stats).reduce((sum, count) => sum + count, 0);
  const overall = OVERALL[report.overall];

  const details = [
    { key: "file", label: "Документ", children: report.filename ?? "Текстовый запрос" },
    { key: "purpose", label: "Назначение", children: PURPOSE_LABELS[report.purpose as DocPurpose] },
    { key: "created", label: "Дата проверки", children: new Date(report.created_at).toLocaleString("ru-RU") },
    { key: "duration", label: "Время обработки", children: `${(report.processing_ms / 1000).toLocaleString("ru-RU")} с` },
  ];

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        title={`Демонстрационный отчёт${reportId ? ` · ${reportId}` : ""}`}
        description="На этом этапе показаны примерные результаты. Данные взяты из демонстрационного отчёта."
      />

      <Card>
        <Typography.Title level={2} style={{ marginTop: 0 }}>
          Результат проверки
        </Typography.Title>
        <Space size="large" align="center" wrap>
          <TrafficLight value={report.overall} />
          <Tag color={overall.color} style={{ fontSize: 16, padding: "4px 12px" }}>
            {overall.label}
          </Tag>
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          {overall.description}
        </Typography.Paragraph>
        <Descriptions column={{ xs: 1, sm: 2, md: 2 }} items={details} />
        <Row gutter={[12, 12]} style={{ marginTop: 20 }}>
          <Col xs={12} md={8}>
            <Card size="small">
              <Statistic title="Найдено данных" value={entityCount} />
            </Card>
          </Col>
          <Col xs={12} md={8}>
            <Card size="small">
              <Statistic title="Серьёзные нарушения" value={redCount} valueStyle={{ color: "#cf1322" }} />
            </Card>
          </Col>
          <Col xs={12} md={8}>
            <Card size="small">
              <Statistic title="Замечания" value={yellowCount} valueStyle={{ color: "#d48806" }} />
            </Card>
          </Col>
        </Row>
      </Card>

      <Card title="Чек-лист документа">
        <Checklist items={report.checklist} />
      </Card>

      <Card title={`Нарушения (${report.violations.length})`}>
        <ViolationsTable violations={report.violations} />
      </Card>

      <Card title={`Найденные данные (${report.entities.length})`}>
        <Typography.Paragraph type="secondary">
          Чувствительные данные отображаются в замаскированном виде.
        </Typography.Paragraph>
        <EntitiesTable entities={report.entities} stats={report.stats} />
      </Card>
    </Space>
  );
}
