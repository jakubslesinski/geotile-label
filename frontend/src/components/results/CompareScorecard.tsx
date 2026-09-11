import { useMemo, useState } from "react";
import {
  Badge,
  Box,
  Checkbox,
  HStack,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  useColorModeValue,
} from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import { f1Score } from "../../utils/metrics";
import type { TrainingRunSummary } from "../../api/client";

// Preferowana kolejność metryk; reszta obecnych dopisywana alfabetycznie. F1 to wiersz
// pochodny (średnia harmoniczna precyzji i czułości), liczony z precision/recall.
const METRIC_ORDER = ["mAP50-95", "mAP50", "mAP75", "precision", "recall", "F1"];

/** Wartość metryki dla runu; dla „F1" liczona z precision/recall, inaczej odczyt z metrics. */
function metricValue(run: TrainingRunSummary, key: string): number | null {
  if (key === "F1") {
    const p = run.metrics?.precision;
    const r = run.metrics?.recall;
    return typeof p === "number" && typeof r === "number" ? f1Score(p, r) : null;
  }
  const raw = run.metrics?.[key];
  return typeof raw === "number" ? raw : null;
}

function runLabel(run: TrainingRunSummary): string {
  return `#${run.attempt} · ${run.base_model || "?"}`;
}

interface Props {
  runs: TrainingRunSummary[];
  baselineRunId: string | null;
  runColors: Map<string, string>;
}

/** Scorecard: metryki (wiersze) × zaznaczone runy (kolumny) + delta vs baseline. */
export default function CompareScorecard({ runs, baselineRunId, runColors }: Props) {
  const { t } = useTranslation();
  const [diffOnly, setDiffOnly] = useState(false);
  const muted = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const border = useColorModeValue("gray.200", "whiteAlpha.300");

  const metricKeys = useMemo(() => {
    const present = new Set<string>();
    runs.forEach((run) => Object.keys(run.metrics || {}).forEach((key) => present.add(key)));
    const ordered = METRIC_ORDER.filter((key) =>
      key === "F1" ? present.has("precision") && present.has("recall") : present.has(key),
    );
    const extra = [...present].filter((key) => !METRIC_ORDER.includes(key)).sort();
    return [...ordered, ...extra];
  }, [runs]);

  const baseline = runs.find((run) => run.training_run_id === baselineRunId) || runs[0];

  const rows = useMemo(() => (
    metricKeys.map((key) => {
      const baseVal = baseline ? metricValue(baseline, key) : null;
      const cells = runs.map((run) => {
        const val = metricValue(run, key);
        const isBase = run.training_run_id === baseline?.training_run_id;
        const delta = val != null && baseVal != null && !isBase ? val - baseVal : null;
        return { run, val, delta };
      });
      const anyDelta = cells.some((cell) => cell.delta != null && Math.abs(cell.delta) >= 0.0005);
      return { key, cells, anyDelta };
    }).filter((row) => !diffOnly || row.anyDelta)
  ), [metricKeys, runs, baseline, diffOnly]);

  if (runs.length === 0) return null;

  return (
    <VStack align="stretch" spacing={2}>
      <HStack justify="flex-end">
        <Checkbox size="sm" isChecked={diffOnly} onChange={(event) => setDiffOnly(event.target.checked)}>
          {t("Diff only")}
        </Checkbox>
      </HStack>
      <Box overflowX="auto" borderWidth="1px" borderColor={border} borderRadius="md">
        <Table size="sm">
          <Thead>
            <Tr>
              <Th>{t("Metric")}</Th>
              {runs.map((run) => (
                <Th key={run.training_run_id} isNumeric>
                  <HStack justify="flex-end" spacing={1}>
                    <Box w="8px" h="8px" borderRadius="full" bg={runColors.get(run.training_run_id) || muted} />
                    <Text noOfLines={1}>
                      {runLabel(run)}{run.training_run_id === baseline?.training_run_id ? " ★" : ""}
                    </Text>
                  </HStack>
                </Th>
              ))}
            </Tr>
          </Thead>
          <Tbody>
            {rows.map((row) => (
              <Tr key={row.key}>
                <Td fontWeight="600">{row.key}</Td>
                {row.cells.map((cell) => (
                  <Td key={cell.run.training_run_id} isNumeric>
                    <HStack justify="flex-end" spacing={1}>
                      <Text>{cell.val == null ? "-" : cell.val.toFixed(3)}</Text>
                      {cell.delta != null && (
                        <Badge fontSize="9px" colorScheme={cell.delta >= 0 ? "green" : "red"}>
                          {cell.delta >= 0 ? "+" : ""}{cell.delta.toFixed(3)}
                        </Badge>
                      )}
                    </HStack>
                  </Td>
                ))}
              </Tr>
            ))}
          </Tbody>
        </Table>
      </Box>
      <Text fontSize="xs" color={muted}>
        {t("Delta is vs the baseline run (★). Green = higher (better) for mAP/precision/recall.")}
      </Text>
    </VStack>
  );
}
