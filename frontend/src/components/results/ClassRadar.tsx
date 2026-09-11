import { useMemo, useState } from "react";
import { Box, HStack, Select, Text } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import CopyChartButton from "./CopyChartButton";
import MetricRadar from "./MetricRadar";
import { f1Score } from "../../utils/metrics";
import type { TrainingRunSummary } from "../../api/client";

const AXES = ["mAP50-95", "mAP50", "precision", "recall", "F1"];

interface Props {
  runs: TrainingRunSummary[];
  runColors: Map<string, string>;
}

/** Radar dla jednej, wybranej z listy klasy — porównanie runów na tej klasie (osie jak
 *  w radarze ogólnym, wartości z metrics_per_class). Lista, bo klas może być bardzo dużo. */
export default function ClassRadar({ runs, runColors }: Props) {
  const { t } = useTranslation();

  // Klasy występujące w którymkolwiek z wybranych runów (posortowane, bez duplikatów).
  const classNames = useMemo(() => {
    const names = new Set<string>();
    runs.forEach((run) => Object.keys(run.metrics_per_class || {}).forEach((name) => names.add(name)));
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [runs]);

  const [selectedClass, setSelectedClass] = useState<string>("");
  const activeClass = selectedClass && classNames.includes(selectedClass) ? selectedClass : classNames[0] || "";

  const series = runs.map((run) => {
    const perClass = run.metrics_per_class?.[activeClass];
    return {
      name: `#${run.attempt} · ${run.base_model || "?"}`,
      data: AXES.map((key) =>
        key === "F1"
          ? f1Score(Number(perClass?.precision ?? 0), Number(perClass?.recall ?? 0))
          : Number(perClass?.[key] ?? 0),
      ),
    };
  });
  const colors = runs.map((run) => runColors.get(run.training_run_id) || "#888888");

  if (runs.length === 0 || classNames.length === 0) {
    return (
      <Box minW={0}>
        <Text fontSize="sm" fontWeight="700" mb={2}>{t("Per-class radar")}</Text>
        <Text fontSize="xs" color="secondaryGray.600">
          {t("No per-class metrics for the selected runs. Re-run training to record them.")}
        </Text>
      </Box>
    );
  }

  return (
    <Box minW={0}>
      <HStack justify="space-between" mb={2} spacing={2}>
        <Text fontSize="sm" fontWeight="700" flexShrink={0}>{t("Per-class radar")}</Text>
        <HStack spacing={1} flex="1" justify="flex-end">
          <Select
            size="xs"
            maxW="160px"
            value={activeClass}
            onChange={(event) => setSelectedClass(event.target.value)}
          >
            {classNames.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </Select>
          <CopyChartButton chartId="class-radar" />
        </HStack>
      </HStack>
      <MetricRadar chartId="class-radar" categories={AXES} series={series} colors={colors} />
    </Box>
  );
}
