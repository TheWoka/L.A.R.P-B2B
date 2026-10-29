import type { LawRef } from "../api/types";

import law152 from "../../../data/legal/152-fz.md?raw";
import law187 from "../../../data/legal/187-fz.md?raw";
import law98 from "../../../data/legal/98-fz.md?raw";
import law44 from "../../../data/legal/44-fz.md?raw";
import law223 from "../../../data/legal/223-fz.md?raw";
import gdpr from "../../../data/legal/gdpr.md?raw";

const TEXTS: Record<string, string> = {
  "152-ФЗ": law152,
  "187-ФЗ": law187,
  "98-ФЗ": law98,
  "44-ФЗ": law44,
  "223-ФЗ": law223,
  GDPR: gdpr,
};

export function getArticleText(law: LawRef): string | null {
  const source = TEXTS[law.law];
  const number = law.article.match(/\d+(?:\.\d+)?/)?.[0];
  if (!source || !number) return null;

  const sections = source.split(/^## /m).slice(1);
  const prefix = law.law === "GDPR" ? "Article" : "Статья";
  const section = sections.find((item) => item.startsWith(`${prefix} ${number}. `));
  return section ? section.trim() : null;
}
