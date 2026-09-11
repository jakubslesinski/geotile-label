import { useRef } from "react";
import { Box, HStack, Text, useColorModeValue } from "@chakra-ui/react";
import type { SceneHistogram } from "../../types";

/**
 * Co rysuje wykres. Uchwyty i etykieta DN pozostają w każdym trybie oparte na krzywej
 * ZLANEJ, bo to z niej renderer bierze progi — tryb zmienia obraz, nie liczby.
 */
export type HistogramView = "pooled" | "luminance" | "bands";

interface Props {
  histogram: SceneHistogram | null;
  low: number; // stretch_low percentile (0–100)
  high: number; // stretch_high percentile (0–100)
  logScale: boolean;
  view?: HistogramView;
  onChange: (low: number, high: number) => void; // during drag (preview)
  onChangeEnd: (low: number, high: number) => void; // on release (commit)
}

/** Kolory krzywych pasm — kolejność RGB, jak w QGIS. */
const BAND_COLORS = ["#F56565", "#48BB78", "#4299E1"];

const MIN_GAP = 1; // keep the two handles at least 1 percentile apart

// Piecewise-linear percentile<->value mapping from the returned percentile points.
function percentilePoints(histogram: SceneHistogram): Array<[number, number]> {
  const points = Object.entries(histogram.percentiles)
    .map(([key, value]) => [Number(key.replace("p", "")), value] as [number, number])
    .filter(([p, v]) => Number.isFinite(p) && Number.isFinite(v))
    .sort((a, b) => a[0] - b[0]);
  return points.length >= 2 ? points : [[0, histogram.min], [100, histogram.max]];
}

function interpolate(points: Array<[number, number]>, x: number, xIndex: 0 | 1): number {
  const yIndex = xIndex === 0 ? 1 : 0;
  if (x <= points[0][xIndex]) return points[0][yIndex];
  if (x >= points[points.length - 1][xIndex]) return points[points.length - 1][yIndex];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    if (x >= a[xIndex] && x <= b[xIndex]) {
      const span = b[xIndex] - a[xIndex];
      const t = span === 0 ? 0 : (x - a[xIndex]) / span;
      return a[yIndex] + t * (b[yIndex] - a[yIndex]);
    }
  }
  return points[points.length - 1][yIndex];
}

// mean ± k·σ (in value space) mapped to stretch percentiles for the σ presets.
export function sigmaStretch(histogram: SceneHistogram, k: number): { low: number; high: number } {
  const points = percentilePoints(histogram);
  const clamp = (p: number) => Math.round(Math.max(0, Math.min(100, p)) * 10) / 10;
  return {
    low: clamp(interpolate(points, histogram.mean - k * histogram.std, 1)),
    high: clamp(interpolate(points, histogram.mean + k * histogram.std, 1)),
  };
}

export default function HistogramStretch({
  histogram, low, high, logScale, view = "pooled", onChange, onChangeEnd,
}: Props) {
  const plotRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<"low" | "high" | null>(null);

  // Single-hue (brand) for the distribution; muted gray for out-of-range bars —
  // dark mode gets its own steps, not an auto-flip.
  const surface = useColorModeValue("gray.50", "whiteAlpha.100");
  const border = useColorModeValue("gray.200", "whiteAlpha.200");
  const inRange = useColorModeValue("#7551FF", "#B7A5FF");
  const outRange = useColorModeValue("#CBD5E0", "rgba(255,255,255,0.16)");
  const selectedFill = useColorModeValue("rgba(117,81,255,0.10)", "rgba(183,165,255,0.14)");
  const handleColor = useColorModeValue("#5B3DF5", "#CDBEFF");
  const readout = useColorModeValue("secondaryGray.700", "whiteAlpha.700");

  const empty = !histogram || histogram.sample_count === 0 || histogram.bin_high <= histogram.bin_low;
  const span = empty ? 1 : histogram!.bin_high - histogram!.bin_low;
  const points = empty ? [] : percentilePoints(histogram!);

  const pctToValue = (p: number) => (empty ? 0 : interpolate(points, p, 0));
  const valueToPct = (v: number) => (empty ? 0 : interpolate(points, v, 1));
  const valueToFrac = (v: number) => (empty ? 0 : Math.min(1, Math.max(0, (v - histogram!.bin_low) / span)));

  const lowValue = pctToValue(low);
  const highValue = pctToValue(high);
  const lowFrac = valueToFrac(lowValue);
  const highFrac = valueToFrac(highValue);

  // Wszystkie krzywe są binowane w tym samym zakresie `bin_low..bin_high`, więc leżą na
  // wspólnej osi i wolno je zestawiać na jednym wykresie.
  const bandCurves = view === "bands" ? histogram?.bands ?? [] : [];
  const drawBands = bandCurves.length > 1;
  const singleCounts = (() => {
    if (empty) return [] as number[];
    if (view === "luminance" && histogram!.luminance) return histogram!.luminance.bins;
    return histogram!.bins;
  })();

  const bars = (() => {
    if (empty || drawBands) return [] as Array<{ x: number; w: number; h: number; inside: boolean }>;
    const counts = singleCounts;
    const scaled = counts.map((c) => (logScale ? Math.log1p(c) : c));
    const maxValue = Math.max(1, ...scaled);
    const n = counts.length;
    return counts.map((_, i) => {
      const center = histogram!.bin_low + ((i + 0.5) / n) * span;
      return {
        x: (i / n) * 1000,
        w: 1000 / n,
        h: (scaled[i] / maxValue) * 100,
        inside: center >= lowValue && center <= highValue,
      };
    });
  })();

  // Wspólna normalizacja wszystkich pasm: inaczej pasmo o mniejszej liczności udawałoby
  // równie liczne i porównanie rozkładów traciłoby sens.
  const bandPaths = (() => {
    if (!drawBands) return [] as Array<{ d: string; color: string }>;
    const scaledSeries = bandCurves.map((curve) =>
      curve.bins.map((c) => (logScale ? Math.log1p(c) : c)),
    );
    const maxValue = Math.max(1, ...scaledSeries.flat());
    return scaledSeries.map((scaled, bandIndex) => {
      const n = scaled.length;
      const points = scaled.map((value, i) => {
        const x = ((i + 0.5) / n) * 1000;
        const y = 100 - (value / maxValue) * 100;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      });
      return {
        d: `M0,100 L${points.join(" L")} L1000,100`,
        color: BAND_COLORS[bandIndex % BAND_COLORS.length],
      };
    });
  })();

  const fracFromClientX = (clientX: number): number => {
    const rect = plotRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  };

  const updateFromPointer = (clientX: number, commit: boolean) => {
    if (empty || !activeRef.current) return;
    const value = histogram!.bin_low + fracFromClientX(clientX) * span;
    const pct = Math.min(100, Math.max(0, valueToPct(value)));
    let nextLow = low;
    let nextHigh = high;
    if (activeRef.current === "low") nextLow = Math.min(pct, high - MIN_GAP);
    else nextHigh = Math.max(pct, low + MIN_GAP);
    nextLow = Math.round(nextLow * 10) / 10;
    nextHigh = Math.round(nextHigh * 10) / 10;
    (commit ? onChangeEnd : onChange)(nextLow, nextHigh);
  };

  const handlePointerDown = (event: React.PointerEvent) => {
    if (empty) return;
    const frac = fracFromClientX(event.clientX);
    activeRef.current = Math.abs(frac - lowFrac) <= Math.abs(frac - highFrac) ? "low" : "high";
    (event.target as Element).setPointerCapture?.(event.pointerId);
    updateFromPointer(event.clientX, false);
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    if (activeRef.current) updateFromPointer(event.clientX, false);
  };

  const handlePointerUp = (event: React.PointerEvent) => {
    if (activeRef.current) {
      updateFromPointer(event.clientX, true);
      activeRef.current = null;
    }
  };

  return (
    <Box>
      <HStack justify="space-between" mb={1}>
        <Text fontSize="xs" color={readout}>
          {low.toFixed(low % 1 ? 1 : 0)}–{high.toFixed(high % 1 ? 1 : 0)}%
        </Text>
        {!empty && (
          <Text fontSize="xs" color={readout}>
            DN {Math.round(lowValue)}–{Math.round(highValue)}
          </Text>
        )}
      </HStack>
      <Box
        ref={plotRef}
        position="relative"
        h="100px"
        bg={surface}
        borderWidth="1px"
        borderColor={border}
        borderRadius="md"
        overflow="hidden"
        cursor={empty ? "default" : "ew-resize"}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        sx={{ touchAction: "none" }}
      >
        <svg
          width="100%"
          height="100%"
          viewBox="0 0 1000 100"
          preserveAspectRatio="none"
          style={{ display: "block", pointerEvents: "none" }}
        >
          {bars.map((bar, i) => (
            <rect
              key={i}
              x={bar.x}
              y={100 - bar.h}
              width={Math.max(0.5, bar.w - 0.4)}
              height={bar.h}
              fill={bar.inside ? inRange : outRange}
            />
          ))}
          {bandPaths.map((path, i) => (
            <path
              key={`band-${i}`}
              d={path.d}
              fill={path.color}
              fillOpacity={0.18}
              stroke={path.color}
              strokeWidth={1.2}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {!empty && (
          <>
            <Box
              position="absolute"
              top={0}
              bottom={0}
              left={`${lowFrac * 100}%`}
              width={`${Math.max(0, (highFrac - lowFrac) * 100)}%`}
              bg={selectedFill}
              pointerEvents="none"
            />
            {[lowFrac, highFrac].map((frac, i) => (
              <Box
                key={i}
                position="absolute"
                top={0}
                bottom={0}
                left={`${frac * 100}%`}
                width="2px"
                bg={handleColor}
                transform="translateX(-1px)"
                pointerEvents="none"
                _before={{
                  content: '""',
                  position: "absolute",
                  top: "50%",
                  left: "50%",
                  transform: "translate(-50%, -50%)",
                  width: "10px",
                  height: "26px",
                  borderRadius: "4px",
                  bg: handleColor,
                }}
              />
            ))}
          </>
        )}
      </Box>
    </Box>
  );
}
