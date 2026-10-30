import { useEffect, useState } from "react";
import { Alert, Button, Card, Collapse, Descriptions, Skeleton, Space, Tag, Typography } from "antd";

import { getLlmLog } from "../api/client";
import type { LlmCall, Report } from "../api/types";
import { detectorLabel, llmPurposeTitle, LLM_VERDICT_LABELS } from "./labels";

interface Props {
  report: Report;
  example?: boolean;
}

function MarkedFragment({ text, quote }: { text: string; quote: string | null }) {
  const placeholders = [...text.matchAll(/\[[A-Z_]+_\d+\]/g)].map((match) => ({
    start: match.index,
    end: match.index + match[0].length,
  }));
  const quoteStart = quote ? text.indexOf(quote) : -1;
  const quoteEnd = quoteStart >= 0 && quote ? quoteStart + quote.length : -1;
  const boundaries = new Set([0, text.length]);
  for (const placeholder of placeholders) {
    boundaries.add(placeholder.start);
    boundaries.add(placeholder.end);
  }
  if (quoteStart >= 0) {
    boundaries.add(quoteStart);
    boundaries.add(quoteEnd);
  }
  const offsets = [...boundaries].sort((a, b) => a - b);

  return (
    <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 240, overflowY: "auto", padding: 12, borderRadius: 8, background: "#f6f8fa" }}>
      {offsets.slice(0, -1).map((start, index) => {
        const end = offsets[index + 1];
        const isPlaceholder = placeholders.some((item) => start >= item.start && end <= item.end);
        const isQuote = quoteStart >= 0 && start >= quoteStart && end <= quoteEnd;
        return (
          <span
            key={start}
            style={{
              background: isQuote ? "#fff1b8" : isPlaceholder ? "#e6f4ff" : "transparent",
              color: isPlaceholder ? "#0958d9" : "inherit",
              borderRadius: isPlaceholder || isQuote ? 3 : 0,
            }}
          >
            {text.slice(start, end)}
          </span>
        );
      })}
    </div>
  );
}

function LlmCallCard({ call, index }: { call: LlmCall; index: number }) {
  const verdict = LLM_VERDICT_LABELS[call.purpose][call.verdict];
  return (
    <Card size="small" title={llmPurposeTitle(call.purpose, call.rule_id)} extra={<Typography.Text type="secondary">Вызов {index + 1}</Typography.Text>} style={{ minWidth: 0 }}>
      <Space orientation="vertical" size="small" style={{ width: "100%", minWidth: 0 }}>
        <Typography.Text strong style={{ overflowWrap: "anywhere" }}>{call.question}</Typography.Text>
        <Space wrap>
          <Tag color={verdict.color}>{verdict.label}</Tag>
          {call.rule_id && <Typography.Text type="secondary" style={{ fontSize: 12, overflowWrap: "anywhere" }}>Правило: {call.rule_id}</Typography.Text>}
          {call.cached && <Tag>Из кэша</Tag>}
        </Space>
        <Typography.Text type="secondary">Текст, отправленный нейросети:</Typography.Text>
        <MarkedFragment text={call.masked_fragment} quote={call.quote} />
        {call.quote && <Typography.Text style={{ overflowWrap: "anywhere" }}>Цитата, которую вернула нейросеть: «{call.quote}»</Typography.Text>}
      </Space>
    </Card>
  );
}

export default function TransparencyPanel({ report, example = false }: Props) {
  const [llmOpen, setLlmOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [calls, setCalls] = useState<LlmCall[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!llmOpen || example || calls !== null) return;
    let active = true;
    setLoading(true);
    setError(null);
    void getLlmLog(report.report_id).then(
      (result) => { if (active) setCalls(result); },
      (reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Попробуйте ещё раз."); },
    ).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [llmOpen, attempt, calls, example, report.report_id]);

  const audit = report.audit;
  const auditItems = [
    {
      key: "hash", label: "Отпечаток документа (SHA-256)",
      children: (
        <span title={audit.document_sha256}>
          <Typography.Text code copyable={{ text: audit.document_sha256 }}>
            {audit.document_sha256.slice(0, 16)}…
          </Typography.Text>
        </span>
      ),
    },
    { key: "rules", label: "Версия правил", children: audit.ruleset_version },
    { key: "dictionaries", label: "Версия словарей", children: audit.dictionaries_version },
    { key: "app", label: "Версия программы", children: audit.app_version },
    {
      key: "detectors", label: "Детекторы",
      children: audit.detectors.length ? (
        <Typography.Text title={audit.detectors.join(", ")}>
          {audit.detectors.map((name) => detectorLabel(name)).join(" · ")}
        </Typography.Text>
      ) : "Не указаны",
    },
    { key: "llm", label: "Генеративная нейросеть (LLM)", children: audit.llm_provider === "mock" ? "Не использовалась" : audit.llm_model ?? "Модель не указана" },
    { key: "calls", label: "Вызовов генеративной нейросети", children: audit.llm_calls },
  ];

  let llmContent;
  if (example) {
    llmContent = <Typography.Paragraph>Генеративная нейросеть (LLM) не использовалась: документ никуда не отправлялся. Локальный классификатор grif-cls работал — см. «Детекторы» в следе проверки.</Typography.Paragraph>;
  } else if (loading || (calls === null && error === null)) {
    llmContent = <Skeleton active paragraph={{ rows: 3 }} />;
  } else if (error) {
    llmContent = <Alert type="error" showIcon title="Не удалось загрузить журнал нейросети" description={error} action={<Button size="small" onClick={() => setAttempt((value) => value + 1)}>Повторить</Button>} />;
  } else if (calls?.length === 0) {
    llmContent = <Typography.Paragraph>{report.llm_used ? "Вызовов нейросети для этого документа не потребовалось." : "Генеративная нейросеть (LLM) не использовалась: документ никуда не отправлялся. Локальный классификатор grif-cls работал — см. «Детекторы» в следе проверки."}</Typography.Paragraph>;
  } else {
    llmContent = <Space orientation="vertical" size="middle" style={{ width: "100%" }}>{calls?.map((call, index) => <LlmCallCard key={`${call.rule_id ?? call.purpose}-${index}`} call={call} index={index} />)}</Space>;
  }

  return (
    <Card title="Прозрачность проверки" style={{ minWidth: 0 }}>
      <Collapse
        onChange={(keys) => setLlmOpen(keys.includes("llm"))}
        items={[
          {
            key: "llm",
            label: "Что видела нейросеть",
            children: (
              <>
                <Typography.Paragraph type="secondary">
                  Это ровно тот текст, который ушёл в нейросеть. Персональные данные заменены метками вида [PERSON_1] ещё до отправки.
                </Typography.Paragraph>
                {llmContent}
              </>
            ),
          },
          {
            key: "audit",
            label: "След проверки",
            children: (
              <>
                <Descriptions column={1} items={auditItems} />
                <Typography.Paragraph type="secondary" style={{ margin: "12px 0 0" }}>
                  По этим данным проверку можно повторить: тот же документ и те же версии правил дают тот же результат.
                </Typography.Paragraph>
              </>
            ),
          },
        ]}
      />
    </Card>
  );
}
