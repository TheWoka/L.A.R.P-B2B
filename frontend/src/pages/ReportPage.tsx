import { useEffect, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Modal, Result, Row, Skeleton, Space, Statistic, Tabs, Tag, Typography } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, getReport, getReportText } from "../api/client";
import type { DocPurpose, Evidence, LawRef, Report, ReportText } from "../api/types";
import Checklist from "../components/Checklist";
import DocumentText from "../components/DocumentText";
import { getArticleText } from "../components/legalArticles";
import EntitiesTable from "../components/EntitiesTable";
import ViolationsTable from "../components/ViolationsTable";
import { PURPOSE_LABELS } from "../components/labels";
import TrafficLight, { OVERALL } from "../components/TrafficLight";
import mockReport from "../mocks/report.example.json";

export default function ReportPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const navigate = useNavigate();
  const [liveReport, setLiveReport] = useState<Report | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [documentText, setDocumentText] = useState<ReportText | null>(null);
  const [textError, setTextError] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [selectedLaw, setSelectedLaw] = useState<LawRef | null>(null);

  useEffect(() => {
    if (!reportId || reportId === "example") return;
    let active = true;
    void getReport(reportId).then(
      (result) => { if (active) setLiveReport(result); },
      (reason: unknown) => { if (active) setError(reason); },
    );
    void getReportText(reportId).then(
      (result) => { if (active) setDocumentText(result); },
      () => { if (active) setTextError(true); },
    );
    return () => { active = false; };
  }, [reportId]);

  if (reportId !== "example" && error instanceof ApiError && error.status === 404) {
    return <Result status="404" title="Отчёт не найден" extra={<Button onClick={() => navigate("/")}>На главную</Button>} />;
  }
  if (reportId !== "example" && error) {
    return <Alert type="error" showIcon title="Не удалось загрузить отчёт" description={error instanceof Error ? error.message : "Попробуйте обновить страницу."} />;
  }
  if (reportId !== "example" && !liveReport) return <Skeleton active paragraph={{ rows: 10 }} />;

  const report = reportId === "example" ? mockReport as Report : liveReport!;
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
    <div className="page-container report-page">
      <div className="page-eyebrow">ГРИФ / Отчёт</div>
      <Link className="report-back" to="/">← Все проверки</Link>
      <header className="page-heading">
        <h1>Результат проверки</h1>
        <p>{report.filename ?? "Текстовый запрос"} · Проверено {new Date(report.created_at).toLocaleDateString("ru-RU")}</p>
      </header>
      {reportId === "example" && <Alert type="info" title="Демонстрационный отчёт · данные вымышленные" style={{ marginBottom: 18 }} />}
      <Row className="report-summary" gutter={[14, 14]}>
        <Col xs={24} sm={8}><Card><Statistic title="Серьёзные риски" value={redCount} valueStyle={{ color: "#1c1c1e" }} /></Card></Col>
        <Col xs={24} sm={8}><Card><Statistic title="Замечания" value={yellowCount} /></Card></Col>
        <Col xs={24} sm={8}><Card><Statistic title="Найденные данные" value={entityCount} /></Card></Col>
      </Row>
      <Card style={{ marginTop: 16 }}>
        <Space size="middle" align="center" wrap>
          <TrafficLight value={report.overall} />
          <Tag color={overall.color}>{overall.label}</Tag>
          <Typography.Text type="secondary">{overall.description}</Typography.Text>
        </Space>
        {!report.llm_used && <Typography.Paragraph type="secondary" style={{ margin: "12px 0 0", fontSize: 12 }}>Смысловые проверки нейросетью не выполнялись (режим без LLM).</Typography.Paragraph>}
      </Card>
      <Tabs
        className="report-tabs"
        style={{ marginTop: 22 }}
        items={[
          { key: "violations", label: `Нарушения (${report.violations.length})`, children: (
            <Card title="Что требует внимания">
              {documentText && report.violations.some((violation) => violation.evidence.length > 0) && (
                <Typography.Paragraph type="secondary">Нажмите на цитату, чтобы увидеть её в тексте документа.</Typography.Paragraph>
              )}
              <ViolationsTable violations={report.violations} onSelectEvidence={documentText ? setSelectedEvidence : undefined} onShowLaw={setSelectedLaw} />
            </Card>
          ) },
          { key: "entities", label: `Найденные данные (${report.entities.length})`, children: (
            <Card title="Найденные данные">
              <Typography.Paragraph type="secondary">Чувствительные данные отображаются в замаскированном виде.</Typography.Paragraph>
              <EntitiesTable entities={report.entities} stats={report.stats} />
            </Card>
          ) },
        ]}
      />
      <h2 className="report-section-title">Чек-лист документа</h2>
      <Checklist items={report.checklist} />
      <Card title="О документе" style={{ marginTop: 24 }}><Descriptions column={{ xs: 1, sm: 2, md: 2 }} items={details} /></Card>
      {documentText && <div style={{ marginTop: 24 }}><DocumentText document={documentText} entities={report.entities} selected={selectedEvidence} /></div>}
      {textError && <Alert type="warning" title="Текст документа пока недоступен" style={{ marginTop: 20 }} />}
      <Button size="large" style={{ marginTop: 24 }} onClick={() => navigate("/")}>Проверить другой документ</Button>

      <Modal title={selectedLaw ? `${selectedLaw.law}, ${selectedLaw.article}` : "Норма закона"} open={selectedLaw !== null} onCancel={() => setSelectedLaw(null)} footer={<Button onClick={() => setSelectedLaw(null)}>Закрыть</Button>}>
        {selectedLaw?.title && <Typography.Paragraph strong>{selectedLaw.title}</Typography.Paragraph>}
        {selectedLaw && getArticleText(selectedLaw) ? (
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", maxHeight: "50vh", overflowY: "auto" }}>{getArticleText(selectedLaw)}</Typography.Paragraph>
        ) : <Typography.Paragraph type="secondary">Текст этой нормы не включён в каталог проекта.</Typography.Paragraph>}
        {selectedLaw?.url && <Typography.Link href={selectedLaw.url} target="_blank" rel="noreferrer">Открыть источник</Typography.Link>}
      </Modal>
    </div>
  );
}
