import { useEffect, useRef } from "react";
import { Alert, Card, Typography } from "antd";

import type { Entity, Evidence, ReportText } from "../api/types";

interface Props {
  document: ReportText;
  entities: Entity[];
  selected: Evidence | null;
}

export default function DocumentText({ document, entities, selected }: Props) {
  const targetRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (selected) targetRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selected]);

  const boundaries = new Set<number>([0, document.text.length]);
  for (const entity of entities) {
    boundaries.add(Math.max(0, Math.min(document.text.length, entity.start)));
    boundaries.add(Math.max(0, Math.min(document.text.length, entity.end)));
  }
  if (selected) {
    boundaries.add(Math.max(0, Math.min(document.text.length, selected.start)));
    boundaries.add(Math.max(0, Math.min(document.text.length, selected.end)));
  }
  const offsets = [...boundaries].sort((a, b) => a - b);

  return (
    <Card id="document-text" title="Текст документа">
      <Alert type="info" showIcon title="Персональные данные скрыты" description="Подсветка и цитаты относятся к замаскированному тексту." style={{ marginBottom: 16 }} />
      <Typography.Text style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: 1.8 }}>
        {offsets.slice(0, -1).map((start, index) => {
          const end = offsets[index + 1];
          const isSelected = selected != null && start >= selected.start && end <= selected.end;
          const isEntity = entities.some((entity) => start >= entity.start && end <= entity.end);
          return (
            <mark
              key={start}
              ref={isSelected && start === selected?.start ? targetRef : undefined}
              style={{ background: isSelected ? "#ffd666" : isEntity ? "#e6f4ff" : "transparent", color: "inherit" }}
            >
              {document.text.slice(start, end)}
            </mark>
          );
        })}
      </Typography.Text>
    </Card>
  );
}
