import { useEffect, useState } from "react";
import { Alert, Button, Card, Col, Descriptions, Grid, Modal, Result, Row, Skeleton, Space, Statistic, Tag, Typography } from "antd";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, exportUrl, getReport, getReportText, redactedUrl } from "../api/client";
import TransparencyPanel from "../components/TransparencyPanel";
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
  const screens = Grid.useBreakpoint();
  const narrow = !screens.md;
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
    { key: "file", label: "Документ", children: <span style={{ overflowWrap: "anywhere" }}>{report.filename ?? "Текстовый запрос"}</span> },
    { key: "purpose", label: "Назначение", children: PURPOSE_LABELS[report.purpose as DocPurpose] },
    { key: "created", label: "Дата проверки", children: new Date(report.created_at).toLocaleString("ru-RU") },
    { key: "duration", label: "Время обработки", children: `${(report.processing_ms / 1000).toLocaleString("ru-RU")} с` },
  ];

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%", minWidth: 0 }}>
      {reportId === "example" && <Alert type="info" showIcon title="Демонстрационный отчёт" description="Данные в этом отчёте вымышленные." />}

      <Card style={{ minWidth: 0 }}>
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
        {!report.llm_used && <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>Смысловые проверки нейросетью не выполнялись (режим без LLM).</Typography.Paragraph>}
        <Descriptions column={{ xs: 1, sm: 2, md: 2 }} items={details} />
        {reportId !== "example" && (
          <div style={{ marginTop: 20 }}>
            <Space orientation={narrow ? "vertical" : "horizontal"} wrap={!narrow} style={{ width: narrow ? "100%" : undefined }}>
              <Button
                type="primary"
                href={report.redacted_available ? redactedUrl(report.report_id) : undefined}
                disabled={!report.redacted_available}
                block={narrow}
              >
                Скачать обезличенную копию
              </Button>
              <Button href={exportUrl(report.report_id)} block={narrow}>Скачать отчёт (JSON)</Button>
            </Space>
            {!report.redacted_available && (
              <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
                Копия недоступна: в документе не нашлось персональных данных или её не удалось безопасно сделать.
              </Typography.Paragraph>
            )}
            <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0", fontSize: 12 }}>
              Персональные данные удалены из файла, их нельзя скопировать. Коммерческая тайна оставлена.
            </Typography.Paragraph>
          </div>
        )}
        <Row gutter={[12, 12]} style={{ marginTop: 20 }}>
          <Col xs={24} md={8}>
            <Card size="small">
              <Statistic title="Найдено фрагментов с чувствительными данными" value={entityCount} />
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card size="small">
              <Statistic title="Серьёзные нарушения" value={redCount} valueStyle={{ color: "#cf1322" }} />
            </Card>
          </Col>
          <Col xs={24} md={8}>
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
        {documentText && report.violations.some((violation) => violation.evidence.length > 0) && (
          <Typography.Paragraph type="secondary">Раскройте нарушение и нажмите на цитату, чтобы увидеть её в тексте документа.</Typography.Paragraph>
        )}
        <ViolationsTable violations={report.violations} onSelectEvidence={documentText ? setSelectedEvidence : undefined} onShowLaw={setSelectedLaw} />
      </Card>

      {documentText && <DocumentText document={documentText} entities={report.entities} selected={selectedEvidence} />}
      {textError && <Alert type="warning" showIcon title="Текст документа пока недоступен" />}

      <Card title={`Найденные данные (${report.entities.length})`}>
        <Typography.Paragraph type="secondary">
          Чувствительные данные отображаются в замаскированном виде.
        </Typography.Paragraph>
        <EntitiesTable entities={report.entities} stats={report.stats} />
      </Card>

      <TransparencyPanel key={report.report_id} report={report} example={reportId === "example"} />

      <Button size="large" onClick={() => navigate("/")}>Проверить другой документ</Button>

      <Modal title={selectedLaw ? `${selectedLaw.law}, ${selectedLaw.article}` : "Норма закона"} open={selectedLaw !== null} onCancel={() => setSelectedLaw(null)} footer={<Button onClick={() => setSelectedLaw(null)}>Закрыть</Button>}>
        {selectedLaw?.title && <Typography.Paragraph strong>{selectedLaw.title}</Typography.Paragraph>}
        {selectedLaw && getArticleText(selectedLaw) ? (
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", maxHeight: "50vh", overflowY: "auto" }}>{getArticleText(selectedLaw)}</Typography.Paragraph>
        ) : <Typography.Paragraph type="secondary">Текст этой нормы не включён в каталог проекта.</Typography.Paragraph>}
        {selectedLaw?.url && <Typography.Link href={selectedLaw.url} target="_blank" rel="noreferrer">Открыть источник</Typography.Link>}
      </Modal>
    </Space>
  );
}
