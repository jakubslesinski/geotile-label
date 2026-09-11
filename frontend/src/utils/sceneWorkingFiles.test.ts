import { describe, expect, it } from "vitest";
import {
  CATEGORY_LABEL_KEYS,
  WORKING_FILE_CATEGORIES,
  canOpenWorkingFolder,
  categoryRows,
  formatBytes,
  formatScanTime,
  initialPanelState,
  reducePanel,
  type PanelState,
} from "./sceneWorkingFiles";
import type { SceneWorkingStorageSummary } from "../api/client";
// `?raw` zamiast `node:fs`: repozytorium nie ma `@types/node`, a `tsc -b` obejmuje
// takze pliki testowe, wiec import z Node wywracalby `npm run build`.
import panelSource from "../components/project/SceneWorkingFilesPanel.tsx?raw";

function category(bytes: number, fileCount = 1, sceneCount = 1) {
  return { bytes, file_count: fileCount, scene_count: sceneCount };
}

function summary(overrides: Partial<SceneWorkingStorageSummary> = {}): SceneWorkingStorageSummary {
  return {
    schema_name: "geotile_scene_working_storage",
    schema_version: 1,
    project_id: "p1",
    generated_at: "2026-09-02T12:41:08.123Z",
    scan_duration_ms: 47,
    working_files_dir: "C:\\projects\\EO_test\\derived_scenes",
    directory_exists: true,
    // Suma kategorii, nie liczba z przykladu w planie — tam total i skladniki
    // sie nie zgadzaja (8 697 308 774 vs 8 711 455 539).
    total: category(8_711_455_539, 27, 4),
    categories: {
      fullres_cog: category(7_301_444_403, 2, 2),
      display_overview: category(1_386_213_376, 4, 4),
      materialized_view: category(23_068_672, 1, 1),
      virtual_view: category(634_880, 8, 4),
      metadata: category(94_208, 12, 4),
      incomplete: category(0, 0, 0),
      other: category(0, 0, 0),
    },
    largest_scenes: [
      { scene_id: "6c14", display_name: "ARSENYEV_0307.jp2", bytes: 5_261_334_938, file_count: 8, categories: {} },
    ],
    scan_errors: 0,
    ...overrides,
  };
}

// --- dyscyplina zadan (bramka P1) -----------------------------------------------------

describe("reducePanel", () => {
  it("nie pyta backendu, dopoki panel jest zwiniety", () => {
    // Wejscie na dashboard renderuje panel w stanie poczatkowym. Gdyby to samo w sobie
    // wywolywalo skan, wrocilaby regresja, przez ktora dashboard czekal na agregaty.
    expect(initialPanelState.expanded).toBe(false);
    expect(initialPanelState.summary).toBeNull();
  });

  it("pierwsze rozwiniecie wykonuje dokladnie jedno zadanie", () => {
    const first = reducePanel(initialPanelState, { type: "toggle" });
    expect(first.fetch).toBe(true);
    expect(first.state.expanded).toBe(true);
    expect(first.state.loading).toBe(true);
  });

  it("drugie rozwiniecie korzysta z pamieci zamiast skanowac ponownie", () => {
    let state = reducePanel(initialPanelState, { type: "toggle" }).state;
    state = reducePanel(state, { type: "loaded", summary: summary() }).state;
    const collapsed = reducePanel(state, { type: "toggle" });
    expect(collapsed.fetch).toBe(false);
    expect(collapsed.state.expanded).toBe(false);
    // Wynik przezywa zwiniecie — inaczej kazde klikniecie kosztowaloby przejscie po dysku.
    expect(collapsed.state.summary).not.toBeNull();

    const reopened = reducePanel(collapsed.state, { type: "toggle" });
    expect(reopened.fetch).toBe(false);
    expect(reopened.state.summary).not.toBeNull();
  });

  it("„Odswiez” jawnie ponawia odczyt", () => {
    let state = reducePanel(initialPanelState, { type: "toggle" }).state;
    state = reducePanel(state, { type: "loaded", summary: summary() }).state;
    const refreshed = reducePanel(state, { type: "refresh" });
    expect(refreshed.fetch).toBe(true);
    expect(refreshed.state.loading).toBe(true);
  });

  it("„Odswiez” w trakcie odczytu nie mnozy zadan", () => {
    const loading: PanelState = { ...initialPanelState, expanded: true, loading: true };
    expect(reducePanel(loading, { type: "refresh" }).fetch).toBe(false);
  });

  it("rozwiniecie w trakcie trwajacego odczytu nie startuje drugiego", () => {
    let state = reducePanel(initialPanelState, { type: "toggle" }).state;
    state = reducePanel(state, { type: "toggle" }).state; // zwiniecie w locie
    expect(reducePanel(state, { type: "toggle" }).fetch).toBe(false);
  });

  it("po bledzie kolejne rozwiniecie probuje jeszcze raz", () => {
    // Blad zwykle jest chwilowy (zajety dysk, przerwane zadanie). Panel, ktory po
    // jednej porazce milczy do konca sesji, wyglada jak zepsuty.
    let state = reducePanel(initialPanelState, { type: "toggle" }).state;
    state = reducePanel(state, { type: "failed", error: "EBUSY" }).state;
    expect(state.loading).toBe(false);
    expect(state.error).toBe("EBUSY");

    state = reducePanel(state, { type: "toggle" }).state; // zwin
    const retry = reducePanel(state, { type: "toggle" });
    expect(retry.fetch).toBe(true);
  });

  it("udany odczyt kasuje poprzedni blad", () => {
    let state = reducePanel(initialPanelState, { type: "toggle" }).state;
    state = reducePanel(state, { type: "failed", error: "EBUSY" }).state;
    state = reducePanel(state, { type: "loaded", summary: summary() }).state;
    expect(state.error).toBeNull();
    expect(state.hasLoaded).toBe(true);
  });
});

// --- wiersze kategorii ----------------------------------------------------------------

describe("categoryRows", () => {
  it("zachowuje kolejnosc kontraktu backendu", () => {
    const rows = categoryRows(summary());
    expect(rows.map((row) => row.category)).toEqual([
      "fullres_cog",
      "display_overview",
      "materialized_view",
      "virtual_view",
      "metadata",
      "incomplete",
    ]);
  });

  it("pomija puste kategorie, ale „pliki przejsciowe” pokazuje zawsze", () => {
    // Wiersz „0 B” niesie informacje: nie ma smieci po przerwanej budowie. Jego brak
    // bylby nieodrozialny od kategorii, ktorej panel nie liczy.
    const rows = categoryRows(summary());
    expect(rows.some((row) => row.category === "other")).toBe(false);
    const incomplete = rows.find((row) => row.category === "incomplete");
    expect(incomplete?.bytes).toBe(0);
  });

  it("pokazuje pliki przejsciowe, gdy sa - scenariusz przerwanego zadania", () => {
    const rows = categoryRows(
      summary({
        categories: { ...summary().categories, incomplete: category(90_000_000_000, 3, 1) },
      }),
    );
    expect(rows.find((row) => row.category === "incomplete")?.bytes).toBe(90_000_000_000);
  });

  it("nieznana kategoria z nowszego backendu ląduje na koncu, a nie w koszu", () => {
    // Wypadniecie z listy rozjechaloby sume wierszy z suma calkowita w naglowku.
    const rows = categoryRows(
      summary({ categories: { ...summary().categories, sidecar_pam: category(4_096, 2, 1) } }),
    );
    expect(rows[rows.length - 1].category).toBe("sidecar_pam");
    expect(rows[rows.length - 1].labelKey).toBe("sidecar_pam");
  });

  it("suma widocznych wierszy zgadza sie z suma calkowita", () => {
    const data = summary();
    const visible = categoryRows(data).reduce((acc, row) => acc + row.bytes, 0);
    const all = Object.values(data.categories).reduce((acc, item) => acc + item.bytes, 0);
    expect(visible).toBe(all);
    expect(all).toBe(data.total.bytes);
  });

  it("kazda kategoria kontraktu ma etykiete", () => {
    for (const name of WORKING_FILE_CATEGORIES) {
      expect(CATEGORY_LABEL_KEYS[name]).toBeTruthy();
    }
  });

  it("nie dopisuje wiersza, ktorego backend nie przyslal", () => {
    // Backend zawsze zwraca komplet siedmiu kategorii, wiec pusty obiekt oznacza
    // odpowiedz niepelna, a nie „zero plikow przejsciowych". Dorysowanie wtedy wiersza
    // „0 B" byloby twierdzeniem o dysku, ktorego nikt nie sprawdzil.
    expect(categoryRows(summary({ categories: {} }))).toEqual([]);
  });
});

// --- otwieranie folderu ---------------------------------------------------------------

describe("canOpenWorkingFolder", () => {
  it("dziala w aplikacji desktopowej, gdy folder istnieje", () => {
    expect(canOpenWorkingFolder(summary(), true)).toBe(true);
  });

  it("jest wylaczone w przegladarce", () => {
    expect(canOpenWorkingFolder(summary(), false)).toBe(false);
  });

  it("jest wylaczone, gdy folder nie istnieje", () => {
    // Zakladanie pustego `derived_scenes` tylko po to, zeby bylo co otworzyc,
    // zacieraloby roznice miedzy „nic nie zbudowano” a „zbudowano i skasowano”.
    expect(canOpenWorkingFolder(summary({ directory_exists: false }), true)).toBe(false);
  });

  it("jest wylaczone przed pierwszym odczytem", () => {
    expect(canOpenWorkingFolder(null, true)).toBe(false);
  });
});

// --- formatowanie ---------------------------------------------------------------------

describe("formatBytes", () => {
  it("dobiera jednostke", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1024)).toBe("1.00 KB");
    expect(formatBytes(8_711_455_539)).toBe("8.11 GB");
  });

  it("nie udaje, ze zna rozmiar, ktorego nie dostal", () => {
    expect(formatBytes(Number.NaN)).toBe("-");
    expect(formatBytes(-1)).toBe("-");
  });
});

describe("formatScanTime", () => {
  it("zwraca wejscie bez zmian, gdy nie jest data", () => {
    expect(formatScanTime("nigdy")).toBe("nigdy");
  });

  it("formatuje poprawny znacznik czasu", () => {
    expect(formatScanTime("2026-09-02T12:41:08.123Z")).not.toBe("2026-09-02T12:41:08.123Z");
  });
});

// --- brak akcji niszczacych (bramka P1) -----------------------------------------------

describe("SceneWorkingFilesPanel", () => {
  // Repozytorium nie ma stosu do testow DOM (brak jsdom i @testing-library), a
  // dokladanie go w trakcie testow instalatora byloby wieksza zmiana niz sam panel.
  // Zrodlo komponentu jest tu wystarczajacym swiadkiem: bramka mowi „nie ma w UI zadnej
  // sciezki do kasowania", a sciezka do kasowania musi byc widoczna w kodzie.
  const source = panelSource;

  it("nie wola zadnego endpointu kasujacego", () => {
    for (const call of ["clearTilePreviewCache", "clearLegacyTileCache", "api.delete"]) {
      expect(source).not.toContain(call);
    }
  });

  it("nie oferuje w interfejsie czyszczenia ani usuwania", () => {
    // Rollback planu jest tu jednoznaczny: bezpiecznym stanem awaryjnym jest brak
    // panelu, nie powrot do przyciskow kasujacych.
    for (const label of ["Clear ", "Remove ", "Delete ", "Wyczyść", "Usuń"]) {
      expect(source).not.toContain(label);
    }
  });

  it("bierze sciezke folderu wylacznie z odpowiedzi backendu", () => {
    expect(source).toContain("openPathInFileManager(summary.working_files_dir)");
    // Bez komentarzy: nazwa katalogu ma nie wystepowac w KODZIE. Skladanie sciezki po
    // stronie frontendu otworzyloby przy pierwszej rozbieznosci cudzy folder.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*/g, "");
    expect(code).not.toContain("derived_scenes");
    expect(code).not.toContain("project_root");
  });
});
