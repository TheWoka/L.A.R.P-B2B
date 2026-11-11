import { useId } from "react";
import { Alert, Radio, Tooltip } from "antd";

import type { LlmMode, LlmModeInfo } from "../api/types";
import { LABELS } from "../lib/llmMode";

interface Props {
  modes: readonly LlmModeInfo[];
  value: LlmMode;
  onChange: (mode: LlmMode) => void;
  loading?: boolean;
  disabled?: boolean;
}

export default function LlmModeSelect({ modes, value, onChange, loading = false, disabled = false }: Props) {
  const headingId = useId();
  return (
    <section className="llm-mode-section" aria-labelledby={headingId} aria-busy={loading}>
      <h2 id={headingId}>Генеративная нейросеть</h2>
      <Radio.Group className={`llm-mode-options${modes.length === 1 ? " is-single" : ""}`} value={value} disabled={disabled || loading} aria-labelledby={headingId}>
        {modes.map((item) => (
          <Tooltip key={item.mode} title={!item.available ? item.reason ?? "Режим недоступен" : undefined}>
            <span className="llm-mode-option" tabIndex={!item.available ? 0 : undefined} aria-label={!item.available ? `${LABELS[item.mode]}: ${item.reason ?? "Режим недоступен"}` : undefined}>
              <Radio.Button value={item.mode} disabled={disabled || loading || !item.available} onChange={() => onChange(item.mode)}>
                <span>{LABELS[item.mode]}</span>
                {item.model && <small className="llm-mode-option-model">{item.model}</small>}
              </Radio.Button>
            </span>
          </Tooltip>
        ))}
      </Radio.Group>
      {loading && <p className="llm-mode-hint" role="status">Проверяем доступность режимов…</p>}
      {value === "cloud" && (
        <Alert type="warning" showIcon title="В облако уходит только текст с замаскированными персональными данными. Коммерческая тайна и финансовые сведения передаются как есть." />
      )}
      {value === "mock" && (
        <p className="llm-mode-hint">Работают все детекторы, включая классификатор grif-cls. Смысловые условия правил не проверяются.</p>
      )}
    </section>
  );
}
