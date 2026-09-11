import { describe, expect, it } from "vitest";
import type { Annotation } from "../../types";
import {
  AnnotationSpatialIndex,
  bboxIntersectsStrict,
} from "./annotationSpatialIndex";
import { expandSceneBBox, hasAnnotationDetailScreenSpace } from "./annotationBuffers";

function annotation(id: string, bbox: [number, number, number, number]): Annotation {
  return {
    id,
    class_id: 1,
    geometry_type: "bbox",
    bbox,
    is_negative: false,
    created_at: "2026-08-30T00:00:00Z",
  };
}

describe("AnnotationSpatialIndex", () => {
  it("returns strict overlaps in original annotation order", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild([
      annotation("first", [50, 50, 80, 80]),
      annotation("outside", [500, 500, 520, 520]),
      annotation("second", [10, 10, 40, 40]),
    ]);

    expect(index.searchIds([0, 0, 100, 100])).toEqual(["first", "second"]);
  });

  it("does not select boxes that only touch the selection edge", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild([
      annotation("touch-left", [0, 10, 10, 20]),
      annotation("inside", [10.01, 10, 20, 20]),
      annotation("touch-right", [30, 10, 40, 20]),
    ]);

    expect(index.searchIds([10, 0, 30, 30])).toEqual(["inside"]);
    expect(bboxIntersectsStrict([0, 0, 10, 10], [10, 0, 20, 10])).toBe(false);
  });

  it("normalizes reversed boxes and skips invalid geometry", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild([
      annotation("reversed", [20, 20, 10, 10]),
      annotation("zero", [1, 1, 1, 2]),
      annotation("nan", [Number.NaN, 0, 1, 1]),
    ]);

    expect(index.size).toBe(1);
    expect(index.searchIds([0, 0, 30, 30])).toEqual(["reversed"]);
  });

  it("rebuild replaces stale entries", () => {
    const index = new AnnotationSpatialIndex();
    index.rebuild([annotation("old", [0, 0, 10, 10])]);
    index.rebuild([annotation("new", [100, 100, 110, 110])]);

    expect(index.searchIds([0, 0, 20, 20])).toEqual([]);
    expect(index.searchIds([90, 90, 120, 120])).toEqual(["new"]);
  });

  it("uses the scene AABB of an OBB for viewport and multi-select candidates", () => {
    const rotated = annotation("obb", [40, 40, 80, 90]);
    rotated.geometry_type = "rotated_bbox";
    rotated.rotated_bbox = { cx: 60, cy: 65, width: 50, height: 20, angle_deg: 32 };
    rotated.polygon_scene_px = [[43, 43], [85, 74], [77, 87], [35, 56]];
    const index = new AnnotationSpatialIndex();
    index.rebuild([rotated]);

    expect(index.searchIds([70, 70, 100, 100])).toEqual(["obb"]);
    expect(index.searchIds([81, 91, 100, 110])).toEqual([]);
  });
});

describe("expandSceneBBox", () => {
  it("adds a clamped viewport buffer", () => {
    expect(expandSceneBBox([0, 20, 100, 120], 1000, 1000, 0.1, 16)).toEqual([
      0, 4, 116, 136,
    ]);
  });

  it("enables details only when geometry is large enough on screen", () => {
    expect(hasAnnotationDetailScreenSpace([{ x: 0, y: 0 }, { x: 47, y: 10 }])).toBe(false);
    expect(hasAnnotationDetailScreenSpace([{ x: 0, y: 0 }, { x: 48, y: 10 }])).toBe(true);
  });
});
