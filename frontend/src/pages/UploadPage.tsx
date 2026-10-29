import { useState } from "react";
import { Alert, Button, Input, Radio, Tabs, Typography, Upload } from "antd";
import type { UploadFile, UploadProps } from "antd";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, submitQuery, uploadDocument } from "../api/client";
import type { DocPurpose } from "../api/types";
import { MAX_BATCH_FILES, submitBatchFiles, validateBatchFile } from "../lib/batch";

const MAX_TEXT_LENGTH = 200_000;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".xml"];

const PURPOSES: { value: DocPurpose; title: string; description: string }[] = [
  { value: "publication", title: "Для публикации", description: "Сайт, ЕИС или открытый доступ" },
  { value: "contract", title: "Договор", description: "С контрагентом или подрядчиком" },
  { value: "internal", title: "Внутренний документ", description: "Останется внутри компании" },
  { value: "international", title: "Международный контракт", description: "Проверка с учётом GDPR" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return "Сервис проверки временно недоступен. Попробуйте через минуту.";
    return error.message;
  }
  if (error instanceof TypeError) return "Нет связи с сервером";
  return "Не удалось выполнить проверку. Попробуйте ещё раз.";
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
  const [fileError, setFileError] = useState<string | null>(null);
  const [rejectedFiles, setRejectedFiles] = useState<{ name: string; reason: string }[]>([]);
  const [queryError, setQueryError] = useState<string | null>(null);

  const beforeUpload: UploadProps["beforeUpload"] = (file) => {
    const validationError = validateBatchFile(file);
    if (validationError) {
      setRejectedFiles((current) => [...current, { name: file.name, reason: validationError }]);
      return Upload.LIST_IGNORE;
    }
    setFileError(null);
    return false;
  };

  async function handleUpload() {
    if (uploading) return;
    const files = fileList.map((entry) => entry.originFileObj).filter((file): file is NonNullable<typeof file> => Boolean(file));
    if (files.length === 0) {
      setFileError("Сначала выберите документы для проверки.");
      return;
    }
    setFileError(null);
    setUploading(true);
    try {
      if (files.length === 1) {
        const task = await uploadDocument(files[0], filePurpose);
        navigate(`/tasks/${encodeURIComponent(task.task_id)}`);
      } else {
        const { created, failed } = await submitBatchFiles(files, filePurpose, uploadDocument);
        if (failed.length > 0) setRejectedFiles((current) => [...current, ...failed]);
        if (created.length === 0) {
          setFileError("Не удалось отправить документы. Проверьте список ошибок и повторите попытку.");
          return;
        }
        try {
          const batchKey = created.map(({ id }) => id).join(",");
          sessionStorage.setItem(`grif-batch-${batchKey}`, JSON.stringify({ names: Object.fromEntries(created.map(({ id, name }) => [id, name])), failed }));
        } catch { /* Проверка продолжится и без подписей файлов. */ }
        navigate(`/batch?tasks=${created.map(({ id }) => encodeURIComponent(id)).join(",")}`);
      }
    } catch (error: unknown) {
      setFileError(errorMessage(error));
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmitQuery() {
    if (submittingQuery) return;
    const text = queryText.trim();
    if (!text) {
      setQueryError("Введите текст, который нужно проверить.");
      return;
    }
    if (queryText.length > MAX_TEXT_LENGTH) {
      setQueryError("Текст длиннее 200 000 символов. Сократите его и попробуйте снова.");
      return;
    }
    setQueryError(null);
    setSubmittingQuery(true);
    try {
      const task = await submitQuery({ text, purpose: queryPurpose });
      navigate(`/tasks/${encodeURIComponent(task.task_id)}`);
    } catch (error: unknown) {
      setQueryError(errorMessage(error));
    } finally {
      setSubmittingQuery(false);
    }
  }

  const fileTab = (
    <>
      <Upload.Dragger
        className="document-dropzone"
        accept={ACCEPTED_EXTENSIONS.join(",")}
        multiple
        fileList={fileList}
        beforeUpload={beforeUpload}
        onChange={({ fileList: nextFiles }) => {
          if (nextFiles.length > MAX_BATCH_FILES) {
            setFileError(`Можно выбрать не больше ${MAX_BATCH_FILES} файлов за один раз.`);
          }
          setFileList(nextFiles.slice(0, MAX_BATCH_FILES));
        }}
        onRemove={() => { setFileError(null); }}
        showUploadList={{ showPreviewIcon: false }}
      >
        <span className="upload-symbol" aria-hidden="true">
          <svg viewBox="0 0 84 84" fill="none" focusable="false">
            <path d="M42 52V20M26 36L42 20L58 36M19 61H65" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        <strong>Перетащите документы сюда</strong>
        <span className="dropzone-hint">До 10 файлов PDF, DOCX, XLSX или XML · каждый до 50 МБ</span>
        <span className="choose-file">Выбрать файлы</span>
      </Upload.Dragger>
      {fileList.length > 0 && <p className="selected-file-count">Выбрано: {fileList.length} из {MAX_BATCH_FILES}</p>}
      {rejectedFiles.length > 0 && <Alert className="rejected-files" type="warning" showIcon title="Некоторые файлы не добавлены" description={<ul>{rejectedFiles.map((entry, index) => <li key={`${entry.name}-${index}`}>{entry.name}: {entry.reason}</li>)}</ul>} closable onClose={() => setRejectedFiles([])} />}
      <section className="purpose-section">
        <h2>Куда пойдёт документ?</h2>
        <p>Это поможет применить нужные правила проверки.</p>
        <PurposePicker value={filePurpose} onChange={setFilePurpose} />
      </section>
      <div className="form-actions">
        {fileError && <Alert type="error" showIcon title={fileError} className="form-error" />}
        <Button type="primary" size="large" loading={uploading} disabled={uploading} onClick={() => { void handleUpload(); }}>
          {fileList.length > 1 ? "Проверить выбранные документы" : "Начать проверку"}
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
        onChange={(event) => { setQueryText(event.target.value); setQueryError(null); }}
        placeholder="Вставьте текст для проверки…"
        autoSize={{ minRows: 8, maxRows: 18 }}
      />
      <Typography.Text className={`query-character-count${queryText.length > MAX_TEXT_LENGTH ? " is-over" : ""}`}>
        {queryText.length.toLocaleString("ru-RU")} / {MAX_TEXT_LENGTH.toLocaleString("ru-RU")} символов
      </Typography.Text>
      <section className="purpose-section">
        <h2>Куда пойдёт документ?</h2>
        <p>Это поможет применить нужные правила проверки.</p>
        <PurposePicker value={queryPurpose} onChange={setQueryPurpose} />
      </section>
      <div className="form-actions">
        {queryError && <Alert type="error" showIcon title={queryError} className="form-error" />}
        <Button type="primary" size="large" loading={submittingQuery} disabled={submittingQuery} onClick={() => { void handleSubmitQuery(); }}>
          Начать проверку
        </Button>
        <Typography.Text type="secondary">Персональные сведения скрываются до анализа</Typography.Text>
      </div>
    </>
  );

  return (
    <div className="page-container upload-page">
      <header className="page-heading">
        <h1>Проверка документа</h1>
        <p>Загрузите файл, а мы покажем риски и объясним каждый результат.</p>
        <h1 className="mobile-upload-title">Проверьте документ</h1>
        <p className="mobile-upload-subtitle">Риски и рекомендации в одном месте</p>
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
            <svg className="feature-icon feature-icon-blue" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="3" /><path d="M5.5 20v-1.5a6.5 6.5 0 0 1 13 0V20M3 4h2M19 4h2" /></svg>
            <strong>Персональные данные</strong>
            <p>Паспорт, телефон, ФИО</p>
          </div>
          <div className="feature-card">
            <svg className="feature-icon feature-icon-amber" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2" /></svg>
            <strong>Коммерческую тайну</strong>
            <p>Цены, скидки и ноу-хау</p>
          </div>
          <div className="feature-card">
            <svg className="feature-icon feature-icon-green" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 3h10l3 3v15H4V3h3ZM17 3v4h3M8 12l2 2 4-4M8 18h8" /></svg>
            <strong>Чек-лист документа</strong>
            <p>Гриф, согласования, срок хранения</p>
          </div>
        </aside>
      </div>
      <div className="privacy-note">
        <svg className="privacy-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 2 20 5v6c0 5-3 8.5-8 11-5-2.5-8-6-8-11V5l8-3Z" /><path d="m9 12 2 2 4-4" /></svg>
        <div><strong>Данные под защитой</strong><p>Персональные сведения скрываются до обращения к нейросети.</p></div>
      </div>
    </div>
  );
}
