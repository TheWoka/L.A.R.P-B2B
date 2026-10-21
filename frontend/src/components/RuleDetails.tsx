import { Descriptions, Drawer, Space, Tag, Typography } from "antd";

import type { Rule } from "../api/types";
import { PURPOSE_LABELS, SEVERITY_LABELS } from "./labels";

interface RuleDetailsProps {
  rule: Rule | null;
  onClose: () => void;
}

export default function RuleDetails({ rule, onClose }: RuleDetailsProps) {
  return (
    <Drawer title="Подробнее" open={rule !== null} onClose={onClose} size="large">
      {rule && (
        <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
          <div>
            <Typography.Text code>{rule.id}</Typography.Text>
            <Typography.Title level={4} style={{ marginTop: 8 }}>{rule.title}</Typography.Title>
            <Tag color={rule.severity === "red" ? "red" : "gold"}>
              {SEVERITY_LABELS[rule.severity]}
            </Tag>
            <Tag color={rule.enabled ? "green" : "default"}>
              {rule.enabled ? "Включено" : "Выключено"}
            </Tag>
          </div>
          <Descriptions
            bordered
            column={1}
            items={[
              { key: "description", label: "Описание", children: rule.description },
              { key: "recommendation", label: "Рекомендация", children: rule.recommendation },
              {
                key: "laws", label: "Законы",
                children: rule.law_refs.length ? rule.law_refs.map((ref, index) => (
                  <div key={`${ref.law}-${ref.article}-${index}`}>
                    {ref.url ? (
                      <Typography.Link href={ref.url} target="_blank" rel="noreferrer">
                        {ref.law}, {ref.article}
                      </Typography.Link>
                    ) : `${ref.law}, ${ref.article}`}
                    {ref.title ? ` — ${ref.title}` : ""}
                  </div>
                )) : "—",
              },
              {
                key: "purpose", label: "Назначение",
                children: rule.applies_to.length
                  ? rule.applies_to.map((purpose) => PURPOSE_LABELS[purpose]).join(", ")
                  : "Все документы",
              },
            ]}
          />
          <div>
            <Typography.Title level={5}>Условие срабатывания</Typography.Title>
            <Typography.Paragraph code style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {JSON.stringify(rule.condition, null, 2)}
            </Typography.Paragraph>
          </div>
        </Space>
      )}
    </Drawer>
  );
}
