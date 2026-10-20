import { useState } from "react";
import { Alert, Button, Card, Input, Radio, Select, Space, Tabs, Typography, Upload, message } from "antd";
import type { UploadFile, UploadProps } from "antd";
import { Link, useNavigate } from "react-router-dom";

import { submitQuery, uploadDocument } from "../api/client";
import type { DocPurpose } from "../api/types";
import { PURPOSE_LABELS } from "../components/labels";

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx"];

const PURPOSE_OPTIONS = (Object.entries(PURPOSE_LABELS) as [DocPurpose, string][]).map(
  ([value, label]) => ({ value, label }),
);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Не удалось выполнить проверку. Попробуйте ещё раз.";
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
    const lowerName = file.name.toLowerCase();
    const supported = ACCEPTED_EXTENSIONS.some((extension) => lowerName.endsWith(extension));

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

  const handleUploadChange: UploadProps["onChange"] = ({ fileList: nextFileList }) => {
    setFileList(nextFileList.slice(-1));
  };

  const handleUpload = async () => {
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
  };

  const handleSubmitQuery = async () => {
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
  };

  const fileTab = (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Paragraph>
        Загрузите документ закупки — система проверит персональные данные, коммерческую тайну,
        согласования, гриф конфиденциальности и сроки хранения.
      </Typography.Paragraph>

      <Upload.Dragger
        accept={ACCEPTED_EXTENSIONS.join(",")}
        multiple={false}
        maxCount={1}
        fileList={fileList}
        beforeUpload={beforeUpload}
        onChange={handleUploadChange}
        onRemove={() => setFileList([])}
        showUploadList={{ showPreviewIcon: false }}
      >
        <p className="ant-upload-drag-icon">
          <span aria-hidden="true" style={{ fontSize: 32 }}>⇧</span>
        </p>
        <Typography.Text strong>Перетащите документ сюда или нажмите для выбора</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          PDF, DOCX или XLSX · не более 50 МБ
        </Typography.Paragraph>
      </Upload.Dragger>

      <div>
        <Typography.Text strong>Назначение документа</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginTop: 4, marginBottom: 0 }}>
          Выберите, как планируете использовать документ: от этого зависят правила проверки.
        </Typography.Paragraph>
        <Radio.Group
          value={filePurpose}
          onChange={(event) => setFilePurpose(event.target.value as DocPurpose)}
          style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}
        >
          {PURPOSE_OPTIONS.map((option) => (
            <Radio key={option.value} value={option.value}>{option.label}</Radio>
          ))}
        </Radio.Group>
      </div>

      <Button type="primary" size="large" block loading={uploading} onClick={handleUpload}>
        Проверить документ
      </Button>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        Хотите посмотреть, как выглядит результат? Откройте <Link to="/reports/example">пример отчёта</Link>.
      </Typography.Paragraph>
    </Space>
  );

  const textTab = (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Paragraph>
        Вставьте текст документа, если не хотите загружать файл. Перед отправкой выберите назначение текста.
      </Typography.Paragraph>
      <Input.TextArea
        value={queryText}
        onChange={(event) => setQueryText(event.target.value)}
        placeholder="Вставьте текст для проверки…"
        autoSize={{ minRows: 8, maxRows: 18 }}
        showCount
        maxLength={100_000}
      />
      <div>
        <Typography.Text strong>Назначение документа</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ marginTop: 4, marginBottom: 0 }}>
          Выберите, как планируете использовать текст: от этого зависят правила проверки.
        </Typography.Paragraph>
        <Select<DocPurpose>
          aria-label="Назначение текста"
          value={queryPurpose}
          onChange={(value: DocPurpose) => setQueryPurpose(value)}
          options={PURPOSE_OPTIONS}
          style={{ width: "100%", marginTop: 8 }}
        />
      </div>
      <Button type="primary" size="large" block loading={submittingQuery} onClick={handleSubmitQuery}>
        Проверить текст
      </Button>
    </Space>
  );

  return (
    <Card>
      <Typography.Title level={2} style={{ marginTop: 0 }}>
        Проверка документа
      </Typography.Title>
      <Alert
        type="info"
        showIcon
        title="Перед проверкой"
        description="Используйте только документы, которые вы вправе передавать на проверку. Демонстрационные данные в проекте вымышленные."
        style={{ marginBottom: 20 }}
      />
      <Tabs
        defaultActiveKey="file"
        items={[
          { key: "file", label: "Загрузить документ", children: fileTab },
          { key: "text", label: "Проверить текст", children: textTab },
        ]}
      />
    </Card>
  );
}
