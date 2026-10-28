import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Alert, Button, Card, Col, Progress, Row, Skeleton, Statistic, Table, Tabs, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";

import { ApiError, getMetrics } from "../api/client";
import type { EntityType, Metrics, TypeMetrics } from "../api/types";
import { ENTITY_TYPE_LABELS } from "../components/labels";
import exampleMetrics from "../mocks/metrics.example.json";

type LoadedMetrics = { data: Metrics; example: boolean };
type Corpus = "reference" | "blind";

const CORPUS_DESCRIPTION: Record<Corpus, string> = {
  reference: "Набор составлен вместе с разработкой детектора, на нём мы проверяем, что ничего не сломали.",
  blind: "Документы написал участник команды, который не видел устройство детектора, и под этот набор детектор не настраивается. Это самая честная оценка: так система поведёт себя на чужих документах.",
};

function percentage(value: number): string {
  return `${(value * 100).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

function MetricCard({ title, value, target }: { title: string; value: number; target: number }) {
  const passed = value >= target;
  return (
    <Card className="quality-metric" style={{ height: "100%", borderColor: passed ? undefined : "#b2373d" }}>
      <Typography.Text strong>{title}</Typography.Text>
      <Statistic
        value={value * 100}
        precision={1}
        suffix="%"
        styles={{ content: { color: passed ? "#1c1c1e" : "#b2373d" } }}
      />
      <Typography.Text className={passed ? "metric-status is-passed" : "metric-status is-below"}>
        {passed ? "Цель достигнута" : "Ниже цели"} · порог {Math.round(target * 100)}%
      </Typography.Text>
      <div style={{ position: "relative", marginTop: 14 }}>
        <Progress percent={Math.round(value * 100)} showInfo={false} strokeColor={passed ? "#087ff5" : "#b2373d"} />
        <span
          aria-hidden="true"
          style={{ position: "absolute", left: `${target * 100}%`, top: 3, height: 10, borderLeft: "2px solid #1c1c1e" }}
        />
      </div>
      <Typography.Text type="secondary">Чёрная отметка на шкале — порог {Math.round(target * 100)}%.</Typography.Text>
    </Card>
  );
}

const columns: TableColumnsType<TypeMetrics> = [
  {
    title: "Тип находки",
    dataIndex: "type",
    key: "type",
    render: (value: string) => ENTITY_TYPE_LABELS[value as EntityType] ?? value,
  },
  { title: "Точность", dataIndex: "precision", key: "precision", render: (value: number) => percentage(value) },
  {
    title: "Полнота",
    dataIndex: "recall",
    key: "recall",
    render: (value: number) => value < 0.85 ? <Tag color="red">{percentage(value)}</Tag> : percentage(value),
  },
  { title: "F1", dataIndex: "f1", key: "f1", render: (value: number) => percentage(value) },
  { title: "Примеров", dataIndex: "support", key: "support", render: (value: number) => value.toLocaleString("ru-RU") },
];

function MetricsTab({ corpus }: { corpus: Corpus }) {
  const [loaded, setLoaded] = useState<LoadedMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestId, setRequestId] = useState(0);
  const [notCounted, setNotCounted] = useState(false);

  useEffect(() => {
    let active = true;
    (corpus === "blind" ? getMetrics("blind") : getMetrics()).then(
      (data) => { if (active) setLoaded({ data, example: false }); },
      (reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && (reason.status === 404 || reason.status === 501)) {
          if (corpus === "blind") setNotCounted(true);
          else setLoaded({ data: exampleMetrics, example: true });
        } else {
          setError(reason instanceof Error ? reason.message : "Попробуйте ещё раз.");
        }
      },
    ).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [corpus, requestId]);

  let content: ReactNode;
  if (loading) {
    content = <Skeleton active paragraph={{ rows: 10 }} />;
  } else if (error) {
    content = (
      <Alert
        type="error"
        showIcon
        title="Не удалось загрузить показатели качества"
        description={error}
        action={<Button size="small" onClick={() => { setError(null); setLoading(true); setRequestId((id) => id + 1); }}>Повторить</Button>}
      />
    );
  } else if (notCounted) {
    content = <Alert type="info" showIcon title="Слепой набор ещё не посчитан" description="Метрики появятся здесь после отдельного запуска оценки слепого набора." />;
  } else {
    content = loaded && <MetricsContent loaded={loaded} corpus={corpus} />;
  }

  return (
    <>
      <Typography.Paragraph>{CORPUS_DESCRIPTION[corpus]}</Typography.Paragraph>
      {content}
    </>
  );
}

function MetricsContent({ loaded, corpus }: { loaded: LoadedMetrics; corpus: Corpus }) {
  const { data, example } = loaded;
  return (
    <>
      {example && (
        <Alert
          type="info"
          showIcon
          title="Метрики ещё не посчитаны на сервере. Ниже — пример."
          style={{ marginBottom: 24 }}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <MetricCard title="Персональные данные: найдено" value={data.pd_recall} target={0.9} />
        </Col>
        <Col xs={24} md={12}>
          <MetricCard title="Коммерческая тайна: найдено" value={data.trade_secret_recall} target={0.85} />
        </Col>
      </Row>

      {corpus === "blind" && data.trade_secret_recall < 0.85 && (
        <Alert
          type="warning"
          showIcon
          title="Порог по коммерческой тайне пока не достигнут"
          description="На чужих формулировках коммерческая тайна находится хуже. Мы добавили проверку нейросетью и расширяем словари на отдельном наборе для настройки; слепой набор пересчитываем без изменений."
          style={{ marginTop: 16 }}
        />
      )}

      <Card style={{ marginTop: 16 }}>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={10}><Typography.Text type="secondary">Корпус</Typography.Text><div>{data.corpus}</div></Col>
          <Col xs={12} md={6}><Typography.Text type="secondary">Документов</Typography.Text><div>{data.documents.toLocaleString("ru-RU")}</div></Col>
          <Col xs={12} md={8}><Typography.Text type="secondary">Дата подсчёта</Typography.Text><div>{new Date(data.created_at).toLocaleDateString("ru-RU")}</div></Col>
        </Row>
      </Card>

      <Card title="Результаты по типам находок" style={{ marginTop: 16 }}>
        <Table<TypeMetrics>
          columns={columns}
          dataSource={data.by_type}
          rowKey="type"
          pagination={false}
          scroll={{ x: 640 }}
          size="middle"
        />
        <Typography.Text type="secondary">Красным отмечена полнота ниже 85%.</Typography.Text>
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="Как считаем" style={{ height: "100%" }}>
            <Typography.Paragraph>
              Находка засчитывается, если тип совпал и она покрывает не меньше половины эталонного фрагмента.
            </Typography.Paragraph>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>Правило сравнения: {data.matching}</Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Почему этим цифрам можно верить" style={{ height: "100%" }}>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              <li>Ручной набор документов содержит сложные случаи и ловушки.</li>
              <li>Независимый корпус генератора содержит 210 документов.</li>
              <li>Слепой набор от другого автора: 12 документов, 150 размеченных фрагментов — вкладка «Слепой набор».</li>
            </ul>
          </Card>
        </Col>
      </Row>
    </>
  );
}

export default function QualityPage() {
  return (
    <div className="page-container quality-page">
      <header className="page-heading">
        <h1>Качество проверки</h1>
        <p>Сколько важных сведений находит система и как мы это проверяем.</p>
      </header>
      <Tabs
        className="upload-tabs"
        defaultActiveKey="reference"
        items={[
          { key: "reference", label: "Эталонный набор", children: <MetricsTab corpus="reference" /> },
          { key: "blind", label: "Слепой набор (другой автор)", children: <MetricsTab corpus="blind" /> },
        ]}
      />
    </div>
  );
}
