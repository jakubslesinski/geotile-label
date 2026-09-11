import { describe, expect, it } from "vitest";
import type { Annotation } from "../../types";
import type { IndexedAnnotation } from "./annotationSpatialIndex";
import {
  annotationGeometryKey,
  planAnnotationRenderLayer,
  type RenderedAnnotationEntry,
} from "./AnnotationRenderLayer";

function item(id: string, ordinal: number): IndexedAnnotation {
  const annotation: Annotation = {
    id,
    class_id: 1,
    geometry_type: "bbox",
    bbox: [ordinal, ordinal, ordinal + 1, ordinal + 1],
    is_negative: false,
    created_at: "2026-08-30T00:00:00Z",
  };
  return {
    id,
    ordinal,
    annotation,
    minX: ordinal,
    minY: ordinal,
    maxX: ordinal + 1,
    maxY: ordinal + 1,
  };
}

/** Ramka zorientowana niesiona jako poligon — tak wygląda ramka wierna mapie. */
function rotated(
  id: string,
  polygon: [number, number][],
  overrides: Partial<Annotation> = {},
): IndexedAnnotation {
  const xs = polygon.map((point) => point[0]);
  const ys = polygon.map((point) => point[1]);
  const annotation: Annotation = {
    id,
    class_id: 1,
    geometry_type: "rotated_bbox",
    bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
    polygon_scene_px: polygon,
    rotated_bbox: { cx: 50, cy: 50, width: 20, height: 10, angle_deg: 30 },
    is_negative: false,
    created_at: "2026-08-30T00:00:00Z",
    ...overrides,
  };
  return {
    id,
    ordinal: 0,
    annotation,
    minX: annotation.bbox[0],
    minY: annotation.bbox[1],
    maxX: annotation.bbox[2],
    maxY: annotation.bbox[3],
  };
}

function renderedFrom(...items: IndexedAnnotation[]): Map<string, RenderedAnnotationEntry> {
  return new Map(
    items.map((entry) => [entry.id, { geometryKey: annotationGeometryKey(entry.annotation) }]),
  );
}

describe("planAnnotationRenderLayer", () => {
  it("keeps the overlap and changes only the viewport delta", () => {
    const keep = item("keep", 1);
    const rendered = renderedFrom(keep);
    rendered.set("outside", { geometryKey: "whatever" });

    const plan = planAnnotationRenderLayer(rendered, [keep, item("new", 2)]);

    expect(plan.keepIds).toEqual(["keep"]);
    expect(plan.add.map((entry) => entry.id)).toEqual(["new"]);
    expect(plan.removeIds).toEqual(["outside"]);
    expect(plan.changed).toEqual([]);
  });

  // --- regresja: edycja ramki musi przebudować warstwę --------------------------------
  //
  // Do 2026-09-04 plan patrzył wyłącznie na `id`, więc edytowana adnotacja trafiała do
  // `keepIds`. Warstwa kształtu, strzałka orientacji i poligon startowy uchwytów zostawały
  // z domknięcia sprzed edycji: strzałka rysowała się w poprzednim położeniu, a kolejna
  // edycja narożnika liczyła się względem nieaktualnego poligonu i wychodziła przekoszona.

  it("przenosi edytowaną ramkę do `changed`, a nie do `keepIds`", () => {
    const before = rotated("obb", [[10, 10], [30, 12], [28, 22], [8, 20]]);
    const after = rotated("obb", [[10, 10], [30, 12], [29, 26], [9, 24]]);

    const plan = planAnnotationRenderLayer(renderedFrom(before), [after]);

    expect(plan.changed.map((entry) => entry.id)).toEqual(["obb"]);
    expect(plan.keepIds).toEqual([]);
    expect(plan.add).toEqual([]);
    expect(plan.removeIds).toEqual([]);
  });

  it("wykrywa zmianę SAMEGO poligonu, przy niezmienionym rotated_bbox i bbox", () => {
    // Sedno błędu na scenach niekonforemnych: prostokąt opisujący i `rotated_bbox` mogą
    // zostać takie same, a mimo to ramka zmienia kształt (ścinanie). Odcisk liczony bez
    // poligonu przepuściłby tę zmianę jako „bez zmian".
    const polygonA: [number, number][] = [[0, 0], [20, 0], [20, 10], [0, 10]];
    const polygonB: [number, number][] = [[0, 0], [20, 0], [18, 10], [2, 10]];
    const before = rotated("shear", polygonA);
    const after = rotated("shear", polygonB, { bbox: before.annotation.bbox });

    expect(after.annotation.bbox).toEqual(before.annotation.bbox);
    expect(after.annotation.rotated_bbox).toEqual(before.annotation.rotated_bbox);

    const plan = planAnnotationRenderLayer(renderedFrom(before), [after]);
    expect(plan.changed.map((entry) => entry.id)).toEqual(["shear"]);
  });

  it("zmiana klasy przebudowuje warstwę, bo kolor pochodzi z klasy", () => {
    const before = item("ann", 1);
    const after = item("ann", 1);
    after.annotation = { ...after.annotation, class_id: 7 };

    const plan = planAnnotationRenderLayer(renderedFrom(before), [after]);
    expect(plan.changed.map((entry) => entry.id)).toEqual(["ann"]);
  });

  it("nieruszona ramka zostaje w `keepIds` - panoramowanie nie przebudowuje warstwy", () => {
    const unchanged = rotated("obb", [[10, 10], [30, 12], [28, 22], [8, 20]]);
    const plan = planAnnotationRenderLayer(renderedFrom(unchanged), [unchanged]);

    expect(plan.keepIds).toEqual(["obb"]);
    expect(plan.changed).toEqual([]);
  });
});

describe("annotationGeometryKey", () => {
  it("jest stabilny dla tej samej geometrii", () => {
    const entry = rotated("obb", [[1, 2], [3, 4], [5, 6], [7, 8]]);
    expect(annotationGeometryKey(entry.annotation)).toBe(
      annotationGeometryKey({ ...entry.annotation }),
    );
  });

  it("nie myli poligonu z prostokątem opisującym", () => {
    const square = rotated("a", [[0, 0], [10, 0], [10, 10], [0, 10]]);
    const sheared = rotated("a", [[0, 0], [10, 0], [8, 10], [2, 10]], {
      bbox: square.annotation.bbox,
    });
    expect(annotationGeometryKey(square.annotation)).not.toBe(
      annotationGeometryKey(sheared.annotation),
    );
  });

  it("reaguje na zmianę `is_negative`", () => {
    const positive = item("n", 1);
    const negative = item("n", 1);
    negative.annotation = { ...negative.annotation, is_negative: true };
    expect(annotationGeometryKey(positive.annotation)).not.toBe(
      annotationGeometryKey(negative.annotation),
    );
  });
});
