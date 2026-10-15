import { Alert, Card, Descriptions, Space, Tag, Typography } from "antd";
import type { DescriptionsProps } from "antd";
import { useParams } from "react-router-dom";

import type { DocPurpose, Overall, Report } from "../api/types";
import mockReport from "../mocks/report.example.json";

// Пока бэкенд не готов (T-009), страница показывает мок-отчёт из mocks/report.example.json.
const report = mockReport as Report;

const PURPOSE_LABELS: Record<DocPurpose, string> = {
  internal: "Внутренний документ",
  publication: "Для публикации",
  contract: "Договор с контрагентом",
  international: "Международный контракт",
};

const OVERALL: Record<Overall, { color: string; label: string }> = {
  red: { color: "#ff4d4f", label: "Есть серьёзные нарушения" },
  yellow: { color: "#faad14", label: "Есть замечания" },
  green: { color: "#52c41a", label: "Нарушений не найдено" },
};

function TrafficLight({ value }: { value: Overall }) {
  const lamps: Overall[] = ["red", "yellow", "green"];
  return (
    <Space size="middle" aria-label={`Итог проверки: ${OVERALL[value].label}`}>
      {lamps.map((lamp) => (
        <span
          key={lamp}
          style={{
            display: "inline-block",
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: OVERALL[lamp].color,
            opacity: lamp === value ? 1 : 0.15,
          }}
        />
      ))}
    </Space>
  );
}

// TODO T-006 (Алиса): загрузка getReport(reportId) и getReportText(reportId),
// таблица нарушений с цитатами и ссылками на статьи, подсветка находок в тексте, чек-лист.
export default function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const redCount = report.violations.filter((v) => v.severity === "red").length;
  const yellowCount = report.violations.length - redCount;
  const entityCount = Object.values(report.stats).reduce((sum, n) => sum + n, 0);
  const details: DescriptionsProps["items"] = [
    { key: "file", label: "Документ", children: report.filename ?? "Текстовый запрос" },
    { key: "purpose", label: "Назначение", children: PURPOSE_LABELS[report.purpose] },
    { key: "entities", label: "Найдено чувствительных данных", children: entityCount },
    {
      key: "violations",
      label: "Нарушения",
      children: `серьёзных — ${redCount}, замечаний — ${yellowCount}`,
    },
  ];

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        title={`Показан пример отчёта (запрошен отчёт «${reportId ?? ""}»)`}
      />
      <Card>
        <Typography.Title level={2}>Результат проверки</Typography.Title>
        <Space size="large" align="center" wrap>
          <TrafficLight value={report.overall} />
          <Tag color={OVERALL[report.overall].color} style={{ fontSize: 16, padding: "4px 12px" }}>
            {OVERALL[report.overall].label}
          </Tag>
        </Space>
        <Descriptions column={1} style={{ marginTop: 24 }} items={details} />
      </Card>
    </Space>
  );
}
