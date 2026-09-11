/**
 * Logika panelu „Pliki robocze scen” — bez Reacta, bez DOM.
 *
 * Wydzielona z komponentu, bo bramka P1 dotyczy dyscypliny żądań, a nie wyglądu:
 * wejście na dashboard nie może wywołać skanu, pierwsze rozwinięcie ma wykonać
 * dokładnie jedno żądanie, kolejne rozwinięcia mają korzystać z pamięci, a „Odśwież”
 * ma świadomie ponowić odczyt. To wszystko jest maszyną stanów, więc daje się
 * sprawdzić wprost, zamiast pośrednio przez renderowanie.
 */

import type { SceneWorkingStorageSummary } from "../api/client";

/** Kolejność prezentacji kategorii. Musi zgadzać się z `CATEGORY_ORDER` w
 *  `backend/services/scene_working_storage.py` — `other` zostaje na końcu, bo istnieje
 *  tylko po to, żeby suma kategorii zawsze równała się `total`. */
export const WORKING_FILE_CATEGORIES = [
  "fullres_cog",
  "display_overview",
  "materialized_view",
  "virtual_view",
  "metadata",
  "incomplete",
  "other",
] as const;

export type WorkingFileCategory = (typeof WORKING_FILE_CATEGORIES)[number];

/** Klucze tłumaczeń. Backend zwraca stabilne klucze angielskie i nie tłumaczy nic sam. */
export const CATEGORY_LABEL_KEYS: Record<string, string> = {
  fullres_cog: "Full-resolution COGs",
  display_overview: "Project overviews",
  materialized_view: "Materialized working views",
  virtual_view: "Virtual views",
  metadata: "Manifests and profiles",
  incomplete: "Incomplete build files",
  other: "Other files",
};

export interface CategoryRow {
  category: string;
  labelKey: string;
  bytes: number;
  fileCount: number;
  sceneCount: number;
}

/**
 * Wiersze kategorii w stałej kolejności.
 *
 * Puste kategorie są pomijane, żeby panel nie był ścianą zer — z jednym wyjątkiem:
 * `incomplete` pokazujemy zawsze. Wiersz „Pliki przejściowe 0 B” niesie informację
 * (nie ma śmieci po przerwanej budowie), a jego zniknięcie byłoby nieodróżnialne od
 * kategorii, której panel nie liczy.
 *
 * Kategoria nieznana frontendowi (backend nowszy niż UI) trafia na koniec listy zamiast
 * wypaść z widoku — inaczej suma wierszy przestałaby zgadzać się z sumą całkowitą.
 */
export function categoryRows(summary: SceneWorkingStorageSummary): CategoryRow[] {
  const categories = summary.categories || {};
  const known = new Set<string>(WORKING_FILE_CATEGORIES);
  const ordered = [
    ...WORKING_FILE_CATEGORIES.filter((name) => name in categories),
    ...Object.keys(categories).filter((name) => !known.has(name)),
  ];
  return ordered
    .map((category) => ({
      category,
      labelKey: CATEGORY_LABEL_KEYS[category] || category,
      bytes: categories[category]?.bytes ?? 0,
      fileCount: categories[category]?.file_count ?? 0,
      sceneCount: categories[category]?.scene_count ?? 0,
    }))
    .filter((row) => row.bytes > 0 || row.fileCount > 0 || row.category === "incomplete");
}

/** Wspólny format rozmiaru — ten sam, którego używał dashboard. */
export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "-";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && amount >= 1024; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
}

/** Godzina odczytu bez daty: panel odświeża się w obrębie jednej sesji. */
export function formatScanTime(generatedAt: string): string {
  const date = new Date(generatedAt);
  if (Number.isNaN(date.getTime())) return generatedAt;
  return date.toLocaleTimeString();
}

// --- maszyna stanów panelu ------------------------------------------------------------

export interface PanelState {
  expanded: boolean;
  loading: boolean;
  /** Wynik ostatniego udanego odczytu. Zachowywany przy zwijaniu, żeby ponowne
   *  rozwinięcie nie kosztowało kolejnego skanu dysku. */
  summary: SceneWorkingStorageSummary | null;
  error: string | null;
  hasLoaded: boolean;
}

export type PanelEvent =
  | { type: "toggle" }
  | { type: "refresh" }
  | { type: "loaded"; summary: SceneWorkingStorageSummary }
  | { type: "failed"; error: string };

export const initialPanelState: PanelState = {
  expanded: false,
  loading: false,
  summary: null,
  error: null,
  hasLoaded: false,
};

export interface PanelTransition {
  state: PanelState;
  /** Czy ten przebieg ma wystrzelić żądanie do backendu. */
  fetch: boolean;
}

/**
 * Jedyne miejsce, które decyduje o odczycie storage.
 *
 * Skan jest wywoływany wyłącznie przy pierwszym rozwinięciu, po nieudanej próbie oraz
 * na jawne „Odśwież”. Zwinięty panel nigdy nie pyta backendu — dlatego wejście na
 * dashboard nie kosztuje ani jednego przejścia po `derived_scenes`.
 */
export function reducePanel(state: PanelState, event: PanelEvent): PanelTransition {
  switch (event.type) {
    case "toggle": {
      const expanded = !state.expanded;
      // Nieudany odczyt zostawia `hasLoaded === false`, więc kolejne rozwinięcie
      // spróbuje ponownie zamiast pokazywać w nieskończoność stary błąd.
      const shouldFetch = expanded && !state.hasLoaded && !state.loading;
      return {
        state: { ...state, expanded, loading: state.loading || shouldFetch },
        fetch: shouldFetch,
      };
    }
    case "refresh": {
      if (state.loading) return { state, fetch: false };
      return { state: { ...state, expanded: true, loading: true, error: null }, fetch: true };
    }
    case "loaded":
      return {
        state: {
          ...state,
          loading: false,
          hasLoaded: true,
          summary: event.summary,
          error: null,
        },
        fetch: false,
      };
    case "failed":
      return {
        state: { ...state, loading: false, hasLoaded: false, error: event.error },
        fetch: false,
      };
    default:
      return { state, fetch: false };
  }
}

/**
 * Czy przycisk otwarcia folderu ma być aktywny.
 *
 * Poza aplikacją desktopową nie ma czego otwierać, a nieistniejącego katalogu nie
 * zakładamy tylko po to, żeby go pokazać — pusty `derived_scenes` byłby nieodróżnialny
 * od katalogu, z którego ktoś świadomie skasował derywaty.
 */
export function canOpenWorkingFolder(
  summary: SceneWorkingStorageSummary | null,
  isDesktop: boolean,
): boolean {
  return Boolean(isDesktop && summary?.directory_exists && summary.working_files_dir);
}
