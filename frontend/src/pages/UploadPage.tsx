import { useState } from "react";
import { Alert, Button, Input, Radio, Tabs, Typography, Upload, message } from "antd";
import type { UploadFile, UploadProps } from "antd";
import { Link, useNavigate } from "react-router-dom";

import { submitQuery, uploadDocument } from "../api/client";
import type { DocPurpose } from "../api/types";

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx"];

const PURPOSES: { value: DocPurpose; title: string; description: string }[] = [
  { value: "publication", title: "Для публикации", description: "Сайт, ЕИС или открытый доступ" },
  { value: "contract", title: "Договор", description: "С контрагентом или подрядчиком" },
  { value: "internal", title: "Внутренний документ", description: "Останется внутри компании" },
  { value: "international", title: "Международный контракт", description: "Проверка с учётом GDPR" },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Не удалось выполнить проверку. Попробуйте ещё раз.";
}

function PurposePicker({ value, onChange }: { value: DocPurpose; onChange: (value: DocPurpose) => void }) {
  return (
    <Radio.Group className="purpose-grid" value={value} onChange={(event) => onChange(event.target.value as DocPurpose)}>
      {PURPOSES.map((purpose) => (
        <Radio key={purpose.value} className="purpose-option" value={purpose.value}>
          <span className="purpose-copy">
            <strong>{purpose.title}</strong>
            <span>{purpose.description}</span>
          </span>
        </Radio>
      ))}
    </Radio.Group>
  );
}

export default function UploadPage() {
  const navigate = useNavigate();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [filePurpose, setFilePurpose] = useState<DocPurpose>("publication");
  const [queryPurpose, setQueryPurpose] = useState<DocPurpose>("publication");
  const [queryText, setQueryText] = useState("");
  const [uploading, setUploading] = useState(false);
  const [submittingQuery, setSubmittingQuery] = useState(false);

  const beforeUpload: UploadProps["beforeUpload"] = (file) => {
    const supported = ACCEPTED_EXTENSIONS.some((extension) => file.name.toLowerCase().endsWith(extension));
    if (!supported) {
      void message.error("Поддерживаются только файлы PDF, DOCX и XLSX.");
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_SIZE) {
      void message.error("Размер файла не должен превышать 50 МБ.");
      return Upload.LIST_IGNORE;
    }
    return false;
  };

  async function handleUpload() {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      void message.warning("Сначала выберите документ для проверки.");
      return;
    }
    setUploading(true);
    try {
      const task = await uploadDocument(file, filePurpose);
      navigate(`/tasks/${encodeURIComponent(task.task_id)}`);
    } catch (error: unknown) {
      void message.error(errorMessage(error));
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmitQuery() {
    const text = queryText.trim();
    if (!text) {
      void message.warning("Введите текст, который нужно проверить.");
      return;
    }
    setSubmittingQuery(true);
    try {
      const task = await submitQuery({ text, purpose: queryPurpose });
      navigate(`/tasks/${encodeURIComponent(task.task_id)}`);
    } catch (error: unknown) {
      void message.error(errorMessage(error));
    } finally {
      setSubmittingQuery(false);
    }
  }

  const fileTab = (
    <>
      <Upload.Dragger
        className="document-dropzone"
        accept={ACCEPTED_EXTENSIONS.join(",")}
        multiple={false}
        maxCount={1}
        fileList={fileList}
        beforeUpload={beforeUpload}
        onChange={({ fileList: nextFiles }) => setFileList(nextFiles.slice(-1))}
        onRemove={() => setFileList([])}
        showUploadList={{ showPreviewIcon: false }}
      >
        <span className="upload-symbol" aria-hidden="true">↑</span>
        <strong>Перетащите документ сюда</strong>
        <span className="dropzone-hint">PDF, DOCX или XLSX · до 50 МБ</span>
        <span className="choose-file">Выбрать файл</span>
      </Upload.Dragger>
      <section className="purpose-section">
        <h2>Куда пойдёт документ?</h2>
        <p>Это поможет применить нужные правила проверки.</p>
        <PurposePicker value={filePurpose} onChange={setFilePurpose} />
      </section>
      <div className="form-actions">
        <Button type="primary" size="large" loading={uploading} onClick={() => { void handleUpload(); }}>
          Начать проверку
        </Button>
        <Typography.Text type="secondary">Обычно занимает меньше минуты</Typography.Text>
      </div>
    </>
  );

  const textTab = (
    <>
      <Input.TextArea
        className="query-input"
        aria-label="Текст документа"
        value={queryText}
        onChange={(event) => setQueryText(event.target.value)}
        placeholder="Вставьте текст для проверки…"
        autoSize={{ minRows: 8, maxRows: 18 }}
        showCount
        maxLength={100_000}
      />
      <section className="purpose-section">
        <h2>Куда пойдёт документ?</h2>
        <p>Это поможет применить нужные правила проверки.</p>
        <PurposePicker value={queryPurpose} onChange={setQueryPurpose} />
      </section>
      <div className="form-actions">
        <Button type="primary" size="large" loading={submittingQuery} onClick={() => { void handleSubmitQuery(); }}>
          Начать проверку
        </Button>
        <Typography.Text type="secondary">Персональные сведения скрываются до анализа</Typography.Text>
      </div>
    </>
  );

  return (
    <div className="page-container upload-page">
      <div className="page-eyebrow">ГРИФ / Проверка документа</div>
      <header className="page-heading">
        <h1>Проверка документа</h1>
        <p>Загрузите файл, а мы покажем риски и объясним каждый результат.</p>
      </header>
      <div className="upload-layout">
        <section className="upload-primary">
          <Tabs
            className="upload-tabs"
            defaultActiveKey="file"
            items={[
              { key: "file", label: "Загрузить файл", children: fileTab },
              { key: "text", label: "Вставить текст", children: textTab },
            ]}
          />
          <Alert
            className="upload-notice"
            type="info"
            title="Перед проверкой"
            description="Используйте только документы, которые вы вправе передавать на проверку. Данные в демонстрационном отчёте вымышленные."
          />
          <p className="example-link">Посмотреть результат заранее: <Link to="/reports/example">пример отчёта</Link></p>
        </section>
        <aside className="upload-aside" aria-label="Что проверит система">
          <h2>Что проверим</h2>
          <div className="feature-card">
            <span className="feature-icon feature-icon-blue" aria-hidden="true" />
            <strong>Персональные данные</strong>
            <p>Паспорт, телефон, ФИО</p>
          </div>
          <div className="feature-card">
            <span className="feature-icon feature-icon-amber" aria-hidden="true" />
            <strong>Коммерческую тайну</strong>
            <p>Цены, скидки и ноу-хау</p>
          </div>
          <div className="feature-card">
            <span className="feature-icon feature-icon-green" aria-hidden="true" />
            <strong>Чек-лист документа</strong>
            <p>Гриф, согласования, срок хранения</p>
          </div>
          <div className="privacy-note">
            <span className="privacy-mark" aria-hidden="true" />
            <div><strong>Данные под защитой</strong><p>Персональные сведения скрываются до обращения к нейросети.</p></div>
          </div>
        </aside>
      </div>
    </div>
  );
}
