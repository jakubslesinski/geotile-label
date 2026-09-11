import type { SceneBBox } from "./annotationSpatialIndex";

export const ANNOTATION_VIEWPORT_PADDING_RATIO = 0.15;
export const ANNOTATION_VIEWPORT_MIN_PADDING_PX = 64;
export const ANNOTATION_DETAIL_MIN_SCREEN_PX = 48;

export interface ScreenPoint {
  x: number;
  y: number;
}

export function expandSceneBBox(
  bbox: SceneBBox,
  sceneWidth: number,
  sceneHeight: number,
  ratio = ANNOTATION_VIEWPORT_PADDING_RATIO,
  minimumPadding = ANNOTATION_VIEWPORT_MIN_PADDING_PX,
): SceneBBox {
  const [x0, y0, x1, y1] = bbox;
  const padX = Math.max(minimumPadding, (x1 - x0) * ratio);
  const padY = Math.max(minimumPadding, (y1 - y0) * ratio);
  return [
    Math.max(0, x0 - padX),
    Math.max(0, y0 - padY),
    Math.min(sceneWidth, x1 + padX),
    Math.min(sceneHeight, y1 + padY),
  ];
}

export function hasAnnotationDetailScreenSpace(
  points: readonly ScreenPoint[],
  threshold = ANNOTATION_DETAIL_MIN_SCREEN_PX,
): boolean {
  if (points.length === 0) return false;
  const width = Math.max(...points.map((point) => point.x)) - Math.min(...points.map((point) => point.x));
  const height = Math.max(...points.map((point) => point.y)) - Math.min(...points.map((point) => point.y));
  return Math.max(width, height) >= threshold;
}
