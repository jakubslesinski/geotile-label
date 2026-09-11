import { useState, useCallback } from "react";
import * as api from "../api/client";
import type { Prediction } from "../types";

export function usePredictions(projectId?: string, sceneId?: string) {
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
    detections_so_far: number;
    current_tile?: string;
    model?: string;
    device?: string;
    batch_size?: number;
    resolved_batch_size?: number;
  } | null>(null);

  const load = useCallback(async () => {
    if (!projectId || !sceneId) return;
    try {
      const data = await api.listPredictions(projectId, sceneId);
      setPredictions(data);
    } catch {
      setPredictions([]);
    }
  }, [projectId, sceneId]);

  const run = useCallback(async () => {
    if (!projectId || !sceneId) return;
    setIsRunning(true);
    setProgress(null);

    try {
      const response = await fetch(api.predictionRunUrl(projectId, sceneId), {
        method: "POST",
        headers: api.authHeaders(),
      });
      if (!response.ok) {
        let detail = `Prediction failed (${response.status})`;
        try {
          const errorData = await response.json();
          detail = errorData.detail || detail;
        } catch {
          // ignore non-JSON errors
        }
        throw new Error(detail);
      }
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        let buffer = "";
        let eventName = "message";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              let data: any = {};
              try {
                data = JSON.parse(line.slice(5).trim());
              } catch {
                if (eventName === "error") {
                  throw new Error("Prediction failed");
                }
              }
              if (eventName === "error") {
                throw new Error(data.detail || "Prediction failed");
              }
              if (eventName === "cancelled") {
                break;
              }
              if (data.done !== undefined && data.total !== undefined) {
                setProgress({
                  done: data.done,
                  total: data.total,
                  detections_so_far: data.detections_so_far || 0,
                  current_tile: data.current_tile,
                  model: data.model,
                  device: data.device,
                  batch_size: data.batch_size,
                  resolved_batch_size: data.resolved_batch_size,
                });
              }
              eventName = "message";
            }
          }
        }
      }

      // Reload predictions after completion
      await load();
    } finally {
      setIsRunning(false);
      setProgress(null);
    }
  }, [projectId, sceneId, load]);

  const cancel = useCallback(async () => {
    if (!projectId || !sceneId) return;
    await api.cancelPrediction(projectId, sceneId);
  }, [projectId, sceneId]);

  const acceptPreds = useCallback(
    async (ids: string[]) => {
      if (!projectId || !sceneId) return;
      const result = await api.acceptPredictions(projectId, sceneId, ids);
      await load();
      return result;
    },
    [projectId, sceneId, load]
  );

  const deletePreds = useCallback(
    async (ids: string[]) => {
      if (!projectId || !sceneId || ids.length === 0) return;
      await api.deletePredictions(projectId, sceneId, ids);
      await load();
    },
    [projectId, sceneId, load]
  );

  const acceptAll = useCallback(async () => {
    if (!projectId || !sceneId) return;
    const result = await api.acceptAllPredictions(projectId, sceneId);
    await load();
    return result;
  }, [projectId, sceneId, load]);

  const clearAll = useCallback(async () => {
    if (!projectId || !sceneId) return;
    await api.clearPredictions(projectId, sceneId);
    setPredictions([]);
  }, [projectId, sceneId]);

  const pendingCount = predictions.filter((p) => p.status === "pending").length;
  const acceptedCount = predictions.filter((p) => p.status === "accepted").length;

  return {
    predictions,
    isRunning,
    progress,
    pendingCount,
    acceptedCount,
    load,
    run,
    cancel,
    acceptPreds,
    deletePreds,
    acceptAll,
    clearAll,
  };
}
