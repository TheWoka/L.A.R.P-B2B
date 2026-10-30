import { useState } from "react";
import { Button, Card, Grid, List, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { Evidence, LawRef, Violation } from "../api/types";
import { SEVERITY_LABELS } from "./labels";

interface ViolationsTableProps {
  violations: Violation[];
  onSelectEvidence?: (evidence: Evidence) => void;
  onShowLaw?: (law: LawRef) => void;
}

function evidenceLocation(evidence: Evidence): string | null {
  const parts: string[] = [];
  if (evidence.page !== null) parts.push(`стр. ${evidence.page}`);
  if (evidence.section) parts.push(evidence.section);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function LawReference({ law, onShow }: { law: LawRef; onShow?: (law: LawRef) => void }) {
  const label = `${law.law}, ${law.article}`;
  if (onShow) return <Button type="link" className="law-reference" onClick={() => onShow(law)}>{label}</Button>;
  if (law.url) {
    return (
      <Typography.Link className="law-reference" href={law.url} target="_blank" rel="noreferrer">
        {label}
      </Typography.Link>
    );
  }
  return <Typography.Text className="law-reference">{label}</Typography.Text>;
}

function EvidenceItems({ evidence, onSelectEvidence }: { evidence: Evidence[]; onSelectEvidence?: (item: Evidence) => void }) {
  if (evidence.length === 0) return <Typography.Text type="secondary">Цитаты не указаны.</Typography.Text>;
  return (
    <List
      size="small"
      header={<Typography.Text type="secondary">Где найдено — фрагменты, из-за которых сработало правило. Нажмите, чтобы показать место в тексте.</Typography.Text>}
      dataSource={evidence}
      renderItem={(item, index) => {
        const location = evidenceLocation(item);
        return (
          <List.Item key={`${item.start}-${item.end}-${index}`}>
            <div style={{ width: "100%", minWidth: 0, overflowWrap: "anywhere" }}>
              {onSelectEvidence ? (
                <Button type="link" style={{ padding: 0, height: "auto", textAlign: "left", whiteSpace: "normal", overflowWrap: "anywhere" }} onClick={() => onSelectEvidence(item)}>
                  {item.quote || "Показать фрагмент в тексте"}
                </Button>
              ) : <Typography.Text style={{ overflowWrap: "anywhere" }}>{item.quote}</Typography.Text>}
              {location && <><br /><Typography.Text type="secondary">{location}</Typography.Text></>}
            </div>
          </List.Item>
        );
      }}
    />
  );
}

function ViolationCard({ violation, onSelectEvidence, onShowLaw }: { violation: Violation } & Omit<ViolationsTableProps, "violations">) {
  const [expanded, setExpanded] = useState(false);
  return (
    <Card size="small" style={{ width: "100%", minWidth: 0 }}>
      <Space orientation="vertical" size="small" style={{ width: "100%", minWidth: 0 }}>
        <Tag color={violation.severity === "red" ? "red" : "gold"} style={{ alignSelf: "flex-start" }}>
          {SEVERITY_LABELS[violation.severity]}
        </Tag>
        <Typography.Text strong style={{ display: "block", fontSize: 16, overflowWrap: "anywhere" }}>{violation.title}</Typography.Text>
        <Space wrap size={[4, 4]} className="law-references">
          {violation.law_refs.map((law, index) => <LawReference key={`${law.law}-${law.article}-${index}`} law={law} onShow={onShowLaw} />)}
        </Space>
        <Button type="link" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} style={{ padding: 0, alignSelf: "flex-start" }}>
          {expanded ? "Скрыть подробности" : "Подробнее"}
        </Button>
        {expanded && (
          <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
            <Typography.Paragraph type="secondary">Источник проверки: {violation.source === "llm" ? "нейросеть" : "правило"}.</Typography.Paragraph>
            <EvidenceItems evidence={violation.evidence} onSelectEvidence={onSelectEvidence} />
            <Typography.Paragraph style={{ marginBottom: 0 }}><strong>Рекомендация:</strong> {violation.recommendation}</Typography.Paragraph>
          </div>
        )}
      </Space>
    </Card>
  );
}

export default function ViolationsTable({ violations, onSelectEvidence, onShowLaw }: ViolationsTableProps) {
  const screens = Grid.useBreakpoint();
  const sortedViolations = [...violations].sort((a, b) => {
    if (a.severity === b.severity) return 0;
    return a.severity === "red" ? -1 : 1;
  });

  if (!screens.md) {
    return (
      <List<Violation>
        dataSource={sortedViolations}
        pagination={{ pageSize: 5, hideOnSinglePage: true }}
        locale={{ emptyText: "Нарушений не найдено" }}
        renderItem={(violation) => (
          <List.Item key={violation.rule_id} style={{ display: "block", padding: "0 0 12px", minWidth: 0 }}>
            <ViolationCard violation={violation} onSelectEvidence={onSelectEvidence} onShowLaw={onShowLaw} />
          </List.Item>
        )}
      />
    );
  }

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
              <LawReference law={law} onShow={onShowLaw} />
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
        expandedRowRender: (record) => <EvidenceItems evidence={record.evidence} onSelectEvidence={onSelectEvidence} />,
        rowExpandable: (record) => record.evidence.length > 0,
      }}
    />
  );
}
