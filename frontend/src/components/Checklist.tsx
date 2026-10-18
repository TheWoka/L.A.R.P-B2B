import { Card, Col, Row, Space, Tag, Typography } from "antd";

import type { ChecklistItem } from "../api/types";
import { CHECKLIST_CATEGORY_LABELS, CHECKLIST_STATUS_LABELS } from "./labels";

interface ChecklistProps {
  items: ChecklistItem[];
}

const STATUS_COLORS: Record<ChecklistItem["status"], string> = {
  ok: "success",
  missing: "error",
  not_required: "default",
};

const STATUS_SYMBOLS: Record<ChecklistItem["status"], string> = {
  ok: "✓",
  missing: "!",
  not_required: "—",
};

export default function Checklist({ items }: ChecklistProps) {
  return (
    <Row gutter={[12, 12]}>
      {items.map((item) => (
        <Col xs={24} md={8} key={item.category}>
          <Card size="small" style={{ height: "100%" }}>
            <Space orientation="vertical" size={8} style={{ width: "100%" }}>
              <Typography.Text strong>
                {CHECKLIST_CATEGORY_LABELS[item.category]}
              </Typography.Text>
              <Tag color={STATUS_COLORS[item.status]}>
                {STATUS_SYMBOLS[item.status]} {CHECKLIST_STATUS_LABELS[item.status]}
              </Tag>
              <Typography.Paragraph style={{ marginBottom: 0 }}>
                {item.comment}
              </Typography.Paragraph>
              {item.evidence ? (
                <Typography.Text type="secondary">
                  {item.evidence.page !== null ? `Страница ${item.evidence.page}` : ""}
                  {item.evidence.page !== null && item.evidence.section ? " · " : ""}
                  {item.evidence.section ?? ""}
                </Typography.Text>
              ) : null}
            </Space>
          </Card>
        </Col>
      ))}
    </Row>
  );
}
