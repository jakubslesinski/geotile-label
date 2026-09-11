/**
 * Higiena kolejki żądań kafli (R0.5).
 *
 * Dwie rzeczy, których Leaflet nie robi sam:
 *
 * 1. **Nie przerywa żądań kafli, które wypadły z widoku.** Przy panoramowaniu przeglądarka
 *    dalej pobiera kafle poprzedniego kadru, a każdy z nich zajmuje po stronie serwera
 *    workera. Przy JP2 to sekundy na kafel, którego nikt już nie zobaczy. Backend jest na
 *    to przygotowany — pas szeregujący sprawdza `request.is_disconnected()` — ale nigdy
 *    nie dostawał sygnału, bo przeglądarka nie zrywała połączenia.
 *
 * 2. **Nie ponawia kafla, który się nie udał.** Wolny albo chwilowo przeciążony backend
 *    zostawia trwałą szachownicę pustych kafli aż do ręcznego przesunięcia mapy.
 */

/** Najmniejszy poprawny PNG — podmiana `src` na niego przerywa trwające żądanie. */
const BLANK_PNG =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

export const TILE_RETRY_LIMIT = 3;
export const TILE_RETRY_BASE_DELAY_MS = 400;

type TileEventLike = { tile?: HTMLImageElement | null };

/**
 * Granica jest celowo luźno typowana. `L.TileLayer.on` ma przeciążenia z literalnymi
 * nazwami zdarzeń, więc strukturalny typ z `event: string` nie jest do niego
 * przypisywalny. Rozluźnienie dotyczy wyłącznie sygnatury wejściowej — handler w środku
 * pozostaje silnie typowany, a moduł nadal nie zależy od Leafleta i da się go testować
 * na atrapie.
 */
type LayerLike = {
  on: (...args: any[]) => unknown;
  off?: (...args: any[]) => unknown;
};

/**
 * Ponawia nieudane kafle z rosnącym opóźnieniem, ograniczoną liczbę razy.
 *
 * Limit jest istotny: bez niego trwale niedostępny kafel (np. zoom powyżej tego, co
 * backend potrafi obsłużyć) generowałby nieskończoną burzę żądań.
 */
export function attachTileRetry(
  layer: LayerLike,
  { limit = TILE_RETRY_LIMIT, baseDelayMs = TILE_RETRY_BASE_DELAY_MS } = {},
): () => void {
  const timers = new Set<ReturnType<typeof setTimeout>>();

  const handler = (event: TileEventLike) => {
    const tile = event?.tile;
    if (!tile) return;
    const attempts = Number(tile.dataset.retries || 0);
    if (attempts >= limit) return;
    tile.dataset.retries = String(attempts + 1);
    const baseUrl = (tile.src || "").split("&_r=")[0];
    if (!baseUrl || baseUrl === BLANK_PNG) return;
    // Globalny `setTimeout`, nie `window.` — moduł nie potrzebuje DOM-u i dzięki temu
    // daje się przetestować bez emulacji przeglądarki.
    const timer = setTimeout(() => {
      timers.delete(timer);
      // Kafel mógł w międzyczasie wypaść z widoku — wtedy ponawianie nie ma sensu.
      if (!tile.isConnected) return;
      tile.src = `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}_r=${Date.now()}`;
    }, baseDelayMs * (attempts + 1));
    timers.add(timer);
  };

  layer.on("tileerror", handler);
  return () => {
    layer.off?.("tileerror", handler);
    timers.forEach((timer) => clearTimeout(timer));
    timers.clear();
  };
}

/**
 * Przerywa pobieranie kafli, które opuściły widok, zanim się załadowały.
 *
 * Zerwanie połączenia jest tym, co pozwala backendowi porzucić pracę — bez tego jego
 * kontrola `is_disconnected()` nigdy nie zadziała.
 */
export function attachTileAbort(layer: LayerLike): () => void {
  const handler = (event: TileEventLike) => {
    const tile = event?.tile;
    // `complete` jest prawdą także dla kafla, który zakończył się błędem — i dobrze:
    // tam nie ma czego przerywać.
    if (!tile || tile.complete) return;
    tile.src = BLANK_PNG;
  };

  layer.on("tileunload", handler);
  return () => {
    layer.off?.("tileunload", handler);
  };
}

/** Obie poprawki naraz; zwraca jedną funkcję sprzątającą. */
export function attachTileQueueHygiene(layer: LayerLike, options?: { limit?: number; baseDelayMs?: number }): () => void {
  const detachRetry = attachTileRetry(layer, options);
  const detachAbort = attachTileAbort(layer);
  return () => {
    detachRetry();
    detachAbort();
  };
}
