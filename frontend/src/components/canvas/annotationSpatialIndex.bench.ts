import { bench, describe } from "vitest";
import type { Annotation } from "../../types";
import { AnnotationSpatialIndex } from "./annotationSpatialIndex";
import { annotationGeometryKey, planAnnotationRenderLayer } from "./AnnotationRenderLayer";

function annotations(count: number): Annotation[] {
  const columns = Math.ceil(Math.sqrt(count));
  return Array.from({ length: count }, (_, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = column * 24;
    const y = row * 24;
    return {
      id: `annotation-${index}`,
      class_id: index % 5,
      geometry_type: "bbox",
      bbox: [x, y, x + 12, y + 12],
      is_negative: false,
      created_at: "2026-08-30T00:00:00Z",
    };
  });
}

const tenThousand = annotations(10_000);
const hundredThousand = annotations(100_000);
const largeIndex = new AnnotationSpatialIndex();
largeIndex.rebuild(hundredThousand);
const viewport = largeIndex.search([2_000, 2_000, 3_000, 3_000]);
const shiftedViewport = largeIndex.search([2_240, 2_000, 3_240, 3_000]);

describe("annotation spatial index", () => {
  bench("bulk rebuild 10k", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild(tenThousand);
  });

  bench("bulk rebuild 100k", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild(hundredThousand);
  });

  bench("viewport query in 100k", () => {
    largeIndex.search([2_000, 2_000, 3_000, 3_000]);
  });

  // Plan porownuje odcisk geometrii, wiec benchmark musi podac ten sam ksztalt wejscia
  // co produkcja: mape id -> odcisk. Liczymy ja RAZ, poza pomiarem.
  const renderedEntries = new Map(
    viewport.map((item) => [item.id, { geometryKey: annotationGeometryKey(item.annotation) }]),
  );

  bench("viewport delta plan in 100k", () => {
    planAnnotationRenderLayer(renderedEntries, shiftedViewport);
  });
});
