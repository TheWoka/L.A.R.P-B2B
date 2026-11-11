/** Clamp OCR ranges to the text; ignore invalid, empty and reversed spans. */
export function ocrRanges(length: number, spans: readonly (readonly [number, number])[]): [number, number][] {
  return spans.flatMap(([start, end]) => {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return [];
    const from = Math.max(0, start);
    const to = Math.min(length, end);
    return from < to ? [[from, to] as [number, number]] : [];
  });
}
