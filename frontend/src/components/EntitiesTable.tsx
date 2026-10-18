import { Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import type { Entity, EntityType } from "../api/types";
import { ENTITY_TYPE_LABELS } from "./labels";

interface EntitiesTableProps {
  entities: Entity[];
  stats: Record<string, number>;
}

function entityLocation(entity: Entity): string {
  const parts: string[] = [];
  if (entity.page !== null) parts.push(`стр. ${entity.page}`);
  if (entity.section) parts.push(entity.section);
  return parts.join(" · ") || "Не указано";
}

export default function EntitiesTable({ entities, stats }: EntitiesTableProps) {
  const columns: TableColumnsType<Entity> = [
    {
      title: "Тип",
      dataIndex: "type",
      key: "type",
      width: 220,
      render: (type: EntityType) => ENTITY_TYPE_LABELS[type] ?? type,
    },
    {
      title: "Значение",
      dataIndex: "value_masked",
      key: "value_masked",
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "Где",
      key: "location",
      width: 260,
      render: (_value: unknown, entity: Entity) => entityLocation(entity),
    },
  ];

  const statEntries = Object.entries(stats).sort(([a], [b]) => {
    const labelA = ENTITY_TYPE_LABELS[a as EntityType] ?? a;
    const labelB = ENTITY_TYPE_LABELS[b as EntityType] ?? b;
    return labelA.localeCompare(labelB, "ru");
  });

  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap size={[8, 8]}>
        {statEntries.map(([type, count]) => (
          <Tag key={type}>
            {ENTITY_TYPE_LABELS[type as EntityType] ?? type}: {count}
          </Tag>
        ))}
      </Space>
      <Table<Entity>
        rowKey={(entity) => `${entity.type}-${entity.start}-${entity.end}`}
        columns={columns}
        dataSource={entities}
        pagination={{ pageSize: 5, hideOnSinglePage: true }}
        scroll={{ x: 650 }}
        locale={{ emptyText: "Чувствительные данные не найдены" }}
      />
    </Space>
  );
}
