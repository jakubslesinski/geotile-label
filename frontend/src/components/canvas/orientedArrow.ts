export interface ArrowPixelPoint {
  x: number;
  y: number;
}

export interface ArrowRotatedState {
  center: ArrowPixelPoint;
  width: number;
  height: number;
  angleDeg: number;
}

export interface OrientedArrowPoints {
  start: ArrowPixelPoint;
  end: ArrowPixelPoint;
  headA: ArrowPixelPoint;
  headB: ArrowPixelPoint;
}

function unitVector(angleDeg: number): ArrowPixelPoint {
  const angle = (angleDeg * Math.PI) / 180;
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

/**
 * Punkty strzałki kierunku przodu, w pikselach sceny.
 *
 * JEDNA konstrukcja dla propozycji AI i dla przyjętej adnotacji. Wcześniej propozycja
 * rysowała strzałkę z pikselowego `front_vector_scene_px`, a adnotacja z krawędzi
 * poligonu w układzie rzutowanym — na scenach niekonforemnych dawało to dwa różne kąty:
 * ramka wychodziła prostokątna, kreska kierunku przekoszona, a kierunek „przeskakiwał"
 * w momencie akceptacji.
 *
 * Przód to zawsze krawędź `p0 -> p1` poligonu, liczona w układzie RZUTOWANYM — tylko tam
 * bok ramki i kreska mają ten sam kąt. Bez poligonu (scena bez geo) liczymy pikselowo
 * z kąta prostokąta; tam piksel JEST mapą, więc wynik jest ten sam.
 *
 * Moduł celowo nie importuje Leafleta: rzutowanie wchodzi przez `toProjected`/
 * `fromProjected`, dzięki czemu geometria daje się testować bez `window`.
 */
export function orientedArrowPoints(
  state: ArrowRotatedState,
  polygon: ArrowPixelPoint[] | null,
  geoMode: boolean,
  toProjected: ((point: ArrowPixelPoint) => ArrowPixelPoint) | null,
  fromProjected: ((point: ArrowPixelPoint) => ArrowPixelPoint) | null,
): OrientedArrowPoints {
  if (geoMode && polygon && polygon.length >= 4 && toProjected && fromProjected) {
    const proj = polygon.slice(0, 4).map(toProjected);
    const center = proj.reduce(
      (acc, point) => ({ x: acc.x + point.x / 4, y: acc.y + point.y / 4 }),
      { x: 0, y: 0 },
    );
    const front = { x: proj[1].x - proj[0].x, y: proj[1].y - proj[0].y };
    const width = Math.hypot(front.x, front.y) || 1;
    const height = Math.hypot(proj[2].x - proj[1].x, proj[2].y - proj[1].y);
    const axis = { x: front.x / width, y: front.y / width };
    const normal = { x: -axis.y, y: axis.x };
    const arrowLength = width * 0.55;
    const headLength = arrowLength * 0.28;
    const headWidth = height * 0.35;
    const start = {
      x: center.x - (axis.x * arrowLength) / 2,
      y: center.y - (axis.y * arrowLength) / 2,
    };
    const end = {
      x: center.x + (axis.x * arrowLength) / 2,
      y: center.y + (axis.y * arrowLength) / 2,
    };
    return {
      start: fromProjected(start),
      end: fromProjected(end),
      headA: fromProjected({
        x: end.x - axis.x * headLength + (normal.x * headWidth) / 2,
        y: end.y - axis.y * headLength + (normal.y * headWidth) / 2,
      }),
      headB: fromProjected({
        x: end.x - axis.x * headLength - (normal.x * headWidth) / 2,
        y: end.y - axis.y * headLength - (normal.y * headWidth) / 2,
      }),
    };
  }

  const axis = unitVector(state.angleDeg);
  const normal = { x: -axis.y, y: axis.x };
  const arrowLength = Math.max(12, Math.min(state.width * 0.55, 80));
  const headLength = Math.max(6, Math.min(arrowLength * 0.28, 18));
  const headWidth = Math.max(5, Math.min(state.height * 0.35, 14));
  const start = {
    x: state.center.x - (axis.x * arrowLength) / 2,
    y: state.center.y - (axis.y * arrowLength) / 2,
  };
  const end = {
    x: state.center.x + (axis.x * arrowLength) / 2,
    y: state.center.y + (axis.y * arrowLength) / 2,
  };
  return {
    start,
    end,
    headA: {
      x: end.x - axis.x * headLength + (normal.x * headWidth) / 2,
      y: end.y - axis.y * headLength + (normal.y * headWidth) / 2,
    },
    headB: {
      x: end.x - axis.x * headLength - (normal.x * headWidth) / 2,
      y: end.y - axis.y * headLength - (normal.y * headWidth) / 2,
    },
  };
}
