import RBush from "rbush";
import type { Annotation } from "../../types";

export type SceneBBox = [number, number, number, number];

export interface IndexedAnnotation {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  id: string;
  ordinal: number;
  annotation: Annotation;
}

function finiteBBox(bbox: SceneBBox): SceneBBox | null {
  if (!bbox.every(Number.isFinite)) return null;
  const [x0, y0, x1, y1] = bbox;
  const minX = Math.min(x0, x1);
  const minY = Math.min(y0, y1);
  const maxX = Math.max(x0, x1);
  const maxY = Math.max(y0, y1);
  if (maxX <= minX || maxY <= minY) return null;
  return [minX, minY, maxX, maxY];
}

/** Exact overlap semantics used by the old linear multi-select implementation.
 * Touching an edge is not an overlap. Keeping this explicit is important because
 * RBush deliberately returns edge-touching candidates as well. */
export function bboxIntersectsStrict(a: SceneBBox, b: SceneBBox): boolean {
  return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
}

/** Rebuildable in-memory index. Annotation JSON remains the source of truth. */
export class AnnotationSpatialIndex {
  private readonly tree = new RBush<IndexedAnnotation>();
  private readonly itemsById = new Map<string, IndexedAnnotation>();

  rebuild(annotations: readonly Annotation[]): void {
    const byId = new Map<string, IndexedAnnotation>();

    annotations.forEach((annotation, ordinal) => {
      const bbox = finiteBBox(annotation.bbox);
      if (!bbox) return;
      const [minX, minY, maxX, maxY] = bbox;
      const item: IndexedAnnotation = {
        minX,
        minY,
        maxX,
        maxY,
        id: annotation.id,
        ordinal,
        annotation,
      };
      byId.set(annotation.id, item);
    });

    const items = [...byId.values()].sort((left, right) => left.ordinal - right.ordinal);
    this.tree.clear();
    if (items.length > 0) this.tree.load(items);
    this.itemsById.clear();
    for (const [id, item] of byId) this.itemsById.set(id, item);
  }

  clear(): void {
    this.tree.clear();
    this.itemsById.clear();
  }

  get size(): number {
    return this.itemsById.size;
  }

  get(id: string): IndexedAnnotation | undefined {
    return this.itemsById.get(id);
  }

  search(bbox: SceneBBox): IndexedAnnotation[] {
    const normalized = finiteBBox(bbox);
    if (!normalized) return [];
    const [minX, minY, maxX, maxY] = normalized;
    return this.tree
      .search({ minX, minY, maxX, maxY })
      .filter((item) => bboxIntersectsStrict(
        [item.minX, item.minY, item.maxX, item.maxY],
        normalized,
      ))
      .sort((left, right) => left.ordinal - right.ordinal);
  }

  searchIds(bbox: SceneBBox): string[] {
    return this.search(bbox).map((item) => item.id);
  }
}
