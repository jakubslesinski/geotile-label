import { describe, expect, it } from "vitest";
import { percentileToValue, resolveViewWindow, roundThreshold } from "./viewStretch";

// Histogram SAR w dziedzinie log1p (wartości zbliżone do typowej sceny SAR Capella).
const SCENE = {
  percentiles: {
    p0: 5.9, "p0.1": 6.1, p1: 6.45, p2: 6.576, p50: 7.006, p98: 7.55, p99: 7.7,
    "p99.8": 8.3, p100: 9.8,
  },
};
// Ciemny fragment sceny (pole): węższy i przesunięty w dół rozkład.
const VIEW = {
  percentiles: { p0: 6.0, p1: 6.3, p2: 6.35, p50: 6.7, p98: 7.1, p99: 7.2, "p99.8": 7.6, p100: 9.1 },
};

describe("percentileToValue", () => {
  it("interpolates between points and reads decimal keys", () => {
    expect(percentileToValue(SCENE, 2)).toBeCloseTo(6.576);
    expect(percentileToValue(SCENE, 99.8)).toBeCloseTo(8.3);
    expect(percentileToValue(SCENE, 99.4)).toBeCloseTo(7.7 + (8.3 - 7.7) * 0.5);
  });

  it("clamps outside the known points", () => {
    expect(percentileToValue(SCENE, -5)).toBe(5.9);
    expect(percentileToValue(SCENE, 150)).toBe(9.8);
    expect(percentileToValue({ percentiles: {} }, 2)).toBeNull();
  });
});

describe("resolveViewWindow", () => {
  const base = { scene: SCENE, stretchLow: 2, stretchHigh: 98, previous: null };

  it("takes the thresholds from the view, not from the scene", () => {
    expect(resolveViewWindow({ ...base, view: VIEW })).toEqual({ min: 6.35, max: 7.1 });
  });

  it("does not stretch at full range", () => {
    expect(resolveViewWindow({ ...base, view: VIEW, stretchLow: 0, stretchHigh: 100 })).toBeNull();
  });

  it("keeps the previous window while the view has no usable statistics", () => {
    const previous = { min: 6.4, max: 7.2 };
    expect(resolveViewWindow({ ...base, view: null, previous })).toBe(previous);
    expect(resolveViewWindow({ ...base, view: { percentiles: {} }, previous })).toBe(previous);
  });

  it("widens a homogeneous view so speckle is not blown to black and white", () => {
    // Morze: bardzo wąski rozkład, okno sceny 2–98% ma szerokość 0,974.
    const sea = { percentiles: { p2: 6.40, p50: 6.45, p98: 6.50 } };
    const window = resolveViewWindow({ ...base, view: sea })!;
    expect(window.max - window.min).toBeCloseTo(0.25 * (7.55 - 6.576), 3);
    expect((window.max + window.min) / 2).toBeCloseTo(6.45, 3);
  });

  it("ignores changes below the hysteresis threshold", () => {
    const previous = { min: 6.35, max: 7.1 };
    const nudged = { percentiles: { ...VIEW.percentiles, p2: 6.36, p98: 7.11 } };
    expect(resolveViewWindow({ ...base, view: nudged, previous })).toBe(previous);
    const moved = { percentiles: { ...VIEW.percentiles, p2: 6.5, p98: 7.3 } };
    expect(resolveViewWindow({ ...base, view: moved, previous })).toEqual({ min: 6.5, max: 7.3 });
  });

  it("rounds thresholds for stable tile URLs", () => {
    expect(roundThreshold(6.576123456)).toBe(6.576);
    expect(roundThreshold(162.34)).toBe(162.3);
    const view = { percentiles: { p2: 6.3512345, p98: 7.0998765 } };
    expect(resolveViewWindow({ ...base, scene: null, view })).toEqual({ min: 6.351, max: 7.1 });
  });
});
