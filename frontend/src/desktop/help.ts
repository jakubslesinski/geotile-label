import { isTauriRuntime } from "./dialogs";
import { LANGUAGE_STORAGE_KEY } from "../i18n";

/**
 * Dokumentacja jest budowana przez `scripts/build-docs.ps1` do `frontend/public/help`,
 * więc w obu trybach — Vite i zbudowany bundle Tauri — leży pod tym samym adresem.
 */
export const HELP_BASE_PATH = "/help/";

/** Domyślna strona otwierana przez przycisk Pomoc i klawisz F1. */
export const HELP_HOME = "index.html";

function normalizeHelpPage(page?: string): string {
  const trimmed = (page ?? "").trim().replace(/^\/+/, "");
  return trimmed.length > 0 ? trimmed : HELP_HOME;
}

function localizedHelpPage(page?: string): string {
  const target = normalizeHelpPage(page);
  const stored = typeof window !== "undefined"
    ? window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    : null;
  // Odwzorowanie musi zgadzac sie z `mkdocs.yml`: jezyk DOMYSLNY serwisu lezy w jego
  // korzeniu, pozostale w podkatalogach. Domyslnym locale MkDocs-a jest `pl` (pliki
  // bazowe w `docs/` sa polskie), wiec angielski jest pod `en/`.
  //
  // Brak zapisanej wartosci znaczy angielski — tak samo jak w `i18n.ts` — inaczej
  // aplikacja startowalaby po angielsku, a F1 otwieralo polska dokumentacje.
  return stored === "pl" ? target : `en/${target}`;
}

/**
 * Otwiera dokumentację offline. W aplikacji desktopowej jest to osobne okno Tauri,
 * ponownie użyte przy kolejnym wywołaniu; w przeglądarce — nowa karta pod tym
 * samym adresem, dzięki czemu tryb dev nie wymaga osobnej ścieżki.
 *
 * `page` może wskazywać konkretną stronę i kotwicę, np.
 * `datasets/audyt.html#spatial-leakage`.
 */
export async function openHelp(page?: string): Promise<void> {
  const target = localizedHelpPage(page);

  if (!isTauriRuntime()) {
    window.open(`${HELP_BASE_PATH}${target}`, "geotile-help", "noopener");
    return;
  }

  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("open_help_window", { path: target });
}
