import { useState, useEffect, useCallback } from "react";
import * as api from "../api/client";
import type { Annotation, AnnotationGeometryPayload } from "../types";

type AnnotationUpdatePayload = AnnotationGeometryPayload & {
  class_id?: number;
  is_negative?: boolean;
};

type BBox = [number, number, number, number];

function geometryPayload(
  geometry: BBox | AnnotationGeometryPayload,
  isNegative?: boolean
): AnnotationGeometryPayload & { is_negative?: boolean } {
  const payload = Array.isArray(geometry) ? { bbox: geometry } : { ...geometry };
  if (isNegative !== undefined) {
    return { ...payload, is_negative: isNegative };
  }
  return payload;
}

export function useAnnotations(projectId: string | undefined, sceneId: string | undefined) {
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId || !sceneId) return;
    setLoading(true);
    try {
      const data = await api.listAnnotations(projectId, sceneId);
      setAnnotations(data);
    } finally {
      setLoading(false);
    }
  }, [projectId, sceneId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addAnnotation = async (
    classId: number,
    geometry: BBox | AnnotationGeometryPayload,
    isNegative = false
  ) => {
    if (!projectId || !sceneId) return;
    const ann = await api.createAnnotation(projectId, sceneId, {
      class_id: classId,
      ...geometryPayload(geometry, isNegative),
    });
    setAnnotations((prev) => [...prev, ann]);
    return ann;
  };

  const removeAnnotation = async (annId: string) => {
    if (!projectId || !sceneId) return;
    await api.deleteAnnotation(projectId, sceneId, annId);
    setAnnotations((prev) => prev.filter((a) => a.id !== annId));
  };

  const updateAnnotation = async (
    annId: string,
    geometry: BBox | AnnotationUpdatePayload
  ) => {
    if (!projectId || !sceneId) return;
    const ann = await api.updateAnnotation(projectId, sceneId, annId, geometryPayload(geometry));
    setAnnotations((prev) =>
      prev.map((item) => (item.id === annId ? ann : item))
    );
    return ann;
  };

  return { annotations, loading, refresh, addAnnotation, removeAnnotation, updateAnnotation };
}
