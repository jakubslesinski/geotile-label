/**
 * Edycja aplikacji ustalana przy budowaniu (build-time).
 *
 * "lite" to uproszczona wersja dla użytkowników końcowych: ukrywa zaawansowane zakładki
 * projektu — Analiza datasetu, Trening i Wyniki — zostawiając kompletowanie danych i
 * labelowanie. Włączana zmienną środowiskową `VITE_GEOTILE_EDITION=lite` przed buildem
 * (Vite wstrzykuje ją do `import.meta.env`). Brak flagi = pełna edycja (domyślnie).
 */
export const isLiteEdition = import.meta.env.VITE_GEOTILE_EDITION === "lite";

/** Ścieżki sekcji projektu ukrywane w edycji "lite" (sufiksy tras). */
export const LITE_HIDDEN_SECTIONS = ["/analysis", "/training", "/results"] as const;
