import { useEffect, useRef, useCallback, useState } from "react";
import { Box, HStack, Input, Button, Text, useToast } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import L from "leaflet";
import proj4 from "proj4";
import { latLonToMgrs, formatLatLon, parseCoordinateInput } from "../../utils/mgrs";
import { attachTileQueueHygiene } from "../../utils/tileLayerQueue";
import { projectedRectanglePixels } from "../../utils/projectedObb";
import {
  AnnotationSpatialIndex,
  type IndexedAnnotation,
} from "./annotationSpatialIndex";
import {
  ANNOTATION_DETAIL_MIN_SCREEN_PX,
  ANNOTATION_VIEWPORT_MIN_PADDING_PX,
  ANNOTATION_VIEWPORT_PADDING_RATIO,
  expandSceneBBox,
  hasAnnotationDetailScreenSpace,
} from "./annotationBuffers";
import { orientedArrowPoints } from "./orientedArrow";
import { annotationGeometryKey, planAnnotationRenderLayer } from "./AnnotationRenderLayer";
import { PIXEL_TILE_ZOOM_DELTA, PIXEL_TILE_ZOOM_SNAP } from "../../utils/pixelTileZoom";
import "leaflet/dist/leaflet.css";
import type {
  Annotation,
  AnnotationGeometryPayload,
  AnnotationMode,
  LabelClass,
  MapTool,
  Prediction,
  TilingPreview,
  TileInfo,
} from "../../types";

const PIXEL_OVERZOOM = 4;
/** Above this visible-shape count, individual Leaflet layer objects dominate RAM and
 * main-thread time even with the Canvas renderer. A single batched Canvas takes over;
 * zooming back below the limit restores fully editable Leaflet layers. */
const BATCHED_ANNOTATION_CANVAS_THRESHOLD = 5_000;

function replaceTileLayerUrl(layer: L.TileLayer, url: string) {
  // Update the template and force one redraw. Without it, WebView/Leaflet can
  // retain visible tile elements until the next pan or zoom.
  layer.setUrl(url, true);
  layer.redraw();
}

/** Po tym czasie podmiana warstwy konczy sie nawet bez `load` (np. widok poza scena). */
const TILE_SWAP_TIMEOUT_MS = 4000;

interface PendingTileSwap {
  layer: L.TileLayer;
  detach: () => void;
  timer: number;
}

function tileLayerUrl(layer: L.TileLayer): string | undefined {
  return (layer as unknown as { _url?: string })._url;
}

function cancelTileSwap(map: L.Map | null, pendingRef: { current: PendingTileSwap | null }) {
  const pending = pendingRef.current;
  if (!pending) return;
  window.clearTimeout(pending.timer);
  pending.detach();
  map?.removeLayer(pending.layer);
  pendingRef.current = null;
}

/**
 * Podmiana URL warstwy kafli bez pustego ekranu (DESIGN_DECISIONS.md, display-stretch D).
 *
 * `setUrl` + `redraw` najpierw zdejmowalo wszystkie kafle, wiec kazda zmiana nastaw - takze
 * automatyczna zmiana progow w zakresie "Widok" - mrugala pusta mapa. Nowa warstwa laduje
 * sie NAD stara; stara znika dopiero po `load` nowej albo po limicie czasu. Kolejna zmiana
 * w trakcie porzuca warstwe oczekujaca, a nie widoczna.
 */
function swapTileLayer(
  map: L.Map,
  current: L.TileLayer,
  url: string,
  pendingRef: { current: PendingTileSwap | null },
  commit: (layer: L.TileLayer, detach: () => void) => void,
) {
  cancelTileSwap(map, pendingRef);
  if (tileLayerUrl(current) === url) {
    // Ten sam adres (np. zmiana dostepnego zoomu natywnego) - wystarczy przerysowac.
    replaceTileLayerUrl(current, url);
    return;
  }
  const layer = L.tileLayer(url, { ...current.options });
  const detach = attachTileQueueHygiene(layer);
  const finish = () => {
    const pending = pendingRef.current;
    if (!pending || pending.layer !== layer) return;
    window.clearTimeout(pending.timer);
    pendingRef.current = null;
    commit(layer, detach);
  };
  pendingRef.current = { layer, detach, timer: window.setTimeout(finish, TILE_SWAP_TIMEOUT_MS) };
  layer.once("load", finish);
  layer.addTo(map);
}

/** Convert L.LatLng to [lat, lng] tuple for type-safe Leaflet API calls */
const toTuple = (ll: L.LatLng): [number, number] => [ll.lat, ll.lng];
/** Renderer warstw wektorowych: canvas zamiast SVG (DESIGN_DECISIONS.md, performance-audit E5).
 *
 * Domyslny renderer Leafleta (SVG) tworzy WEZEL DOM NA KAZDY ksztalt, wiec na najgestszej
 * scenie DOTA (10 206 adnotacji) przegladarka placi layoutem i paintem przy kazdym pan/zoom.
 * Canvas rysuje wszystko do jednego elementu.
 *
 * Zakres zysku jest ograniczony i warto o tym pamietac: etykiety adnotacji sa wiazane jako
 * `permanent: true` tooltipy, ktore sa DOM-em NIEZALEZNIE od renderera — a `showAnnotationLabels`
 * domyslnie jest wlaczone. Przy wlaczonych etykietach canvas zdejmuje ksztalty, ale zostawia
 * tyle samo wezlow tooltipow (patrz uwaga o etykietach w E5 w planie).
 *
 * Wylaczenie: ustaw na `false` i odbuduj — cala zmiana E5 sprowadza sie do tej flagi.
 */
const PREFER_CANVAS = true;

type PixelRectLayer = L.Rectangle | L.Polygon;
/** Wszystko, czego `syncSelection` potrzebuje, by przestylowac adnotacje i zbudowac jej
 *  uchwyty BEZ odtwarzania geometrii (DESIGN_DECISIONS.md, performance-audit E4). */
type AnnotShapeEntry = {
  layer: PixelRectLayer;
  color: string;
  classId: number;
  bbox: BBox;
  rotatedState: RotatedGeometryState | null;
  rotatedPoints: PixelPoint[] | null;
  /** Odcisk geometrii, z której zbudowano ten wpis — patrz `annotationGeometryKey`. */
  geometryKey: string;
  refreshDetails: () => void;
  disposeDetails: () => void;
};
type PixelToLatLngFn = (x: number, y: number) => L.LatLng;
type LatLngToPixelFn = (latlng: L.LatLng) => PixelPoint;
type BBox = [number, number, number, number];
type PixelPoint = { x: number; y: number };
type ObbCornerHandle = 0 | 1 | 2 | 3;

type AnnotationBenchmarkSnapshot = {
  total: number;
  rendered: number;
  renderer: "leaflet-canvas" | "batched-canvas";
  labels: number;
  canvases: number;
  domNodes: number;
  zoom: number;
  minZoom: number;
  maxZoom: number;
  viewportMs: number | null;
  indexMs: number | null;
};

type AnnotationBenchmarkBridge = {
  snapshot: () => AnnotationBenchmarkSnapshot;
  fit: () => void;
  panBy: (x: number, y: number) => void;
  panEnd: () => void;
  zoomBy: (delta: number) => void;
  renderedCenters: (geometryType?: Annotation["geometry_type"], limit?: number) => Array<{
    id: string;
    geometryType: Annotation["geometry_type"];
    x: number;
    y: number;
  }>;
  renderedGeometry: (id: string) => {
    id: string;
    geometryType: Annotation["geometry_type"];
    center: PixelPoint;
    corners: PixelPoint[];
    rotateHandle: PixelPoint | null;
  } | null;
};

type AnnotationBenchmarkWindow = Window & {
  __GEOTILE_ANNOTATION_BENCHMARK__?: AnnotationBenchmarkBridge;
};

interface RotatedGeometryState {
  center: PixelPoint;
  width: number;
  height: number;
  angleDeg: number;
}

interface Props {
  projectId: string;
  tileUrlTemplate: string;
  sceneWidth: number;
  sceneHeight: number;
  /**
   * Poziom REFERENCYJNY układu współrzędnych sceny (`source_max_zoom`). Używany przez
   * `map.unproject()`, geometrię sceny i adnotacje — nie wolno go zmieniać w zależności
   * od tego, co renderer akurat potrafi obsłużyć.
   */
  maxZoom: number;
  /** Najwyższy poziom, który backend potrafi teraz obsłużyć (limit żądań, nie geometrii). */
  availableNativeZoom?: number;
  /** To samo dla siatki XYZ w trybie geo; `null`, gdy nie da się go wyznaczyć. */
  availableNativeXyzZoom?: number | null;
  annotations: Annotation[];
  classes: LabelClass[];
  activeClassId: number | null;
  annotationMode?: AnnotationMode;
  tilingPreview?: TilingPreview | null;
  showGrid?: boolean;
  drawingMode: boolean;
  activeMapTool?: MapTool;
  onAnnotationCreate?: (geometry: AnnotationGeometryPayload, classIdOverride?: number) => void;
  selectedAnnotationId?: string | null;
  selectedAnnotationIds?: string[];
  onAnnotationSelect?: (annId: string | null) => void;
  onAnnotationMultiSelect?: (annIds: string[]) => void;
  onAnnotationUpdate?: (annId: string, geometry: AnnotationGeometryPayload) => void;
  onAnnotationClassChange?: (annId: string, classId: number) => void;
  /** Benchmark/test harnesses may disable background map drag while exercising handles. */
  enableMapDragging?: boolean;
  showAnnotationBoxes?: boolean;
  showAnnotationLabels?: boolean;
  focusBBox?: [number, number, number, number] | null;
  focusBBoxToken?: number;
  // Geo mode props
  geoMode?: boolean;
  geoBounds?: [number, number, number, number] | null; // [west, south, east, north]
  geoTileUrlTemplate?: string;
  geoCrs?: string | null;
  geoProj4?: string | null;
  geoTransform?: number[] | null; // [a, b, c, d, e, f]
  basemapUrl?: string | null;
  onBasemapStateChange?: (state: "idle" | "loading" | "ready" | "error") => void;
  /** Called once after all scene-raster tiles in the first visible view have loaded. */
  onSceneRasterReady?: () => void;
  sceneOpacity?: number; // 0-100
  // Interactive tile grid props
  tiles?: TileInfo[];
  showTileGrid?: boolean;
  onTileClick?: (tileIndex: number) => void;
  /** Tiles painted during one drag, committed as a single request on mouse-up. */
  onTilePaintCommit?: (tileIndices: number[]) => void;
  tileSize?: number;
  hoveredTileRef?: React.MutableRefObject<number | null>;
  getVisibleTilesRef?: React.MutableRefObject<() => number[]>;
  getVisibleSceneBoundsRef?: React.MutableRefObject<() => BBox | null>;
  /** Widoczny fragment sceny po każdym zakończonym ruchu mapy (zakres rozciągnięcia „Widok”). */
  onViewportSettled?: (bounds: BBox | null) => void;
  layoutVersion?: string;
  // Predictions layer props
  predictions?: Prediction[];
  showPredictions?: boolean;
  selectedPredictionId?: string | null;
  selectedPredictionIds?: string[];
  onPredictionSelect?: (predictionId: string | null) => void;
  onPredictionMultiSelect?: (predictionIds: string[]) => void;
  // SAM click-to-box: map click -> scene pixels
  onSamClick?: (x: number, y: number) => void;
  /** Scene display name — used for the "coordinates + scene id" copy option (GEO only). */
  sceneName?: string;
}

/** Convert scene pixel (x, y) to geographic (lng, lat) using affine transform. */
function pixelToGeo(
  x: number,
  y: number,
  transform: number[]
): [number, number] {
  const [a, b, c, d, e, f] = transform;
  const lng = a * x + b * y + c;
  const lat = d * x + e * y + f;
  return [lng, lat];
}

/** Convert geographic (lng, lat) to scene pixel (x, y) using inverse affine. */
function geoToPixel(
  lng: number,
  lat: number,
  transform: number[]
): [number, number] {
  const [a, b, c, d, e, f] = transform;
  const det = a * e - b * d;
  const x = (e * (lng - c) - b * (lat - f)) / det;
  const y = (-d * (lng - c) + a * (lat - f)) / det;
  return [x, y];
}

function sceneBoundsForMap(
  map: L.Map,
  latLngToPixel: LatLngToPixelFn,
  sceneWidth: number,
  sceneHeight: number,
): BBox | null {
  const bounds = map.getBounds();
  const points = [
    bounds.getNorthWest(),
    bounds.getNorthEast(),
    bounds.getSouthEast(),
    bounds.getSouthWest(),
  ].map(latLngToPixel);
  const x0 = Math.max(0, Math.min(sceneWidth, Math.min(...points.map((point) => point.x))));
  const y0 = Math.max(0, Math.min(sceneHeight, Math.min(...points.map((point) => point.y))));
  const x1 = Math.max(0, Math.min(sceneWidth, Math.max(...points.map((point) => point.x))));
  const y1 = Math.max(0, Math.min(sceneHeight, Math.max(...points.map((point) => point.y))));
  if (x1 <= x0 || y1 <= y0) return null;
  return [x0, y0, x1, y1];
}

function pixelRectCorners(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  pixelToLatLng: PixelToLatLngFn
): [number, number][] {
  return [
    toTuple(pixelToLatLng(x0, y0)),
    toTuple(pixelToLatLng(x1, y0)),
    toTuple(pixelToLatLng(x1, y1)),
    toTuple(pixelToLatLng(x0, y1)),
  ];
}

function pixelRectBounds(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  pixelToLatLng: PixelToLatLngFn
): L.LatLngBounds {
  return L.latLngBounds(pixelRectCorners(x0, y0, x1, y1, pixelToLatLng));
}

// â”€â”€â”€ Tile grid helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Geometry and appearance are built separately: geometry costs a reprojection per
// corner and an SVG node per tile, appearance is a style swap. Marking a tile only
// changes appearance, so the two must not share an effect.

type TileRect = {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  reviewed: boolean;
  excluded: boolean;
  col: number;
  row: number;
};

function buildTileRects(
  tiles: TileInfo[] | undefined,
  tilingPreview: TilingPreview | null | undefined,
  tileSize: number
): TileRect[] {
  if (tiles && tiles.length > 0) {
    return tiles.map((t) => ({
      x0: t.x0,
      y0: t.y0,
      x1: t.x0 + tileSize,
      y1: t.y0 + tileSize,
      reviewed: t.reviewed,
      excluded: t.excluded,
      col: t.col,
      row: t.row,
    }));
  }
  if (!tilingPreview || tilingPreview.tile_rects.length === 0) return [];

  const rects: TileRect[] = [];
  let col = 0;
  let row = 0;
  for (const [x0, y0, x1, y1] of tilingPreview.tile_rects) {
    col++;
    if (col > tilingPreview.num_cols) {
      col = 1;
      row++;
    }
    rects.push({ x0, y0, x1, y1, reviewed: false, excluded: false, col, row });
  }
  return rects;
}

/** Annotations overlapping each tile, queried through the shared P2.4 spatial index.
 * The strict overlap predicate in the index preserves the previous tile semantics. */
function annotationCountsPerTile(rects: TileRect[], index: AnnotationSpatialIndex): number[] {
  const counts = new Array<number>(rects.length).fill(0);
  if (rects.length === 0 || index.size === 0) return counts;
  for (let i = 0; i < rects.length; i++) {
    const rect = rects[i];
    counts[i] = index.search([rect.x0, rect.y0, rect.x1, rect.y1]).length;
  }
  return counts;
}

function tileStyleFor(reviewed: boolean, excluded: boolean, annCount: number): L.PathOptions {
  if (excluded) return { color: "#ef4444", fillOpacity: 0.05 };
  if (reviewed && annCount > 0) return { color: "#22c55e", fillOpacity: 0.1 };
  if (reviewed) return { color: "#22c55e", fillOpacity: 0.05 };
  if (annCount > 0) return { color: "#60a5fa", fillOpacity: 0.08 };
  return { color: "rgba(160, 160, 160, 0.6)", fillOpacity: 0 };
}

function tileTooltip(rect: TileRect, annCount: number): string {
  const label = `Tile ${rect.col}×${rect.row}`;
  if (rect.excluded) return `${label} - excluded`;
  if (annCount > 0) {
    return `${label} - ${annCount} annotation${annCount > 1 ? "s" : ""}`;
  }
  return rect.reviewed ? `${label} - reviewed (empty)` : `${label} - unchecked`;
}

/** Style a tile is about to receive while the user paints over it.
 *
 * Painting updates the map immediately and defers the request to mouse-up, so the
 * preview has to come from the active tool rather than from server state.
 */
function paintPreviewStyle(tool: MapTool, annCount: number): L.PathOptions | null {
  if (tool === "grid_review") return tileStyleFor(true, false, annCount);
  if (tool === "grid_clear") return tileStyleFor(false, false, annCount);
  if (tool === "grid_exclude") return tileStyleFor(false, true, annCount);
  return null;
}

function createPixelRectLayer(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  pixelToLatLng: PixelToLatLngFn,
  geoMode: boolean,
  options: L.PolylineOptions
): PixelRectLayer {
  if (geoMode) {
    return L.polygon(pixelRectCorners(x0, y0, x1, y1, pixelToLatLng), options);
  }

  const sw = pixelToLatLng(x0, y1);
  const ne = pixelToLatLng(x1, y0);
  return L.rectangle([toTuple(sw), toTuple(ne)], options);
}

function updatePixelRectLayer(
  layer: PixelRectLayer,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  pixelToLatLng: PixelToLatLngFn,
  geoMode: boolean
) {
  if (geoMode) {
    (layer as L.Polygon).setLatLngs(pixelRectCorners(x0, y0, x1, y1, pixelToLatLng));
    return;
  }

  const sw = pixelToLatLng(x0, y1);
  const ne = pixelToLatLng(x1, y0);
  (layer as L.Rectangle).setBounds([toTuple(sw), toTuple(ne)]);
}

function pixelPointToLatLng(
  point: PixelPoint,
  pixelToLatLng: PixelToLatLngFn
): [number, number] {
  return toTuple(pixelToLatLng(point.x, point.y));
}

function createPixelPolygonLayer(
  points: PixelPoint[],
  pixelToLatLng: PixelToLatLngFn,
  options: L.PathOptions
): L.Polygon {
  return L.polygon(points.map((point) => pixelPointToLatLng(point, pixelToLatLng)), options);
}

function updatePixelPolygonLayer(
  layer: L.Polygon,
  points: PixelPoint[],
  pixelToLatLng: PixelToLatLngFn
) {
  layer.setLatLngs(points.map((point) => pixelPointToLatLng(point, pixelToLatLng)));
}

function bboxFromPoints(points: PixelPoint[]): BBox {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function pointInPolygon(point: PixelPoint, polygon: readonly PixelPoint[]): boolean {
  let inside = false;
  for (let current = 0, previous = polygon.length - 1; current < polygon.length; previous = current++) {
    const a = polygon[current];
    const b = polygon[previous];
    if (
      (a.y > point.y) !== (b.y > point.y) &&
      point.x < (b.x - a.x) * (point.y - a.y) / ((b.y - a.y) || Number.EPSILON) + a.x
    ) {
      inside = !inside;
    }
  }
  return inside;
}

function pointToSegmentDistance(point: PixelPoint, start: PixelPoint, end: PixelPoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (dx === 0 && dy === 0) return Math.hypot(point.x - start.x, point.y - start.y);
  const t = Math.max(0, Math.min(1, (
    (point.x - start.x) * dx + (point.y - start.y) * dy
  ) / (dx * dx + dy * dy)));
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
}

function annotationScenePoints(annotation: Annotation): PixelPoint[] {
  if (annotation.geometry_type === "rotated_bbox") {
    if (annotation.polygon_scene_px && annotation.polygon_scene_px.length >= 4) {
      return annotation.polygon_scene_px.slice(0, 4).map(([x, y]) => ({ x, y }));
    }
    const state = rotatedStateFromAnnotation(annotation);
    if (state) return rotatedCorners(state);
  }
  const [x0, y0, x1, y1] = annotation.bbox;
  return [
    { x: x0, y: y0 },
    { x: x1, y: y0 },
    { x: x1, y: y1 },
    { x: x0, y: y1 },
  ];
}

function annotationContainsPoint(
  annotation: Annotation,
  point: PixelPoint,
  tolerance: number,
): boolean {
  if (annotation.geometry_type !== "rotated_bbox") {
    const [x0, y0, x1, y1] = annotation.bbox;
    return point.x >= x0 - tolerance && point.x <= x1 + tolerance &&
      point.y >= y0 - tolerance && point.y <= y1 + tolerance;
  }
  const polygon = annotationScenePoints(annotation);
  if (pointInPolygon(point, polygon)) return true;
  return polygon.some((start, index) => (
    pointToSegmentDistance(point, start, polygon[(index + 1) % polygon.length]) <= tolerance
  ));
}

function roundPoint(point: PixelPoint): [number, number] {
  return [Number(point.x.toFixed(3)), Number(point.y.toFixed(3))];
}

function normalizeAngleDeg(angleDeg: number): number {
  let angle = angleDeg % 360;
  if (angle <= -180) angle += 360;
  if (angle > 180) angle -= 360;
  return angle;
}

function unitVector(angleDeg: number): PixelPoint {
  const angle = angleDeg * Math.PI / 180;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

function perpendicular(vector: PixelPoint): PixelPoint {
  return { x: -vector.y, y: vector.x };
}

function rotatedCorners(state: RotatedGeometryState): PixelPoint[] {
  const axis = unitVector(state.angleDeg);
  const normal = perpendicular(axis);
  const halfWidth = state.width / 2;
  const halfHeight = state.height / 2;

  return [
    {
      x: state.center.x - axis.x * halfWidth - normal.x * halfHeight,
      y: state.center.y - axis.y * halfWidth - normal.y * halfHeight,
    },
    {
      x: state.center.x + axis.x * halfWidth - normal.x * halfHeight,
      y: state.center.y + axis.y * halfWidth - normal.y * halfHeight,
    },
    {
      x: state.center.x + axis.x * halfWidth + normal.x * halfHeight,
      y: state.center.y + axis.y * halfWidth + normal.y * halfHeight,
    },
    {
      x: state.center.x - axis.x * halfWidth + normal.x * halfHeight,
      y: state.center.y - axis.y * halfWidth + normal.y * halfHeight,
    },
  ];
}

function rotatedPayloadFromState(state: RotatedGeometryState): AnnotationGeometryPayload {
  const safeState: RotatedGeometryState = {
    center: {
      x: Number(state.center.x.toFixed(3)),
      y: Number(state.center.y.toFixed(3)),
    },
    width: Number(Math.max(3, state.width).toFixed(3)),
    height: Number(Math.max(3, state.height).toFixed(3)),
    angleDeg: Number(normalizeAngleDeg(state.angleDeg).toFixed(6)),
  };
  const points = rotatedCorners(safeState);
  const bbox = bboxFromPoints(points).map((value) => Number(value.toFixed(3))) as BBox;
  const frontVector = unitVector(safeState.angleDeg);

  return {
    geometry_type: "rotated_bbox",
    bbox,
    rotated_bbox: {
      cx: safeState.center.x,
      cy: safeState.center.y,
      width: safeState.width,
      height: safeState.height,
      angle_deg: safeState.angleDeg,
    },
    polygon_scene_px: points.map(roundPoint),
    front_edge_scene_px: [roundPoint(points[0]), roundPoint(points[1])],
    front_vector_scene_px: [
      Number(frontVector.x.toFixed(8)),
      Number(frontVector.y.toFixed(8)),
    ],
    orientation_angle_deg: safeState.angleDeg,
  };
}

/**
 * Stan prostokąta zorientowanego wyprowadzony z adnotacji.
 *
 * Kolejność źródeł jest istotna: NAJPIERW `polygon_scene_px`, dopiero potem `rotated_bbox`.
 * Poligon niesie rzeczywisty kształt, w tym ścinanie, które na scenach niekonforemnych
 * (np. EPSG:4326 w Web Mercatorze) jest częścią ramki wiernej mapie. `rotated_bbox` potrafi
 * opisać wyłącznie prostokąt W PIKSELACH, więc odbudowa z niego spłaszcza ścinanie i po
 * konwersji na mapę daje równoległobok. Ta sama zasada stoi za `movedRotatedPayload`
 * i za `annotationScenePoints`.
 */
function rotatedStateFromAnnotation(annotation: Annotation): RotatedGeometryState | null {
  const points = annotation.polygon_scene_px;
  if (!points || points.length < 4) {
    if (!annotation.rotated_bbox) return null;
    return {
      center: {
        x: annotation.rotated_bbox.cx,
        y: annotation.rotated_bbox.cy,
      },
      width: Math.max(3, annotation.rotated_bbox.width),
      height: Math.max(3, annotation.rotated_bbox.height),
      angleDeg: annotation.rotated_bbox.angle_deg,
    };
  }
  const p0 = { x: points[0][0], y: points[0][1] };
  const p1 = { x: points[1][0], y: points[1][1] };
  const p2 = { x: points[2][0], y: points[2][1] };
  const center = points.slice(0, 4).reduce(
    (acc, point) => ({ x: acc.x + point[0] / 4, y: acc.y + point[1] / 4 }),
    { x: 0, y: 0 }
  );

  return {
    center,
    width: Math.max(3, Math.hypot(p1.x - p0.x, p1.y - p0.y)),
    height: Math.max(3, Math.hypot(p2.x - p1.x, p2.y - p1.y)),
    angleDeg: Math.atan2(p1.y - p0.y, p1.x - p0.x) * 180 / Math.PI,
  };
}

function rotatedPayloadFromDrawing(
  start: PixelPoint,
  end: PixelPoint,
  side: PixelPoint
): AnnotationGeometryPayload | null {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length < 3) return null;

  const axis = { x: dx / length, y: dy / length };
  const normal = perpendicular(axis);
  const signedHeight = (side.x - start.x) * normal.x + (side.y - start.y) * normal.y;
  const height = Math.abs(signedHeight);
  if (height < 3) return null;

  const center = {
    x: (start.x + end.x) / 2 + normal.x * signedHeight / 2,
    y: (start.y + end.y) / 2 + normal.y * signedHeight / 2,
  };
  const angleDeg = Math.atan2(axis.y, axis.x) * 180 / Math.PI;
  const points = [
    start,
    end,
    { x: end.x + normal.x * signedHeight, y: end.y + normal.y * signedHeight },
    { x: start.x + normal.x * signedHeight, y: start.y + normal.y * signedHeight },
  ];
  const bbox = bboxFromPoints(points).map((value) => Number(value.toFixed(3))) as BBox;

  return {
    geometry_type: "rotated_bbox",
    bbox,
    rotated_bbox: {
      cx: Number(center.x.toFixed(3)),
      cy: Number(center.y.toFixed(3)),
      width: Number(length.toFixed(3)),
      height: Number(height.toFixed(3)),
      angle_deg: Number(normalizeAngleDeg(angleDeg).toFixed(6)),
    },
    polygon_scene_px: points.map(roundPoint),
    front_edge_scene_px: [roundPoint(start), roundPoint(end)],
    front_vector_scene_px: [
      Number(axis.x.toFixed(8)),
      Number(axis.y.toFixed(8)),
    ],
    orientation_angle_deg: Number(normalizeAngleDeg(angleDeg).toFixed(6)),
  };
}

function rotatedPayloadFromPolygon(points: PixelPoint[]): AnnotationGeometryPayload | null {
  if (points.length < 4) return null;
  const corners = points.slice(0, 4);
  const [start, end, , fourth] = corners;
  const frontDx = end.x - start.x;
  const frontDy = end.y - start.y;
  const sideDx = fourth.x - start.x;
  const sideDy = fourth.y - start.y;
  const width = Math.hypot(frontDx, frontDy);
  const height = Math.hypot(sideDx, sideDy);
  if (width < 3 || height < 3) return null;

  const center = corners.reduce(
    (result, point) => ({ x: result.x + point.x / 4, y: result.y + point.y / 4 }),
    { x: 0, y: 0 }
  );
  const angleDeg = Math.atan2(frontDy, frontDx) * 180 / Math.PI;
  const bbox = bboxFromPoints(corners).map((value) => Number(value.toFixed(3))) as BBox;
  const frontLength = Math.max(width, 1e-9);

  return {
    geometry_type: "rotated_bbox",
    bbox,
    rotated_bbox: {
      cx: Number(center.x.toFixed(3)),
      cy: Number(center.y.toFixed(3)),
      width: Number(width.toFixed(3)),
      height: Number(height.toFixed(3)),
      angle_deg: Number(normalizeAngleDeg(angleDeg).toFixed(6)),
    },
    polygon_scene_px: corners.map(roundPoint),
    front_edge_scene_px: [roundPoint(start), roundPoint(end)],
    front_vector_scene_px: [
      Number((frontDx / frontLength).toFixed(8)),
      Number((frontDy / frontLength).toFixed(8)),
    ],
    orientation_angle_deg: Number(normalizeAngleDeg(angleDeg).toFixed(6)),
  };
}

function moveRotatedState(state: RotatedGeometryState, dx: number, dy: number): RotatedGeometryState {
  return {
    ...state,
    center: {
      x: state.center.x + dx,
      y: state.center.y + dy,
    },
  };
}

/**
 * Payload przesuniętej rotowanej ramki, zachowujący jej kształt na MAPIE.
 *
 * Na scenach, gdzie piksel↔mapa nie jest konforemne (np. EPSG:4326 w Web Mercatorze),
 * ramka wierna mapie jest w pikselach równoległobokiem — a `rotated_bbox`/`RotatedGeometryState`
 * potrafią opisać tylko prostokąt, więc odbudowa przez nie GUBI ścinanie i kopia wychodzi
 * przekoszona. Gdy mamy oryginalny `polygon_scene_px`, translujemy jego wierzchołki i
 * odtwarzamy payload z poligonu; dopiero bez poligonu wracamy do stanu prostokątnego.
 */
function movedRotatedPayload(
  startState: RotatedGeometryState,
  startPolygon: PixelPoint[] | null,
  dx: number,
  dy: number
): AnnotationGeometryPayload {
  if (startPolygon && startPolygon.length >= 4) {
    const moved = startPolygon.slice(0, 4).map((point) => ({ x: point.x + dx, y: point.y + dy }));
    const payload = rotatedPayloadFromPolygon(moved);
    if (payload) return payload;
  }
  return rotatedPayloadFromState(moveRotatedState(startState, dx, dy));
}

/**
 * Stan prostokąta zorientowanego w układzie RZUTOWANYM (mapa/Mercator), zbudowany z
 * pikselowego poligonu ramki. Resize/rotate liczone na tym stanie (a nie na pikselowym)
 * dają prostokąt na MAPIE — na scenach niekonforemnych to jedyny sposób, by edycja nie
 * spłaszczała ścinania (patrz movedRotatedPayload).
 */
function projectedStateFromPolygon(
  polygon: PixelPoint[],
  toProjected: (point: PixelPoint) => PixelPoint
): RotatedGeometryState {
  const proj = polygon.slice(0, 4).map(toProjected);
  const [p0, p1, p2] = proj;
  const center = proj.reduce(
    (acc, point) => ({ x: acc.x + point.x / 4, y: acc.y + point.y / 4 }),
    { x: 0, y: 0 }
  );
  return {
    center,
    width: Math.max(1e-6, Math.hypot(p1.x - p0.x, p1.y - p0.y)),
    height: Math.max(1e-6, Math.hypot(p2.x - p1.x, p2.y - p1.y)),
    angleDeg: Math.atan2(p1.y - p0.y, p1.x - p0.x) * 180 / Math.PI,
  };
}

/** Payload (wierny poligon pikselowy) z prostokąta zdefiniowanego w układzie rzutowanym. */
function pixelPayloadFromProjectedState(
  state: RotatedGeometryState,
  toPixel: (point: PixelPoint) => PixelPoint
): AnnotationGeometryPayload | null {
  const pixels = rotatedCorners(state).map(toPixel);
  return rotatedPayloadFromPolygon(pixels);
}

function resizeRotatedState(
  state: RotatedGeometryState,
  handle: ObbCornerHandle,
  point: PixelPoint
): RotatedGeometryState {
  const axis = unitVector(state.angleDeg);
  const normal = perpendicular(axis);
  const sx = handle === 1 || handle === 2 ? 1 : -1;
  const sy = handle === 2 || handle === 3 ? 1 : -1;
  const fixed = {
    x: state.center.x - axis.x * sx * state.width / 2 - normal.x * sy * state.height / 2,
    y: state.center.y - axis.y * sx * state.width / 2 - normal.y * sy * state.height / 2,
  };
  const delta = { x: point.x - fixed.x, y: point.y - fixed.y };
  const width = Math.max(3, Math.abs(delta.x * axis.x + delta.y * axis.y));
  const height = Math.max(3, Math.abs(delta.x * normal.x + delta.y * normal.y));

  return {
    ...state,
    width,
    height,
    center: {
      x: fixed.x + axis.x * sx * width / 2 + normal.x * sy * height / 2,
      y: fixed.y + axis.y * sx * width / 2 + normal.y * sy * height / 2,
    },
  };
}

function formatDistanceMeters(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} km`;
  if (value >= 100) return `${value.toFixed(0)} m`;
  if (value >= 10) return `${value.toFixed(1)} m`;
  return `${value.toFixed(2)} m`;
}

function normalizeLabelAngle(angleDegrees: number): number {
  let angle = angleDegrees;
  while (angle > 90) angle -= 180;
  while (angle < -90) angle += 180;
  return angle;
}

function safeCssColor(color: string | undefined): string {
  if (!color) return "#facc15";
  if (/^#[0-9a-fA-F]{3,8}$/.test(color)) return color;
  if (/^(rgb|rgba|hsl|hsla)\([^)]+\)$/.test(color)) return color;
  return "#facc15";
}

function createSideMeasureLabelHtml(
  text: string,
  color: string,
  angleDegrees: number
): string {
  const safeColor = safeCssColor(color);
  const safeAngle = normalizeLabelAngle(angleDegrees).toFixed(2);
  return `
    <div class="bbox-side-measure-label" style="color: ${safeColor}; --bbox-measure-angle: ${safeAngle}deg;">
      ${text}
    </div>
  `;
}

export default function SceneMapCanvas({
  projectId,
  tileUrlTemplate,
  sceneWidth,
  sceneHeight,
  maxZoom,
  availableNativeZoom,
  availableNativeXyzZoom,
  annotations,
  classes,
  activeClassId,
  annotationMode = "bbox",
  tilingPreview,
  showGrid = false,
  drawingMode,
  activeMapTool = drawingMode ? "draw" : "select",
  onAnnotationCreate,
  selectedAnnotationId,
  selectedAnnotationIds = [],
  onAnnotationSelect,
  onAnnotationMultiSelect,
  onAnnotationUpdate,
  onAnnotationClassChange,
  enableMapDragging = true,
  showAnnotationBoxes = true,
  showAnnotationLabels = true,
  focusBBox = null,
  focusBBoxToken,
  geoMode = false,
  geoBounds,
  geoTileUrlTemplate,
  geoCrs,
  geoProj4,
  geoTransform,
  basemapUrl,
  onBasemapStateChange,
  onSceneRasterReady,
  sceneOpacity = 100,
  tiles,
  showTileGrid = false,
  onTileClick,
  onTilePaintCommit,
  tileSize = 640,
  hoveredTileRef,
  getVisibleTilesRef,
  getVisibleSceneBoundsRef,
  onViewportSettled,
  layoutVersion,
  predictions = [],
  showPredictions = false,
  selectedPredictionId,
  selectedPredictionIds = [],
  onPredictionSelect,
  onPredictionMultiSelect,
  onSamClick,
  sceneName,
}: Props) {
  const toast = useToast();
  const { t } = useTranslation();
  // Coordinate tools (GEO scenes only). Cursor read-out is updated via a DOM ref, not
  // state, so mousemove never re-renders this large component; the context menu and the
  // "go to" box use state because they change rarely.
  const [coordMenu, setCoordMenu] = useState<{ x: number; y: number; lat: number; lon: number } | null>(null);
  const [gotoValue, setGotoValue] = useState("");
  const [gotoError, setGotoError] = useState<string | null>(null);
  const [gotoOutside, setGotoOutside] = useState<{ lat: number; lon: number } | null>(null);
  const [annotationViewportRevision, setAnnotationViewportRevision] = useState(0);
  const cursorHudRef = useRef<HTMLSpanElement | null>(null);
  const coordHudThrottleRef = useRef(0);
  const coordFlashRef = useRef<L.CircleMarker | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  /** Ksztalty adnotacji po id, zeby zmiana zaznaczenia zmieniala STYL zamiast przebudowywac
   *  warstwe (DESIGN_DECISIONS.md, performance-audit E4). Ten sam wzorzec co `tilePolygonsRef` nizej. */
  const annotShapesRef = useRef<Map<string, AnnotShapeEntry>>(new Map());
  const batchedAnnotationCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const batchedVisibleAnnotationsRef = useRef<IndexedAnnotation[]>([]);
  const batchedAnnotationModeRef = useRef(false);
  const batchedAnnotationDrawRafRef = useRef<number | null>(null);
  const requestBatchedAnnotationDrawRef = useRef<() => void>(() => undefined);
  const batchedAnnotationHitTestRef = useRef<((point: L.Point) => IndexedAnnotation | null) | null>(null);
  /** P2.4: immutable snapshot index used by viewport culling and rectangle selection. */
  const annotationSpatialIndexRef = useRef(new AnnotationSpatialIndex());
  const annotationIndexSourceRef = useRef<readonly Annotation[] | null>(null);
  const annotationRenderInputsRef = useRef<readonly unknown[] | null>(null);
  const hoveredAnnotationIdRef = useRef<string | null>(null);
  const annotationViewportRafRef = useRef<number | null>(null);
  /** Zaznaczenie zastosowane do warstwy — zrodlo roznicy przy kolejnej synchronizacji. */
  const prevSelectionRef = useRef<Set<string>>(new Set());
  const annotLayerRef = useRef<L.LayerGroup>(new L.LayerGroup());
  const gridLayerRef = useRef<L.LayerGroup>(new L.LayerGroup());
  const tileGridLayerRef = useRef<L.LayerGroup>(new L.LayerGroup());
  const predictionLayerRef = useRef<L.LayerGroup>(new L.LayerGroup());
  const resizeHandleLayerRef = useRef<L.LayerGroup>(new L.LayerGroup());
  const drawPreviewRef = useRef<PixelRectLayer | null>(null);
  const drawAxisPreviewRef = useRef<L.Polyline | null>(null);
  const drawMeasureLabelsRef = useRef<{ width: L.Marker; height: L.Marker } | null>(null);
  const drawMeasureBboxRef = useRef<PixelPoint[] | null>(null);
  const measureStateRef = useRef<{ start: L.LatLng } | null>(null);
  const measureLineRef = useRef<L.Polyline | null>(null);
  const measureLabelRef = useRef<L.Marker | null>(null);
  const multiSelectStateRef = useRef<{ start: L.LatLng } | null>(null);
  const multiSelectRectRef = useRef<PixelRectLayer | null>(null);
  const dragPreviewRef = useRef<PixelRectLayer | null>(null);
  const drawStateRef = useRef<
    | { mode: "bbox"; start: L.LatLng }
    | { mode: "rotated_bbox"; start: L.LatLng; end?: L.LatLng }
    | null
  >(null);
  const draggingAnnotationRef = useRef<{
    annId: string;
    startLatLng: L.LatLng;
    startBbox: [number, number, number, number];
    classId: number;
    copyMode: boolean;
  } | null>(null);
  const movingRotatedAnnotationRef = useRef<{
    annId: string;
    startLatLng: L.LatLng;
    startState: RotatedGeometryState;
    startPolygon: PixelPoint[] | null;
    classId: number;
    copyMode: boolean;
  } | null>(null);
  const resizingRotatedAnnotationRef = useRef<{
    annId: string;
    handle: ObbCornerHandle;
    startState: RotatedGeometryState;
    startPolygon: PixelPoint[] | null;
  } | null>(null);
  const rotatingRotatedAnnotationRef = useRef<{
    annId: string;
    startState: RotatedGeometryState;
    edgeOffsetDeg: number;
    startPolygon: PixelPoint[] | null;
  } | null>(null);
  const resizingAnnotationRef = useRef<{
    annId: string;
    handle: "nw" | "ne" | "se" | "sw";
    startLatLng: L.LatLng;
    startBbox: [number, number, number, number];
  } | null>(null);
  const suppressClickRef = useRef(false);
  const suppressContextMenuRef = useRef(false);
  const copyModifierRef = useRef(false);
  const rightPanRef = useRef<{
    x: number;
    y: number;
    moved: boolean;
  } | null>(null);
  const basemapLayerRef = useRef<L.TileLayer | null>(null);
  const basemapRequestIdRef = useRef(0);
  const pixelQueueDetachRef = useRef<(() => void) | null>(null);
  const geoQueueDetachRef = useRef<(() => void) | null>(null);
  const sceneLayerRef = useRef<L.TileLayer | null>(null);
  const pixelTileLayerRef = useRef<L.TileLayer | null>(null);
  const pixelSwapRef = useRef<PendingTileSwap | null>(null);
  const geoSwapRef = useRef<PendingTileSwap | null>(null);
  const sceneRasterReadyReportedRef = useRef(false);
  const onSceneRasterReadyRef = useRef(onSceneRasterReady);
  onSceneRasterReadyRef.current = onSceneRasterReady;
  const reportSceneRasterReady = useCallback(() => {
    if (sceneRasterReadyReportedRef.current) return;
    sceneRasterReadyReportedRef.current = true;
    onSceneRasterReadyRef.current?.();
  }, []);

  // Stable refs for callbacks used in Leaflet event handlers â€” avoids
  // reinstalling handlers on every render when upstream callbacks are unstable.
  const onAnnotationCreateRef = useRef(onAnnotationCreate);
  onAnnotationCreateRef.current = onAnnotationCreate;
  const onAnnotationSelectRef = useRef(onAnnotationSelect);
  onAnnotationSelectRef.current = onAnnotationSelect;
  const onAnnotationMultiSelectRef = useRef(onAnnotationMultiSelect);
  onAnnotationMultiSelectRef.current = onAnnotationMultiSelect;
  const onPredictionSelectRef = useRef(onPredictionSelect);
  onPredictionSelectRef.current = onPredictionSelect;
  const onPredictionMultiSelectRef = useRef(onPredictionMultiSelect);
  onPredictionMultiSelectRef.current = onPredictionMultiSelect;
  const onAnnotationUpdateRef = useRef(onAnnotationUpdate);
  onAnnotationUpdateRef.current = onAnnotationUpdate;
  const onAnnotationClassChangeRef = useRef(onAnnotationClassChange);
  onAnnotationClassChangeRef.current = onAnnotationClassChange;
  const activeMapToolRef = useRef<MapTool>(activeMapTool);
  activeMapToolRef.current = activeMapTool;
  const canEditAnnotationsRef = useRef(activeMapTool === "select");
  canEditAnnotationsRef.current = activeMapTool === "select";
  const enableMapDraggingRef = useRef(enableMapDragging);
  enableMapDraggingRef.current = enableMapDragging;
  const activeClassIdRef = useRef(activeClassId);
  activeClassIdRef.current = activeClassId;
  const selectedAnnotationIdRef = useRef(selectedAnnotationId);
  selectedAnnotationIdRef.current = selectedAnnotationId;
  const selectedAnnotationIdsRef = useRef(selectedAnnotationIds);
  selectedAnnotationIdsRef.current = selectedAnnotationIds;
  const annotationModeRef = useRef(annotationMode);
  annotationModeRef.current = annotationMode;
  const latLngToPixelRef = useRef<(latlng: L.LatLng) => { x: number; y: number }>(
    (latlng) => ({ x: latlng.lng, y: -latlng.lat })
  );
  const pixelToLatLngRef = useRef<(x: number, y: number) => L.LatLng>(
    (x, y) => L.latLng(-y, x)
  );
  const onTilePaintCommitRef = useRef(onTilePaintCommit);
  onTilePaintCommitRef.current = onTilePaintCommit;
  const onTileClickRef = useRef(onTileClick);
  onTileClickRef.current = onTileClick;
  const onSamClickRef = useRef(onSamClick);
  onSamClickRef.current = onSamClick;
  const tilePaintActiveRef = useRef(false);
  const paintedTileIndicesRef = useRef<Set<number>>(new Set());
  /** Styles applied optimistically while painting, before the request goes out. */
  const paintedStyleRef = useRef<Map<number, L.PathOptions>>(new Map());
  /** Leaflet fires click after mouse-up, so a committed stroke must swallow the click
   *  that follows it — otherwise the last tile is sent twice. */
  const suppressTileClickRef = useRef(false);
  /** Tile polygons by index, so appearance can be updated without rebuilding them. */
  const tilePolygonsRef = useRef<PixelRectLayer[]>([]);
  /** Style each tile returns to when the cursor leaves it. */
  const tileBaseStyleRef = useRef<L.PathOptions[]>([]);
  const tileAnnCountsRef = useRef<number[]>([]);
  // Read by the geometry pass, which must not re-run when review state changes.
  const tilesRef = useRef(tiles);
  tilesRef.current = tiles;
  const tilingPreviewRef = useRef(tilingPreview);
  tilingPreviewRef.current = tilingPreview;

  const classMap = new Map(classes.map((c) => [c.id, c]));
  const classMapRef = useRef(classMap);
  classMapRef.current = classMap;
  const predictionsRef = useRef(predictions);
  predictionsRef.current = predictions;
  const sceneProjection = geoProj4 || geoCrs || "EPSG:4326";

  useEffect(() => {
    const isTypingTarget = (target: EventTarget | null): boolean => {
      return (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      );
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.key.toLowerCase() === "c" &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !isTypingTarget(event.target)
      ) {
        copyModifierRef.current = true;
      }
    };

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "c") {
        copyModifierRef.current = false;
      }
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      copyModifierRef.current = false;
    };
  }, []);

  // â”€â”€â”€ Geo-mode helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const sceneToWgs84 = useCallback(
    (x: number, y: number): [number, number] => {
      if (!geoMode || sceneProjection === "EPSG:4326") return [x, y];
      try {
        const [lng, lat] = proj4(sceneProjection, "EPSG:4326", [x, y]) as [number, number];
        return [lng, lat];
      } catch {
        return [x, y];
      }
    },
    [geoMode, sceneProjection]
  );

  const wgs84ToScene = useCallback(
    (lng: number, lat: number): [number, number] => {
      if (!geoMode || sceneProjection === "EPSG:4326") return [lng, lat];
      try {
        const [x, y] = proj4("EPSG:4326", sceneProjection, [lng, lat]) as [number, number];
        return [x, y];
      } catch {
        return [lng, lat];
      }
    },
    [geoMode, sceneProjection]
  );

  const pixelToLatLngGeo = useCallback(
    (x: number, y: number): L.LatLng => {
      if (!geoTransform) return L.latLng(0, 0);
      const [sceneX, sceneY] = pixelToGeo(x, y, geoTransform);
      const [lng, lat] = sceneToWgs84(sceneX, sceneY);
      return L.latLng(lat, lng);
    },
    [geoTransform, sceneToWgs84]
  );

  const latLngToPixelGeo = useCallback(
    (latlng: L.LatLng): { x: number; y: number } => {
      if (!geoTransform) return { x: 0, y: 0 };
      const [sceneX, sceneY] = wgs84ToScene(latlng.lng, latlng.lat);
      const [x, y] = geoToPixel(sceneX, sceneY, geoTransform);
      return { x, y };
    },
    [geoTransform, wgs84ToScene]
  );

  // â”€â”€â”€ Pixel-mode helpers (CRS.Simple) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const pixelToLatLngSimple = useCallback(
    (x: number, y: number): L.LatLng => {
      const map = mapRef.current;
      if (!map) return L.latLng(0, 0);
      return map.unproject([x, y], maxZoom);
    },
    [maxZoom]
  );

  const latLngToPixelSimple = useCallback(
    (latlng: L.LatLng): { x: number; y: number } => {
      const map = mapRef.current;
      if (!map) return { x: 0, y: 0 };
      const pt = map.project(latlng, maxZoom);
      return { x: pt.x, y: pt.y };
    },
    [maxZoom]
  );

  // Unified helpers that dispatch based on mode
  const pixelToLatLng = geoMode ? pixelToLatLngGeo : pixelToLatLngSimple;
  pixelToLatLngRef.current = pixelToLatLng;
  const latLngToPixel = geoMode ? latLngToPixelGeo : latLngToPixelSimple;
  latLngToPixelRef.current = latLngToPixel;

  const clearMeasureOverlay = useCallback(() => {
    const map = mapRef.current;
    measureStateRef.current = null;
    if (map && measureLineRef.current) {
      map.removeLayer(measureLineRef.current);
    }
    if (map && measureLabelRef.current) {
      map.removeLayer(measureLabelRef.current);
    }
    measureLineRef.current = null;
    measureLabelRef.current = null;
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && activeMapToolRef.current === "measure") {
        clearMeasureOverlay();
      }
      if (event.key === "Escape" && activeMapToolRef.current === "multi_select") {
        const map = mapRef.current;
        multiSelectStateRef.current = null;
        if (map && multiSelectRectRef.current) {
          map.removeLayer(multiSelectRectRef.current);
        }
        multiSelectRectRef.current = null;
        map?.dragging.enable();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [clearMeasureOverlay]);

  // â”€â”€â”€ Initialize map â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    if (geoMode && geoBounds) {
      // Geographic mode â€” standard EPSG:3857
      const map = L.map(containerRef.current, {
        dragging: enableMapDragging,
        minZoom: 2,
        maxZoom: 22,
        zoomSnap: 0.25,
        zoomDelta: 0.5,
        doubleClickZoom: false,
        attributionControl: false,
        zoomControl: false,
        preferCanvas: PREFER_CANVAS,
      });
      L.control.zoom({ position: "topright" }).addTo(map);

      annotLayerRef.current.addTo(map);
      gridLayerRef.current.addTo(map);
      tileGridLayerRef.current.addTo(map);
      predictionLayerRef.current.addTo(map);
      resizeHandleLayerRef.current.addTo(map);

      const [west, south, east, north] = geoBounds;
      map.fitBounds([
        [south, west],
        [north, east],
      ]);

      mapRef.current = map;
    } else {
      // Pixel mode â€” CRS.Simple (existing behavior)
      const map = L.map(containerRef.current, {
        dragging: enableMapDragging,
        crs: L.CRS.Simple,
        minZoom: 0,
        maxZoom: maxZoom + PIXEL_OVERZOOM,
        // The pixel tile endpoint uses integer path parameters. Fractional map
        // zooms previously leaked values such as z=2.25 and produced HTTP 422.
        zoomSnap: PIXEL_TILE_ZOOM_SNAP,
        zoomDelta: PIXEL_TILE_ZOOM_DELTA,
        doubleClickZoom: false,
        attributionControl: false,
        zoomControl: false,
        preferCanvas: PREFER_CANVAS,
      });
      L.control.zoom({ position: "topright" }).addTo(map);

      const southWest = map.unproject([0, sceneHeight], maxZoom);
      const northEast = map.unproject([sceneWidth, 0], maxZoom);
      const bounds = new L.LatLngBounds(southWest, northEast);

      const tileLayer = L.tileLayer(tileUrlTemplate, {
        minZoom: 0,
        maxZoom: maxZoom + PIXEL_OVERZOOM,
        // Limit żądań, nie geometrii — patrz komentarz przy `maxZoom` w Props.
        maxNativeZoom: availableNativeZoom ?? maxZoom,
        tileSize: 256,
        noWrap: true,
        bounds: bounds,
        opacity: sceneOpacity / 100,
        keepBuffer: 0,
        updateWhenIdle: true,
        updateWhenZooming: false,
      });
      tileLayer.once("load", reportSceneRasterReady);
      tileLayer.addTo(map);
      pixelTileLayerRef.current = tileLayer;

      annotLayerRef.current.addTo(map);
      gridLayerRef.current.addTo(map);
      tileGridLayerRef.current.addTo(map);
      predictionLayerRef.current.addTo(map);
      resizeHandleLayerRef.current.addTo(map);

      map.fitBounds(bounds);
      mapRef.current = map;
    }

    return () => {
      if (batchedAnnotationDrawRafRef.current !== null) {
        window.cancelAnimationFrame(batchedAnnotationDrawRafRef.current);
        batchedAnnotationDrawRafRef.current = null;
      }
      batchedAnnotationCanvasRef.current?.remove();
      batchedAnnotationCanvasRef.current = null;
      batchedVisibleAnnotationsRef.current = [];
      batchedAnnotationModeRef.current = false;
      requestBatchedAnnotationDrawRef.current = () => undefined;
      batchedAnnotationHitTestRef.current = null;
      for (const entry of annotShapesRef.current.values()) entry.disposeDetails();
      annotLayerRef.current.clearLayers();
      resizeHandleLayerRef.current.clearLayers();
      annotShapesRef.current = new Map();
      annotationRenderInputsRef.current = null;
      prevSelectionRef.current = new Set();
      cancelTileSwap(mapRef.current, pixelSwapRef);
      cancelTileSwap(mapRef.current, geoSwapRef);
      mapRef.current?.dragging.disable();
      mapRef.current?.remove();
      mapRef.current = null;
      basemapLayerRef.current = null;
      basemapRequestIdRef.current += 1;
      sceneLayerRef.current = null;
      pixelTileLayerRef.current = null;
    };
  }, [projectId, geoMode, enableMapDragging]); // re-init when project, mode or drag policy changes

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const invalidate = () => {
      map.invalidateSize({ pan: false });
    };

    invalidate();
    const timers = [80, 220, 420].map((delay) => window.setTimeout(invalidate, delay));
    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [layoutVersion]);

  useEffect(() => {
    const container = containerRef.current;
    const map = mapRef.current;
    if (!container || !map || typeof ResizeObserver === "undefined") return;

    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        map.invalidateSize({ pan: false });
        frame = null;
      });
    });

    observer.observe(container);
    return () => {
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, []);

  // â”€â”€â”€ Pixel mode: update tile URL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    if (geoMode) return;
    const map = mapRef.current;
    if (!map) return;

    const southWest = map.unproject([0, sceneHeight], maxZoom);
    const northEast = map.unproject([sceneWidth, 0], maxZoom);
    const bounds = new L.LatLngBounds(southWest, northEast);

    if (pixelTileLayerRef.current) {
      pixelTileLayerRef.current.options.maxZoom = maxZoom + PIXEL_OVERZOOM;
      pixelTileLayerRef.current.options.maxNativeZoom = availableNativeZoom ?? maxZoom;
      swapTileLayer(map, pixelTileLayerRef.current, tileUrlTemplate, pixelSwapRef, (layer, detach) => {
        pixelQueueDetachRef.current?.();
        pixelQueueDetachRef.current = detach;
        if (pixelTileLayerRef.current) map.removeLayer(pixelTileLayerRef.current);
        pixelTileLayerRef.current = layer;
      });
    } else {
      const tileLayer = L.tileLayer(tileUrlTemplate, {
        minZoom: 0,
        maxZoom: maxZoom + PIXEL_OVERZOOM,
        maxNativeZoom: maxZoom,
        tileSize: 256,
        noWrap: true,
        bounds: bounds,
        opacity: sceneOpacity / 100,
        keepBuffer: 0,
        updateWhenIdle: true,
        updateWhenZooming: false,
      });
      tileLayer.once("load", reportSceneRasterReady);
      // Ponawianie nieudanych kafli ORAZ przerywanie tych, które wypadły z widoku
      // (R0.5). Wcześniej było tu tylko ponawianie, wklejone w miejscu.
      pixelQueueDetachRef.current?.();
      pixelQueueDetachRef.current = attachTileQueueHygiene(tileLayer);
      tileLayer.addTo(map);
      pixelTileLayerRef.current = tileLayer;

      // On first open the container may still be sizing; fitBounds before the map
      // has a real size leaves the scene off-view and Leaflet requests no tiles
      // until a manual zoom. Recompute the size from the DOM each frame (Leaflet's
      // getSize() stays cached at 0 until invalidateSize) and fit once the container
      // actually has a non-zero size.
      const fitWhenReady = (tries = 0) => {
        if (mapRef.current !== map) return;
        map.invalidateSize({ pan: false });
        const container = map.getContainer();
        const ready = !!container && container.clientWidth > 0 && container.clientHeight > 0;
        if (ready) {
          map.fitBounds(bounds);
        } else if (tries < 60) {
          window.requestAnimationFrame(() => fitWhenReady(tries + 1));
        }
      };
      fitWhenReady();
    }

    map.setMaxZoom(maxZoom + PIXEL_OVERZOOM);
  }, [tileUrlTemplate, sceneWidth, sceneHeight, maxZoom, geoMode]);

  // â”€â”€â”€ Geo mode: basemap layer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    if (!geoMode) return;
    const map = mapRef.current;
    if (!map) return;

    if (!basemapUrl) {
      if (basemapLayerRef.current) map.removeLayer(basemapLayerRef.current);
      basemapLayerRef.current = null;
      basemapRequestIdRef.current += 1;
      onBasemapStateChange?.("idle");
      return;
    }

    const requestId = basemapRequestIdRef.current + 1;
    basemapRequestIdRef.current = requestId;
    let tileErrors = 0;
    let completed = false;

    const finishLoading = () => {
      if (completed || basemapRequestIdRef.current !== requestId) return;
      completed = true;
      onBasemapStateChange?.(tileErrors > 0 ? "error" : "ready");
    };

    const handleTileError = () => {
      if (basemapRequestIdRef.current !== requestId) return;
      tileErrors += 1;
    };

    let layer = basemapLayerRef.current;
    onBasemapStateChange?.("loading");
    if (!layer) {
      layer = L.tileLayer(basemapUrl, {
        maxZoom: 22,
        maxNativeZoom: 19,
        tileSize: 256,
        noWrap: true,
        keepBuffer: 1,
        updateWhenIdle: false,
        updateWhenZooming: true,
        updateInterval: 200,
      });
      basemapLayerRef.current = layer;
      layer.on("tileerror", handleTileError);
      layer.once("load", finishLoading);
      layer.setZIndex(1);
      layer.addTo(map);
    } else {
      layer.on("tileerror", handleTileError);
      layer.once("load", finishLoading);
      layer.setUrl(basemapUrl);
      layer.setZIndex(1);
      if (!map.hasLayer(layer)) layer.addTo(map);
    }

    const fallbackTimer = window.setTimeout(() => {
      tileErrors += 1;
      finishLoading();
    }, 10000);

    return () => {
      window.clearTimeout(fallbackTimer);
      layer.off("tileerror", handleTileError);
      layer.off("load", finishLoading);
    };
  }, [basemapUrl, geoMode, onBasemapStateChange]);

  // â"€â"€â"€ Geo mode: scene tile layer â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

  useEffect(() => {
    if (!geoMode) return;
    const map = mapRef.current;
    if (!map) return;

    if (geoTileUrlTemplate) {
      if (sceneLayerRef.current) {
        // Nowa warstwa ładuje się nad starą — stare kafle zostają widoczne do końca.
        swapTileLayer(map, sceneLayerRef.current, geoTileUrlTemplate, geoSwapRef, (layer, detach) => {
          geoQueueDetachRef.current?.();
          geoQueueDetachRef.current = detach;
          if (sceneLayerRef.current) map.removeLayer(sceneLayerRef.current);
          sceneLayerRef.current = layer;
        });
      } else {
        const layer = L.tileLayer(geoTileUrlTemplate, {
          maxZoom: 22,
          // Bez tego Leaflet żądał kafli do z22, czyli trzy poziomy powyżej natywnej
          // rozdzielczości sceny. Powyżej `maxNativeZoom` skaluje ostatni dostępny
          // poziom zamiast prosić backend o coś, czego nie da się obsłużyć.
          ...(availableNativeXyzZoom != null ? { maxNativeZoom: availableNativeXyzZoom } : {}),
          tileSize: 256,
          opacity: sceneOpacity / 100,
          keepBuffer: 0,
          updateWhenIdle: true,
          updateWhenZooming: false,
        });
        layer.once("load", reportSceneRasterReady);
        // Warstwa geo nie miała dotąd ani ponawiania, ani przerywania żądań (R0.5).
        // Przerywanie jest tu szczególnie istotne: przy JP2 porzucony kafel poprzedniego
        // kadru trzyma workera przez sekundy, a backend potrafi go porzucić dopiero,
        // gdy przeglądarka zerwie połączenie.
        geoQueueDetachRef.current?.();
        geoQueueDetachRef.current = attachTileQueueHygiene(layer);
        layer.addTo(map);
        layer.setZIndex(10);
        sceneLayerRef.current = layer;
      }
    } else if (sceneLayerRef.current) {
      cancelTileSwap(map, geoSwapRef);
      map.removeLayer(sceneLayerRef.current);
      sceneLayerRef.current = null;
    }
  }, [geoTileUrlTemplate, geoMode, reportSceneRasterReady]);

  // â”€â”€â”€ Geo mode: scene opacity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    const opacity = sceneOpacity / 100;
    for (const layer of [
      sceneLayerRef.current,
      pixelTileLayerRef.current,
      geoSwapRef.current?.layer,
      pixelSwapRef.current?.layer,
    ]) {
      layer?.setOpacity(opacity);
    }
  }, [sceneOpacity]);

  // â”€â”€â”€ Render annotations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  // --- Warstwa adnotacji: budowa geometrii oddzielona od zaznaczenia ---
  //
  // DESIGN_DECISIONS.md, performance-audit E4. Wczesniej JEDEN efekt wolal `clearLayers()` i odtwarzal
  // wszystkie ksztalty, majac w zaleznosciach `selectedAnnotationId` — wiec KAZDE klikniecie
  // przebudowywalo caly zbior. Audyt zmierzyl skale, przy ktorej to boli: 10 206 adnotacji na
  // najgestszej scenie DOTA (mediana 357 w xView3).
  //
  // Rozdzielone na dwa etapy, dokladnie jak warstwa kafli (`tilePolygonsRef`):
  //   1. BUDOWA — tylko gdy zmieni sie zbior adnotacji, rzutowanie, klasy albo narzedzie;
  //   2. `syncSelection` — przy samej zmianie zaznaczenia rusza WYLACZNIE styl warstw, ktore
  //      weszly lub wyszly z zaznaczenia, plus uchwyty jednej adnotacji. Koszt jest
  //      proporcjonalny do ZMIANY, nie do liczby adnotacji na scenie.
  const syncSelection = useCallback(() => {
    const map = mapRef.current;
    const handleLayer = resizeHandleLayerRef.current;
    // Uchwyty dotycza jednej adnotacji, wiec ich odtwarzanie jest tanie i zostaje pelne.
    handleLayer.clearLayers();
    if (!map) return;

    if (batchedAnnotationModeRef.current) {
      const selected = new Set(selectedAnnotationIds);
      if (selectedAnnotationId) selected.add(selectedAnnotationId);
      prevSelectionRef.current = selected;
      requestBatchedAnnotationDrawRef.current();
      return;
    }

    const shapes = annotShapesRef.current;
    const canEditAnnotations = activeMapTool === "select";
    const selected = new Set(selectedAnnotationIds);
    if (selectedAnnotationId) selected.add(selectedAnnotationId);

    // Roznica symetryczna: dotykamy tylko tego, co zmienilo stan.
    const previous = prevSelectionRef.current;
    for (const id of new Set([...previous, ...selected])) {
      const entry = shapes.get(id);
      if (!entry) continue;
      const isSelected = selected.has(id);
      entry.layer.setStyle({
        weight: isSelected ? 3 : 2,
        dashArray: isSelected ? "6, 4" : undefined,
      });
      // Przeciaganie ramki „za srodek" jest zwiazane z zaznaczeniem, wiec handler wedruje
      // razem z nim. `off` przed `on`, zeby nie nawarstwiac go przy kolejnych zmianach.
      entry.layer.off("mousedown");
      if (canEditAnnotations && id === selectedAnnotationId && !entry.rotatedState) {
        entry.layer.on("mousedown", (e: L.LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e);
          draggingAnnotationRef.current = {
            annId: id,
            classId: entry.classId,
            startLatLng: e.latlng,
            startBbox: entry.bbox,
            copyMode: false,
          };
          map.dragging.disable();
        });
      }
      entry.refreshDetails();
    }
    prevSelectionRef.current = selected;

    if (!canEditAnnotations || !selectedAnnotationId) return;
    const entry = shapes.get(selectedAnnotationId);
    if (!entry) return;

    const handleStyle: L.CircleMarkerOptions = {
      radius: 5,
      color: "#ffffff",
      weight: 2,
      fillColor: "#2563eb",
      fillOpacity: 1,
    };

    const addResizeHandle = (
      annId: string,
      handle: "nw" | "ne" | "se" | "sw",
      latlng: L.LatLng,
      startBbox: [number, number, number, number]
    ) => {
      const marker = L.circleMarker(toTuple(latlng), { ...handleStyle, interactive: true });
      marker.on("mousedown", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        resizingAnnotationRef.current = {
          annId,
          handle,
          startLatLng: e.latlng,
          startBbox,
        };
        map.dragging.disable();
      });
      marker.addTo(handleLayer);
    };

    const addBoxCenterHandle = (
      annId: string,
      classId: number,
      startBbox: [number, number, number, number]
    ) => {
      const [x0, y0, x1, y1] = startBbox;
      const center = pixelToLatLng((x0 + x1) / 2, (y0 + y1) / 2);
      const marker = L.circleMarker(toTuple(center), {
        radius: 6,
        color: "#ffffff",
        weight: 2,
        fillColor: "#a855f7",
        fillOpacity: 1,
        interactive: true,
      });
      marker.on("mousedown", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        draggingAnnotationRef.current = {
          annId,
          classId,
          startLatLng: e.latlng,
          startBbox,
          copyMode: copyModifierRef.current,
        };
        map.dragging.disable();
      });
      marker.addTo(handleLayer);
    };

    const addRotatedCornerHandle = (
      annId: string,
      handle: ObbCornerHandle,
      point: PixelPoint,
      startState: RotatedGeometryState,
      polygon: PixelPoint[] | null
    ) => {
      const marker = L.circleMarker(toTuple(pixelToLatLng(point.x, point.y)), {
        ...handleStyle,
        fillColor: "#22c55e",
        interactive: true,
      });
      marker.on("mousedown", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        resizingRotatedAnnotationRef.current = {
          annId,
          handle,
          startState,
          startPolygon: polygon ? polygon.map((p) => ({ x: p.x, y: p.y })) : null,
        };
        map.dragging.disable();
      });
      marker.addTo(handleLayer);
    };

    const addRotatedCenterHandle = (
      annId: string,
      classId: number,
      state: RotatedGeometryState,
      polygon: PixelPoint[] | null
    ) => {
      const marker = L.circleMarker(toTuple(pixelToLatLng(state.center.x, state.center.y)), {
        radius: 6,
        color: "#ffffff",
        weight: 2,
        fillColor: "#a855f7",
        fillOpacity: 1,
        interactive: true,
      });
      marker.on("mousedown", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        movingRotatedAnnotationRef.current = {
          annId,
          classId,
          startLatLng: e.latlng,
          startState: state,
          // Poligon wierny mapie — źródło prawdy dla przesuwania/kopii (zachowuje ścinanie).
          startPolygon: polygon ? polygon.map((point) => ({ x: point.x, y: point.y })) : null,
          copyMode: copyModifierRef.current,
        };
        map.dragging.disable();
      });
      marker.addTo(handleLayer);
    };

    const addRotatedRotateHandle = (
      annId: string,
      state: RotatedGeometryState,
      edgeStart: PixelPoint,
      edgeEnd: PixelPoint,
      polygon: PixelPoint[] | null
    ) => {
      const edgeMid = {
        x: (edgeStart.x + edgeEnd.x) / 2,
        y: (edgeStart.y + edgeEnd.y) / 2,
      };
      const edgeAngleDeg = Math.atan2(
        edgeMid.y - state.center.y,
        edgeMid.x - state.center.x
      ) * 180 / Math.PI;
      const marker = L.circleMarker(toTuple(pixelToLatLng(edgeMid.x, edgeMid.y)), {
        radius: 6,
        color: "#ffffff",
        weight: 2,
        fillColor: "#dc2626",
        fillOpacity: 1,
        interactive: true,
      });
      marker.on("mousedown", (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e);
        rotatingRotatedAnnotationRef.current = {
          annId,
          startState: state,
          edgeOffsetDeg: normalizeAngleDeg(edgeAngleDeg - state.angleDeg),
          startPolygon: polygon ? polygon.map((p) => ({ x: p.x, y: p.y })) : null,
        };
        map.dragging.disable();
      });
      marker.addTo(handleLayer);
    };

    const { rotatedState, rotatedPoints, bbox } = entry;
    if (rotatedState && rotatedPoints) {
      addRotatedCenterHandle(selectedAnnotationId, entry.classId, rotatedState, rotatedPoints);
      addRotatedRotateHandle(selectedAnnotationId, rotatedState, rotatedPoints[0], rotatedPoints[1], rotatedPoints);
      rotatedPoints.forEach((point, index) => {
        addRotatedCornerHandle(selectedAnnotationId, index as ObbCornerHandle, point, rotatedState, rotatedPoints);
      });
    } else {
      const [x0, y0, x1, y1] = bbox;
      addBoxCenterHandle(selectedAnnotationId, entry.classId, bbox);
      addResizeHandle(selectedAnnotationId, "nw", pixelToLatLng(x0, y0), bbox);
      addResizeHandle(selectedAnnotationId, "ne", pixelToLatLng(x1, y0), bbox);
      addResizeHandle(selectedAnnotationId, "se", pixelToLatLng(x1, y1), bbox);
      addResizeHandle(selectedAnnotationId, "sw", pixelToLatLng(x0, y1), bbox);
    }
  }, [activeMapTool, selectedAnnotationId, selectedAnnotationIds, pixelToLatLng]);

  // Efekt budowy czyta `syncSelection` przez ref, zeby NIE zalezec od zaznaczenia — inaczej
  // wrocilaby dokladnie ta przebudowa, ktora ten etap usuwa.
  const syncSelectionRef = useRef(syncSelection);
  syncSelectionRef.current = syncSelection;

  useEffect(() => {
    const layer = annotLayerRef.current;
    const map = mapRef.current;
    if (!map) return;
    const renderStarted = performance.now();
    let indexBuildMs = 0;
    if (annotationIndexSourceRef.current !== annotations) {
      const indexStarted = performance.now();
      annotationSpatialIndexRef.current.rebuild(annotations);
      indexBuildMs = performance.now() - indexStarted;
      annotationIndexSourceRef.current = annotations;
    }
    const canEditAnnotations = activeMapTool === "select";
    const canPaintClass = activeMapTool === "class_paint" && activeClassId !== null;
    const annotationsInteractive = canEditAnnotations || canPaintClass;
    const renderInputs: readonly unknown[] = [
      annotations,
      classes,
      projectId,
      annotationsInteractive,
      geoMode,
      pixelToLatLng,
      showAnnotationBoxes,
      showAnnotationLabels,
    ];
    const previousInputs = annotationRenderInputsRef.current;
    const requiresFullRebuild = (
      !previousInputs ||
      previousInputs.length !== renderInputs.length ||
      renderInputs.some((value, index) => value !== previousInputs[index])
    );
    annotationRenderInputsRef.current = renderInputs;

    if (requiresFullRebuild) {
      for (const entry of annotShapesRef.current.values()) entry.disposeDetails();
      layer.clearLayers();
      annotShapesRef.current = new Map();
      // Fresh shapes have base style, so current selection must be applied again.
      prevSelectionRef.current = new Set();
    }
    if (!showAnnotationBoxes) {
      if (batchedAnnotationDrawRafRef.current !== null) {
        window.cancelAnimationFrame(batchedAnnotationDrawRafRef.current);
        batchedAnnotationDrawRafRef.current = null;
      }
      batchedAnnotationCanvasRef.current?.remove();
      batchedAnnotationCanvasRef.current = null;
      batchedVisibleAnnotationsRef.current = [];
      batchedAnnotationModeRef.current = false;
      requestBatchedAnnotationDrawRef.current = () => undefined;
      batchedAnnotationHitTestRef.current = null;
      resizeHandleLayerRef.current.clearLayers();
      if (containerRef.current) {
        containerRef.current.dataset.annotationTotal = String(annotationSpatialIndexRef.current.size);
        containerRef.current.dataset.annotationRendered = "0";
        containerRef.current.dataset.annotationViewportMs = (performance.now() - renderStarted).toFixed(3);
        containerRef.current.dataset.annotationIndexMs = indexBuildMs.toFixed(3);
      }
      return;
    }
    const visibleSceneBounds = sceneBoundsForMap(
      map,
      latLngToPixelRef.current,
      sceneWidth,
      sceneHeight,
    );
    const visibleAnnotations: IndexedAnnotation[] = visibleSceneBounds
      ? annotationSpatialIndexRef.current.search(expandSceneBBox(
        visibleSceneBounds,
        sceneWidth,
        sceneHeight,
        ANNOTATION_VIEWPORT_PADDING_RATIO,
        ANNOTATION_VIEWPORT_MIN_PADDING_PX,
      ))
      : [];

    const removeBatchedCanvas = () => {
      if (batchedAnnotationDrawRafRef.current !== null) {
        window.cancelAnimationFrame(batchedAnnotationDrawRafRef.current);
        batchedAnnotationDrawRafRef.current = null;
      }
      batchedAnnotationCanvasRef.current?.remove();
      batchedAnnotationCanvasRef.current = null;
      batchedVisibleAnnotationsRef.current = [];
      batchedAnnotationModeRef.current = false;
      requestBatchedAnnotationDrawRef.current = () => undefined;
      batchedAnnotationHitTestRef.current = null;
    };

    if (visibleAnnotations.length > BATCHED_ANNOTATION_CANVAS_THRESHOLD) {
      if (annotShapesRef.current.size > 0) {
        for (const entry of annotShapesRef.current.values()) entry.disposeDetails();
        layer.clearLayers();
        annotShapesRef.current = new Map();
        prevSelectionRef.current = new Set();
      }
      resizeHandleLayerRef.current.clearLayers();
      batchedAnnotationModeRef.current = true;
      const container = containerRef.current;
      if (!container) return;
      let canvas = batchedAnnotationCanvasRef.current;
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.className = "annotation-batched-canvas";
        canvas.dataset.testid = "annotation-batched-canvas";
        Object.assign(canvas.style, {
          position: "absolute",
          zIndex: "450",
          pointerEvents: "none",
        });
        (map.getPane("overlayPane") ?? container).appendChild(canvas);
        batchedAnnotationCanvasRef.current = canvas;
      }

      const drawBatchedAnnotations = () => {
        const activeCanvas = batchedAnnotationCanvasRef.current;
        if (!activeCanvas || !batchedAnnotationModeRef.current) return;
        const started = performance.now();
        const bounds = sceneBoundsForMap(
          map,
          latLngToPixelRef.current,
          sceneWidth,
          sceneHeight,
        );
        const visible = bounds
          ? annotationSpatialIndexRef.current.search(expandSceneBBox(
            bounds,
            sceneWidth,
            sceneHeight,
            ANNOTATION_VIEWPORT_PADDING_RATIO,
            ANNOTATION_VIEWPORT_MIN_PADDING_PX,
          ))
          : [];
        batchedVisibleAnnotationsRef.current = visible;

        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const canvasBuffer = 256;
        const viewportWidth = Math.max(1, container.clientWidth);
        const viewportHeight = Math.max(1, container.clientHeight);
        const width = viewportWidth + canvasBuffer * 2;
        const height = viewportHeight + canvasBuffer * 2;
        const pixelWidth = Math.round(width * dpr);
        const pixelHeight = Math.round(height * dpr);
        if (activeCanvas.width !== pixelWidth || activeCanvas.height !== pixelHeight) {
          activeCanvas.width = pixelWidth;
          activeCanvas.height = pixelHeight;
          activeCanvas.style.width = `${width}px`;
          activeCanvas.style.height = `${height}px`;
        }
        const mapPanePosition = L.DomUtil.getPosition(map.getPane("mapPane") ?? container) ?? L.point(0, 0);
        activeCanvas.style.left = `${-mapPanePosition.x - canvasBuffer}px`;
        activeCanvas.style.top = `${-mapPanePosition.y - canvasBuffer}px`;
        const context = activeCanvas.getContext("2d");
        if (!context) return;
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.clearRect(0, 0, width, height);
        context.lineJoin = "round";
        context.lineCap = "round";

        const selected = new Set(selectedAnnotationIdsRef.current);
        if (selectedAnnotationIdRef.current) selected.add(selectedAnnotationIdRef.current);
        const groups = new Map<string, { color: string; selected: boolean; paths: PixelPoint[][] }>();
        const pointGroups = new Map<string, PixelPoint[]>();
        const labels: Array<{ text: string; color: string; point: PixelPoint }> = [];
        // Pixel-mode mapping is affine. Deriving it from three points avoids four complete
        // Leaflet project/unproject calls per annotation (400k conversions at 100k shapes).
        const sceneToScreen = (() => {
          if (geoMode) {
            return (point: PixelPoint): PixelPoint => {
              const screen = map.latLngToContainerPoint(pixelToLatLng(point.x, point.y));
              return { x: screen.x + canvasBuffer, y: screen.y + canvasBuffer };
            };
          }
          const originPoint = map.latLngToContainerPoint(pixelToLatLng(0, 0));
          const unitX = map.latLngToContainerPoint(pixelToLatLng(1, 0));
          const unitY = map.latLngToContainerPoint(pixelToLatLng(0, 1));
          const origin = { x: originPoint.x + canvasBuffer, y: originPoint.y + canvasBuffer };
          const xx = unitX.x - originPoint.x;
          const xy = unitX.y - originPoint.y;
          const yx = unitY.x - originPoint.x;
          const yy = unitY.y - originPoint.y;
          return (point: PixelPoint): PixelPoint => ({
            x: origin.x + point.x * xx + point.y * yx,
            y: origin.y + point.x * xy + point.y * yy,
          });
        })();
        for (const item of visible) {
          const annotation = item.annotation;
          const color = classMapRef.current.get(annotation.class_id)?.color || "#ff0000";
          const screenPoints = annotationScenePoints(annotation).map(sceneToScreen);
          const isSelected = selected.has(annotation.id);
          const isHovered = hoveredAnnotationIdRef.current === annotation.id;
          let minX = Number.POSITIVE_INFINITY;
          let minY = Number.POSITIVE_INFINITY;
          let maxX = Number.NEGATIVE_INFINITY;
          let maxY = Number.NEGATIVE_INFINITY;
          let centerX = 0;
          let centerY = 0;
          for (const point of screenPoints) {
            minX = Math.min(minX, point.x);
            minY = Math.min(minY, point.y);
            maxX = Math.max(maxX, point.x);
            maxY = Math.max(maxY, point.y);
            centerX += point.x / screenPoints.length;
            centerY += point.y / screenPoints.length;
          }
          const screenSize = Math.max(maxX - minX, maxY - minY);
          if (!isSelected && !isHovered && screenSize < 8) {
            const points = pointGroups.get(color) ?? [];
            points.push({ x: centerX, y: centerY });
            pointGroups.set(color, points);
          } else {
            const key = `${color}|${isSelected ? "selected" : "normal"}`;
            const group = groups.get(key) ?? { color, selected: isSelected, paths: [] };
            group.paths.push(screenPoints);
            groups.set(key, group);
          }

          if (showAnnotationLabels && (
            isSelected || isHovered || screenSize >= ANNOTATION_DETAIL_MIN_SCREEN_PX
          )) {
            labels.push({
              text: classMapRef.current.get(annotation.class_id)?.name || `Class ${annotation.class_id}`,
              color,
              point: { x: centerX, y: centerY },
            });
          }
        }

        context.globalAlpha = 0.85;
        for (const [color, points] of pointGroups) {
          context.fillStyle = color;
          for (const point of points) context.fillRect(point.x - 1, point.y - 1, 2, 2);
        }
        context.globalAlpha = 1;
        for (const group of groups.values()) {
          context.beginPath();
          for (const points of group.paths) {
            if (points.length === 0) continue;
            context.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
              context.lineTo(points[index].x, points[index].y);
            }
            context.closePath();
          }
          context.fillStyle = group.color;
          context.globalAlpha = 0.15;
          context.fill();
          context.globalAlpha = 1;
          context.strokeStyle = group.color;
          context.lineWidth = group.selected ? 3 : 2;
          context.setLineDash(group.selected ? [6, 4] : []);
          context.stroke();
        }
        context.setLineDash([]);
        context.font = "12px system-ui, sans-serif";
        context.textAlign = "center";
        context.textBaseline = "bottom";
        for (const label of labels) {
          context.lineWidth = 3;
          context.strokeStyle = "rgba(0, 0, 0, 0.8)";
          context.strokeText(label.text, label.point.x, label.point.y - 4);
          context.fillStyle = label.color;
          context.fillText(label.text, label.point.x, label.point.y - 4);
        }
        container.dataset.annotationRenderer = "batched-canvas";
        container.dataset.annotationTotal = String(annotationSpatialIndexRef.current.size);
        container.dataset.annotationRendered = String(visible.length);
        container.dataset.annotationAdded = String(visible.length);
        container.dataset.annotationRemoved = "0";
        container.dataset.annotationKept = "0";
        container.dataset.annotationViewportMs = (performance.now() - started).toFixed(3);
        container.dataset.annotationIndexMs = indexBuildMs.toFixed(3);
      };

      requestBatchedAnnotationDrawRef.current = () => {
        if (batchedAnnotationDrawRafRef.current !== null) return;
        batchedAnnotationDrawRafRef.current = window.requestAnimationFrame(() => {
          batchedAnnotationDrawRafRef.current = null;
          drawBatchedAnnotations();
        });
      };
      batchedAnnotationHitTestRef.current = (screenPoint) => {
        const point = latLngToPixelRef.current(map.containerPointToLatLng(screenPoint));
        const tolerancePoint = latLngToPixelRef.current(map.containerPointToLatLng(
          L.point(screenPoint.x + 6, screenPoint.y + 6),
        ));
        const tolerance = Math.max(
          Math.abs(tolerancePoint.x - point.x),
          Math.abs(tolerancePoint.y - point.y),
          0.5,
        );
        const candidates = annotationSpatialIndexRef.current.search([
          point.x - tolerance,
          point.y - tolerance,
          point.x + tolerance,
          point.y + tolerance,
        ]);
        for (let index = candidates.length - 1; index >= 0; index -= 1) {
          if (annotationContainsPoint(candidates[index].annotation, point, tolerance)) {
            return candidates[index];
          }
        }
        return null;
      };
      requestBatchedAnnotationDrawRef.current();
      return;
    }

    if (batchedAnnotationModeRef.current || batchedAnnotationCanvasRef.current) {
      removeBatchedCanvas();
      if (containerRef.current) containerRef.current.dataset.annotationRenderer = "leaflet-canvas";
    }
    const renderPlan = planAnnotationRenderLayer(annotShapesRef.current, visibleAnnotations);
    for (const id of renderPlan.removeIds) {
      const entry = annotShapesRef.current.get(id);
      if (!entry) continue;
      entry.disposeDetails();
      layer.removeLayer(entry.layer);
      annotShapesRef.current.delete(id);
      if (hoveredAnnotationIdRef.current === id) hoveredAnnotationIdRef.current = null;
    }
    // Adnotacja po edycji jest budowana OD NOWA, a nie łatana w miejscu: warstwa kształtu,
    // strzałka orientacji i poligon startowy uchwytów pochodzą z domknięcia zrobionego przy
    // dodaniu. Łatanie samego kształtu zostawiłoby resztę w stanie sprzed edycji — stąd
    // brała się strzałka w poprzednim położeniu i przekoszenie przy kolejnej edycji
    // narożnika (uchwyty liczyły się względem nieaktualnego poligonu).
    //
    // Kursor NIE jest tu zerowany jak przy `removeIds`: ramka zostaje pod myszą, a nowa
    // warstwa zaraz podepnie własne `mouseover`/`mouseout`.
    for (const item of renderPlan.changed) {
      const entry = annotShapesRef.current.get(item.id);
      if (!entry) continue;
      entry.disposeDetails();
      layer.removeLayer(entry.layer);
      annotShapesRef.current.delete(item.id);
    }
    for (const id of renderPlan.keepIds) annotShapesRef.current.get(id)?.refreshDetails();
    // Piksel <-> układ rzutowany mapy. Strzałka kierunku liczy się w tym układzie, bo
    // tylko tam bok ramki i kreska kierunku mają ten sam kąt (patrz `orientedArrowPoints`).
    const arrowCrs = map.options.crs ?? L.CRS.EPSG3857;
    const projectPixel = (point: PixelPoint): PixelPoint => {
      const projected = arrowCrs.project(pixelToLatLng(point.x, point.y));
      return { x: projected.x, y: projected.y };
    };
    const unprojectPixel = (point: PixelPoint): PixelPoint =>
      latLngToPixelRef.current(arrowCrs.unproject(L.point(point.x, point.y)));
    const addRotatedArrow = (
      state: RotatedGeometryState,
      color: string,
      polygon: PixelPoint[] | null,
    ): L.Layer[] => {
      // Na scenach niekonforemnych (piksel<->mapa anizotropowe) grot budowany w pikselach
      // sie scina/rozciaga — ten sam problem co skos ramki. W geoMode, gdy mamy poligon
      // wierny mapie, liczymy strzalke w ukladzie RZUTOWANYM (Mercator), inaczej pikselowo.
      const { start, end, headA, headB } = orientedArrowPoints(
        state,
        polygon,
        geoMode,
        projectPixel,
        unprojectPixel,
      );

      const line = L.polyline([
        pixelPointToLatLng(start, pixelToLatLng),
        pixelPointToLatLng(end, pixelToLatLng),
      ], { color, weight: 2, interactive: false });
      const head = createPixelPolygonLayer([end, headA, headB], pixelToLatLng, {
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 1,
        interactive: false,
      });
      return [line, head];
    };

    for (const { annotation: ann } of [...renderPlan.add, ...renderPlan.changed]) {
      const cls = classMap.get(ann.class_id);
      const color = cls?.color || "#FF0000";
      const [x0, y0, x1, y1] = ann.bbox;
      const rotatedState = ann.geometry_type === "rotated_bbox"
        ? rotatedStateFromAnnotation(ann)
        : null;
      const rotatedPoints = rotatedState
        ? (ann.polygon_scene_px && ann.polygon_scene_px.length >= 4
          ? ann.polygon_scene_px.slice(0, 4).map((point) => ({ x: point[0], y: point[1] }))
          : rotatedCorners(rotatedState))
        : null;
      // Styl BAZOWY (niezaznaczony). Wyroznienie zaznaczenia naklada `syncSelection`,
      // zeby budowa nie zalezala od zaznaczenia.
      const baseStyle: L.PathOptions = {
        color,
        weight: 2,
        fillOpacity: 0.15,
        interactive: annotationsInteractive,
      };
      const shape = rotatedPoints
        ? createPixelPolygonLayer(rotatedPoints, pixelToLatLng, baseStyle)
        : createPixelRectLayer(x0, y0, x1, y1, pixelToLatLng, geoMode, baseStyle);

      const label = cls?.name || `Class ${ann.class_id}`;
      let tooltipVisible = false;
      let detailLayers: L.Layer[] = [];
      const isSelectedNow = () => (
        selectedAnnotationIdRef.current === ann.id ||
        selectedAnnotationIdsRef.current.includes(ann.id)
      );
      const hasDetailScreenSpace = () => {
        const points = rotatedPoints ?? [
          { x: x0, y: y0 },
          { x: x1, y: y0 },
          { x: x1, y: y1 },
          { x: x0, y: y1 },
        ];
        const screen = points.map((point) => map.latLngToContainerPoint(
          pixelToLatLng(point.x, point.y),
        ));
        return hasAnnotationDetailScreenSpace(screen, ANNOTATION_DETAIL_MIN_SCREEN_PX);
      };
      const disposeDetails = () => {
        if (tooltipVisible) {
          shape.unbindTooltip();
          tooltipVisible = false;
        }
        for (const detailLayer of detailLayers) layer.removeLayer(detailLayer);
        detailLayers = [];
      };
      const refreshDetails = () => {
        const showDetails = (
          isSelectedNow() ||
          hoveredAnnotationIdRef.current === ann.id ||
          hasDetailScreenSpace()
        );
        if (!showDetails) {
          disposeDetails();
          return;
        }
        if (showAnnotationLabels && !tooltipVisible) {
          shape.bindTooltip(label, {
            permanent: true,
            direction: "top",
            className: "annotation-label",
            offset: [0, 0],
          });
          shape.openTooltip();
          tooltipVisible = true;
        }
        // `rotatedState`/`rotatedPoints` pochodzą z domknięcia zrobionego przy budowie tego
        // wpisu, a wpis jest budowany od nowa przy KAŻDEJ zmianie geometrii (patrz
        // `renderPlan.changed`). Domknięcie nie może więc być nieaktualne i strzałka nie
        // wymaga dodatkowego porównania — zanim ta niezmiennik istniał, właśnie tutaj
        // powstawała strzałka w poprzednim położeniu ramki.
        if (rotatedState && detailLayers.length === 0) {
          detailLayers = addRotatedArrow(rotatedState, color, rotatedPoints);
          for (const detailLayer of detailLayers) layer.addLayer(detailLayer);
        }
      };

      if (annotationsInteractive) {
        shape.on("mouseover", () => {
          hoveredAnnotationIdRef.current = ann.id;
          refreshDetails();
        });
        shape.on("mouseout", () => {
          if (hoveredAnnotationIdRef.current === ann.id) {
            hoveredAnnotationIdRef.current = null;
          }
          refreshDetails();
        });
        shape.on("click", (e: L.LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e);
          if (activeMapToolRef.current === "class_paint" && activeClassIdRef.current !== null) {
            if (ann.class_id !== activeClassIdRef.current) {
              onAnnotationClassChangeRef.current?.(ann.id, activeClassIdRef.current);
            }
            onAnnotationSelectRef.current?.(ann.id);
            return;
          }
          if (canEditAnnotationsRef.current) {
            onAnnotationSelectRef.current?.(ann.id);
          }
        });
      }

      layer.addLayer(shape);
      annotShapesRef.current.set(ann.id, {
        layer: shape,
        color,
        classId: ann.class_id,
        bbox: [x0, y0, x1, y1],
        rotatedState,
        rotatedPoints,
        geometryKey: annotationGeometryKey(ann),
        refreshDetails,
        disposeDetails,
      });
      // If a selected annotation has just entered the viewport, make the selection
      // synchronizer treat it as new and attach its edit handler/selection style.
      prevSelectionRef.current.delete(ann.id);
      refreshDetails();
    }

    // Ksztalty gotowe — naloz na nie aktualne zaznaczenie (styl + uchwyty).
    syncSelectionRef.current();
    if (containerRef.current) {
      containerRef.current.dataset.annotationRenderer = "leaflet-canvas";
      containerRef.current.dataset.annotationTotal = String(annotationSpatialIndexRef.current.size);
      containerRef.current.dataset.annotationRendered = String(annotShapesRef.current.size);
      containerRef.current.dataset.annotationAdded = String(renderPlan.add.length);
      containerRef.current.dataset.annotationChanged = String(renderPlan.changed.length);
      containerRef.current.dataset.annotationRemoved = String(renderPlan.removeIds.length);
      containerRef.current.dataset.annotationKept = String(renderPlan.keepIds.length);
      containerRef.current.dataset.annotationViewportMs = (performance.now() - renderStarted).toFixed(3);
      containerRef.current.dataset.annotationIndexMs = indexBuildMs.toFixed(3);
    }
  }, [
    annotations,
    activeClassId,
    activeMapTool,
    classes,
    projectId,
    geoMode,
    annotationViewportRevision,
    pixelToLatLng,
    sceneHeight,
    sceneWidth,
    showAnnotationBoxes,
    showAnnotationLabels,
  ]);

  // Sama zmiana zaznaczenia: bez odtwarzania geometrii.
  useEffect(() => {
    syncSelection();
  }, [syncSelection]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const refreshBatchedCanvas = () => requestBatchedAnnotationDrawRef.current();
    const refreshBatchedHover = (event: L.LeafletMouseEvent) => {
      if (!batchedAnnotationModeRef.current) return;
      const nextId = batchedAnnotationHitTestRef.current?.(event.containerPoint)?.id ?? null;
      if (hoveredAnnotationIdRef.current === nextId) return;
      hoveredAnnotationIdRef.current = nextId;
      refreshBatchedCanvas();
    };
    const clearBatchedHover = () => {
      if (!batchedAnnotationModeRef.current || hoveredAnnotationIdRef.current === null) return;
      hoveredAnnotationIdRef.current = null;
      refreshBatchedCanvas();
    };
    const refreshViewport = () => {
      refreshBatchedCanvas();
      if (annotationViewportRafRef.current !== null) {
        window.cancelAnimationFrame(annotationViewportRafRef.current);
      }
      annotationViewportRafRef.current = window.requestAnimationFrame(() => {
        annotationViewportRafRef.current = null;
        setAnnotationViewportRevision((revision) => revision + 1);
      });
    };
    map.on("zoom", refreshBatchedCanvas);
    map.on("resize", refreshBatchedCanvas);
    map.on("mousemove", refreshBatchedHover);
    map.on("mouseout", clearBatchedHover);
    map.on("moveend", refreshViewport);
    map.on("zoomend", refreshViewport);
    return () => {
      map.off("zoom", refreshBatchedCanvas);
      map.off("resize", refreshBatchedCanvas);
      map.off("mousemove", refreshBatchedHover);
      map.off("mouseout", clearBatchedHover);
      map.off("moveend", refreshViewport);
      map.off("zoomend", refreshViewport);
      if (annotationViewportRafRef.current !== null) {
        window.cancelAnimationFrame(annotationViewportRafRef.current);
        annotationViewportRafRef.current = null;
      }
    };
  }, [projectId, geoMode]);

  // P2.4-B: read-only diagnostics for the local WebView2 benchmark. Vite removes this
  // branch from production builds. The bridge exposes map operations and counters only;
  // it cannot create, update or delete project annotations.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const map = mapRef.current;
    const container = containerRef.current;
    if (!map || !container) return;
    const benchmarkWindow = window as AnnotationBenchmarkWindow;
    const bridge: AnnotationBenchmarkBridge = {
      snapshot: () => {
        const numberFromDataset = (value: string | undefined): number | null => {
          if (value === undefined) return null;
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : null;
        };
        return {
          total: annotationSpatialIndexRef.current.size,
          rendered: batchedAnnotationModeRef.current
            ? batchedVisibleAnnotationsRef.current.length
            : annotShapesRef.current.size,
          renderer: batchedAnnotationModeRef.current ? "batched-canvas" : "leaflet-canvas",
          labels: container.querySelectorAll(".annotation-label").length,
          canvases: container.querySelectorAll("canvas").length,
          domNodes: container.querySelectorAll("*").length,
          zoom: map.getZoom(),
          minZoom: map.getMinZoom(),
          maxZoom: map.getMaxZoom(),
          viewportMs: numberFromDataset(container.dataset.annotationViewportMs),
          indexMs: numberFromDataset(container.dataset.annotationIndexMs),
        };
      },
      fit: () => {
        if (geoMode && geoBounds) {
          const [west, south, east, north] = geoBounds;
          map.fitBounds([[south, west], [north, east]], { animate: false });
          return;
        }
        const southWest = map.unproject([0, sceneHeight], maxZoom);
        const northEast = map.unproject([sceneWidth, 0], maxZoom);
        map.fitBounds(new L.LatLngBounds(southWest, northEast), { animate: false });
      },
      panBy: (x, y) => {
        const rawMap = map as L.Map & { _rawPanBy: (offset: L.Point) => void };
        rawMap._rawPanBy(L.point(x, y));
        map.fire("move");
      },
      panEnd: () => map.fire("moveend"),
      zoomBy: (delta) => map.setZoom(
        Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), map.getZoom() + delta)),
        { animate: false },
      ),
      renderedCenters: (geometryType, limit = 10) => {
        const points: Array<{
          id: string;
          geometryType: Annotation["geometry_type"];
          x: number;
          y: number;
        }> = [];
        const renderedIds = batchedAnnotationModeRef.current
          ? batchedVisibleAnnotationsRef.current.map((item) => item.id)
          : [...annotShapesRef.current.keys()];
        for (const id of renderedIds) {
          const indexed = annotationSpatialIndexRef.current.get(id);
          if (!indexed || (geometryType && indexed.annotation.geometry_type !== geometryType)) continue;
          const [x0, y0, x1, y1] = indexed.annotation.bbox;
          const screen = map.latLngToContainerPoint(pixelToLatLng((x0 + x1) / 2, (y0 + y1) / 2));
          if (screen.x < 0 || screen.y < 0 || screen.x > container.clientWidth || screen.y > container.clientHeight) {
            continue;
          }
          points.push({
            id,
            geometryType: indexed.annotation.geometry_type,
            x: screen.x,
            y: screen.y,
          });
          if (points.length >= limit) break;
        }
        return points;
      },
      renderedGeometry: (id) => {
        const entry = annotShapesRef.current.get(id);
        const indexed = annotationSpatialIndexRef.current.get(id);
        if (!indexed) return null;
        const sceneCorners = entry?.rotatedPoints ?? annotationScenePoints(indexed.annotation);
        const toScreen = (point: PixelPoint): PixelPoint => {
          const screen = map.latLngToContainerPoint(pixelToLatLng(point.x, point.y));
          return { x: screen.x, y: screen.y };
        };
        const corners = sceneCorners.map(toScreen);
        const rotatedState = entry?.rotatedState ?? rotatedStateFromAnnotation(indexed.annotation);
        const bbox = entry?.bbox ?? indexed.annotation.bbox;
        const center = rotatedState
          ? toScreen(rotatedState.center)
          : toScreen({
            x: (bbox[0] + bbox[2]) / 2,
            y: (bbox[1] + bbox[3]) / 2,
          });
        return {
          id,
          geometryType: indexed.annotation.geometry_type,
          center,
          corners,
          rotateHandle: rotatedState && corners.length >= 2
            ? {
              x: (corners[0].x + corners[1].x) / 2,
              y: (corners[0].y + corners[1].y) / 2,
            }
            : null,
        };
      },
    };
    benchmarkWindow.__GEOTILE_ANNOTATION_BENCHMARK__ = bridge;
    return () => {
      if (benchmarkWindow.__GEOTILE_ANNOTATION_BENCHMARK__ === bridge) {
        delete benchmarkWindow.__GEOTILE_ANNOTATION_BENCHMARK__;
      }
    };
  }, [geoBounds, geoMode, maxZoom, pixelToLatLng, projectId, sceneHeight, sceneWidth]);

  // --- Render predictions layer ---

  useEffect(() => {
    const layer = predictionLayerRef.current;
    layer.clearLayers();
    if (!showPredictions || predictions.length === 0) return;
    const selectedPredictionSet = new Set(selectedPredictionIds);

    // Piksel <-> układ rzutowany mapy. Strzałka kierunku liczy się w tym układzie, bo
    // tylko tam bok ramki i kreska kierunku mają ten sam kąt (patrz `orientedArrowPoints`).
    const arrowCrs = mapRef.current?.options.crs ?? L.CRS.EPSG3857;
    const projectPixel = (point: PixelPoint): PixelPoint => {
      const projected = arrowCrs.project(pixelToLatLng(point.x, point.y));
      return { x: projected.x, y: projected.y };
    };
    const unprojectPixel = (point: PixelPoint): PixelPoint =>
      latLngToPixelRef.current(arrowCrs.unproject(L.point(point.x, point.y)));

    for (const pred of predictions) {
      if (pred.status === "accepted") continue; // accepted become annotations
      const [x0, y0, x1, y1] = pred.bbox;

      const isPending = pred.status === "pending";
      const isSelected = pred.id === selectedPredictionId || selectedPredictionSet.has(pred.id);
      const color = isSelected ? "#a78bfa" : (isPending ? "#f97316" : "#ef4444");
      const weight = isSelected ? 4 : (isPending ? 2 : 1);
      const fillOpacity = isSelected ? 0.18 : (isPending ? 0.1 : 0.03);
      const dashArray = isSelected ? "8, 4" : (isPending ? "6, 4" : "3, 3");
      const options: L.PolylineOptions = {
        color,
        weight,
        fillOpacity,
        dashArray,
        interactive: activeMapTool === "select" && isPending,
      };

      const rb = pred.rotated_bbox;
      let shape: L.Layer;
      if (pred.geometry_type === "rotated_bbox" && rb) {
        const state: RotatedGeometryState = {
          center: { x: rb.cx, y: rb.cy },
          width: rb.width,
          height: rb.height,
          angleDeg: rb.angle_deg,
        };
        // Ta sama kolejność źródeł co dla adnotacji: poligon niesie kształt wierny
        // mapie, `rotated_bbox` tylko prostokąt pikselowy. Propozycja musi wyglądać
        // dokładnie tak, jak wyjdzie po akceptacji — inaczej użytkownik zatwierdza
        // co innego, niż widzi.
        const polygon = pred.polygon_scene_px;
        const corners = polygon && polygon.length >= 4
          ? polygon.slice(0, 4).map(([x, y]) => ({ x, y }))
          : rotatedCorners(state);
        shape = createPixelPolygonLayer(corners, pixelToLatLng, options);
        // Strzałka kierunku przodu — DOKŁADNIE ta sama konstrukcja co dla przyjętej
        // adnotacji (`orientedArrowPoints`). Wcześniej propozycja liczyła ją z
        // pikselowego `front_vector_scene_px`, a adnotacja z krawędzi poligonu: na
        // scenach niekonforemnych dawało to dwa różne kąty i kierunek „przeskakiwał"
        // w momencie akceptacji.
        if (pred.front_vector_scene_px || polygon) {
          const arrow = orientedArrowPoints(
            state,
            polygon && polygon.length >= 4
              ? polygon.slice(0, 4).map(([px, py]) => ({ x: px, y: py }))
              : null,
            geoMode,
            projectPixel,
            unprojectPixel,
          );
          layer.addLayer(
            L.polyline([
              pixelPointToLatLng(arrow.start, pixelToLatLng),
              pixelPointToLatLng(arrow.end, pixelToLatLng),
            ], {
              color,
              weight: weight + 1,
              dashArray: isSelected ? "8, 4" : undefined,
            })
          );
          layer.addLayer(
            createPixelPolygonLayer([arrow.end, arrow.headA, arrow.headB], pixelToLatLng, {
              color,
              fillColor: color,
              fillOpacity: 0.85,
              weight: 1,
              interactive: false,
            })
          );
        }
      } else {
        shape = createPixelRectLayer(x0, y0, x1, y1, pixelToLatLng, geoMode, options);
      }

      (shape as L.Path).bindTooltip(
        `${pred.class_name} (${pred.confidence.toFixed(2)})`,
        { sticky: true }
      );
      shape.on("click", (event: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(event);
        if (activeMapToolRef.current === "select") {
          onPredictionSelectRef.current?.(pred.id);
        }
      });

      layer.addLayer(shape);
    }
  }, [
    predictions,
    showPredictions,
    pixelToLatLng,
    geoMode,
    activeMapTool,
    selectedPredictionId,
    selectedPredictionIds,
  ]);

  // â"€â"€â"€ Render preview grid (TilingPreview) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

  useEffect(() => {
    const layer = gridLayerRef.current;
    layer.clearLayers();

    if (!showGrid || !tilingPreview) return;

    for (const [x0, y0, x1, y1] of tilingPreview.tile_rects) {
      createPixelRectLayer(x0, y0, x1, y1, pixelToLatLng, geoMode, {
        color: "rgba(0, 255, 0, 0.5)",
        weight: 1,
        fillOpacity: 0.03,
        interactive: false,
      }).addTo(layer);
    }

    createPixelRectLayer(0, 0, sceneWidth, sceneHeight, pixelToLatLng, geoMode, {
      color: "rgba(255, 255, 0, 0.6)",
      weight: 2,
      fillOpacity: 0,
      dashArray: "8, 4",
      interactive: false,
    }).addTo(layer);
  }, [tilingPreview, showGrid, sceneWidth, sceneHeight, pixelToLatLng, geoMode]);

  // --- Render interactive tile grid ------------------------------------------
  // Uses real TileInfo[] if available, or falls back to tilingPreview rects.
  //
  // Split into two passes on purpose. Building a tile costs four reprojections and an
  // SVG node; marking one only changes its colour. Sharing an effect meant every click
  // tore down and rebuilt the entire grid, which is what made the review tools crawl.

  // Stands in for the tile positions. Review state is deliberately absent: it must not
  // trigger a geometry rebuild.
  const tileGeometryKey = [
    showTileGrid ? "on" : "off",
    tiles?.length ?? 0,
    tilingPreview?.tile_rects.length ?? 0,
    tilingPreview?.num_cols ?? 0,
    tileSize,
  ].join(":");

  useEffect(() => {
    const layer = tileGridLayerRef.current;
    layer.clearLayers();
    tilePolygonsRef.current = [];
    tileBaseStyleRef.current = [];

    if (!showTileGrid) return;

    const rects = buildTileRects(tilesRef.current, tilingPreviewRef.current, tileSize);
    if (rects.length === 0) return;

    // Review needs real tile objects; preview rects are display-only.
    const interactive = (tilesRef.current?.length ?? 0) > 0;

    for (let i = 0; i < rects.length; i++) {
      const rect = rects[i];
      const shape = createPixelRectLayer(rect.x0, rect.y0, rect.x1, rect.y1, pixelToLatLng, geoMode, {
        color: "rgba(160, 160, 160, 0.6)",
        weight: 1,
        fillOpacity: 0,
        interactive: true,
      });
      shape.bindTooltip("", { sticky: true });

      shape.on("mouseover", () => {
        shape.setStyle({ weight: 2, color: "#60a5fa" });
        if (hoveredTileRef) hoveredTileRef.current = i;
        if (
          tilePaintActiveRef.current &&
          ["grid_review", "grid_clear", "grid_exclude"].includes(activeMapToolRef.current) &&
          interactive &&
          !paintedTileIndicesRef.current.has(i)
        ) {
          paintedTileIndicesRef.current.add(i);
          // Paint now, send once on mouse-up. Previously each hovered tile fired its
          // own awaited request plus a full state update mid-drag.
          const preview = paintPreviewStyle(
            activeMapToolRef.current,
            tileAnnCountsRef.current[i] ?? 0
          );
          if (preview) paintedStyleRef.current.set(i, preview);
        }
      });
      shape.on("mouseout", () => {
        const painted = paintedStyleRef.current.get(i);
        shape.setStyle({ weight: 1, ...(painted ?? tileBaseStyleRef.current[i] ?? {}) });
        if (hoveredTileRef) hoveredTileRef.current = null;
      });
      shape.on("mousedown", (e: L.LeafletMouseEvent) => {
        // A stroke that ended off the grid never got its trailing click, so clear the
        // guard here rather than leaving it to swallow the next real one.
        suppressTileClickRef.current = false;
        if (!["grid_review", "grid_clear", "grid_exclude"].includes(activeMapToolRef.current)) return;
        L.DomEvent.stopPropagation(e);
        tilePaintActiveRef.current = true;
        paintedTileIndicesRef.current.clear();
        paintedStyleRef.current.clear();
        if (!interactive) return;
        // The tile under the cursor fired its mouseover before this mousedown, so the
        // stroke has to claim it here or it would be the one tile left unpainted.
        paintedTileIndicesRef.current.add(i);
        const preview = paintPreviewStyle(
          activeMapToolRef.current,
          tileAnnCountsRef.current[i] ?? 0
        );
        if (preview) paintedStyleRef.current.set(i, preview);
      });

      if (interactive) {
        const tileIndex = i;
        shape.on("click", (e: L.LeafletMouseEvent) => {
          L.DomEvent.stopPropagation(e);
          // The stroke that just committed on mouse-up owns this click.
          if (suppressTileClickRef.current) {
            suppressTileClickRef.current = false;
            return;
          }
          // A drag already queued this tile; committing happens on mouse-up.
          if (paintedTileIndicesRef.current.size > 0) return;
          onTileClickRef.current?.(tileIndex);
        });
      }

      tilePolygonsRef.current.push(shape);
      layer.addLayer(shape);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tileGeometryKey, pixelToLatLng, geoMode, hoveredTileRef]);

  // Pass two: appearance only. Runs on every review change but touches existing
  // polygons, so no reprojection and no DOM churn.
  useEffect(() => {
    if (!showTileGrid) return;
    const shapes = tilePolygonsRef.current;
    if (shapes.length === 0) return;

    const rects = buildTileRects(tiles, tilingPreview, tileSize);
    // A geometry change lands here first; the geometry pass rebuilds and this reruns.
    if (rects.length !== shapes.length) return;

    const counts = annotationCountsPerTile(rects, annotationSpatialIndexRef.current);
    tileAnnCountsRef.current = counts;
    tileBaseStyleRef.current = rects.map((rect, i) =>
      tileStyleFor(rect.reviewed, rect.excluded, counts[i])
    );

    for (let i = 0; i < shapes.length; i++) {
      shapes[i].setStyle({ weight: 1, ...tileBaseStyleRef.current[i] });
      shapes[i].setTooltipContent(tileTooltip(rects[i], counts[i]));
    }
  }, [tiles, tilingPreview, annotations, tileSize, showTileGrid, tileGeometryKey]);

  // â”€â”€â”€ Drawing interaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // Handlers read from refs so the effect only runs once per map instance.

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const RIGHT_PAN_DRAG_THRESHOLD = 2;

    const clampDraggedBbox = (
      bbox: [number, number, number, number],
      dx: number,
      dy: number
    ): [number, number, number, number] => {
      const [x0, y0, x1, y1] = bbox;
      const width = x1 - x0;
      const height = y1 - y0;

      const nx0 = Math.min(Math.max(x0 + dx, 0), Math.max(0, sceneWidth - width));
      const ny0 = Math.min(Math.max(y0 + dy, 0), Math.max(0, sceneHeight - height));
      return [nx0, ny0, nx0 + width, ny0 + height];
    };

    const clampResizedBbox = (
      bbox: [number, number, number, number],
      handle: "nw" | "ne" | "se" | "sw",
      dx: number,
      dy: number
    ): [number, number, number, number] => {
      const MIN_SIZE = 3;
      const [x0, y0, x1, y1] = bbox;

      if (handle === "nw") {
        const nx0 = Math.min(Math.max(x0 + dx, 0), x1 - MIN_SIZE);
        const ny0 = Math.min(Math.max(y0 + dy, 0), y1 - MIN_SIZE);
        return [nx0, ny0, x1, y1];
      }
      if (handle === "ne") {
        const nx1 = Math.max(Math.min(x1 + dx, sceneWidth), x0 + MIN_SIZE);
        const ny0 = Math.min(Math.max(y0 + dy, 0), y1 - MIN_SIZE);
        return [x0, ny0, nx1, y1];
      }
      if (handle === "se") {
        const nx1 = Math.max(Math.min(x1 + dx, sceneWidth), x0 + MIN_SIZE);
        const ny1 = Math.max(Math.min(y1 + dy, sceneHeight), y0 + MIN_SIZE);
        return [x0, y0, nx1, ny1];
      }

      const nx0 = Math.min(Math.max(x0 + dx, 0), x1 - MIN_SIZE);
      const ny1 = Math.max(Math.min(y1 + dy, sceneHeight), y0 + MIN_SIZE);
      return [nx0, y0, x1, ny1];
    };

    const updateDragPreview = (bbox: [number, number, number, number]) => {
      const [bx0, by0, bx1, by1] = bbox;
      if (dragPreviewRef.current) {
        updatePixelRectLayer(dragPreviewRef.current, bx0, by0, bx1, by1, pixelToLatLng, geoMode);
      } else {
        dragPreviewRef.current = createPixelRectLayer(bx0, by0, bx1, by1, pixelToLatLng, geoMode, {
          color: "#f59e0b",
          weight: 2,
          dashArray: "6, 6",
          fillOpacity: 0.05,
          interactive: false,
        });
        dragPreviewRef.current.addTo(map);
      }
    };

    const updateRotatedPreview = (geometry: AnnotationGeometryPayload) => {
      const points = geometry.polygon_scene_px?.map((point) => ({ x: point[0], y: point[1] })) ?? [];
      if (points.length < 4) return;
      if (dragPreviewRef.current) {
        updatePixelPolygonLayer(dragPreviewRef.current as L.Polygon, points, pixelToLatLng);
      } else {
        dragPreviewRef.current = createPixelPolygonLayer(points, pixelToLatLng, {
          color: "#f59e0b",
          weight: 2,
          dashArray: "6, 6",
          fillOpacity: 0.05,
          interactive: false,
        });
        dragPreviewRef.current.addTo(map);
      }
    };

    const updateDrawMeasureLabelFromPoints = (points: PixelPoint[]) => {
      if (!geoMode || !geoTransform) return;
      if (points.length < 4) return;

      const widthPixels = Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y);
      const heightPixels = Math.hypot(points[2].x - points[1].x, points[2].y - points[1].y);
      if (widthPixels < 1 || heightPixels < 1) return;

      drawMeasureBboxRef.current = points;
      const activeClass = activeClassIdRef.current === null
        ? undefined
        : classMapRef.current.get(activeClassIdRef.current);
      const labelColor = activeClass?.color || "#facc15";

      const topLeft = pixelToLatLng(points[0].x, points[0].y);
      const topRight = pixelToLatLng(points[1].x, points[1].y);
      const bottomRight = pixelToLatLng(points[2].x, points[2].y);
      const bottomLeft = pixelToLatLng(points[3].x, points[3].y);
      const widthMeters = (topLeft.distanceTo(topRight) + bottomLeft.distanceTo(bottomRight)) / 2;
      const heightMeters = (topRight.distanceTo(bottomRight) + topLeft.distanceTo(bottomLeft)) / 2;

      const topLeftPt = map.latLngToContainerPoint(topLeft);
      const topRightPt = map.latLngToContainerPoint(topRight);
      const bottomRightPt = map.latLngToContainerPoint(bottomRight);
      const bottomLeftPt = map.latLngToContainerPoint(bottomLeft);

      const centerPt = L.point(
        (topLeftPt.x + topRightPt.x + bottomRightPt.x + bottomLeftPt.x) / 4,
        (topLeftPt.y + topRightPt.y + bottomRightPt.y + bottomLeftPt.y) / 4
      );

      const sideLabelPoint = (a: L.Point, b: L.Point, offsetPixels: number): L.Point => {
        const mid = L.point((a.x + b.x) / 2, (a.y + b.y) / 2);
        const outward = L.point(mid.x - centerPt.x, mid.y - centerPt.y);
        const length = Math.hypot(outward.x, outward.y) || 1;
        return L.point(
          mid.x + (outward.x / length) * offsetPixels,
          mid.y + (outward.y / length) * offsetPixels
        );
      };

      const sideAngle = (a: L.Point, b: L.Point): number =>
        Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;

      const widthLatLng = map.containerPointToLatLng(sideLabelPoint(topLeftPt, topRightPt, 16));
      const heightLatLng = map.containerPointToLatLng(sideLabelPoint(topRightPt, bottomRightPt, 18));

      const widthIcon = L.divIcon({
        className: "bbox-measure-leaflet-icon",
        html: createSideMeasureLabelHtml(
          formatDistanceMeters(widthMeters),
          labelColor,
          sideAngle(topLeftPt, topRightPt)
        ),
        iconSize: undefined,
        iconAnchor: [0, 0],
      });

      const heightIcon = L.divIcon({
        className: "bbox-measure-leaflet-icon",
        html: createSideMeasureLabelHtml(
          formatDistanceMeters(heightMeters),
          labelColor,
          sideAngle(topRightPt, bottomRightPt)
        ),
        iconSize: undefined,
        iconAnchor: [0, 0],
      });

      if (drawMeasureLabelsRef.current) {
        drawMeasureLabelsRef.current.width.setLatLng(widthLatLng);
        drawMeasureLabelsRef.current.width.setIcon(widthIcon);
        drawMeasureLabelsRef.current.height.setLatLng(heightLatLng);
        drawMeasureLabelsRef.current.height.setIcon(heightIcon);
      } else {
        drawMeasureLabelsRef.current = {
          width: L.marker(widthLatLng, {
            icon: widthIcon,
            interactive: false,
            keyboard: false,
          }).addTo(map),
          height: L.marker(heightLatLng, {
            icon: heightIcon,
            interactive: false,
            keyboard: false,
          }).addTo(map),
        };
      }
    };

    const updateDrawMeasureLabel = (
      x0: number,
      y0: number,
      x1: number,
      y1: number
    ) => {
      const left = Math.min(x0, x1);
      const right = Math.max(x0, x1);
      const top = Math.min(y0, y1);
      const bottom = Math.max(y0, y1);
      updateDrawMeasureLabelFromPoints([
        { x: left, y: top },
        { x: right, y: top },
        { x: right, y: bottom },
        { x: left, y: bottom },
      ]);
    };

    const refreshDrawMeasureLabels = () => {
      const points = drawMeasureBboxRef.current;
      if (!points) return;
      updateDrawMeasureLabelFromPoints(points);
    };

    const clearDrawMeasureLabel = () => {
      drawMeasureBboxRef.current = null;
      if (drawMeasureLabelsRef.current) {
        map.removeLayer(drawMeasureLabelsRef.current.width);
        map.removeLayer(drawMeasureLabelsRef.current.height);
        drawMeasureLabelsRef.current = null;
      }
    };

    const getActiveClassColor = (): string => {
      if (activeClassIdRef.current === null) return "rgba(255, 255, 0, 0.8)";
      return classMapRef.current.get(activeClassIdRef.current)?.color || "rgba(255, 255, 0, 0.8)";
    };

    const createDrawPreview = (
      x0: number,
      y0: number,
      x1: number,
      y1: number
    ) => {
      drawPreviewRef.current = createPixelRectLayer(x0, y0, x1, y1, pixelToLatLng, geoMode, {
        color: getActiveClassColor(),
        weight: 2,
        dashArray: "5, 5",
        fillOpacity: 0.05,
        interactive: false,
      });
      drawPreviewRef.current.addTo(map);
    };

    const clearDrawAxisPreview = () => {
      if (drawAxisPreviewRef.current) {
        map.removeLayer(drawAxisPreviewRef.current);
        drawAxisPreviewRef.current = null;
      }
    };

    const updateDrawAxisPreview = (start: PixelPoint, end: PixelPoint) => {
      const latLngs = [
        pixelPointToLatLng(start, pixelToLatLng),
        pixelPointToLatLng(end, pixelToLatLng),
      ];
      if (drawAxisPreviewRef.current) {
        drawAxisPreviewRef.current.setLatLngs(latLngs);
      } else {
        drawAxisPreviewRef.current = L.polyline(latLngs, {
          color: getActiveClassColor(),
          weight: 2,
          dashArray: "5, 5",
          interactive: false,
        }).addTo(map);
      }
    };

    const updateDrawRotatedPreview = (geometry: AnnotationGeometryPayload) => {
      const points = geometry.polygon_scene_px?.map((point) => ({ x: point[0], y: point[1] })) ?? [];
      if (points.length < 4) return;
      clearDrawAxisPreview();
      if (drawPreviewRef.current) {
        updatePixelPolygonLayer(drawPreviewRef.current as L.Polygon, points, pixelToLatLng);
      } else {
        drawPreviewRef.current = createPixelPolygonLayer(points, pixelToLatLng, {
          color: getActiveClassColor(),
          weight: 2,
          dashArray: "5, 5",
          fillOpacity: 0.05,
          interactive: false,
        });
        drawPreviewRef.current.addTo(map);
      }
      updateDrawMeasureLabelFromPoints(points);
    };

    const rotatedGeometryFromMap = (
      start: L.LatLng,
      end: L.LatLng,
      side: L.LatLng
    ): AnnotationGeometryPayload | null => {
      if (!geoMode) {
        return rotatedPayloadFromDrawing(
          latLngToPixelRef.current(start),
          latLngToPixelRef.current(end),
          latLngToPixelRef.current(side)
        );
      }

      const crs = map.options.crs ?? L.CRS.EPSG3857;
      const points = projectedRectanglePixels(
        start,
        end,
        side,
        (point) => {
          const projected = crs.project(L.latLng(point.lat, point.lng));
          return { x: projected.x, y: projected.y };
        },
        (point) => {
          const latLng = crs.unproject(L.point(point.x, point.y));
          return { lat: latLng.lat, lng: latLng.lng };
        },
        (point) => latLngToPixelRef.current(L.latLng(point.lat, point.lng))
      );
      return points ? rotatedPayloadFromPolygon(points) : null;
    };

    // Konwersje piksel↔układ rzutowany mapy (Mercator) do edycji rotowanych ramek w
    // ramce mapy — dzięki temu resize/rotate dają prostokąt na MAPIE (bez spłaszczania
    // ścinania na scenach niekonforemnych). Poza geoMode zostaje matematyka pikselowa.
    const editCrs = map.options.crs ?? L.CRS.EPSG3857;
    const toProjected = (point: PixelPoint): PixelPoint => {
      const projected = editCrs.project(pixelToLatLngRef.current(point.x, point.y));
      return { x: projected.x, y: projected.y };
    };
    const toPixel = (point: PixelPoint): PixelPoint =>
      latLngToPixelRef.current(editCrs.unproject(L.point(point.x, point.y)));

    const resizedRotatedPayload = (
      resize: NonNullable<typeof resizingRotatedAnnotationRef.current>,
      latlng: L.LatLng
    ): AnnotationGeometryPayload => {
      if (geoMode && resize.startPolygon) {
        const projState = projectedStateFromPolygon(resize.startPolygon, toProjected);
        const projected = editCrs.project(latlng);
        const payload = pixelPayloadFromProjectedState(
          resizeRotatedState(projState, resize.handle, { x: projected.x, y: projected.y }),
          toPixel
        );
        if (payload) return payload;
      }
      const current = latLngToPixelRef.current(latlng);
      return rotatedPayloadFromState(resizeRotatedState(resize.startState, resize.handle, current));
    };

    const rotatedRotatedPayload = (
      rotate: NonNullable<typeof rotatingRotatedAnnotationRef.current>,
      latlng: L.LatLng
    ): AnnotationGeometryPayload => {
      if (geoMode && rotate.startPolygon) {
        const projState = projectedStateFromPolygon(rotate.startPolygon, toProjected);
        const projStart = toProjected(rotate.startPolygon[0]);
        const projEnd = toProjected(rotate.startPolygon[1]);
        const edgeMid = { x: (projStart.x + projEnd.x) / 2, y: (projStart.y + projEnd.y) / 2 };
        const edgeAngle = Math.atan2(edgeMid.y - projState.center.y, edgeMid.x - projState.center.x) * 180 / Math.PI;
        const edgeOffset = normalizeAngleDeg(edgeAngle - projState.angleDeg);
        const projected = editCrs.project(latlng);
        const angleDeg = Math.atan2(projected.y - projState.center.y, projected.x - projState.center.x) * 180 / Math.PI - edgeOffset;
        const payload = pixelPayloadFromProjectedState({ ...projState, angleDeg }, toPixel);
        if (payload) return payload;
      }
      const current = latLngToPixelRef.current(latlng);
      const angleDeg = Math.atan2(current.y - rotate.startState.center.y, current.x - rotate.startState.center.x) * 180 / Math.PI - rotate.edgeOffsetDeg;
      return rotatedPayloadFromState({ ...rotate.startState, angleDeg });
    };

    const clearDragState = () => {
      draggingAnnotationRef.current = null;
      resizingAnnotationRef.current = null;
      movingRotatedAnnotationRef.current = null;
      resizingRotatedAnnotationRef.current = null;
      rotatingRotatedAnnotationRef.current = null;
      if (dragPreviewRef.current) {
        map.removeLayer(dragPreviewRef.current);
        dragPreviewRef.current = null;
      }
      if (enableMapDraggingRef.current) map.dragging.enable();
    };

    const clearMeasure = () => {
      measureStateRef.current = null;
      if (measureLineRef.current) {
        map.removeLayer(measureLineRef.current);
        measureLineRef.current = null;
      }
      if (measureLabelRef.current) {
        map.removeLayer(measureLabelRef.current);
        measureLabelRef.current = null;
      }
    };

    const clearMultiSelectPreview = () => {
      multiSelectStateRef.current = null;
      if (multiSelectRectRef.current) {
        map.removeLayer(multiSelectRectRef.current);
        multiSelectRectRef.current = null;
      }
    };

    const normalizedPixelRect = (start: L.LatLng, end: L.LatLng): BBox => {
      const p1 = latLngToPixelRef.current(start);
      const p2 = latLngToPixelRef.current(end);
      return [
        Math.min(p1.x, p2.x),
        Math.min(p1.y, p2.y),
        Math.max(p1.x, p2.x),
        Math.max(p1.y, p2.y),
      ];
    };

    const updateMultiSelectPreview = (start: L.LatLng, end: L.LatLng) => {
      const [x0, y0, x1, y1] = normalizedPixelRect(start, end);
      if (multiSelectRectRef.current) {
        updatePixelRectLayer(multiSelectRectRef.current, x0, y0, x1, y1, pixelToLatLng, geoMode);
      } else {
        multiSelectRectRef.current = createPixelRectLayer(x0, y0, x1, y1, pixelToLatLng, geoMode, {
          color: "#38bdf8",
          weight: 2,
          dashArray: "6, 4",
          fillColor: "#38bdf8",
          fillOpacity: 0.12,
          interactive: false,
        });
        multiSelectRectRef.current.addTo(map);
      }
    };

    const annotationIdsInRect = (rect: BBox): string[] => {
      return annotationSpatialIndexRef.current.searchIds(rect);
    };

    const predictionIdsInRect = (rect: BBox): string[] => {
      const [x0, y0, x1, y1] = rect;
      return predictionsRef.current
        .filter((prediction) => {
          if (prediction.status !== "pending" || prediction.bbox.length !== 4) return false;
          const [px0, py0, px1, py1] = prediction.bbox;
          return px0 < x1 && px1 > x0 && py0 < y1 && py1 > y0;
        })
        .map((prediction) => prediction.id);
    };

    const formatMeasureDistance = (start: L.LatLng, end: L.LatLng): string => {
      if (geoMode) {
        return formatDistanceMeters(start.distanceTo(end));
      }
      const p1 = latLngToPixelRef.current(start);
      const p2 = latLngToPixelRef.current(end);
      const distance = Math.hypot(p2.x - p1.x, p2.y - p1.y);
      if (distance >= 100) return `${distance.toFixed(0)} px`;
      if (distance >= 10) return `${distance.toFixed(1)} px`;
      return `${distance.toFixed(2)} px`;
    };

    const updateMeasurePreview = (start: L.LatLng, end: L.LatLng) => {
      const startPoint = map.latLngToContainerPoint(start);
      const endPoint = map.latLngToContainerPoint(end);
      const angleDeg = Math.atan2(endPoint.y - startPoint.y, endPoint.x - startPoint.x) * 180 / Math.PI;
      const midpoint = L.latLng(
        (start.lat + end.lat) / 2,
        (start.lng + end.lng) / 2
      );
      const labelIcon = L.divIcon({
        className: "bbox-measure-leaflet-icon",
        html: createSideMeasureLabelHtml(formatMeasureDistance(start, end), "#facc15", angleDeg),
        iconSize: [0, 0],
        iconAnchor: [0, 0],
      });

      if (measureLineRef.current) {
        measureLineRef.current.setLatLngs([start, end]);
      } else {
        measureLineRef.current = L.polyline([start, end], {
          color: "#facc15",
          weight: 2,
          dashArray: "6, 4",
          interactive: false,
        }).addTo(map);
      }

      if (measureLabelRef.current) {
        measureLabelRef.current.setLatLng(midpoint);
        measureLabelRef.current.setIcon(labelIcon);
      } else {
        measureLabelRef.current = L.marker(midpoint, {
          icon: labelIcon,
          interactive: false,
          zIndexOffset: 1400,
        }).addTo(map);
      }
    };

    const finishRightPan = (): boolean => {
      const rightPan = rightPanRef.current;
      if (!rightPan) return false;
      rightPanRef.current = null;
      if (enableMapDraggingRef.current) map.dragging.enable();
      if (rightPan.moved) {
        suppressContextMenuRef.current = true;
      }
      return true;
    };

    const onClick = (e: L.LeafletMouseEvent) => {
      if (suppressClickRef.current) {
        suppressClickRef.current = false;
        return;
      }

      if (activeMapToolRef.current === "measure") {
        const measureState = measureStateRef.current;
        if (!measureState) {
          clearMeasure();
          measureStateRef.current = { start: e.latlng };
          updateMeasurePreview(e.latlng, e.latlng);
          return;
        }

        updateMeasurePreview(measureState.start, e.latlng);
        measureStateRef.current = null;
        return;
      }

      if (activeMapToolRef.current === "sam_click") {
        const point = latLngToPixelRef.current(e.latlng);
        onSamClickRef.current?.(point.x, point.y);
        return;
      }

      const batchedHit = batchedAnnotationHitTestRef.current?.(e.containerPoint) ?? null;
      if (batchedHit) {
        if (activeMapToolRef.current === "class_paint" && activeClassIdRef.current !== null) {
          if (batchedHit.annotation.class_id !== activeClassIdRef.current) {
            onAnnotationClassChangeRef.current?.(batchedHit.id, activeClassIdRef.current);
          }
          onAnnotationSelectRef.current?.(batchedHit.id);
          return;
        }
        if (canEditAnnotationsRef.current) {
          onAnnotationSelectRef.current?.(batchedHit.id);
          return;
        }
      }

      if (activeMapToolRef.current !== "draw") {
        if (canEditAnnotationsRef.current) {
          onAnnotationSelectRef.current?.(null);
          onPredictionSelectRef.current?.(null);
        }
        return;
      }

      if (activeClassIdRef.current === null) return;

      if (annotationModeRef.current === "rotated_bbox") {
        const drawState = drawStateRef.current;
        if (!drawState || drawState.mode !== "rotated_bbox") {
          drawStateRef.current = { mode: "rotated_bbox", start: e.latlng };
          return;
        }

        if (!drawState.end) {
          clearDrawAxisPreview();
          drawStateRef.current = { ...drawState, end: e.latlng };
          return;
        }

        const geometry = rotatedGeometryFromMap(drawState.start, drawState.end, e.latlng);
        if (geometry) {
          onAnnotationCreateRef.current?.(geometry);
        }

        drawStateRef.current = null;
        if (drawPreviewRef.current) {
          map.removeLayer(drawPreviewRef.current);
          drawPreviewRef.current = null;
        }
        clearDrawAxisPreview();
        clearDrawMeasureLabel();
        return;
      }

      if (!drawStateRef.current || drawStateRef.current.mode !== "bbox") {
        // First click - start drawing
        drawStateRef.current = { mode: "bbox", start: e.latlng };
      } else {
        // Second click - finalize rectangle
        const p1 = latLngToPixelRef.current(drawStateRef.current.start);
        const p2 = latLngToPixelRef.current(e.latlng);

        const x0 = Math.round(Math.min(p1.x, p2.x));
        const y0 = Math.round(Math.min(p1.y, p2.y));
        const x1 = Math.round(Math.max(p1.x, p2.x));
        const y1 = Math.round(Math.max(p1.y, p2.y));

        if (x1 - x0 > 2 && y1 - y0 > 2) {
          onAnnotationCreateRef.current?.({
            geometry_type: "bbox",
            bbox: [x0, y0, x1, y1],
          });
        }

        drawStateRef.current = null;
        if (drawPreviewRef.current) {
          map.removeLayer(drawPreviewRef.current);
          drawPreviewRef.current = null;
        }
        clearDrawAxisPreview();
        clearDrawMeasureLabel();
      }
    };

    const onMouseDown = (e: L.LeafletMouseEvent) => {
      const mouseEvent = e.originalEvent as MouseEvent;
      if (
        activeMapToolRef.current === "multi_select" &&
        mouseEvent.button === 0 &&
        !draggingAnnotationRef.current &&
        !resizingAnnotationRef.current &&
        !movingRotatedAnnotationRef.current &&
        !resizingRotatedAnnotationRef.current &&
        !rotatingRotatedAnnotationRef.current
      ) {
        clearMultiSelectPreview();
        multiSelectStateRef.current = { start: e.latlng };
        updateMultiSelectPreview(e.latlng, e.latlng);
        map.dragging.disable();
        L.DomEvent.stopPropagation(mouseEvent);
        L.DomEvent.preventDefault(mouseEvent);
        return;
      }

      if (
        mouseEvent.button !== 2 ||
        draggingAnnotationRef.current ||
        resizingAnnotationRef.current ||
        movingRotatedAnnotationRef.current ||
        resizingRotatedAnnotationRef.current ||
        rotatingRotatedAnnotationRef.current
      ) return;
      rightPanRef.current = {
        x: mouseEvent.clientX,
        y: mouseEvent.clientY,
        moved: false,
      };
      map.dragging.disable();
      L.DomEvent.stopPropagation(mouseEvent);
      L.DomEvent.preventDefault(mouseEvent);
    };

    const onMouseMove = (e: L.LeafletMouseEvent) => {
      const rightPan = rightPanRef.current;
      if (rightPan) {
        const mouseEvent = e.originalEvent as MouseEvent;
        const dx = mouseEvent.clientX - rightPan.x;
        const dy = mouseEvent.clientY - rightPan.y;

        if (dx !== 0 || dy !== 0) {
          map.panBy(L.point(-dx, -dy), { animate: false });
          rightPan.x = mouseEvent.clientX;
          rightPan.y = mouseEvent.clientY;

          if (
            !rightPan.moved &&
            (Math.abs(dx) > RIGHT_PAN_DRAG_THRESHOLD || Math.abs(dy) > RIGHT_PAN_DRAG_THRESHOLD)
          ) {
            rightPan.moved = true;
            suppressContextMenuRef.current = true;
          }
        }

        L.DomEvent.stopPropagation(mouseEvent);
        L.DomEvent.preventDefault(mouseEvent);
        return;
      }

      if (activeMapToolRef.current === "multi_select" && multiSelectStateRef.current) {
        updateMultiSelectPreview(multiSelectStateRef.current.start, e.latlng);
        return;
      }

      if (activeMapToolRef.current === "measure" && measureStateRef.current) {
        updateMeasurePreview(measureStateRef.current.start, e.latlng);
        return;
      }

      if (rotatingRotatedAnnotationRef.current) {
        updateRotatedPreview(rotatedRotatedPayload(rotatingRotatedAnnotationRef.current, e.latlng));
        return;
      }

      if (resizingRotatedAnnotationRef.current) {
        updateRotatedPreview(resizedRotatedPayload(resizingRotatedAnnotationRef.current, e.latlng));
        return;
      }

      if (movingRotatedAnnotationRef.current) {
        const drag = movingRotatedAnnotationRef.current;
        if (copyModifierRef.current) {
          drag.copyMode = true;
        }
        const start = latLngToPixelRef.current(drag.startLatLng);
        const current = latLngToPixelRef.current(e.latlng);
        updateRotatedPreview(
          movedRotatedPayload(drag.startState, drag.startPolygon, current.x - start.x, current.y - start.y)
        );
        return;
      }

      if (resizingAnnotationRef.current) {
        const resize = resizingAnnotationRef.current;
        const start = latLngToPixelRef.current(resize.startLatLng);
        const current = latLngToPixelRef.current(e.latlng);
        const resized = clampResizedBbox(
          resize.startBbox,
          resize.handle,
          Math.round(current.x - start.x),
          Math.round(current.y - start.y)
        );
        updateDragPreview(resized);
        return;
      }

      if (draggingAnnotationRef.current) {
        const drag = draggingAnnotationRef.current;
        if (copyModifierRef.current) {
          drag.copyMode = true;
        }
        const start = latLngToPixelRef.current(drag.startLatLng);
        const current = latLngToPixelRef.current(e.latlng);
        const moved = clampDraggedBbox(
          drag.startBbox,
          Math.round(current.x - start.x),
          Math.round(current.y - start.y)
        );
        updateDragPreview(moved);
        return;
      }

      if (!drawStateRef.current || activeMapToolRef.current !== "draw") return;

      if (drawStateRef.current.mode === "rotated_bbox") {
        const start = latLngToPixelRef.current(drawStateRef.current.start);
        const current = latLngToPixelRef.current(e.latlng);
        if (!drawStateRef.current.end) {
          updateDrawAxisPreview(start, current);
          return;
        }

        const geometry = rotatedGeometryFromMap(
          drawStateRef.current.start,
          drawStateRef.current.end,
          e.latlng
        );
        if (geometry) updateDrawRotatedPreview(geometry);
        return;
      }

      const p1 = latLngToPixelRef.current(drawStateRef.current.start);
      const p2 = latLngToPixelRef.current(e.latlng);
      const x0 = Math.min(p1.x, p2.x);
      const y0 = Math.min(p1.y, p2.y);
      const x1 = Math.max(p1.x, p2.x);
      const y1 = Math.max(p1.y, p2.y);

      if (drawPreviewRef.current) {
        updatePixelRectLayer(drawPreviewRef.current, x0, y0, x1, y1, pixelToLatLng, geoMode);
      } else {
        createDrawPreview(x0, y0, x1, y1);
      }
      updateDrawMeasureLabel(x0, y0, x1, y1);
    };

    const onMouseUp = (e: L.LeafletMouseEvent) => {
      if (finishRightPan()) {
        return;
      }

      if (multiSelectStateRef.current) {
        const selectionRect = normalizedPixelRect(multiSelectStateRef.current.start, e.latlng);
        const selectedAnnotationIds = annotationIdsInRect(selectionRect);
        const selectedPredictionIds = predictionIdsInRect(selectionRect);
        clearMultiSelectPreview();
        if (enableMapDraggingRef.current) map.dragging.enable();
        onAnnotationMultiSelectRef.current?.(selectedAnnotationIds);
        onPredictionMultiSelectRef.current?.(selectedPredictionIds);
        suppressClickRef.current = true;
        return;
      }

      if (rotatingRotatedAnnotationRef.current) {
        const rotate = rotatingRotatedAnnotationRef.current;
        onAnnotationUpdateRef.current?.(rotate.annId, rotatedRotatedPayload(rotate, e.latlng));
        clearDragState();
        suppressClickRef.current = true;
        return;
      }

      if (resizingRotatedAnnotationRef.current) {
        const resize = resizingRotatedAnnotationRef.current;
        onAnnotationUpdateRef.current?.(resize.annId, resizedRotatedPayload(resize, e.latlng));
        clearDragState();
        suppressClickRef.current = true;
        return;
      }

      if (movingRotatedAnnotationRef.current) {
        const drag = movingRotatedAnnotationRef.current;
        const start = latLngToPixelRef.current(drag.startLatLng);
        const end = latLngToPixelRef.current(e.latlng);
        const geometry = movedRotatedPayload(
          drag.startState, drag.startPolygon, end.x - start.x, end.y - start.y
        );
        if (drag.copyMode) {
          onAnnotationCreateRef.current?.(geometry, drag.classId);
        } else {
          onAnnotationUpdateRef.current?.(drag.annId, geometry);
        }
        clearDragState();
        suppressClickRef.current = true;
        return;
      }

      if (resizingAnnotationRef.current) {
        const resize = resizingAnnotationRef.current;
        const start = latLngToPixelRef.current(resize.startLatLng);
        const end = latLngToPixelRef.current(e.latlng);
        const resized = clampResizedBbox(
          resize.startBbox,
          resize.handle,
          Math.round(end.x - start.x),
          Math.round(end.y - start.y)
        ).map((value) => Math.round(value)) as [number, number, number, number];
        onAnnotationUpdateRef.current?.(resize.annId, {
          geometry_type: "bbox",
          bbox: resized,
        });
        clearDragState();
        suppressClickRef.current = true;
        return;
      }

      if (!draggingAnnotationRef.current) return;
      const drag = draggingAnnotationRef.current;
      const start = latLngToPixelRef.current(drag.startLatLng);
      const end = latLngToPixelRef.current(e.latlng);
      const moved = clampDraggedBbox(
        drag.startBbox,
        Math.round(end.x - start.x),
        Math.round(end.y - start.y)
      ).map((value) => Math.round(value)) as [number, number, number, number];

      const geometry: AnnotationGeometryPayload = {
        geometry_type: "bbox",
        bbox: moved,
      };

      if (drag.copyMode) {
        onAnnotationCreateRef.current?.(geometry, drag.classId);
      } else {
        onAnnotationUpdateRef.current?.(drag.annId, geometry);
      }
      clearDragState();
      suppressClickRef.current = true;
    };

    const onWindowMouseUp = () => {
      tilePaintActiveRef.current = false;
      // One request for the whole stroke. The tiles are already painted locally, so
      // the round-trip only reconciles state instead of gating the next tile.
      if (paintedTileIndicesRef.current.size > 0) {
        const painted = [...paintedTileIndicesRef.current];
        paintedTileIndicesRef.current.clear();
        paintedStyleRef.current.clear();
        suppressTileClickRef.current = true;
        onTilePaintCommitRef.current?.(painted);
      }
      paintedTileIndicesRef.current.clear();
      paintedStyleRef.current.clear();
      if (finishRightPan()) return;
      if (
        draggingAnnotationRef.current ||
        resizingAnnotationRef.current ||
        movingRotatedAnnotationRef.current ||
        resizingRotatedAnnotationRef.current ||
        rotatingRotatedAnnotationRef.current ||
        multiSelectStateRef.current
      ) {
        clearMultiSelectPreview();
        clearDragState();
      }
    };

    const onContextMenu = (e: L.LeafletMouseEvent) => {
      L.DomEvent.preventDefault(e.originalEvent);
      if (suppressContextMenuRef.current) {
        suppressContextMenuRef.current = false;
        return;
      }
      if (
        draggingAnnotationRef.current ||
        resizingAnnotationRef.current ||
        movingRotatedAnnotationRef.current ||
        resizingRotatedAnnotationRef.current ||
        rotatingRotatedAnnotationRef.current
      ) {
        clearDragState();
        return;
      }
      if (drawStateRef.current) {
        drawStateRef.current = null;
        if (drawPreviewRef.current) {
          map.removeLayer(drawPreviewRef.current);
          drawPreviewRef.current = null;
        }
        clearDrawAxisPreview();
        clearDrawMeasureLabel();
        return;
      }
      if (measureLineRef.current || measureLabelRef.current || measureStateRef.current) {
        clearMeasure();
        return;
      }
      if (multiSelectRectRef.current || multiSelectStateRef.current) {
        clearMultiSelectPreview();
        return;
      }
      // Nothing to cancel: on a GEO scene offer the coordinate context menu at the click.
      if (geoMode) {
        const point = e.containerPoint;
        setCoordMenu({ x: point.x, y: point.y, lat: e.latlng.lat, lon: e.latlng.lng });
      }
    };

    // Live cursor coordinate read-out (GEO only). Writes to a DOM node directly and is
    // throttled, so it never triggers a React re-render of this component.
    const onCoordMove = (e: L.LeafletMouseEvent) => {
      if (!geoMode) return;
      const el = cursorHudRef.current;
      if (!el) return;
      const now = performance.now();
      if (now - coordHudThrottleRef.current < 60) return;
      coordHudThrottleRef.current = now;
      const { lat, lng } = e.latlng;
      const mgrs = latLonToMgrs(lat, lng, { digits: 5, spaced: true });
      el.textContent = mgrs ? `${mgrs}  ·  ${formatLatLon(lat, lng, 5)}` : formatLatLon(lat, lng, 5);
    };
    const onCoordOut = () => {
      if (cursorHudRef.current) cursorHudRef.current.textContent = "-";
    };

    map.on("mousedown", onMouseDown);
    map.on("click", onClick);
    map.on("mousemove", onMouseMove);
    map.on("mousemove", onCoordMove);
    map.on("mouseout", onCoordOut);
    map.on("mouseup", onMouseUp);
    map.on("contextmenu", onContextMenu);
    map.on("zoomend", refreshDrawMeasureLabels);
    map.on("moveend", refreshDrawMeasureLabels);
    window.addEventListener("mouseup", onWindowMouseUp);

    return () => {
      map.off("mousedown", onMouseDown);
      map.off("click", onClick);
      map.off("mousemove", onMouseMove);
      map.off("mousemove", onCoordMove);
      map.off("mouseout", onCoordOut);
      map.off("mouseup", onMouseUp);
      map.off("contextmenu", onContextMenu);
      map.off("zoomend", refreshDrawMeasureLabels);
      map.off("moveend", refreshDrawMeasureLabels);
      window.removeEventListener("mouseup", onWindowMouseUp);
      rightPanRef.current = null;
      suppressContextMenuRef.current = false;
      clearDrawMeasureLabel();
      clearDrawAxisPreview();
      clearMeasure();
      clearMultiSelectPreview();
      clearDragState();
    };
  }, [projectId, geoMode, geoTransform, pixelToLatLng, sceneHeight, sceneWidth]); // re-binds only when map re-inits

  // â”€â”€â”€ Focus selected bbox â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusBBox) return;

    const [x0, y0, x1, y1] = focusBBox;
    const sw = pixelToLatLng(x0, y1);
    const ne = pixelToLatLng(x1, y0);
    const bounds = L.latLngBounds(sw, ne);

    map.fitBounds(bounds, {
      padding: [48, 48],
      maxZoom: geoMode ? Math.min(20, map.getMaxZoom()) : maxZoom + PIXEL_OVERZOOM,
      animate: true,
    });
  }, [focusBBox, focusBBoxToken, pixelToLatLng, geoMode, maxZoom]);

  // â”€â”€â”€ Cursor â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if ((activeMapTool === "draw" && activeClassId !== null) || activeMapTool === "measure") {
      container.style.cursor = "crosshair";
    } else if (activeMapTool === "multi_select" || activeMapTool === "sam_click") {
      container.style.cursor = "crosshair";
    } else if (activeMapTool === "class_paint" && activeClassId !== null) {
      container.style.cursor = "copy";
    } else if (["grid_review", "grid_clear", "grid_exclude"].includes(activeMapTool)) {
      container.style.cursor = "cell";
    } else {
      container.style.cursor = "";
    }
  }, [activeMapTool, activeClassId]);

  useEffect(() => {
    if (["grid_review", "grid_clear", "grid_exclude"].includes(activeMapTool)) return;
    tilePaintActiveRef.current = false;
    paintedTileIndicesRef.current.clear();
  }, [activeMapTool]);

  useEffect(() => {
    if (activeMapTool === "measure") return;
    clearMeasureOverlay();
  }, [activeMapTool, clearMeasureOverlay]);

  useEffect(() => {
    if (activeMapTool === "multi_select") return;
    const map = mapRef.current;
    multiSelectStateRef.current = null;
    if (map && multiSelectRectRef.current) {
      map.removeLayer(multiSelectRectRef.current);
      multiSelectRectRef.current = null;
      if (enableMapDraggingRef.current) map.dragging.enable();
    }
  }, [activeMapTool]);

  useEffect(() => {
    if (activeMapTool === "draw" && activeClassId !== null) return;
    const map = mapRef.current;
    if (!map) return;

    drawStateRef.current = null;
    if (drawPreviewRef.current) {
      map.removeLayer(drawPreviewRef.current);
      drawPreviewRef.current = null;
    }
    if (drawAxisPreviewRef.current) {
      map.removeLayer(drawAxisPreviewRef.current);
      drawAxisPreviewRef.current = null;
    }
    drawMeasureBboxRef.current = null;
    if (drawMeasureLabelsRef.current) {
      map.removeLayer(drawMeasureLabelsRef.current.width);
      map.removeLayer(drawMeasureLabelsRef.current.height);
      drawMeasureLabelsRef.current = null;
    }
  }, [activeMapTool, activeClassId, annotationMode]);

  useEffect(() => {
    if (activeMapTool === "select") return;
    const map = mapRef.current;
    if (!map) return;

    draggingAnnotationRef.current = null;
    resizingAnnotationRef.current = null;
    movingRotatedAnnotationRef.current = null;
    resizingRotatedAnnotationRef.current = null;
    rotatingRotatedAnnotationRef.current = null;
    if (enableMapDraggingRef.current) map.dragging.enable();
    if (dragPreviewRef.current) {
      map.removeLayer(dragPreviewRef.current);
      dragPreviewRef.current = null;
    }
  }, [activeMapTool]);

  // â”€â”€â”€ Expose getVisibleTileIndices to parent via ref â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const getVisibleTileIndices = useCallback((): number[] => {
    const map = mapRef.current;
    if (!map || !tiles || tiles.length === 0) return [];

    const bounds = map.getBounds();
    const indices: number[] = [];

    for (let i = 0; i < tiles.length; i++) {
      const t = tiles[i];
      const tileBounds = pixelRectBounds(t.x0, t.y0, t.x0 + tileSize, t.y0 + tileSize, pixelToLatLng);
      if (bounds.intersects(tileBounds)) {
        indices.push(i);
      }
    }
    return indices;
  }, [tiles, tileSize, pixelToLatLng]);

  const getVisibleSceneBounds = useCallback((): BBox | null => {
    const map = mapRef.current;
    if (!map) return null;
    return sceneBoundsForMap(map, latLngToPixel, sceneWidth, sceneHeight);
  }, [latLngToPixel, sceneHeight, sceneWidth]);

  // Keep parent ref in sync
  useEffect(() => {
    if (getVisibleTilesRef) {
      getVisibleTilesRef.current = getVisibleTileIndices;
    }
  }, [getVisibleTilesRef, getVisibleTileIndices]);

  useEffect(() => {
    if (getVisibleSceneBoundsRef) {
      getVisibleSceneBoundsRef.current = getVisibleSceneBounds;
    }
  }, [getVisibleSceneBoundsRef, getVisibleSceneBounds]);

  // Zakres rozciągnięcia „Widok” (DESIGN_DECISIONS.md, display-stretch D): rodzic dostaje widoczny
  // fragment sceny po każdym zakończonym ruchu mapy i od razu po włączeniu zakresu.
  // Opóźnienie i anulowanie zapytań są po stronie `useViewStretch`.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !onViewportSettled) return undefined;
    const notify = () => onViewportSettled(getVisibleSceneBounds());
    map.on("moveend", notify);
    notify();
    return () => {
      map.off("moveend", notify);
    };
  }, [onViewportSettled, getVisibleSceneBounds]);

  const copyCoordinate = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast({ title: `${t("Copied")}: ${label}`, description: text, status: "success", duration: 1800 });
    } catch {
      toast({ title: t("Cannot copy to clipboard"), status: "error", duration: 3000 });
    }
    setCoordMenu(null);
  };

  const flyToCoordinate = (lat: number, lon: number) => {
    const map = mapRef.current;
    if (!map) return;
    map.flyTo([lat, lon], Math.max(map.getZoom(), 18), { duration: 0.6 });
    if (coordFlashRef.current) map.removeLayer(coordFlashRef.current);
    const marker = L.circleMarker([lat, lon], { radius: 11, color: "#4EBA65", weight: 3, opacity: 1, fillOpacity: 0 });
    marker.addTo(map);
    coordFlashRef.current = marker;
    window.setTimeout(() => {
      if (coordFlashRef.current) {
        map.removeLayer(coordFlashRef.current);
        coordFlashRef.current = null;
      }
    }, 2500);
  };

  const handleGoto = () => {
    const parsed = parseCoordinateInput(gotoValue);
    if (!parsed) {
      setGotoError(t("Invalid coordinates. Use MGRS or decimal lat, lon."));
      setGotoOutside(null);
      return;
    }
    // Soft guard: point outside the scene bounds is likely a mistake — warn and require a
    // second confirmation rather than silently jumping far away (geoBounds is [W,S,E,N]).
    if (geoBounds) {
      const [west, south, east, north] = geoBounds;
      const outside = parsed.lat < south || parsed.lat > north || parsed.lon < west || parsed.lon > east;
      if (outside) {
        setGotoError(t("Coordinates are outside this scene."));
        setGotoOutside(parsed);
        return;
      }
    }
    setGotoError(null);
    setGotoOutside(null);
    flyToCoordinate(parsed.lat, parsed.lon);
  };

  // Na ekranie MGRS jest ze spacjami, bo tak się go czyta. Do SCHOWKA idzie bez spacji:
  // skopiowany identyfikator ma być jednym ciągiem, który da się wkleić w pole wyszukiwania
  // albo w nazwę pliku bez poprawiania go ręcznie.
  const menuMgrs = coordMenu ? latLonToMgrs(coordMenu.lat, coordMenu.lon, { digits: 5, spaced: true }) : "";
  const menuMgrsCompact = coordMenu
    ? latLonToMgrs(coordMenu.lat, coordMenu.lon, { digits: 5, spaced: false })
    : "";
  const menuLatLon = coordMenu ? formatLatLon(coordMenu.lat, coordMenu.lon, 6) : "";
  const menuCombined = coordMenu
    ? `${menuMgrsCompact || t("MGRS unavailable")} | ${menuLatLon}${sceneName ? ` | ${sceneName}` : ""}`
    : "";

  return (
    <Box position="relative" w="100%" h="100%" overflow="hidden">
    <Box
      ref={containerRef}
      data-testid="scene-map-canvas"
      w="100%"
      h="100%"
      position="relative"
      overflow="hidden"
      sx={{
        "&.leaflet-container": {
          width: "100%",
          height: "100%",
          background: "#131316",
        },
        ".annotation-label": {
          background: "transparent !important",
          border: "none !important",
          boxShadow: "none !important",
          color: "white",
          fontWeight: "bold",
          fontSize: "11px",
          textShadow: "1px 1px 2px rgba(0,0,0,0.8)",
          padding: "0 !important",
          pointerEvents: "none",
        },
        ".bbox-measure-leaflet-icon": {
          width: "auto !important",
          height: "auto !important",
        },
        ".bbox-side-measure-label": {
          transform: "translate(-50%, -50%) rotate(var(--bbox-measure-angle))",
          transformOrigin: "center center",
          fontSize: "12px",
          fontWeight: 800,
          lineHeight: 1.2,
          whiteSpace: "nowrap",
          pointerEvents: "none",
          textShadow: [
            "0 1px 2px rgba(0,0,0,0.95)",
            "1px 0 2px rgba(0,0,0,0.95)",
            "0 -1px 2px rgba(0,0,0,0.95)",
            "-1px 0 2px rgba(0,0,0,0.95)",
          ].join(", "),
          userSelect: "none",
        },
      }}
    />

      {geoMode && (
        <HStack
          position="absolute"
          bottom="10px"
          left="10px"
          zIndex={1000}
          spacing={2}
          align="center"
          bg="rgba(19,19,22,0.85)"
          borderRadius="md"
          px={2.5}
          py={1.5}
          border="1px solid rgba(255,255,255,0.12)"
          boxShadow="0 2px 8px rgba(0,0,0,0.4)"
          onContextMenu={(e) => e.stopPropagation()}
        >
          <Text
            as="span"
            fontSize="xs"
            fontFamily="mono"
            color="whiteAlpha.900"
            minW="230px"
            title={t("Cursor coordinates (MGRS · lat, lon)")}
          >
            <span ref={cursorHudRef}>-</span>
          </Text>
          <Input
            size="xs"
            width="200px"
            variant="filled"
            placeholder={t("Go to MGRS or lat, lon")}
            value={gotoValue}
            onChange={(e) => {
              setGotoValue(e.target.value);
              if (gotoError) setGotoError(null);
              if (gotoOutside) setGotoOutside(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleGoto();
            }}
            isInvalid={!!gotoError}
          />
          <Button size="xs" colorScheme="brand" onClick={handleGoto}>
            {t("Go")}
          </Button>
          {gotoOutside && (
            <Button
              size="xs"
              variant="outline"
              colorScheme="orange"
              onClick={() => {
                const target = gotoOutside;
                setGotoOutside(null);
                setGotoError(null);
                flyToCoordinate(target.lat, target.lon);
              }}
            >
              {t("Show anyway")}
            </Button>
          )}
          {gotoError && (
            <Text as="span" fontSize="xs" color="orange.300" maxW="220px">
              {gotoError}
            </Text>
          )}
        </HStack>
      )}

      {geoMode && coordMenu && (
        <>
          <Box
            position="absolute"
            inset={0}
            zIndex={1500}
            onClick={() => setCoordMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setCoordMenu(null);
            }}
          />
          <Box
            position="absolute"
            left={`${coordMenu.x}px`}
            top={`${coordMenu.y}px`}
            zIndex={1600}
            bg="rgba(24,24,28,0.98)"
            border="1px solid rgba(255,255,255,0.14)"
            borderRadius="md"
            boxShadow="0 6px 20px rgba(0,0,0,0.5)"
            py={1}
            minW="220px"
            overflow="hidden"
          >
            <Text px={3} py={1} fontSize="xx-small" color="whiteAlpha.600" fontFamily="mono">
              {menuMgrs || t("MGRS unavailable")}
            </Text>
            {[
              { label: t("Copy MGRS"), value: menuMgrsCompact, enabled: !!menuMgrsCompact },
              { label: t("Copy lat, lon"), value: menuLatLon, enabled: true },
              { label: t("Copy coordinates + ID"), value: menuCombined, enabled: true },
            ].map((item) => (
              <Box
                key={item.label}
                as="button"
                display="block"
                width="100%"
                textAlign="left"
                px={3}
                py={1.5}
                fontSize="sm"
                color={item.enabled ? "whiteAlpha.900" : "whiteAlpha.400"}
                cursor={item.enabled ? "pointer" : "not-allowed"}
                _hover={item.enabled ? { bg: "whiteAlpha.200" } : undefined}
                onClick={() => item.enabled && copyCoordinate(item.value, item.label)}
              >
                {item.label}
              </Box>
            ))}
          </Box>
        </>
      )}
    </Box>
  );
}
