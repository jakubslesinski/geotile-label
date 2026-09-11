import { useMemo } from "react";
import { Box, HStack, Text } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import CopyChartButton from "./CopyChartButton";
import MetricRadar from "./MetricRadar";
import { f1Score } from "../../utils/metrics";
import type { TrainingRunSummary } from "../../api/client";

const BASE_METRICS = ["mAP50-95", "mAP50", "precision", "recall"];

interface Props {
  runs: TrainingRunSummary[];
  runColors: Map<string, string>;
}

/** Radar wielometryczny (0..1) — porównanie „kształtu" wybranych runów, z osią F1
 *  liczoną z precision/recall. */
export default function CompareRadar({ runs, runColors }: Props) {
  const { t } = useTranslation();

  const categories = useMemo(() => {
    const present = new Set<string>();
    runs.forEach((run) => Object.keys(run.metrics || {}).forEach((key) => present.add(key)));
    const base = BASE_METRICS.filter((key) => present.has(key));
    // F1 to oś pochodna — dokładamy ją, gdy są obie składowe (precision i recall).
    if (present.has("precision") && present.has("recall")) base.push("F1");
    return base;
  }, [runs]);

  const series = runs.map((run) => ({
    name: `#${run.attempt} · ${run.base_model || "?"}`,
    data: categories.map((key) =>
      key === "F1"
        ? f1Score(Number(run.metrics?.precision ?? 0), Number(run.metrics?.recall ?? 0))
        : Number(run.metrics?.[key] ?? 0),
    ),
  }));
  const colors = runs.map((run) => runColors.get(run.training_run_id) || "#888888");

  if (runs.length === 0 || categories.length < 3) return null;

  return (
    <Box minW={0}>
      <HStack justify="space-between" mb={2}>
        <Text fontSize="sm" fontWeight="700">{t("Metric radar")}</Text>
        <CopyChartButton chartId="compare-radar" />
      </HStack>
      <MetricRadar chartId="compare-radar" categories={categories} series={series} colors={colors} />
    </Box>
  );
}
