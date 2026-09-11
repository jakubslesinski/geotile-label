import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Flex,
  HStack,
  Icon,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  useColorModeValue,
} from "@chakra-ui/react";
import { MdChevronRight } from "react-icons/md";
import { useTranslation } from "react-i18next";
import InfoPopover from "../common/InfoPopover";
import type { ConfusionAnalysis } from "../../api/client";

interface Props {
  data: ConfusionAnalysis;
  /** Nagłówek widgetu (domyślnie „Class confusion" — kontekst treningu). */
  title?: string;
  /** Podpis pod nagłówkiem (domyślnie o pomyłkach modelu). */
  caption?: string;
  /**
   * Rozciągnij paletę na faktyczny zakres wartości poza-przekątną [min, max]
   * zamiast kotwiczyć od 0. Dla podobieństwa z embeddingów (wartości ~0.85–0.99)
   * daje realny kontrast między klasami; dla liczb pomyłek (0 = brak) zostaw false.
   */
  contrastStretch?: boolean;
  /** Klik w kafel poza-przekątną / w parę na liście emituje nazwy klas (dla galerii). */
  onSelectPair?: (a: string, b: string) => void;
  /** Para do podświetlenia (nazwy klas). */
  selectedPair?: [string, string] | null;
  /** Opcjonalna ikona (i) przy tytule. */
  info?: { titleKey: string; bodyKey: string; helpPage?: string };
}

// Paleta magma (matplotlib) — kilka punktów kontrolnych, interpolacja liniowa w sRGB.
// Ta sama estetyka co reszta aplikacji; ciemne tło -> ciepłe akcenty dla silnych pomyłek.
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

// Build a rounded-rect path if the canvas supports roundRect; returns false otherwise
// so callers can fall back to a plain rectangle.
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

// Rounded-rect fill with graceful fallback if the canvas lacks roundRect.
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

/**
 * Macierz podobieństwa/pomyłek klas jako heatmapa na canvasie (kafle w stylu
 * github-contribution-graph, paleta magma). Canvas zamiast siatki DOM, bo przy
 * 200-300 klasach byłoby ~90 000 komórek. Klasy są uporządkowane wg leaf-order z
 * klasteryzacji, więc mylone pary tworzą bloki przy przekątnej.
 *
 * WARSTWA WIZUALIZACJI: pokazuje, które klasy model myli. Decyzja „scalić / rozdzielić
 * te klasy" pozostaje po stronie analityka i jest bramkowana przez DI0 — widżet niczego
 * nie przesądza, jedynie uwidacznia strukturę.
 */
export default function ConfusionSimilarityMatrix({
  data,
  title,
  caption,
  contrastStretch = false,
  onSelectPair,
  selectedPair,
  info,
}: Props) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [useLeafOrder, setUseLeafOrder] = useState(true);
  const [logScale, setLogScale] = useState(false);
  const [showDiagonal, setShowDiagonal] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const [width, setWidth] = useState(560);

  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const canvasBg = useColorModeValue("#f7f7f9", "#0f1117");
  const gridLine = useColorModeValue("rgba(0,0,0,0.06)", "rgba(255,255,255,0.06)");
  const rowHover = useColorModeValue("blackAlpha.100", "whiteAlpha.200");

  const n = data.class_names.length;

  // Kolejność wyświetlania: leaf-order (mylone pary obok siebie) lub oryginalna.
  const order = useMemo(() => {
    if (useLeafOrder && data.leaf_order?.length === n) return data.leaf_order;
    return Array.from({ length: n }, (_, i) => i);
  }, [useLeafOrder, data.leaf_order, n]);

  // Zakres do normalizacji koloru liczony z komórek off-diagonal (przekątna = trafienia
  // własne i zdominowałaby skalę). Dzięki temu słabe pomyłki nie giną w czerni.
  const { maxOff, minOff } = useMemo(() => {
    let mx = 0;
    let mn = Infinity;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        const v = data.similarity[i]?.[j] ?? 0;
        mx = Math.max(mx, v);
        mn = Math.min(mn, v);
      }
    }
    return { maxOff: mx || 1, minOff: Number.isFinite(mn) ? mn : 0 };
  }, [data.similarity, n]);

  // Dolna kotwica palety: dla contrastStretch = faktyczne minimum poza-przekątną,
  // inaczej 0 (0 = brak pomyłki ma zostać czarne).
  const colorLow = contrastStretch ? minOff : 0;
  const colorSpan = maxOff - colorLow || 1;

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

  // Dobierz rozmiar komórki tak, by macierz WYPEŁNIAŁA szerokość panelu (a nie była
  // maleńka i dosunięta do lewej przy kilku klasach). Uwzględnij rynnę etykiet, gdy
  // komórki są duże (etykiety pokazujemy dla cell >= 11 px).
  const LABEL_GUTTER = 96;
  const MAX_CELL = 96;
  const cellWithLabels = Math.floor((width - LABEL_GUTTER) / Math.max(1, n));
  let cell: number;
  let labelGutter: number;
  if (cellWithLabels >= 11) {
    cell = Math.max(2, Math.min(MAX_CELL, cellWithLabels));
    labelGutter = LABEL_GUTTER;
  } else {
    cell = Math.max(2, Math.min(MAX_CELL, Math.floor(width / Math.max(1, n))));
    labelGutter = cell >= 11 ? LABEL_GUTTER : 0;
  }
  const size = cell * n;
  const showLabels = labelGutter > 0;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || n === 0) return;
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

    const norm = (raw: number) => {
      const v = Math.max(0, Math.min(1, (raw - colorLow) / colorSpan));
      return logScale ? Math.log1p(v * 9) / Math.log(10) : v;
    };
    // Kafle jak „chipy": odstęp i zaokrąglenie rosną z rozmiarem komórki. Przy bardzo
    // małych komórkach (setki klas) wracamy do pełnych prostokątów — dla czytelności i wydajności.
    const gap = cell >= 6 ? Math.min(6, Math.max(1, Math.round(cell * 0.09))) : 0;
    const radius = cell >= 8 ? Math.min(8, Math.max(2, Math.round(cell * 0.16))) : 0;
    const tile = cell - gap;
    const showValues = cell >= 44; // wartość wpisana w kafel, gdy jest na nią miejsce
    const valueFont = Math.min(14, Math.max(9, Math.round(cell * 0.2)));

    ctx.fillStyle = canvasBg;
    ctx.fillRect(labelGutter, labelGutter, size, size);

    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        const gi = order[r];
        const gj = order[c];
        const px = labelGutter + c * cell + gap / 2;
        const py = labelGutter + r * cell + gap / 2;

        if (gi === gj && !showDiagonal) {
          // Komórka „sama ze sobą": pusty zaokrąglony chip z delikatnym obrysem, żeby
          // czytał się jako brak danych, a nie jako zerowa pomyłka.
          ctx.fillStyle = canvasBg;
          fillRoundRect(ctx, px, py, tile, tile, radius);
          ctx.strokeStyle = gridLine;
          ctx.lineWidth = 1;
          if (pathRoundRect(ctx, px + 0.5, py + 0.5, tile - 1, tile - 1, radius)) ctx.stroke();
          continue;
        }

        const raw = data.similarity[gi]?.[gj] ?? 0;
        const rgb = raw <= 0 ? null : magmaRGB(norm(raw));
        ctx.fillStyle = rgb ? `rgb(${rgb[0]},${rgb[1]},${rgb[2]})` : canvasBg;
        fillRoundRect(ctx, px, py, tile, tile, radius);

        if (showValues && rgb) {
          ctx.fillStyle = isLightColor(rgb) ? "rgba(20,20,25,0.88)" : "rgba(255,255,255,0.92)";
          ctx.font = `${valueFont}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(raw.toFixed(2), px + tile / 2, py + tile / 2);
        }
      }
    }

    // Etykiety klas na osiach — tylko gdy komórki są dostatecznie duże, inaczej hover.
    if (showLabels) {
      ctx.fillStyle = "#8a8f98";
      ctx.font = "10px sans-serif";
      ctx.textBaseline = "middle";
      for (let r = 0; r < n; r++) {
        const name = data.class_names[order[r]] ?? "";
        ctx.textAlign = "right";
        ctx.fillText(name.slice(0, 16), labelGutter - 6, labelGutter + r * cell + cell / 2);
      }
      ctx.save();
      ctx.textAlign = "left";
      for (let c = 0; c < n; c++) {
        const name = data.class_names[order[c]] ?? "";
        const x = labelGutter + c * cell + cell / 2;
        ctx.translate(x, labelGutter - 6);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(name.slice(0, 16), 0, 0);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      ctx.restore();
    }

    // Podświetlenie wiersza/kolumny pod kursorem + obrys samego kafla.
    if (hover) {
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1;
      ctx.strokeRect(labelGutter + hover.col * cell - 0.5, labelGutter, cell, size);
      ctx.strokeRect(labelGutter, labelGutter + hover.row * cell - 0.5, size, cell);
      const hx = labelGutter + hover.col * cell + gap / 2;
      const hy = labelGutter + hover.row * cell + gap / 2;
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 1.5;
      if (pathRoundRect(ctx, hx, hy, tile, tile, radius)) ctx.stroke();
      else ctx.strokeRect(hx, hy, tile, tile);
    }

    // Obrys wybranej pary (obie komórki symetryczne).
    if (selectedPair) {
      const gA = data.class_names.indexOf(selectedPair[0]);
      const gB = data.class_names.indexOf(selectedPair[1]);
      const dispA = order.indexOf(gA);
      const dispB = order.indexOf(gB);
      if (dispA >= 0 && dispB >= 0) {
        ctx.strokeStyle = "#38E0C8";
        ctx.lineWidth = 2;
        for (const [rr, cc] of [[dispA, dispB], [dispB, dispA]] as const) {
          const sx = labelGutter + cc * cell + gap / 2;
          const sy = labelGutter + rr * cell + gap / 2;
          if (pathRoundRect(ctx, sx, sy, tile, tile, radius)) ctx.stroke();
          else ctx.strokeRect(sx, sy, tile, tile);
        }
      }
    }
  }, [
    n, size, cell, order, data, colorLow, colorSpan, logScale, showDiagonal, showLabels,
    labelGutter, hover, canvasBg, gridLine, selectedPair,
  ]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - labelGutter;
    const y = e.clientY - rect.top - labelGutter;
    if (x < 0 || y < 0 || x >= size || y >= size) {
      setHover(null);
      return;
    }
    const col = Math.floor(x / cell);
    const row = Math.floor(y / cell);
    // Tooltip pozycjonujemy względem panelu (wrap), bo canvas bywa wyśrodkowany
    // (marginInline auto) — inaczej dymek odjechałby o offset centrowania.
    const wrapRect = wrapRef.current?.getBoundingClientRect();
    const ox = wrapRect ? wrapRect.left : rect.left;
    const oy = wrapRect ? wrapRect.top : rect.top;
    setHover({ row, col, x: e.clientX - ox, y: e.clientY - oy });
  };

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onSelectPair) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - labelGutter;
    const y = e.clientY - rect.top - labelGutter;
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    const gi = order[Math.floor(y / cell)];
    const gj = order[Math.floor(x / cell)];
    if (gi == null || gj == null || gi === gj) return;
    onSelectPair(data.class_names[gi], data.class_names[gj]);
  };

  const hoverInfo = useMemo(() => {
    if (!hover) return null;
    const gi = order[hover.row];
    const gj = order[hover.col];
    if (gi == null || gj == null) return null;
    return {
      a: data.class_names[gi] ?? String(gi),
      b: data.class_names[gj] ?? String(gj),
      value: data.similarity[gi]?.[gj] ?? 0,
      diag: gi === gj,
    };
  }, [hover, order, data]);

  if (n === 0) return null;

  return (
    <Box borderWidth="1px" borderColor={border} borderRadius="md" p={3} minW={0}>
      <HStack justify="space-between" mb={2} flexWrap="wrap" gap={2}>
        <HStack spacing={1}>
          <Text fontSize="sm" fontWeight="600">{title ?? t("Class confusion")}</Text>
          {info && <InfoPopover titleKey={info.titleKey} bodyKey={info.bodyKey} helpPage={info.helpPage} />}
        </HStack>
        <HStack spacing={1}>
          <ToggleButton active={useLeafOrder} onClick={() => setUseLeafOrder((v) => !v)}>
            {t("Cluster order")}
          </ToggleButton>
          <ToggleButton active={logScale} onClick={() => setLogScale((v) => !v)}>
            {t("Log")}
          </ToggleButton>
          <ToggleButton active={showDiagonal} onClick={() => setShowDiagonal((v) => !v)}>
            {t("Diagonal")}
          </ToggleButton>
          <InfoPopover titleKey="info.matrix.controls.title" bodyKey="info.matrix.controls.body" />
        </HStack>
      </HStack>

      <Text fontSize="xs" color={muted} mb={2}>
        {caption ?? t("Warmer tiles = classes the model confuses more often. Order and similarity are guidance; merge/split decisions stay with you.")}
      </Text>

      <Box ref={wrapRef} position="relative" overflowX="auto">
        <canvas
          ref={canvasRef}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          onClick={onClick}
          style={{ display: "block", marginInline: "auto", cursor: onSelectPair ? "pointer" : "crosshair" }}
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
              <>
                <Text>{hoverInfo.a} ↔ {hoverInfo.b}</Text>
                <Text opacity={0.8}>{t("similarity")}: {hoverInfo.value.toFixed(3)}</Text>
              </>
            )}
          </Box>
        )}
      </Box>

      <Flex justify="center" mt={3}>
        <MagmaLegend
          low={colorLow}
          high={maxOff}
          logScale={logScale}
          muted={muted}
          lessLabel={t("less")}
          moreLabel={t("more")}
        />
      </Flex>

      {data.confused_pairs.length > 0 && (
        <Box mt={4}>
          <HStack spacing={2} mb={1} flexWrap="wrap">
            <Text fontSize="sm" fontWeight="700">{t("Most confused pairs")}</Text>
            {onSelectPair && (
              <Text fontSize="xs" color={muted}>· {t("click a row to compare examples")}</Text>
            )}
          </HStack>
          <Box borderWidth="1px" borderColor={border} borderRadius="md" overflow="hidden">
            <Table size="sm">
              <Thead>
                <Tr>
                  <Th>{t("Class pair")}</Th>
                  <Th isNumeric>{t("Similarity")}</Th>
                  {onSelectPair && <Th px={2} w="1%" aria-hidden />}
                </Tr>
              </Thead>
              <Tbody>
                {data.confused_pairs.slice(0, 8).map((p, i) => {
                  const isSel =
                    !!selectedPair &&
                    ((selectedPair[0] === p.class_a && selectedPair[1] === p.class_b) ||
                      (selectedPair[0] === p.class_b && selectedPair[1] === p.class_a));
                  return (
                    <Tr
                      key={i}
                      bg={isSel ? "teal.400" : undefined}
                      color={isSel ? "white" : undefined}
                      cursor={onSelectPair ? "pointer" : undefined}
                      _hover={onSelectPair ? { bg: isSel ? "teal.400" : rowHover } : undefined}
                      onClick={onSelectPair ? () => onSelectPair(p.class_a, p.class_b) : undefined}
                    >
                      <Td>{p.class_a} ↔ {p.class_b}</Td>
                      <Td isNumeric sx={{ fontVariantNumeric: "tabular-nums" }}>{p.similarity.toFixed(3)}</Td>
                      {onSelectPair && (
                        <Td px={2}>
                          <Icon as={MdChevronRight} boxSize={4} opacity={0.7} />
                        </Td>
                      )}
                    </Tr>
                  );
                })}
              </Tbody>
            </Table>
          </Box>
        </Box>
      )}
    </Box>
  );
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      size="xs"
      variant={active ? "solid" : "outline"}
      colorScheme={active ? "brand" : "gray"}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function MagmaLegend({
  low,
  high,
  logScale,
  muted,
  lessLabel,
  moreLabel,
}: {
  low: number;
  high: number;
  logScale: boolean;
  muted: string;
  lessLabel: string;
  moreLabel: string;
}) {
  const barW = 360;
  const stops = Array.from({ length: 64 }, (_, i) => magma(i / 63));
  const span = high - low || 1;
  const pos = (v: number) => {
    const c = Math.max(0, Math.min(1, (v - low) / span));
    const nrm = logScale ? Math.log1p(c * 9) / Math.log(10) : c;
    return nrm * barW;
  };
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
              <Text fontSize="10px" whiteSpace="nowrap">{v.toFixed(2)}</Text>
            </Box>
          ))}
        </Box>
      </Box>
      <Text lineHeight="14px">{moreLabel}</Text>
    </HStack>
  );
}
