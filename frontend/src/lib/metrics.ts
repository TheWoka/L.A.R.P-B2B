/** Округляет долю из API до десятых процента перед выводом в Statistic. */
export function roundPercentage(value: number): number {
  return Math.round(value * 1000) / 10;
}
