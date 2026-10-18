import { List, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { Evidence, LawRef, Violation } from "../api/types";
import { SEVERITY_LABELS } from "./labels";

interface ViolationsTableProps {
  violations: Violation[];
}

function evidenceLocation(evidence: Evidence): string {
  const parts: string[] = [];
  if (evidence.page !== null) parts.push(`стр. ${evidence.page}`);
  if (evidence.section) parts.push(evidence.section);
  return parts.join(" · ") || "Место не указано";
}

function LawReference({ law }: { law: LawRef }) {
  const label = `${law.law}, ${law.article}`;
  if (law.url) {
    return (
      <Typography.Link href={law.url} target="_blank" rel="noreferrer">
        {label}
      </Typography.Link>
    );
  }
  return <Typography.Text>{label}</Typography.Text>;
}

export default function ViolationsTable({ violations }: ViolationsTableProps) {
  const sortedViolations = [...violations].sort((a, b) => {
    if (a.severity === b.severity) return 0;
    return a.severity === "red" ? -1 : 1;
  });

  const columns: TableColumnsType<Violation> = [
    {
      title: "Уровень",
      dataIndex: "severity",
      key: "severity",
      width: 140,
      render: (severity: Violation["severity"]) => (
        <Tag color={severity === "red" ? "red" : "gold"}>
          {SEVERITY_LABELS[severity]}
        </Tag>
      ),
    },
    {
      title: "Нарушение",
      dataIndex: "title",
      key: "title",
      width: 260,
      render: (title: string) => <Typography.Text strong>{title}</Typography.Text>,
    },
    {
      title: "Закон",
      dataIndex: "law_refs",
      key: "law_refs",
      render: (lawRefs: LawRef[]) => (
        <List
          size="small"
          split={false}
          dataSource={lawRefs}
          renderItem={(law, index) => (
            <List.Item key={`${law.law}-${law.article}-${index}`} style={{ paddingBlock: 2 }}>
              <LawReference law={law} />
            </List.Item>
          )}
        />
      ),
    },
    {
      title: "Рекомендация",
      dataIndex: "recommendation",
      key: "recommendation",
      render: (recommendation: string) => recommendation,
    },
  ];

  return (
    <Table<Violation>
      rowKey="rule_id"
      columns={columns}
      dataSource={sortedViolations}
      pagination={{ pageSize: 5, hideOnSinglePage: true }}
      scroll={{ x: 900 }}
      locale={{ emptyText: "Нарушений не найдено" }}
      expandable={{
        expandedRowRender: (record) =>
          record.evidence.length > 0 ? (
            <List
              size="small"
              dataSource={record.evidence}
              renderItem={(evidence, index) => (
                <List.Item key={`${evidence.start}-${evidence.end}-${index}`}>
                  <div style={{ width: "100%" }}>
                    <Typography.Text>{evidence.quote}</Typography.Text>
                    <br />
                    <Typography.Text type="secondary">
                      {evidenceLocation(evidence)}
                    </Typography.Text>
                  </div>
                </List.Item>
              )}
            />
          ) : (
            <Typography.Text type="secondary">Цитаты не указаны.</Typography.Text>
          ),
        rowExpandable: (record) => record.evidence.length > 0,
      }}
    />
  );
}
