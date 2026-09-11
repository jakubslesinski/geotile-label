import { describe, it, expect } from "vitest";
import { orientedArrowPoints } from "./orientedArrow";
import type { ArrowRotatedState } from "./orientedArrow";

/**
 * Strzałka kierunku przodu musi leżeć wzdłuż krawędzi `p0 -> p1` ramki — także wtedy,
 * gdy piksel i mapa nie są konforemne. Objaw sprzed poprawki: ramka wychodziła
 * prostokątna, a kreska kierunku przekoszona, bo propozycja AI liczyła ją z pikselowego
 * `front_vector_scene_px`, a przyjęta adnotacja z krawędzi poligonu w układzie
 * rzutowanym. Obie muszą używać tej samej konstrukcji.
 */

/** Anizotropia jak w EPSG:4326 na wysokiej szerokości: oś Y rozciągnięta 2x. */
const STRETCH_Y = 2;
const project = (point: { x: number; y: number }) => ({ x: point.x, y: point.y * STRETCH_Y });
const unproject = (point: { x: number; y: number }) => ({ x: point.x, y: point.y / STRETCH_Y });

/** Prostokąt NA MAPIE (obrócony o 30°), zapisany w pikselach — czyli równoległobok. */
function mapRectangleInPixels(angleDeg: number, halfW: number, halfH: number) {
  const rad = (angleDeg * Math.PI) / 180;
  const ax = { x: Math.cos(rad), y: Math.sin(rad) };
  const nx = { x: -ax.y, y: ax.x };
  const projectedCorners = [
    { x: -ax.x * halfW - nx.x * halfH, y: -ax.y * halfW - nx.y * halfH },
    { x: +ax.x * halfW - nx.x * halfH, y: +ax.y * halfW - nx.y * halfH },
    { x: +ax.x * halfW + nx.x * halfH, y: +ax.y * halfW + nx.y * halfH },
    { x: -ax.x * halfW + nx.x * halfH, y: -ax.y * halfW + nx.y * halfH },
  ].map((point) => ({ x: point.x + 500, y: point.y + 500 }));
  return projectedCorners.map(unproject);
}

const STATE: ArrowRotatedState = {
  center: { x: 500, y: 250 },
  width: 200,
  height: 80,
  angleDeg: 30,
};

function angleBetween(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI;
}

describe("orientedArrowPoints", () => {
  it("kładzie strzałkę wzdłuż krawędzi p0→p1 w układzie mapy", () => {
    const polygon = mapRectangleInPixels(30, 100, 40);
    const arrow = orientedArrowPoints(STATE, polygon, true, project, unproject);

    const edgeAngle = angleBetween(project(polygon[0]), project(polygon[1]));
    const arrowAngle = angleBetween(project(arrow.start), project(arrow.end));
    expect(Math.abs(arrowAngle - edgeAngle)).toBeLessThan(0.01);
  });

  it("bez wspólnej konstrukcji kąty się rozjeżdżają - dowód, że problem był realny", () => {
    const polygon = mapRectangleInPixels(30, 100, 40);
    // Tak liczyła propozycja przed poprawką: kierunek pikselowy z krawędzi poligonu.
    const pixelAngle = angleBetween(polygon[0], polygon[1]);
    const mapAngle = angleBetween(project(polygon[0]), project(polygon[1]));
    expect(Math.abs(mapAngle - pixelAngle)).toBeGreaterThan(5);
  });

  it("strzałka zaczyna się w środku ramki", () => {
    const polygon = mapRectangleInPixels(30, 100, 40);
    const arrow = orientedArrowPoints(STATE, polygon, true, project, unproject);
    const centre = polygon.reduce(
      (acc, point) => ({ x: acc.x + point.x / 4, y: acc.y + point.y / 4 }),
      { x: 0, y: 0 },
    );
    const midpoint = { x: (arrow.start.x + arrow.end.x) / 2, y: (arrow.start.y + arrow.end.y) / 2 };
    expect(Math.hypot(midpoint.x - centre.x, midpoint.y - centre.y)).toBeLessThan(0.5);
  });

  it("obrót wierzchołków o jeden obraca strzałkę o 90° na mapie", () => {
    const polygon = mapRectangleInPixels(30, 100, 40);
    const rotated = [polygon[1], polygon[2], polygon[3], polygon[0]];
    const before = orientedArrowPoints(STATE, polygon, true, project, unproject);
    const after = orientedArrowPoints(STATE, rotated, true, project, unproject);

    const delta =
      angleBetween(project(after.start), project(after.end)) -
      angleBetween(project(before.start), project(before.end));
    const normalised = ((delta % 360) + 540) % 360 - 180;
    expect(Math.abs(Math.abs(normalised) - 90)).toBeLessThan(0.01);
  });

  it("bez poligonu liczy się pikselowo - dla scen bez geo piksel JEST mapą", () => {
    const arrow = orientedArrowPoints(STATE, null, false, null, null);
    expect(Math.abs(angleBetween(arrow.start, arrow.end) - STATE.angleDeg)).toBeLessThan(0.01);
    const midpoint = { x: (arrow.start.x + arrow.end.x) / 2, y: (arrow.start.y + arrow.end.y) / 2 };
    expect(midpoint.x).toBeCloseTo(STATE.center.x, 6);
    expect(midpoint.y).toBeCloseTo(STATE.center.y, 6);
  });
});
