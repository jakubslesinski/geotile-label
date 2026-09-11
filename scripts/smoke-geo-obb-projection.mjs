import assert from "node:assert/strict";

import { projectedRectanglePixels } from "../frontend/src/utils/projectedObb.ts";

const earthRadius = 6378137;
const transform = [2.7e-6, 0, 26.0171784, 0, -2.7e-6, 53.1118404];

function pixelToGeographic(point) {
  const [a, b, c, d, e, f] = transform;
  return {
    lng: a * point.x + b * point.y + c,
    lat: d * point.x + e * point.y + f,
  };
}

function geographicToPixel(point) {
  const [a, b, c, d, e, f] = transform;
  const determinant = a * e - b * d;
  return {
    x: (e * (point.lng - c) - b * (point.lat - f)) / determinant,
    y: (-d * (point.lng - c) + a * (point.lat - f)) / determinant,
  };
}

function project(point) {
  const latitude = Math.max(-85.0511287798, Math.min(85.0511287798, point.lat));
  return {
    x: earthRadius * point.lng * Math.PI / 180,
    y: earthRadius * Math.log(Math.tan(Math.PI / 4 + latitude * Math.PI / 360)),
  };
}

function unproject(point) {
  return {
    lng: point.x / earthRadius * 180 / Math.PI,
    lat: (2 * Math.atan(Math.exp(point.y / earthRadius)) - Math.PI / 2) * 180 / Math.PI,
  };
}

const start = pixelToGeographic({ x: 10000, y: 3000 });
const end = pixelToGeographic({ x: 10120, y: 3110 });
const projectedStart = project(start);
const projectedEnd = project(end);
const axis = {
  x: projectedEnd.x - projectedStart.x,
  y: projectedEnd.y - projectedStart.y,
};
const axisLength = Math.hypot(axis.x, axis.y);
const side = unproject({
  x: projectedStart.x - axis.y / axisLength * 25,
  y: projectedStart.y + axis.x / axisLength * 25,
});

const pixels = projectedRectanglePixels(
  start,
  end,
  side,
  project,
  unproject,
  geographicToPixel
);
assert.ok(pixels && pixels.length === 4);

const projected = pixels.map((point) => project(pixelToGeographic(point)));
const firstEdge = {
  x: projected[1].x - projected[0].x,
  y: projected[1].y - projected[0].y,
};
const sideEdge = {
  x: projected[3].x - projected[0].x,
  y: projected[3].y - projected[0].y,
};
const normalizedDot = Math.abs(
  (firstEdge.x * sideEdge.x + firstEdge.y * sideEdge.y)
  / (Math.hypot(firstEdge.x, firstEdge.y) * Math.hypot(sideEdge.x, sideEdge.y))
);
assert.ok(normalizedDot < 1e-8, `Projected edges are not perpendicular: ${normalizedDot}`);
assert.ok(Math.abs(Math.hypot(sideEdge.x, sideEdge.y) - 25) < 1e-6);

console.log("Projected OBB geometry smoke test passed.");
