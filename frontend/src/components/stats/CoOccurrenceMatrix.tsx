import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Flex,
  HStack,
  IconButton,
  Text,
  Tooltip,
  useColorModeValue,
  useToast,
} from "@chakra-ui/react";
import { MdContentCopy } from "react-icons/md";
import { useTranslation } from "react-i18next";

// Paleta magma (matplotlib) — ta sama estetyka co macierz Podobieństwa klas (embeddingi),
// żeby oba widoki czytały się jako „ten sam rodzaj heatmapy".
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

// Perceived luminance (sRGB) — decides black vs white text on a coloured tile.
function isLightColor([r, g, b]: [number, number, number]): boolean {
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

type RoundRectFn = (x: number, y: number, w: number, h: number, r: number) => void;

function pathRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): boolean {
  const rr = (ctx as unknown as { roundRect?: RoundRectFn }).roundRect;
  if (r > 0 && typeof rr === "function") {
    ctx.beginPath();
    rr.call(ctx, x, y, w, h, r);
    return true;
  }
  return false;
}

function fillRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  if (pathRoundRect(ctx, x, y, w, h, r)) ctx.fill();
  else ctx.fillRect(x, y, w, h);
}

interface Hover {
  row: number;
  col: number;
  x: number;
  y: number;
}

interface Props {
  classes: string[];
  /** matrix[i][j] = wartość dla wybranej metryki (liczba / lift / Jaccard). */
  matrix: number[][];
  metric: "counts" | "lift" | "jaccard";
}

/**
 * Macierz współwystępowania klas jako heatmapa na canvasie w stylu „chipów" (paleta magma,
 * zaokrąglone rogi) — spójna wizualnie z macierzą Podobieństwa klas (embeddingi). W odróżnieniu
 * od tamtej komórki mogą być PROSTOKĄTNE (szersze niż wyższe): dzięki temu macierz wypełnia
 * szerokość kafla strony i nie rozpycha się na tak dużą wysokość jak wariant kwadratowy.
 */
export default function CoOccurrenceMatrix({ classes, matrix, metric }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [width, setWidth] = useState(560);

  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const canvasBg = useColorModeValue("#f7f7f9", "#0f1117");
  const gridLine = useColorModeValue("rgba(0,0,0,0.06)", "rgba(255,255,255,0.06)");
  const labelColor = useColorModeValue("#5b6270", "#8a8f98");
  // Solidne tło całego canvasu → czytelny PNG po skopiowaniu na jasnym i ciemnym motywie.
  const exportBg = useColorModeValue("#FFFFFF", "#242428");

  const n = classes.length;

  // Zakres koloru z komórek poza-przekątną (przekątna = suma własna i zdominowałaby skalę).
  const { maxOff, minOff } = useMemo(() => {
    let mx = 0;
    let mn = Infinity;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        const v = matrix[i]?.[j] ?? 0;
        mx = Math.max(mx, v);
        mn = Math.min(mn, v);
      }
    }
    return { maxOff: mx || 1, minOff: Number.isFinite(mn) ? mn : 0 };
  }, [matrix, n]);

  // Lift jest neutralny przy 1 — kotwiczymy dół skali przy faktycznym minimum dla kontrastu.
  // Liczba i Jaccard startują od 0 (0 = brak współwystępowania ma być ciemne).
  const colorLow = metric === "lift" ? Math.min(minOff, 1) : 0;
  const colorSpan = maxOff - colorLow || 1;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.max(220, Math.floor(w)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Szerokość komórki wypełnia panel; wysokość jest ograniczona → komórki prostokątne (płaskie),
  // co utrzymuje sensowną wysokość macierzy niezależnie od liczby klas.
  const LABEL_GUTTER = 108;
  const MAX_CELL_W = 128;
  const MAX_CELL_H = 54;
  const cellWWithLabels = Math.floor((width - LABEL_GUTTER) / Math.max(1, n));
  let cellW: number;
  let labelGutter: number;
  if (cellWWithLabels >= 14) {
    cellW = Math.max(4, Math.min(MAX_CELL_W, cellWWithLabels));
    labelGutter = LABEL_GUTTER;
  } else {
    cellW = Math.max(4, Math.min(MAX_CELL_W, Math.floor(width / Math.max(1, n))));
    labelGutter = cellW >= 14 ? LABEL_GUTTER : 0;
  }
  const cellH = Math.min(cellW, MAX_CELL_H);
  const gridW = cellW * n;
  const gridH = cellH * n;
  const showLabels = labelGutter > 0;

  const fmt = (v: number) => (metric === "counts" ? String(Math.round(v)) : v.toFixed(2));

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || n === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const totalW = gridW + labelGutter;
    const totalH = gridH + labelGutter;
    canvas.width = totalW * dpr;
    canvas.height = totalH * dpr;
    canvas.style.width = `${totalW}px`;
    canvas.style.height = `${totalH}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Solidne tło całości (nie tylko siatki) — dla czytelnego kopiowania do PNG.
    ctx.fillStyle = exportBg;
    ctx.fillRect(0, 0, totalW, totalH);

    const norm = (raw: number) => Math.max(0, Math.min(1, (raw - colorLow) / colorSpan));

    const unit = Math.min(cellW, cellH);
    const gap = unit >= 6 ? Math.min(6, Math.max(1, Math.round(unit * 0.09))) : 0;
    const radius = unit >= 8 ? Math.min(8, Math.max(2, Math.round(unit * 0.16))) : 0;
    const tileW = cellW - gap;
    const tileH = cellH - gap;
    const showValues = cellW >= 30 && cellH >= 18;
    const valueFont = Math.min(13, Math.max(8, Math.round(cellH * 0.34)));

    ctx.fillStyle = canvasBg;
    ctx.fillRect(labelGutter, labelGutter, gridW, gridH);

    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        const px = labelGutter + c * cellW + gap / 2;
        const py = labelGutter + r * cellH + gap / 2;
        const raw = matrix[r]?.[c] ?? 0;
        // Przekątna (para klasy z samą sobą): pełny kafel u szczytu skali — czyta się jako
        // „suma własna / kręgosłup" macierzy, a nie jako zero.
        const t01 = r === c ? 1 : norm(raw);
        const rgb = raw <= 0 && r !== c ? null : magmaRGB(t01);
        ctx.fillStyle = rgb ? `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` : canvasBg;
        fillRoundRect(ctx, px, py, tileW, tileH, radius);
        if (!rgb) {
          ctx.strokeStyle = gridLine;
          ctx.lineWidth = 1;
          if (pathRoundRect(ctx, px + 0.5, py + 0.5, tileW - 1, tileH - 1, radius)) ctx.stroke();
          continue;
        }
        if (showValues) {
          ctx.fillStyle = isLightColor(rgb) ? "rgba(20,20,25,0.88)" : "rgba(255,255,255,0.92)";
          ctx.font = `${valueFont}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(fmt(raw), px + tileW / 2, py + tileH / 2);
        }
      }
    }

    if (showLabels) {
      ctx.fillStyle = labelColor;
      ctx.font = "10px sans-serif";
      ctx.textBaseline = "middle";
      for (let r = 0; r < n; r++) {
        ctx.textAlign = "right";
        ctx.fillText((classes[r] ?? "").slice(0, 18), labelGutter - 6, labelGutter + r * cellH + cellH / 2);
      }
      ctx.save();
      ctx.textAlign = "left";
      for (let c = 0; c < n; c++) {
        const x = labelGutter + c * cellW + cellW / 2;
        ctx.translate(x, labelGutter - 6);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText((classes[c] ?? "").slice(0, 18), 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      ctx.restore();
    }

    if (hover) {
      ctx.strokeStyle = "rgba(120,120,130,0.55)";
      ctx.lineWidth = 1;
      ctx.strokeRect(labelGutter + hover.col * cellW - 0.5, labelGutter, cellW, gridH);
      ctx.strokeRect(labelGutter, labelGutter + hover.row * cellH - 0.5, gridW, cellH);
      const hx = labelGutter + hover.col * cellW + gap / 2;
      const hy = labelGutter + hover.row * cellH + gap / 2;
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 1.5;
      if (pathRoundRect(ctx, hx, hy, tileW, tileH, radius)) ctx.stroke();
      else ctx.strokeRect(hx, hy, tileW, tileH);
    }
  }, [
    n, classes, matrix, metric, cellW, cellH, gridW, gridH, labelGutter, showLabels,
    colorLow, colorSpan, canvasBg, gridLine, labelColor, exportBg, hover,
  ]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - labelGutter;
    const y = e.clientY - rect.top - labelGutter;
    if (x < 0 || y < 0 || x >= gridW || y >= gridH) {
      setHover(null);
      return;
    }
    const col = Math.floor(x / cellW);
    const row = Math.floor(y / cellH);
    const wrapRect = wrapRef.current?.getBoundingClientRect();
    const ox = wrapRect ? wrapRect.left : rect.left;
    const oy = wrapRect ? wrapRect.top : rect.top;
    setHover({ row, col, x: e.clientX - ox, y: e.clientY - oy });
  };

  const hoverInfo = useMemo(() => {
    if (!hover) return null;
    const a = classes[hover.row];
    const b = classes[hover.col];
    if (a == null || b == null) return null;
    return { a, b, value: matrix[hover.row]?.[hover.col] ?? 0, diag: hover.row === hover.col };
  }, [hover, classes, matrix]);

  const copy = async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      const blob: Blob | null = await new Promise((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/png"),
      );
      if (!blob) throw new Error("no image produced");
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      toast({ title: t("Chart copied to clipboard"), status: "success", duration: 1800 });
    } catch (error: any) {
      toast({
        title: t("Could not copy chart"),
        description: error?.message || String(error),
        status: "error",
        duration: 4000,
        isClosable: true,
      });
    }
  };

  const metricLabel =
    metric === "counts" ? t("Tiles (count)") : metric === "lift" ? t("Lift") : t("Jaccard");

  if (n === 0) return null;

  return (
    <Box>
      <Flex justify="flex-end" mb={1}>
        <Tooltip label={t("Copy chart as PNG")} openDelay={400}>
          <IconButton
            aria-label={t("Copy chart as PNG")}
            size="xs"
            variant="ghost"
            icon={<MdContentCopy />}
            onClick={copy}
          />
        </Tooltip>
      </Flex>
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
            {hoverInfo.diag ? (
              <Text>{hoverInfo.a}</Text>
            ) : (
              <Text>{hoverInfo.a} ↔ {hoverInfo.b}</Text>
            )}
            <Text opacity={0.8}>{metricLabel}: {fmt(hoverInfo.value)}</Text>
          </Box>
        )}
      </Box>
      <Flex justify="center" mt={3}>
        <MagmaLegend low={colorLow} high={maxOff} muted={muted} lessLabel={t("less")} moreLabel={t("more")} />
      </Flex>
    </Box>
  );
}

function MagmaLegend({
  low,
  high,
  muted,
  lessLabel,
  moreLabel,
}: {
  low: number;
  high: number;
  muted: string;
  lessLabel: string;
  moreLabel: string;
}) {
  const barW = 320;
  const stops = Array.from({ length: 64 }, (_, i) => magma(i / 63));
  const span = high - low || 1;
  const pos = (v: number) => Math.max(0, Math.min(1, (v - low) / span)) * barW;
  const ticks = [low, (low + high) / 2, high];
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
              <Text fontSize="10px" whiteSpace="nowrap">{v >= 100 ? Math.round(v) : v.toFixed(2)}</Text>
            </Box>
          ))}
        </Box>
      </Box>
      <Text lineHeight="14px">{moreLabel}</Text>
    </HStack>
  );
}
