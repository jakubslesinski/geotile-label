import { useState, useCallback } from "react";
import * as api from "../api/client";
import type { TileInfo, TileProgress } from "../types";

export function useTileReview(projectId?: string, sceneId?: string) {
  const [tiles, setTiles] = useState<TileInfo[]>([]);
  const [progress, setProgress] = useState<TileProgress>({
    total_all: 0,
    total: 0,
    reviewed: 0,
    positive: 0,
    negative: 0,
    excluded: 0,
  });

  const refresh = useCallback(async () => {
    if (!projectId || !sceneId) return;
    const [tileData, progressData] = await Promise.all([
      api.listTilesForScene(projectId, sceneId),
      api.getTileProgress(projectId, sceneId),
    ]);
    setTiles(tileData);
    setProgress(progressData);
  }, [projectId, sceneId]);

  const toggleReview = useCallback(
    async (index: number) => {
      if (!projectId || !sceneId) return;
      const current = tiles[index];
      if (!current) return;
      const newReviewed = !current.reviewed;
      const stats = await api.reviewTiles(projectId, sceneId, [index], newReviewed);
      setTiles((prev) =>
        prev.map((t, i) =>
          i === index
            ? {
                ...t,
                reviewed: newReviewed,
                review_status: newReviewed ? "reviewed" : "unreviewed",
                excluded: newReviewed ? false : t.excluded,
                exclude_from_dataset: newReviewed ? false : t.exclude_from_dataset,
              }
            : t
        )
      );
      setProgress(stats);
    },
    [projectId, sceneId, tiles]
  );

  const markReviewed = useCallback(
    async (indices: number[]) => {
      if (!projectId || !sceneId || indices.length === 0) return;
      const stats = await api.reviewTiles(projectId, sceneId, indices, true);
      setTiles((prev) =>
        prev.map((t, i) =>
          indices.includes(i)
            ? { ...t, reviewed: true, review_status: "reviewed", excluded: false, exclude_from_dataset: false }
            : t
        )
      );
      setProgress(stats);
    },
    [projectId, sceneId]
  );

  const clearReviewed = useCallback(
    async (indices: number[]) => {
      if (!projectId || !sceneId || indices.length === 0) return;
      const stats = await api.reviewTiles(projectId, sceneId, indices, false);
      setTiles((prev) =>
        prev.map((t, i) =>
          indices.includes(i)
            ? { ...t, reviewed: false, review_status: "unreviewed", reviewed_at: null, reviewed_by: null }
            : t
        )
      );
      setProgress(stats);
    },
    [projectId, sceneId]
  );

  const toggleExclude = useCallback(
    async (index: number) => {
      if (!projectId || !sceneId) return;
      const current = tiles[index];
      if (!current) return;
      const newExcluded = !current.excluded;
      const stats = await api.excludeTiles(projectId, sceneId, [index], newExcluded);
      setTiles((prev) =>
        prev.map((t, i) =>
          i === index
            ? {
                ...t,
                excluded: newExcluded,
                exclude_from_dataset: newExcluded,
                reviewed: newExcluded ? false : t.reviewed,
                review_status: newExcluded ? "unreviewed" : t.review_status,
              }
            : t
        )
      );
      setProgress(stats);
    },
    [projectId, sceneId, tiles]
  );

  const markExcluded = useCallback(
    async (indices: number[]) => {
      if (!projectId || !sceneId || indices.length === 0) return;
      const stats = await api.excludeTiles(projectId, sceneId, indices, true);
      setTiles((prev) =>
        prev.map((t, i) =>
          indices.includes(i)
            ? { ...t, excluded: true, exclude_from_dataset: true, reviewed: false, review_status: "unreviewed" }
            : t
        )
      );
      setProgress(stats);
    },
    [projectId, sceneId]
  );

  return {
    tiles,
    progress,
    refresh,
    toggleReview,
    markReviewed,
    clearReviewed,
    toggleExclude,
    markExcluded,
  };
}
