import { Space } from "antd";

import type { Overall } from "../api/types";

export const OVERALL: Record<Overall, { color: string; label: string; description: string }> = {
  red: { color: "#cf1322", label: "Есть серьёзные нарушения", description: "Перед использованием документа необходимо устранить серьёзные нарушения." },
  yellow: { color: "#d48806", label: "Есть замечания", description: "Проверьте замечания и рекомендации перед использованием документа." },
  green: { color: "#389e0d", label: "Нарушений не найдено", description: "По результатам проверки нарушений не обнаружено." },
};

export default function TrafficLight({ value }: { value: Overall }) {
  const lamps: Overall[] = ["red", "yellow", "green"];
  return (
    <Space size="middle" aria-label={`Итог проверки: ${OVERALL[value].label}`}>
      {lamps.map((lamp) => (
        <span
          key={lamp}
          title={lamp === value ? OVERALL[lamp].label : undefined}
          style={{
            display: "inline-block",
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: OVERALL[lamp].color,
            opacity: lamp === value ? 1 : 0.16,
            boxShadow: lamp === value ? `0 0 0 4px ${OVERALL[lamp].color}22` : "none",
          }}
        />
      ))}
    </Space>
  );
}
