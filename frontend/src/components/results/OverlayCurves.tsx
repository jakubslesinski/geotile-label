import { useMemo } from "react";
import Chart from "react-apexcharts";
import { Box, HStack, SimpleGrid, Text, useColorModeValue } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import CopyChartButton from "./CopyChartButton";
import type { TrainingHistory, TrainingRunSummary } from "../../api/client";

// (etykieta, dopasowanie kolumny). Kolumny results.csv (Ultralytics) bywają różnie nazwane
// między architekturami, więc dopasowujemy regexem, nie po dokładnej nazwie.
const CURVES: { label: string; match: (col: string) => boolean; range01?: boolean }[] = [
  { label: "box_loss", match: (c) => /box_loss/i.test(c) },
  { label: "mAP50-95", match: (c) => /map50-95/i.test(c), range01: true },
  { label: "mAP50", match: (c) => /map50(?!-95)/i.test(c), range01: true },
  { label: "precision", match: (c) => /precision/i.test(c), range01: true },
  { label: "recall", match: (c) => /recall/i.test(c), range01: true },
];

function epochColumn(columns: string[]): string | null {
  return columns.find((col) => col.trim().toLowerCase() === "epoch") ?? null;
}

interface Props {
  runs: TrainingRunSummary[];
  historyByRun: Map<string, TrainingHistory>;
  runColors: Map<string, string>;
}

/** Siatka wykresów: jedna metryka = jeden wykres, nakładający krzywe wybranych runów. */
export default function OverlayCurves({ runs, historyByRun, runColors }: Props) {
  const { t } = useTranslation();
  const gridColor = useColorModeValue("#E2E8F0", "rgba(255,255,255,0.12)");
  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  // Solidne tło + foreColor + tryb motywu → czytelny eksport PNG na obu motywach.
  const chartBg = useColorModeValue("#FFFFFF", "#242428");
  const foreColor = useColorModeValue("#1B2559", "#D6D6D8");
  const chartMode = useColorModeValue("light", "dark") as "light" | "dark";

  const charts = useMemo(() => (
    CURVES.map((curve) => {
      const series = runs
        .map((run) => {
          const history = historyByRun.get(run.training_run_id);
          if (!history) return null;
          const ecol = epochColumn(history.columns);
          const col = history.columns.find(curve.match);
          if (!col) return null;
          const data = history.rows
            .filter((row) => typeof row[col] === "number")
            .map((row, index) => ({ x: Number(row[ecol || ""] ?? index + 1), y: Number(row[col]) }));
          if (data.length === 0) return null;
          return {
            name: `#${run.attempt} · ${run.base_model || "?"}`,
            color: runColors.get(run.training_run_id) || "#888888",
            data,
          };
        })
        .filter((serie): serie is NonNullable<typeof serie> => !!serie);
      return { curve, series };
    }).filter((chart) => chart.series.length > 0)
  ), [runs, historyByRun, runColors]);

  if (charts.length === 0) return null;

  return (
    <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
      {charts.map(({ curve, series }) => (
        <Box key={curve.label} borderWidth="1px" borderColor={border} borderRadius="md" p={3} minW={0}>
          <HStack justify="space-between" mb={2}>
            <Text fontSize="sm" fontWeight="600">{curve.label}</Text>
            <CopyChartButton chartId={`overlay-${curve.label}`} />
          </HStack>
          <Chart
            type="line"
            height={240}
            series={series.map((serie) => ({ name: serie.name, data: serie.data }))}
            options={{
              chart: { id: `overlay-${curve.label}`, toolbar: { show: false }, zoom: { enabled: false }, background: chartBg, foreColor },
              theme: { mode: chartMode },
              colors: series.map((serie) => serie.color),
              stroke: { width: 2, curve: "straight" },
              markers: { size: 0 },
              grid: { borderColor: gridColor },
              xaxis: { type: "numeric", title: { text: t("Epoch") } },
              yaxis: curve.range01 ? { min: 0, max: 1, decimalsInFloat: 3 } : { decimalsInFloat: 4 },
              legend: { position: "top" },
              tooltip: { theme: chartMode, shared: true },
            }}
          />
        </Box>
      ))}
    </SimpleGrid>
  );
}
