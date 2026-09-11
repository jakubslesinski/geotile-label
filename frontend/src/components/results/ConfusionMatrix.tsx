import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, Flex, HStack, Text, useColorModeValue } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import type { ConfusionAnalysis } from "../../api/client";

interface Props {
  data: ConfusionAnalysis;
}

// Paleta magma (jak w macierzy podobieństwa) — spójna estetyka w całej aplikacji.
const MAGMA: [number, number[]][] = [
  [0.0, [0, 0, 4]],
  [0.15, [28, 16, 68]],
  [0.3, [79, 18, 123]],
  [0.45, [129, 37, 129]],
  [0.6, [181, 54, 122]],
  [0.75, [229, 80, 100]],
  [0.87, [251, 135, 97]],
  [1.0, [252, 253, 191]],
];

function magmaRGB(t: number): [number, number, number] {
  const v = Math.max(0, Math.min(1, t));
  for (let i = 1; i < MAGMA.length; i++) {
    if (v <= MAGMA[i][0]) {
      const [t0, c0] = MAGMA[i - 1];
      const [t1, c1] = MAGMA[i];
      const f = (v - t0) / (t1 - t0 || 1);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * f),
        Math.round(c0[1] + (c1[1] - c0[1]) * f),
        Math.round(c0[2] + (c1[2] - c0[2]) * f),
      ];
    }
  }
  return [252, 253, 191];
}

function magma(t: number): string {
  const [r, g, b] = magmaRGB(t);
  return `rgb(${r},${g},${b})`;
}

function isLightColor([r, g, b]: [number, number, number]): boolean {
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

type RoundRectFn = (x: number, y: number, w: number, h: number, r: number) => void;

function fillRoundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = (ctx as unknown as { roundRect?: RoundRectFn }).roundRect;
  if (r > 0 && typeof rr === "function") {
    ctx.beginPath();
    rr.call(ctx, x, y, w, h, r);
    ctx.fill();
  } else {
    ctx.fillRect(x, y, w, h);
  }
}

interface Hover {
  row: number;
  col: number;
  x: number;
  y: number;
}

/**
 * Interaktywna macierz pomyłek (nasza, zamiast PNG z ultralytics). Orientacja jak w
 * `confusion_matrix.json`: matrix[predykcja][prawda], ostatni indeks = tło. Kolumna „tło"
 * = fałszywe wykrycia (FP), wiersz „tło" = pominięcia (FN); przekątna = trafienia.
 * Domyślnie normalizacja po kolumnach (prawdzie) — jak obraz z YOLO — z opcją zliczeń surowych.
 */
export default function ConfusionMatrix({ data }: Props) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [normalize, setNormalize] = useState(true);
  const [logScale, setLogScale] = useState(false);
  const [useLeafOrder, setUseLeafOrder] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const [width, setWidth] = useState(560);

  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const canvasBg = useColorModeValue("#f7f7f9", "#0f1117");
  const gridLine = useColorModeValue("rgba(0,0,0,0.06)", "rgba(255,255,255,0.06)");

  const matrix = data.matrix;
  const nClasses = data.class_names.length;
  const total = matrix.length; // zwykle nClasses + 1 (tło)
  const hasBackground = total === nClasses + 1;
  const bgLabel = t("background");

  const labels = useMemo(
    () => (hasBackground ? [...data.class_names, bgLabel] : data.class_names.slice(0, total)),
    [data.class_names, hasBackground, bgLabel, total],
  );

  // Kolejność wyświetlania: opcjonalnie leaf-order dla realnych klas, tło zawsze na końcu.
  const order = useMemo(() => {
    const base =
      useLeafOrder && data.leaf_order?.length === nClasses
        ? [...data.leaf_order]
        : Array.from({ length: nClasses }, (_, i) => i);
    return hasBackground ? [...base, nClasses] : base;
  }, [useLeafOrder, data.leaf_order, nClasses, hasBackground]);

  // Sumy kolumn (po prawdzie) do normalizacji „recall-owej" oraz globalne max dla trybu surowego.
  const { colSums, maxCount } = useMemo(() => {
    const sums = new Array(total).fill(0);
    let mx = 0;
    for (let i = 0; i < total; i++) {
      for (let j = 0; j < total; j++) {
        const v = matrix[i]?.[j] ?? 0;
        sums[j] += v;
        if (v > mx) mx = v;
      }
    }
    return { colSums: sums, maxCount: mx || 1 };
  }, [matrix, total]);

  const cellValue = (pred: number, tru: number): number => matrix[pred]?.[tru] ?? 0;
  const displayFraction = (pred: number, tru: number): number => {
    const raw = cellValue(pred, tru);
    return colSums[tru] ? raw / colSums[tru] : 0;
  };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.max(200, Math.floor(w)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const dim = total;
  const LABEL_GUTTER = 96;
  const MAX_CELL = 72;
  const cellWithLabels = Math.floor((width - LABEL_GUTTER) / Math.max(1, dim));
  let cell: number;
  let labelGutter: number;
  if (cellWithLabels >= 11) {
    cell = Math.max(2, Math.min(MAX_CELL, cellWithLabels));
    labelGutter = LABEL_GUTTER;
  } else {
    cell = Math.max(2, Math.min(MAX_CELL, Math.floor(width / Math.max(1, dim))));
    labelGutter = cell >= 11 ? LABEL_GUTTER : 0;
  }
  const size = cell * dim;
  const showLabels = labelGutter > 0;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || dim === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const totalW = size + labelGutter;
    const totalH = size + labelGutter;
    canvas.width = totalW * dpr;
    canvas.height = totalH * dpr;
    canvas.style.width = `${totalW}px`;
    canvas.style.height = `${totalH}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, totalW, totalH);

    const norm = (v: number) => (logScale ? Math.log1p(Math.max(0, v) * 9) / Math.log(10) : Math.max(0, Math.min(1, v)));
    const gap = cell >= 6 ? Math.min(6, Math.max(1, Math.round(cell * 0.09))) : 0;
    const radius = cell >= 8 ? Math.min(8, Math.max(2, Math.round(cell * 0.16))) : 0;
    const tile = cell - gap;
    const showValues = cell >= 40;
    const valueFont = Math.min(13, Math.max(9, Math.round(cell * 0.2)));

    ctx.fillStyle = canvasBg;
    ctx.fillRect(labelGutter, labelGutter, size, size);

    for (let r = 0; r < dim; r++) {
      for (let c = 0; c < dim; c++) {
        const gi = order[r]; // predykcja
        const gj = order[c]; // prawda
        const px = labelGutter + c * cell + gap / 2;
        const py = labelGutter + r * cell + gap / 2;
        const raw = cellValue(gi, gj);
        const frac = displayFraction(gi, gj);
        const magnitude = normalize ? frac : raw / maxCount;
        const rgb = raw <= 0 ? null : magmaRGB(norm(magnitude));
        ctx.fillStyle = rgb ? `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` : canvasBg;
        fillRoundRect(ctx, px, py, tile, tile, radius);
        if (!rgb) {
          ctx.strokeStyle = gridLine;
          ctx.lineWidth = 1;
          ctx.strokeRect(px + 0.5, py + 0.5, tile - 1, tile - 1);
        }
        if (showValues && rgb) {
          ctx.fillStyle = isLightColor(rgb) ? "rgba(20,20,25,0.9)" : "rgba(255,255,255,0.94)";
          ctx.font = `${valueFont}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(normalize ? frac.toFixed(2) : String(Math.round(raw)), px + tile / 2, py + tile / 2);
        }
      }
    }

    if (showLabels) {
      ctx.fillStyle = "#8a8f98";
      ctx.font = "10px sans-serif";
      ctx.textBaseline = "middle";
      for (let r = 0; r < dim; r++) {
        ctx.textAlign = "right";
        ctx.fillText((labels[order[r]] ?? "").slice(0, 16), labelGutter - 6, labelGutter + r * cell + cell / 2);
      }
      ctx.save();
      ctx.textAlign = "left";
      for (let c = 0; c < dim; c++) {
        const x = labelGutter + c * cell + cell / 2;
        ctx.translate(x, labelGutter - 6);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText((labels[order[c]] ?? "").slice(0, 16), 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      ctx.restore();
    }

    if (hover) {
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1;
      ctx.strokeRect(labelGutter + hover.col * cell - 0.5, labelGutter, cell, size);
      ctx.strokeRect(labelGutter, labelGutter + hover.row * cell - 0.5, size, cell);
    }
  }, [
    dim, size, cell, order, labels, matrix, normalize, logScale, maxCount, colSums,
    showLabels, labelGutter, hover, canvasBg, gridLine,
  ]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - labelGutter;
    const y = e.clientY - rect.top - labelGutter;
    if (x < 0 || y < 0 || x >= size || y >= size) {
      setHover(null);
      return;
    }
    const wrapRect = wrapRef.current?.getBoundingClientRect();
    const ox = wrapRect ? wrapRect.left : rect.left;
    const oy = wrapRect ? wrapRect.top : rect.top;
    setHover({ row: Math.floor(y / cell), col: Math.floor(x / cell), x: e.clientX - ox, y: e.clientY - oy });
  };

  const hoverInfo = useMemo(() => {
    if (!hover) return null;
    const gi = order[hover.row];
    const gj = order[hover.col];
    if (gi == null || gj == null) return null;
    return {
      pred: labels[gi] ?? String(gi),
      tru: labels[gj] ?? String(gj),
      count: cellValue(gi, gj),
      frac: displayFraction(gi, gj),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hover, order, labels]);

  if (dim === 0) return null;

  return (
    <Box borderWidth="1px" borderColor={border} borderRadius="md" p={3} minW={0}>
      <HStack justify="space-between" mb={2} flexWrap="wrap" gap={2}>
        <Text fontSize="sm" fontWeight="600">{t("Confusion matrix")}</Text>
        <HStack spacing={1}>
          <ToggleButton active={normalize} onClick={() => setNormalize((v) => !v)}>{t("Normalize")}</ToggleButton>
          <ToggleButton active={logScale} onClick={() => setLogScale((v) => !v)}>{t("Log")}</ToggleButton>
          <ToggleButton active={useLeafOrder} onClick={() => setUseLeafOrder((v) => !v)}>{t("Cluster order")}</ToggleButton>
        </HStack>
      </HStack>

      <Text fontSize="xs" color={muted} mb={2}>
        {t("Rows = predicted, columns = true; the last row/column is background (missed / false positives). The diagonal is correct.")}
      </Text>

      <Box ref={wrapRef} position="relative" overflowX="auto">
        <canvas
          ref={canvasRef}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          style={{ display: "block", marginInline: "auto", cursor: "crosshair" }}
        />
        {hover && hoverInfo && (
          <Box
            position="absolute"
            left={`${Math.min(hover.x + 12, width - 40)}px`}
            top={`${hover.y + 12}px`}
            bg="blackAlpha.800"
            color="white"
            fontSize="xs"
            px={2}
            py={1}
            borderRadius="md"
            pointerEvents="none"
            zIndex={2}
            whiteSpace="nowrap"
          >
            <Text>{t("Predicted")}: {hoverInfo.pred}</Text>
            <Text>{t("True")}: {hoverInfo.tru}</Text>
            <Text opacity={0.85}>
              {t("Count")}: {Math.round(hoverInfo.count)} · {(hoverInfo.frac * 100).toFixed(1)}%
            </Text>
          </Box>
        )}
      </Box>

      <Flex justify="center" mt={3}>
        <MagmaLegend
          high={normalize ? 1 : maxCount}
          logScale={logScale}
          muted={muted}
          lessLabel={t("less")}
          moreLabel={t("more")}
          integer={!normalize}
        />
      </Flex>
    </Box>
  );
}

function ToggleButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Button size="xs" variant={active ? "solid" : "outline"} colorScheme={active ? "brand" : "gray"} onClick={onClick}>
      {children}
    </Button>
  );
}

function MagmaLegend({
  high,
  logScale,
  muted,
  lessLabel,
  moreLabel,
  integer,
}: {
  high: number;
  logScale: boolean;
  muted: string;
  lessLabel: string;
  moreLabel: string;
  integer: boolean;
}) {
  const barW = 320;
  const stops = Array.from({ length: 64 }, (_, i) => magma(i / 63));
  const pos = (v: number) => {
    const c = Math.max(0, Math.min(1, v / (high || 1)));
    const nrm = logScale ? Math.log1p(c * 9) / Math.log(10) : c;
    return nrm * barW;
  };
  const ticks = [0, high / 2, high];
  const fmt = (v: number) => (integer ? String(Math.round(v)) : v.toFixed(2));
  return (
    <HStack spacing={3} align="flex-start" fontSize="xs" color={muted}>
      <Text lineHeight="14px">{lessLabel}</Text>
      <Box>
        <Box display="flex" w={`${barW}px`} borderRadius="sm" overflow="hidden">
          {stops.map((c, i) => (
            <Box key={i} flex="1" h="14px" bg={c} />
          ))}
        </Box>
        <Box position="relative" w={`${barW}px`} h="18px">
          {ticks.map((v, i) => (
            <Box key={i} position="absolute" left={`${pos(v)}px`} top="0" transform="translateX(-50%)" textAlign="center">
              <Box w="1px" h="4px" bg={muted} mx="auto" />
              <Text fontSize="10px" whiteSpace="nowrap">{fmt(v)}</Text>
            </Box>
          ))}
        </Box>
      </Box>
      <Text lineHeight="14px">{moreLabel}</Text>
    </HStack>
  );
}
