import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Input, message, Segmented, Skeleton, Space, Switch, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import { ApiError, listRules, updateRule } from "../api/client";
import type { Rule, Severity } from "../api/types";
import { PURPOSE_LABELS, SEVERITY_LABELS } from "../components/labels";
import RuleDetails from "../components/RuleDetails";

type SeverityFilter = "all" | Severity;

function errorText(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "Попробуйте ещё раз.";
}

export default function RulesPage() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [messageApi, messageContext] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setRules(await listRules());
    } catch (error) {
      setLoadError(errorText(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const filteredRules = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ru-RU");
    return rules.filter((rule) =>
      (severity === "all" || rule.severity === severity) &&
      (!normalized || `${rule.id} ${rule.title}`.toLocaleLowerCase("ru-RU").includes(normalized)),
    );
  }, [query, rules, severity]);

  const selectedRule = rules.find((rule) => rule.id === selectedId) ?? null;

  async function toggleRule(rule: Rule) {
    if (pendingId !== null) return;
    setPendingId(rule.id);
    try {
      const updated = await updateRule(rule.id, { ...rule, enabled: !rule.enabled });
      setRules((current) => current.map((item) => item.id === rule.id ? updated : item));
      messageApi.success(updated.enabled ? "Правило включено" : "Правило выключено");
    } catch (error) {
      messageApi.error(errorText(error));
    } finally {
      setPendingId(null);
    }
  }

  const columns: TableColumnsType<Rule> = [
    {
      title: "ID", dataIndex: "id", key: "id", width: 210,
      render: (id: string) => <Typography.Text code>{id}</Typography.Text>,
    },
    { title: "Название", dataIndex: "title", key: "title", width: 260 },
    {
      title: "Уровень", dataIndex: "severity", key: "severity", width: 150,
      render: (value: Severity) => (
        <Tag color={value === "red" ? "red" : "gold"}>{SEVERITY_LABELS[value]}</Tag>
      ),
    },
    {
      title: "Законы", dataIndex: "law_refs", key: "law_refs", width: 230,
      render: (refs: Rule["law_refs"]) => refs.length
        ? refs.map((ref, index) => <div key={`${ref.law}-${ref.article}-${index}`}>{ref.law}, {ref.article}</div>)
        : "—",
    },
    {
      title: "Назначение", dataIndex: "applies_to", key: "applies_to", width: 230,
      render: (purposes: Rule["applies_to"]) => purposes.length
        ? purposes.map((purpose) => <div key={purpose}>{PURPOSE_LABELS[purpose]}</div>)
        : "Все документы",
    },
    {
      title: "Включено", dataIndex: "enabled", key: "enabled", width: 125,
      render: (_enabled: boolean, rule: Rule) => (
        <span onClick={(event) => event.stopPropagation()}>
          <Switch
            checked={rule.enabled}
            loading={pendingId === rule.id}
            disabled={pendingId !== null && pendingId !== rule.id}
            onChange={() => { void toggleRule(rule); }}
            aria-label={`${rule.enabled ? "Выключить" : "Включить"} правило ${rule.id}`}
          />
        </span>
      ),
    },
  ];

  return (
    <>
      {messageContext}
      <Card>
        <Typography.Title level={2} style={{ marginTop: 0 }}>Правила проверки</Typography.Title>
        <Typography.Paragraph type="secondary">
          Выключенные правила не применяются к новым проверкам.
        </Typography.Paragraph>

        {loadError ? (
          <Alert
            type="error"
            showIcon
            title="Не удалось загрузить правила"
            description={loadError}
            action={<Button size="small" onClick={() => { void load(); }}>Повторить</Button>}
          />
        ) : (
          <>
            <Space wrap style={{ marginBottom: 16 }}>
              <Input.Search
                placeholder="Поиск по ID или названию"
                aria-label="Поиск правил"
                allowClear
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                style={{ width: 300, maxWidth: "100%" }}
              />
              <Segmented<SeverityFilter>
                aria-label="Фильтр по уровню"
                options={[
                  { label: "Все", value: "all" },
                  { label: "Серьёзные", value: "red" },
                  { label: "Замечания", value: "yellow" },
                ]}
                value={severity}
                onChange={setSeverity}
              />
            </Space>
            {loading && rules.length === 0 ? <Skeleton active paragraph={{ rows: 8 }} /> : (
              <Table<Rule>
                rowKey="id"
                columns={columns}
                dataSource={filteredRules}
                loading={loading}
                scroll={{ x: 1150 }}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                locale={{ emptyText: "Правила не найдены" }}
                onRow={(rule) => ({
                  onClick: () => setSelectedId(rule.id),
                  style: { cursor: "pointer" },
                })}
              />
            )}
          </>
        )}
      </Card>
      <RuleDetails rule={selectedRule} onClose={() => setSelectedId(null)} />
    </>
  );
}
