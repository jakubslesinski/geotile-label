export interface GeometryPoint {
  x: number;
  y: number;
}

export interface GeographicPoint {
  lat: number;
  lng: number;
}

type ProjectPoint = (point: GeographicPoint) => GeometryPoint;
type UnprojectPoint = (point: GeometryPoint) => GeographicPoint;
type GeographicToPixel = (point: GeographicPoint) => GeometryPoint;

export function projectedRectanglePixels(
  start: GeographicPoint,
  end: GeographicPoint,
  side: GeographicPoint,
  project: ProjectPoint,
  unproject: UnprojectPoint,
  geographicToPixel: GeographicToPixel
): GeometryPoint[] | null {
  const projectedStart = project(start);
  const projectedEnd = project(end);
  const projectedSide = project(side);
  const dx = projectedEnd.x - projectedStart.x;
  const dy = projectedEnd.y - projectedStart.y;
  const length = Math.hypot(dx, dy);
  if (!Number.isFinite(length) || length <= 1e-9) return null;

  const normal = { x: -dy / length, y: dx / length };
  const signedHeight = (
    (projectedSide.x - projectedStart.x) * normal.x
    + (projectedSide.y - projectedStart.y) * normal.y
  );
  if (!Number.isFinite(signedHeight) || Math.abs(signedHeight) <= 1e-9) return null;

  const projectedCorners = [
    projectedStart,
    projectedEnd,
    {
      x: projectedEnd.x + normal.x * signedHeight,
      y: projectedEnd.y + normal.y * signedHeight,
    },
    {
      x: projectedStart.x + normal.x * signedHeight,
      y: projectedStart.y + normal.y * signedHeight,
    },
  ];

  const pixels = projectedCorners.map((point) => geographicToPixel(unproject(point)));
  return pixels.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
    ? pixels
    : null;
}
