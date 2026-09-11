import type { Annotation } from "../../types";
import type { IndexedAnnotation } from "./annotationSpatialIndex";

export interface AnnotationRenderPlan {
  add: IndexedAnnotation[];
  /** Ta sama adnotacja, inna geometria lub klasa — warstwę trzeba zbudować od nowa. */
  changed: IndexedAnnotation[];
  keepIds: string[];
  removeIds: string[];
}

/** Wpis cache'u warstwy, na tyle, ile potrzebuje planer. */
export interface RenderedAnnotationEntry {
  geometryKey: string;
}

/**
 * Odcisk tego, co wpływa na wygląd warstwy i na stan cache'owany do edycji.
 *
 * Musi obejmować `polygon_scene_px`, a nie sam `rotated_bbox`: na scenach, gdzie piksel↔mapa
 * nie jest konforemne, wierna mapie ramka jest w pikselach równoległobokiem, którego
 * `rotated_bbox` nie potrafi opisać. Zmiana samego poligonu przy niezmienionym prostokącie
 * opisującym jest więc realną zmianą kształtu i nie może umknąć.
 *
 * `class_id` jest tu, bo kolor warstwy pochodzi z klasy — zmiana klasy bez przebudowy
 * zostawiłaby ramkę w starym kolorze.
 */
export function annotationGeometryKey(annotation: Annotation): string {
  const parts: (string | number)[] = [
    annotation.geometry_type ?? "bbox",
    annotation.class_id,
    annotation.is_negative ? 1 : 0,
  ];
  for (const value of annotation.bbox) parts.push(value);
  const polygon = annotation.polygon_scene_px;
  if (polygon) {
    parts.push("p");
    for (const point of polygon) parts.push(point[0], point[1]);
  }
  const rotated = annotation.rotated_bbox;
  if (rotated) {
    parts.push("r", rotated.cx, rotated.cy, rotated.width, rotated.height, rotated.angle_deg);
  }
  return parts.join(",");
}

/**
 * Minimalna różnica dla warstwy adnotacji.
 *
 * Obiekty Leaflet są świadomie zachowywane dla adnotacji, które zostają w buforowanym
 * viewporcie — dzięki temu panoramowanie nie przebudowuje warstwy. Zachowanie ich po
 * EDYCJI byłoby jednak błędem: warstwa kształtu, strzałka orientacji i zapamiętany poligon
 * startowy uchwytów pochodzą z domknięcia zrobionego przy dodaniu, więc po zmianie
 * geometrii pokazywałyby i liczyłyby stan sprzed edycji. Dlatego zmiana odcisku geometrii
 * przenosi adnotację do `changed`, obsługiwanego jak usunięcie i ponowne dodanie.
 */
export function planAnnotationRenderLayer(
  rendered: ReadonlyMap<string, RenderedAnnotationEntry>,
  visible: readonly IndexedAnnotation[],
): AnnotationRenderPlan {
  const visibleIds = new Set(visible.map((item) => item.id));
  const add: IndexedAnnotation[] = [];
  const changed: IndexedAnnotation[] = [];
  const keepIds: string[] = [];

  for (const item of visible) {
    const entry = rendered.get(item.id);
    if (!entry) {
      add.push(item);
      continue;
    }
    if (entry.geometryKey !== annotationGeometryKey(item.annotation)) changed.push(item);
    else keepIds.push(item.id);
  }

  const removeIds: string[] = [];
  for (const id of rendered.keys()) {
    if (!visibleIds.has(id)) removeIds.push(id);
  }
  return { add, changed, keepIds, removeIds };
}
