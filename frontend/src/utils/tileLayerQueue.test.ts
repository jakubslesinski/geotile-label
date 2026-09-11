import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import {
  TILE_RETRY_BASE_DELAY_MS,
  TILE_RETRY_LIMIT,
  attachTileAbort,
  attachTileQueueHygiene,
  attachTileRetry,
} from "./tileLayerQueue";

/** Minimalna atrapa warstwy Leaflet — interesują nas tylko zdarzenia kafli. */
type TileEvent = { tile?: HTMLImageElement | null };

function fakeLayer() {
  const handlers: Record<string, Array<(event: TileEvent) => void>> = {};
  return {
    on(event: string, handler: (event: TileEvent) => void) {
      (handlers[event] ||= []).push(handler);
    },
    off(event: string, handler: (event: TileEvent) => void) {
      handlers[event] = (handlers[event] || []).filter((item) => item !== handler);
    },
    emit(event: string, payload: TileEvent) {
      (handlers[event] || []).forEach((handler) => handler(payload));
    },
    count(event: string) {
      return (handlers[event] || []).length;
    },
  };
}

function fakeTile(src: string, { complete = false, connected = true } = {}) {
  return {
    src,
    complete,
    isConnected: connected,
    dataset: {} as Record<string, string>,
  } as unknown as HTMLImageElement;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("attachTileRetry", () => {
  it("ponawia nieudany kafel z rosnącym opóźnieniem", () => {
    const layer = fakeLayer();
    attachTileRetry(layer);
    const tile = fakeTile("http://x/tile.png");

    layer.emit("tileerror", { tile });
    expect(tile.src).toBe("http://x/tile.png");
    vi.advanceTimersByTime(TILE_RETRY_BASE_DELAY_MS);
    expect(tile.src).toContain("_r=");
    expect(tile.dataset.retries).toBe("1");
  });

  it("przestaje ponawiać po osiągnięciu limitu", () => {
    // Bez limitu trwale niedostępny kafel generowałby nieskończoną burzę żądań.
    const layer = fakeLayer();
    attachTileRetry(layer);
    const tile = fakeTile("http://x/tile.png");

    for (let i = 0; i < TILE_RETRY_LIMIT + 3; i += 1) {
      layer.emit("tileerror", { tile });
      vi.advanceTimersByTime(5000);
    }
    expect(tile.dataset.retries).toBe(String(TILE_RETRY_LIMIT));
  });

  it("nie ponawia kafla, który wypadł z widoku", () => {
    const layer = fakeLayer();
    attachTileRetry(layer);
    const tile = fakeTile("http://x/tile.png", { connected: false });

    layer.emit("tileerror", { tile });
    vi.advanceTimersByTime(5000);
    expect(tile.src).toBe("http://x/tile.png");
  });

  it("sprzątanie usuwa handler i anuluje oczekujące ponowienia", () => {
    const layer = fakeLayer();
    const detach = attachTileRetry(layer);
    const tile = fakeTile("http://x/tile.png");

    layer.emit("tileerror", { tile });
    detach();
    vi.advanceTimersByTime(5000);
    expect(tile.src).toBe("http://x/tile.png");
    expect(layer.count("tileerror")).toBe(0);
  });
});

describe("attachTileAbort", () => {
  it("przerywa kafel, który opuścił widok przed załadowaniem", () => {
    // To jest sygnał, dzięki któremu backend może porzucić pracę nad kaflem.
    const layer = fakeLayer();
    attachTileAbort(layer);
    const tile = fakeTile("http://x/tile.png", { complete: false });

    layer.emit("tileunload", { tile });
    expect(tile.src.startsWith("data:image/png;base64,")).toBe(true);
  });

  it("nie rusza kafla, który zdążył się załadować", () => {
    const layer = fakeLayer();
    attachTileAbort(layer);
    const tile = fakeTile("http://x/tile.png", { complete: true });

    layer.emit("tileunload", { tile });
    expect(tile.src).toBe("http://x/tile.png");
  });
});

describe("attachTileQueueHygiene", () => {
  it("podpina oba zachowania i sprząta oba", () => {
    const layer = fakeLayer();
    const detach = attachTileQueueHygiene(layer);
    expect(layer.count("tileerror")).toBe(1);
    expect(layer.count("tileunload")).toBe(1);
    detach();
    expect(layer.count("tileerror")).toBe(0);
    expect(layer.count("tileunload")).toBe(0);
  });
});
