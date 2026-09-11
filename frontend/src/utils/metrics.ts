/** Harmonic mean of precision and recall (F1). Returns 0 when both are 0. */
export function f1Score(precision: number, recall: number): number {
  const p = Number(precision) || 0;
  const r = Number(recall) || 0;
  return p + r > 0 ? (2 * p * r) / (p + r) : 0;
}
