/**
 * Zakres rozciągnięcia „Widok” (DESIGN_DECISIONS.md, display-stretch D) — czysta logika progów.
 *
 * Statystyki widoku przychodzą z backendu (`view-stats`). Tutaj zamieniamy je na JEDNO okno
 * dla wszystkich kafli widoku, pilnując trzech rzeczy:
 *
 * - jednorodny fragment (morze, gładkie pole) nie może wzmocnić szumu plamkowego do pełnej
 *   czerni i bieli — okno nie bywa węższe niż ułamek okna sceny;
 * - drobne przesunięcie mapy nie przeładowuje kafli — histereza;
 * - progi lądują w URL kafla, więc są zaokrąglane, żeby cache przeglądarki miał trafienia.
 */
import type { SceneHistogram, ViewStretchWindow } from "../types";

export const VIEW_STRETCH_DEBOUNCE_MS = 300;
/** Nowe progi tylko, gdy któryś przesunął się o więcej niż 2% szerokości okna. */
export const VIEW_STRETCH_HYSTERESIS = 0.02;
/** Okno widoku nie węższe niż 25% okna sceny dla tych samych percentyli (display-stretch D). */
export const VIEW_STRETCH_MIN_WIDTH_RATIO = 0.25;

type PercentileSource = Pick<SceneHistogram, "percentiles">;

/**
 * Percentyl -> wartość, liniowo między punktami histogramu. Ten sam przepis co backendowe
 * `percentile_to_value` i etykieta panelu, więc liczby nie rozjeżdżają się między warstwami.
 */
export function percentileToValue(histogram: PercentileSource, percentile: number): number | null {
  const points = Object.entries(histogram.percentiles || {})
    .map(([key, value]) => [Number(key.replace("p", "")), Number(value)] as [number, number])
    .filter(([point, value]) => Number.isFinite(point) && Number.isFinite(value))
    .sort((a, b) => a[0] - b[0]);
  if (points.length === 0) return null;
  if (percentile <= points[0][0]) return points[0][1];
  const last = points[points.length - 1];
  if (percentile >= last[0]) return last[1];
  for (let index = 1; index < points.length; index += 1) {
    const [rightPct, rightValue] = points[index];
    if (percentile <= rightPct) {
      const [leftPct, leftValue] = points[index - 1];
      const span = rightPct - leftPct;
      if (span <= 0) return rightValue;
      return leftValue + (rightValue - leftValue) * ((percentile - leftPct) / span);
    }
  }
  return last[1];
}

/** Zaokrąglenie do cyfr znaczących — stabilny URL kafla przy tych samych statystykach. */
export function roundThreshold(value: number, digits = 4): number {
  return Number(value.toPrecision(digits));
}

export interface ResolveViewWindowInput {
  /** Statystyki bieżącego widoku; `null`, gdy jeszcze ich nie ma albo są niewystarczające. */
  view: PercentileSource | null;
  /** Histogram sceny — odniesienie dla minimalnej szerokości okna. */
  scene: PercentileSource | null;
  stretchLow: number;
  stretchHigh: number;
  /** Okno obowiązujące dotąd; `null` wyłącza histerezę (np. po zmianie nastawy). */
  previous: ViewStretchWindow | null;
  minWidthRatio?: number;
  hysteresis?: number;
}

/**
 * Okno zakresu „Widok” albo `null` (kafle zostają przy progach sceny).
 *
 * „Pełny zakres” (0–100%) nie rozciąga w żadnym zakresie, więc daje `null`.
 */
export function resolveViewWindow({
  view,
  scene,
  stretchLow,
  stretchHigh,
  previous,
  minWidthRatio = VIEW_STRETCH_MIN_WIDTH_RATIO,
  hysteresis = VIEW_STRETCH_HYSTERESIS,
}: ResolveViewWindowInput): ViewStretchWindow | null {
  if (stretchLow <= 0 && stretchHigh >= 100) return null;
  if (!view) return previous;
  let low = percentileToValue(view, stretchLow);
  let high = percentileToValue(view, stretchHigh);
  if (low === null || high === null || !(high > low)) return previous;

  if (scene) {
    const sceneLow = percentileToValue(scene, stretchLow);
    const sceneHigh = percentileToValue(scene, stretchHigh);
    if (sceneLow !== null && sceneHigh !== null && sceneHigh > sceneLow) {
      const minWidth = minWidthRatio * (sceneHigh - sceneLow);
      if (high - low < minWidth) {
        const center = (low + high) / 2;
        low = center - minWidth / 2;
        high = center + minWidth / 2;
      }
    }
  }

  let next = { min: roundThreshold(low), max: roundThreshold(high) };
  if (!(next.max > next.min)) next = { min: roundThreshold(low, 8), max: roundThreshold(high, 8) };

  if (previous) {
    const tolerance = hysteresis * (previous.max - previous.min);
    if (Math.abs(next.min - previous.min) <= tolerance && Math.abs(next.max - previous.max) <= tolerance) {
      return previous;
    }
  }
  return next;
}

const STORAGE_PREFIX = "geotile.displayStretchScope.";

/** Zakres statystyk zapamiętany dla projektu (display-stretch D: lokalnie, nie w paczkach zespołowych). */
export function readStoredStretchScope(projectId: string | undefined): "scene" | "view" {
  if (!projectId) return "scene";
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + projectId) === "view" ? "view" : "scene";
  } catch {
    return "scene";
  }
}

export function storeStretchScope(projectId: string | undefined, scope: "scene" | "view"): void {
  if (!projectId) return;
  try {
    window.localStorage.setItem(STORAGE_PREFIX + projectId, scope);
  } catch {
    // Brak dostępu do localStorage (tryb prywatny) — wybór obowiązuje do końca sesji.
  }
}
