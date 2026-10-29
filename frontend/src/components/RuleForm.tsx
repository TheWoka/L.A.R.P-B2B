import { useState } from "react";
import { Button, Card, Form, Input, message, Modal, Radio, Select, Space, Switch, Typography } from "antd";

import { ApiError, createRule, updateRule } from "../api/client";
import type { LawRef, Rule } from "../api/types";
import { PURPOSE_LABELS, SEVERITY_LABELS } from "./labels";

interface RuleFormProps {
  rule: Rule | null;
  onCancel: () => void;
  onSaved: (rule: Rule) => void;
}

type RuleFormValues = Omit<Rule, "condition"> & { condition: string };

const EMPTY_LAW: LawRef = { law: "", article: "", title: "", url: "" };
const DEFAULT_CONDITION = {
  all: [
    { type: "entity_present", categories: ["pd"] },
    { type: "marker_absent", marker: "consent_clause" },
  ],
};

export default function RuleForm({ rule, onCancel, onSaved }: RuleFormProps) {
  const [form] = Form.useForm<RuleFormValues>();
  const [saving, setSaving] = useState(false);
  const [messageApi, messageContext] = message.useMessage();
  const initialValues: Partial<RuleFormValues> = {
    ...rule,
    severity: rule?.severity ?? "yellow",
    applies_to: rule?.applies_to ?? [],
    enabled: rule?.enabled ?? true,
    law_refs: rule?.law_refs.length ? rule.law_refs.map((ref) => ({
      ...ref, title: ref.title ?? "", url: ref.url ?? "",
    })) : [{ ...EMPTY_LAW }],
    condition: JSON.stringify(rule?.condition ?? DEFAULT_CONDITION, null, 2),
  };

  function fieldError(name: "condition" | "id", text: string) {
    form.setFields([{ name, errors: [text] }]);
    form.scrollToField(name, { block: "center", focus: true });
  }

  async function save(values: RuleFormValues) {
    if (saving) return;
    let condition: unknown;
    try {
      condition = JSON.parse(values.condition);
    } catch {
      fieldError("condition", "Условие — не JSON: проверьте скобки, кавычки и запятые.");
      return;
    }
    if (typeof condition !== "object" || condition === null || Array.isArray(condition)) {
      fieldError("condition", "Условие должно содержать тип проверки или сочетание условий в фигурных скобках.");
      return;
    }
    const payload: Rule = {
      ...values,
      id: rule?.id ?? values.id.trim(),
      title: values.title.trim(),
      description: values.description.trim(),
      recommendation: values.recommendation.trim(),
      condition: condition as Rule["condition"],
      applies_to: values.applies_to ?? [],
      law_refs: values.law_refs.map((ref) => ({
        law: ref.law.trim(), article: ref.article.trim(),
        title: ref.title?.trim() || null, url: ref.url?.trim() || null,
      })),
    };
    setSaving(true);
    try {
      const saved = rule ? await updateRule(rule.id, payload) : await createRule(payload);
      onSaved(saved);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 400 || error.status === 409)) {
        fieldError(error.status === 400 ? "condition" : "id", error.message);
      } else {
        messageApi.error(error instanceof Error ? error.message : "Не удалось сохранить правило. Попробуйте ещё раз.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {messageContext}
      <Modal
        title={rule ? "Изменить правило" : "Новое правило"}
        open
        width={880}
        style={{ top: 24 }}
        styles={{ body: { maxHeight: "calc(100vh - 180px)", overflowY: "auto", paddingRight: 12 } }}
        okText="Сохранить"
        cancelText="Отмена"
        confirmLoading={saving}
        cancelButtonProps={{ disabled: saving }}
        closable={!saving}
        keyboard={!saving}
        mask={{ closable: !saving }}
        onOk={() => form.submit()}
        onCancel={onCancel}
      >
        <Form<RuleFormValues>
          form={form}
          layout="vertical"
          initialValues={initialValues}
          disabled={saving}
          onFinish={(values) => { void save(values); }}
          scrollToFirstError={{ block: "center", focus: true }}
        >
          <Form.Item
            name="id"
            label="Код правила"
            extra="Заглавные латинские буквы, цифры и знак _. Первый символ — буква."
            rules={[
              { required: true, message: "Укажите код правила" },
              { pattern: /^[A-Z][A-Z0-9_]*$/, message: "Начните с заглавной латинской буквы; далее допустимы заглавные латинские буквы, цифры и _." },
            ]}
          >
            <Input readOnly={rule !== null} />
          </Form.Item>
          <Form.Item name="title" label="Название" rules={[{ required: true, whitespace: true, message: "Укажите название" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="Описание" rules={[{ required: true, whitespace: true, message: "Добавьте описание" }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="recommendation" label="Рекомендация" rules={[{ required: true, whitespace: true, message: "Добавьте рекомендацию" }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="severity" label="Уровень" rules={[{ required: true, message: "Выберите уровень" }]}>
            <Radio.Group options={Object.entries(SEVERITY_LABELS).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="applies_to" label="Назначение документа" extra="Пусто = ко всем документам">
            <Select
              mode="multiple"
              allowClear
              placeholder="Все документы"
              options={Object.entries(PURPOSE_LABELS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Typography.Title level={5}>Ссылки на законы</Typography.Title>
          <Form.List
            name="law_refs"
            rules={[{ validator: async (_rule, refs: LawRef[] | undefined) => {
              if (!refs?.length) throw new Error("Добавьте хотя бы одну ссылку на закон");
            } }]}
          >
            {(fields, { add, remove }, { errors }) => (
              <Space orientation="vertical" style={{ display: "flex", marginBottom: 24 }}>
                {fields.map((field, index) => (
                  <Card key={field.key} size="small" title={`Основание ${index + 1}`}>
                    <Form.Item name={[field.name, "law"]} label="Закон" rules={[{ required: true, whitespace: true, message: "Укажите закон" }]}>
                      <Input placeholder="Например, 152-ФЗ" />
                    </Form.Item>
                    <Form.Item name={[field.name, "article"]} label="Статья" rules={[{ required: true, whitespace: true, message: "Укажите статью" }]}>
                      <Input placeholder="Например, ст. 7" />
                    </Form.Item>
                    <Form.Item name={[field.name, "title"]} label="Название статьи (необязательно)">
                      <Input />
                    </Form.Item>
                    <Form.Item name={[field.name, "url"]} label="Ссылка (необязательно)" rules={[{ type: "url", message: "Укажите полную ссылку на страницу закона" }]}>
                      <Input placeholder="https://…" />
                    </Form.Item>
                    <Button danger disabled={fields.length <= 1 || saving} onClick={() => remove(field.name)}>Удалить ссылку</Button>
                  </Card>
                ))}
                <Button onClick={() => add({ ...EMPTY_LAW })}>Добавить ссылку на закон</Button>
                <Form.ErrorList errors={errors} />
              </Space>
            )}
          </Form.List>
          <Form.Item
            name="condition"
            label="Условие (JSON)"
            rules={[{ required: true, whitespace: true, message: "Укажите условие проверки" }]}
            extra={(
              <div style={{ fontSize: 12 }}>
                <div>entity_present — найдены данные (categories, types, min_count); marker_present / marker_absent — есть / нет отметки (marker).</div>
                <div>purpose_is — назначение документа (purposes); llm — вопрос нейросети (question) по обезличенному тексту, с цитатой.</div>
                <div>all — все условия; any — хотя бы одно; not — условие не выполнено.</div>
                <div>llm нельзя ставить внутрь not (на любой глубине).</div>
              </div>
            )}
          >
            <Input.TextArea rows={12} spellCheck={false} style={{ fontFamily: "monospace" }} />
          </Form.Item>
          <Form.Item name="enabled" label="Правило включено" valuePropName="checked">
            <Switch checkedChildren="Да" unCheckedChildren="Нет" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
