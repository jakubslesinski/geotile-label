import { useEffect, useRef, useState } from "react";
import * as api from "../api/client";
import type { SceneViewStats } from "../types";
import { VIEW_STRETCH_DEBOUNCE_MS } from "../utils/viewStretch";

export type ViewStretchStatus = "idle" | "loading" | "ready" | "insufficient" | "error";

export type SceneBounds = [number, number, number, number];

interface Options {
  projectId?: string;
  sceneId?: string;
  /** Rewizja zasobu wyświetlania — odpowiedź dla innej rewizji jest odrzucana. */
  revision?: string | null;
  /** Zakres „Widok” aktywny. */
  enabled: boolean;
  /** Zamrożone progi: statystyki zostają z widoku, w którym kliknięto kłódkę. */
  frozen: boolean;
  /** Ostatni zakończony widok mapy w pikselach siatki sceny. */
  bounds: SceneBounds | null;
}

/** Klucz, do którego należą statystyki: projekt, scena i rewizja zasobu wyświetlania. */
export function viewStretchKey(projectId?: string, sceneId?: string, revision?: string | null): string {
  return `${projectId ?? ""}/${sceneId ?? ""}/${revision ?? ""}`;
}

/**
 * Statystyki bieżącego widoku dla zakresu rozciągnięcia „Widok” (DESIGN_DECISIONS.md, display-stretch D).
 *
 * Jedno zapytanie na zakończony ruch mapy (opóźnienie po `moveend`), poprzednie jest
 * anulowane. Przy braku danych w widoku zostają poprzednie statystyki — obraz nie przeskakuje
 * wtedy na chwilę do innych progów. Statystyki są przypisane do klucza sceny: po zmianie
 * sceny albo rewizji znikają od razu, a nie dopiero po efekcie czyszczącym.
 */
export function useViewStretch({ projectId, sceneId, revision, enabled, frozen, bounds }: Options) {
  const key = viewStretchKey(projectId, sceneId, revision);
  const [result, setResult] = useState<{ key: string; stats: SceneViewStats } | null>(null);
  const [status, setStatus] = useState<{ key: string; value: ViewStretchStatus }>({ key, value: "idle" });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) {
      abortRef.current?.abort();
      setResult(null);
      setStatus({ key, value: "idle" });
    }
  }, [enabled, key]);

  useEffect(() => {
    if (!enabled || frozen || !projectId || !sceneId || !bounds) return undefined;
    const timer = window.setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus({ key, value: "loading" });
      api.getSceneViewStats(projectId, sceneId, bounds, controller.signal)
        .then((next) => {
          if (controller.signal.aborted) return;
          if (revision && next.display_revision && next.display_revision !== revision) return;
          if (next.insufficient) {
            setStatus({ key, value: "insufficient" });
            return;
          }
          setResult({ key, stats: next });
          setStatus({ key, value: "ready" });
        })
        .catch((error) => {
          if (controller.signal.aborted || error?.code === "ERR_CANCELED") return;
          setStatus({ key, value: "error" });
        });
    }, VIEW_STRETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [enabled, frozen, projectId, sceneId, revision, bounds, key]);

  useEffect(() => () => abortRef.current?.abort(), []);

  return {
    key,
    stats: result && result.key === key ? result.stats : null,
    status: status.key === key ? status.value : "idle",
  };
}
