import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Accordion,
  AccordionButton,
  AccordionIcon,
  AccordionItem,
  AccordionPanel,
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Code,
  Divider,
  Grid,
  GridItem,
  HStack,
  Icon,
  Select,
  SimpleGrid,
  Table,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  useColorModeValue,
  useDisclosure,
  useToast,
} from "@chakra-ui/react";
import { MdChevronLeft, MdFolderOpen, MdDownload } from "react-icons/md";
import Chart from "react-apexcharts";
import Card from "../components/common/Card";
import InfoPopover from "../components/common/InfoPopover";
import ConfusionSimilarityMatrix from "../components/stats/ConfusionSimilarityMatrix";
import ConfusionMatrix from "../components/results/ConfusionMatrix";
import RunsRail from "../components/results/RunsRail";
import CompareScorecard from "../components/results/CompareScorecard";
import CompareRadar from "../components/results/CompareRadar";
import ClassRadar from "../components/results/ClassRadar";
import OverlayCurves from "../components/results/OverlayCurves";
import CopyChartButton from "../components/results/CopyChartButton";
import DeleteRunDialog from "../components/common/DeleteRunDialog";
import { assignRunColors } from "../components/results/runColors";
import * as api from "../api/client";
import type { DatasetRunSummary } from "../types";
import { useTranslation } from "react-i18next";

const STATUS_COLORS: Record<string, string> = {
  completed: "green",
  running: "purple",
  queued: "blue",
  cancelled: "gray",
  failed: "red",
  interrupted: "orange",
};

function formatMetric(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

// Metryki „na żywo" z ostatniego wiersza historii (results.csv) — dla runów w trakcie,
// zanim powstanie finalne metrics.json. Kolumny ultralytics bywają różnie nazwane.
function liveMetricsFromHistory(history?: api.TrainingHistory): Record<string, number> | null {
  if (!history || history.rows.length === 0) return null;
  const last = history.rows[history.rows.length - 1];
  const pick = (re: RegExp): number | undefined => {
    const col = history.columns.find((c) => re.test(c));
    const value = col ? last[col] : undefined;
    return typeof value === "number" ? value : undefined;
  };
  const metrics: Record<string, number> = {};
  const map5095 = pick(/mAP50-95/i);
  if (map5095 != null) metrics["mAP50-95"] = map5095;
  const map50 = pick(/map50(?!-95)/i);
  if (map50 != null) metrics["mAP50"] = map50;
  const precision = pick(/precision/i);
  if (precision != null) metrics["precision"] = precision;
  const recall = pick(/recall/i);
  if (recall != null) metrics["recall"] = recall;
  return Object.keys(metrics).length > 0 ? metrics : null;
}

export default function ResultsView() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const toast = useToast();
  const mutedColor = useColorModeValue("secondaryGray.600", "whiteAlpha.600");
  const gridColor = useColorModeValue("#E2E8F0", "rgba(255,255,255,0.12)");
  // Solidne tło + foreColor + tryb motywu → czytelny eksport PNG wykresów na obu motywach.
  const chartBg = useColorModeValue("#FFFFFF", "#242428");
  const chartForeColor = useColorModeValue("#1B2559", "#D6D6D8");
  const chartMode = useColorModeValue("light", "dark") as "light" | "dark";

  const [datasetRuns, setDatasetRuns] = useState<DatasetRunSummary[]>([]);
  const [datasetRunId, setDatasetRunId] = useState("");
  const [runs, setRuns] = useState<api.TrainingRunSummary[]>([]);
  const [railOpen, setRailOpen] = useState(true);
  // `focusRunId` — jeden run „w centrum" (detal/confusion/rejestracja).
  // `selectedRunIds` — wielokrotny wybór do paneli porównawczych (scorecard/radar/krzywe).
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(() => new Set());
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);
  const [historyByRun, setHistoryByRun] = useState<Map<string, api.TrainingHistory>>(() => new Map());
  const [detail, setDetail] = useState<api.TrainingRunDetail | null>(null);
  const [history, setHistory] = useState<api.TrainingHistory | null>(null);
  const [confusionMatrixUrl, setConfusionMatrixUrl] = useState<string | null>(null);
  const [confusionAnalysis, setConfusionAnalysis] = useState<api.ConfusionAnalysis | null>(null);
  const [evaluation, setEvaluation] = useState<api.TestEvaluation | null>(null);
  const [models, setModels] = useState<api.RegisteredModel[]>([]);
  const [currentModelId, setCurrentModelId] = useState<string | null>(null);
  const [lineage, setLineage] = useState<api.ModelLineage | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getDatasetRuns(id)
      .then((index) => {
        const completed = index.runs.filter((run) => run.status === "complete");
        setDatasetRuns(completed);
        setDatasetRunId((current) => current || completed[0]?.run_id || "");
      })
      .catch((err) => toast({ title: t("Could not load datasets"), description: err?.message, status: "error" }));
  }, [id, t, toast]);

  const loadRuns = useCallback(async () => {
    if (!id || !datasetRunId) {
      setRuns([]);
      return;
    }
    try {
      const listing = await api.getTrainingRuns(id, datasetRunId);
      setRuns(listing.runs);
    } catch (err: any) {
      toast({ title: t("Could not load training runs"), description: err?.message, status: "error" });
    }
  }, [id, datasetRunId, t, toast]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const deleteDisc = useDisclosure();
  const [deleteRunId, setDeleteRunId] = useState<string | null>(null);
  const openDeleteRun = useCallback((runId: string) => {
    setDeleteRunId(runId);
    deleteDisc.onOpen();
  }, [deleteDisc]);
  const openRunFolder = useCallback(async (path: string) => {
    try {
      await api.openPathInFileManager(path);
    } catch (error: any) {
      toast({
        title: t("Cannot open run folder"),
        description: error?.message || String(error),
        status: "error",
        duration: 4000,
        isClosable: true,
      });
    }
  }, [t, toast]);
  const deleteRunLabel = useMemo(() => {
    const run = runs.find((item) => item.training_run_id === deleteRunId);
    return run ? `#${run.attempt} · ${run.base_model || "?"} · ${run.training_run_id.slice(-12)}` : "";
  }, [runs, deleteRunId]);
  const handleRunDeleted = useCallback(async (deletedId: string) => {
    setSelectedRunIds((prev) => {
      if (!prev.has(deletedId)) return prev;
      const next = new Set(prev);
      next.delete(deletedId);
      return next;
    });
    setFocusRunId((prev) => (prev === deletedId ? null : prev));
    setBaselineRunId((prev) => (prev === deletedId ? null : prev));
    await loadRuns();
  }, [loadRuns]);

  // Keep polling only while something is actually in flight.
  useEffect(() => {
    if (!runs.some((run) => run.status === "running" || run.status === "queued")) return;
    const timer = setInterval(loadRuns, 3000);
    return () => clearInterval(timer);
  }, [runs, loadRuns]);

  const runColors = useMemo(() => assignRunColors([...selectedRunIds]), [selectedRunIds]);

  const toggleRunSelected = useCallback((runId: string) => {
    setSelectedRunIds((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  }, []);

  // Domyślnie zaznacz najlepszy ukończony run (mAP50-95); utrzymuj zaznaczenie przy
  // odświeżaniu listy (poll) i usuwaj runy, których już nie ma.
  useEffect(() => {
    setSelectedRunIds((prev) => {
      const valid = [...prev].filter((rid) => runs.some((r) => r.training_run_id === rid));
      if (valid.length > 0 && valid.length === prev.size) return prev;
      if (valid.length > 0) return new Set(valid);
      const completed = runs.filter((r) => r.status === "completed");
      if (completed.length === 0) return prev;
      const best = [...completed].sort(
        (a, b) => Number(b.metrics?.["mAP50-95"] ?? -1) - Number(a.metrics?.["mAP50-95"] ?? -1),
      )[0];
      return new Set([best.training_run_id]);
    });
  }, [runs]);

  // Focus domyślny tylko gdy pusty/nieistniejący (klik w nazwę może wskazać dowolny run).
  useEffect(() => {
    const exists = focusRunId && runs.some((r) => r.training_run_id === focusRunId);
    if (exists) return;
    setFocusRunId([...selectedRunIds][0] ?? null);
  }, [selectedRunIds, focusRunId, runs]);

  // Baseline zawsze wskazuje zaznaczony run (domyślnie najlepszy).
  useEffect(() => {
    if (baselineRunId && selectedRunIds.has(baselineRunId)) return;
    const best = [...selectedRunIds]
      .map((rid) => runs.find((r) => r.training_run_id === rid))
      .filter((r): r is api.TrainingRunSummary => !!r)
      .sort((a, b) => Number(b.metrics?.["mAP50-95"] ?? -1) - Number(a.metrics?.["mAP50-95"] ?? -1))[0];
    setBaselineRunId(best ? best.training_run_id : null);
  }, [selectedRunIds, baselineRunId, runs]);

  const selectedRuns = useMemo(
    () => [...selectedRunIds]
      .map((rid) => runs.find((r) => r.training_run_id === rid))
      .filter((r): r is api.TrainingRunSummary => !!r)
      .map((r) => {
        // Run w trakcie nie ma jeszcze metrics.json — pokaż metryki z ostatniej epoki.
        if (r.metrics && Object.keys(r.metrics).length > 0) return r;
        const live = liveMetricsFromHistory(historyByRun.get(r.training_run_id));
        return live ? { ...r, metrics: live } : r;
      }),
    [selectedRunIds, runs, historyByRun],
  );

  // Runy zaznaczone, które są w trakcie — po nich odświeżamy historię na żywo.
  const liveRunKey = useMemo(
    () => [...selectedRunIds]
      .filter((rid) => {
        const r = runs.find((x) => x.training_run_id === rid);
        return !!r && (r.status === "running" || r.status === "queued");
      })
      .sort()
      .join(","),
    [selectedRunIds, runs],
  );

  // Historia (krzywe per epoka) dla zaznaczonych runów — pobierana per run, cache w mapie.
  useEffect(() => {
    if (!id) return;
    const missing = [...selectedRunIds].filter((rid) => !historyByRun.has(rid));
    if (missing.length === 0) return;
    let cancelled = false;
    Promise.all(
      missing.map((rid) =>
        api.getTrainingHistory(id, rid).then((h) => [rid, h] as const).catch(() => null),
      ),
    ).then((results) => {
      if (cancelled) return;
      setHistoryByRun((prev) => {
        const next = new Map(prev);
        for (const entry of results) if (entry) next.set(entry[0], entry[1]);
        return next;
      });
    });
    return () => { cancelled = true; };
  }, [id, selectedRunIds, historyByRun]);

  // Monitoring „na żywo" (styl W&B): dopóki zaznaczony run jest w trakcie, odświeżaj jego
  // historię co 3 s — nakładane krzywe rosną, a metryki bieżące (z ostatniej epoki) też.
  useEffect(() => {
    if (!id || !liveRunKey) return;
    const ids = liveRunKey.split(",");
    let cancelled = false;
    const tick = async () => {
      const results = await Promise.all(
        ids.map((rid) =>
          api.getTrainingHistory(id, rid).then((h) => [rid, h] as const).catch(() => null),
        ),
      );
      if (cancelled) return;
      setHistoryByRun((prev) => {
        const next = new Map(prev);
        for (const entry of results) if (entry) next.set(entry[0], entry[1]);
        return next;
      });
    };
    tick();
    const timer = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [id, liveRunKey]);

  const loadModels = useCallback(async () => {
    if (!id) return;
    try {
      const registry = await api.getRegisteredModels(id);
      setModels(registry.models);
      setCurrentModelId(registry.current_model_id);
    } catch {
      setModels([]);
    }
  }, [id]);

  useEffect(() => { loadModels(); }, [loadModels]);

  const loadDetail = useCallback(async () => {
    if (!id || !focusRunId) {
      setDetail(null);
      setHistory(null);
      setConfusionMatrixUrl(null);
      setConfusionAnalysis(null);
      setEvaluation(null);
      setLineage(null);
      return;
    }
    const [run, evalStatus, runHistory, confusionBlob, confusionData] = await Promise.all([
      api.getTrainingRun(id, focusRunId).catch(() => null),
      api.getTestEvaluation(id, focusRunId).catch(() => null),
      api.getTrainingHistory(id, focusRunId).catch(() => null),
      api.getTrainingConfusionMatrix(id, focusRunId).catch(() => null),
      api.getTrainingConfusionAnalysis(id, focusRunId).catch(() => null),
    ]);
    setDetail(run);
    setHistory(runHistory);
    setConfusionAnalysis(confusionData);
    setConfusionMatrixUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return confusionBlob ? URL.createObjectURL(confusionBlob) : null;
    });
    setEvaluation(evalStatus);
    const registered = models.find((model) => model.model_id === focusRunId);
    setLineage(registered ? await api.getModelLineage(id, focusRunId).catch(() => null) : null);
  }, [id, focusRunId, models]);

  useEffect(() => { loadDetail(); }, [loadDetail]);

  useEffect(() => () => {
    if (confusionMatrixUrl) URL.revokeObjectURL(confusionMatrixUrl);
  }, [confusionMatrixUrl]);

  const runTestEvaluation = async () => {
    if (!id || !focusRunId) return;
    // The wording carries the reason, not just the warning: this is the one number
    // that must not be optimised against.
    if (!window.confirm(t("testEvalConfirm"))) return;
    setBusy("eval");
    try {
      await api.startTestEvaluation(id, focusRunId);
      const deadline = Date.now() + 120000;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const status = await api.getTestEvaluation(id, focusRunId);
        if (status.evaluated || status.error) {
          setEvaluation(status);
          break;
        }
      }
    } catch (err: any) {
      toast({
        title: t("Test evaluation failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
        duration: 10000,
      });
    } finally {
      setBusy(null);
    }
  };

  const registerAndPromote = async (promote: boolean) => {
    if (!id || !focusRunId) return;
    setBusy(promote ? "promote" : "register");
    try {
      await api.registerModel(id, focusRunId);
      if (promote) {
        await api.promoteModel(id, focusRunId);
        toast({ title: t("Model promoted"), description: t("It is now the project prediction model."), status: "success" });
      } else {
        toast({ title: t("Model registered"), status: "success" });
      }
      await loadModels();
    } catch (err: any) {
      toast({
        title: promote ? t("Could not promote the model") : t("Could not register the model"),
        description: err.response?.data?.detail || err.message,
        status: "error",
        duration: 12000,
        isClosable: true,
      });
    } finally {
      setBusy(null);
    }
  };

  const selectedDataset = datasetRuns.find((run) => run.run_id === datasetRunId);

  const epochChart = useMemo(() => {
    const rows = history?.rows || [];
    const columns = history?.columns || [];
    const epochKey = columns.find((column) => column.trim().toLowerCase() === "epoch");
    const numericSeries = (keys: string[]) => keys.map((key) => ({
      name: key.replace(/^train\//, "").replace(/^val\//, ""),
      data: rows
        .filter((row) => typeof row[key] === "number")
        .map((row, index) => ({ x: Number(row[epochKey || ""] ?? index + 1), y: Number(row[key]) })),
    }));
    const lossKeys = columns.filter((column) => column.toLowerCase().includes("loss"));
    const metricKeys = columns.filter((column) => {
      const key = column.toLowerCase();
      return key.startsWith("metrics/") || key.includes("map50") || key.includes("precision") || key.includes("recall");
    });
    return { loss: numericSeries(lossKeys), metrics: numericSeries(metricKeys) };
  }, [history]);

  const perClassMetrics = useMemo(() => (
    Object.entries(detail?.metrics_detail?.per_class || {})
  ), [detail]);

  return (
    <VStack align="stretch" spacing={5} p={2}>
      <Card>
        <VStack align="stretch" spacing={2}>
          <HStack flexWrap="wrap" spacing={3}>
            <Text fontWeight="bold" minW="90px">{t("Dataset")}</Text>
            <Select
              size="sm"
              maxW="460px"
              value={datasetRunId}
              onChange={(event) => {
                setDatasetRunId(event.target.value);
                setSelectedRunIds(new Set());
                setFocusRunId(null);
                setBaselineRunId(null);
              }}
            >
              {datasetRuns.length === 0 && <option value="">{t("No completed dataset runs")}</option>}
              {datasetRuns.map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.publication_label ? `${run.publication_label} · ` : ""}
                  {run.total_tiles} {t("Tiles").toLowerCase()} · {run.split_mode} · {t("Seed").toLowerCase()} {run.split_seed}
                </option>
              ))}
            </Select>
            {selectedDataset?.publication_status === "published" && (
              <Badge colorScheme="green">{t("published")}</Badge>
            )}
          </HStack>
          {/* The mandatory selector is the barrier that keeps comparison honest: with
              one dataset in view at a time, an incomparable comparison is unexpressible. */}
          <Text fontSize="xs" color={mutedColor}>
            {t("Models are only comparable within one dataset - the test split must be identical.")}
          </Text>
        </VStack>
      </Card>

      {runs.length === 0 ? (
        <Alert status="info" borderRadius="md">
          <AlertIcon />
          {t("No training runs for this dataset yet.")}
        </Alert>
      ) : (
        <Grid
          templateColumns={{ base: "1fr", lg: railOpen ? "minmax(0, 1fr) 340px" : "minmax(0, 1fr) auto" }}
          gap={5}
          alignItems="start"
        >
          <GridItem
            order={{ base: 0, lg: 2 }}
            alignSelf="start"
            position={{ base: "static", lg: "sticky" }}
            top={2}
          >
            {railOpen ? (
              <RunsRail
                runs={runs}
                selectedRunIds={selectedRunIds}
                focusRunId={focusRunId}
                baselineRunId={baselineRunId}
                runColors={runColors}
                onToggleSelect={toggleRunSelected}
                onSetFocus={setFocusRunId}
                onSetBaseline={setBaselineRunId}
                onDeleteRun={openDeleteRun}
                onCollapse={() => setRailOpen(false)}
              />
            ) : (
              <Button
                size="sm"
                variant="outline"
                leftIcon={<Icon as={MdChevronLeft} />}
                onClick={() => setRailOpen(true)}
              >
                {t("Training runs")} ({runs.length})
              </Button>
            )}
          </GridItem>

          <GridItem order={{ base: 1, lg: 1 }} minW={0}>
            <VStack align="stretch" spacing={5}>
              {selectedRuns.length > 0 && (
                <Accordion allowMultiple defaultIndex={[0, 1]}>
                  <AccordionItem border="none">
                    <HStack spacing={1}>
                      <AccordionButton px={0} flex="1">
                        <Box as="span" flex="1" textAlign="left" fontWeight="bold">{t("Compare")}</Box>
                        <AccordionIcon />
                      </AccordionButton>
                      <InfoPopover titleKey="info.results.compare.title" bodyKey="info.results.compare.body" helpPage="training/wyniki.html" />
                    </HStack>
                    <AccordionPanel px={0} pb={4}>
                      <Card>
                        <VStack align="stretch" spacing={4}>
                          <CompareScorecard
                            runs={selectedRuns}
                            baselineRunId={baselineRunId}
                            runColors={runColors}
                          />
                          <SimpleGrid columns={{ base: 1, lg: 2 }} spacing={4}>
                            <CompareRadar runs={selectedRuns} runColors={runColors} />
                            <ClassRadar runs={selectedRuns} runColors={runColors} />
                          </SimpleGrid>
                        </VStack>
                      </Card>
                    </AccordionPanel>
                  </AccordionItem>
                  <AccordionItem border="none">
                    <HStack spacing={1}>
                      <AccordionButton px={0} flex="1">
                        <Box as="span" flex="1" textAlign="left" fontWeight="bold">{t("Training curves")}</Box>
                        <AccordionIcon />
                      </AccordionButton>
                      <InfoPopover titleKey="info.results.trainingCurves.title" bodyKey="info.results.trainingCurves.body" helpPage="training/wyniki.html" />
                    </HStack>
                    <AccordionPanel px={0} pb={4}>
                      <Card>
                        <OverlayCurves
                          runs={selectedRuns}
                          historyByRun={historyByRun}
                          runColors={runColors}
                        />
                      </Card>
                    </AccordionPanel>
                  </AccordionItem>
                </Accordion>
              )}

              {detail && (
        <Card>
          <VStack align="stretch" spacing={3}>
            <HStack justify="space-between" flexWrap="wrap" gap={2}>
              <Text fontWeight="bold">{detail.base_model} · #{detail.attempt}</Text>
              <HStack spacing={2}>
                {api.isTauriRuntime() && detail.run_dir && (
                  <Button
                    size="xs"
                    variant="outline"
                    leftIcon={<Icon as={MdFolderOpen} />}
                    onClick={() => openRunFolder(detail.run_dir as string)}
                  >
                    {t("Open folder")}
                  </Button>
                )}
                <Badge colorScheme={STATUS_COLORS[detail.status] || "gray"}>{t(`trainingStatus.${detail.status}`)}</Badge>
              </HStack>
            </HStack>

            {detail.error && (
              <Alert status="error" borderRadius="md">
                <AlertIcon />
                {detail.error}
              </Alert>
            )}

            <SimpleGrid columns={{ base: 2, md: 4 }} gap={3}>
              {Object.entries(detail.metrics || {}).map(([key, value]) => (
                <Box key={key}>
                  <Text fontSize="xs" color={mutedColor}>{key}</Text>
                  <Text fontWeight="bold">{formatMetric(value as number)}</Text>
                </Box>
              ))}
            </SimpleGrid>

            {(epochChart.loss.length > 0 || epochChart.metrics.length > 0) && (
              <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
                {epochChart.loss.length > 0 && (
                  <Box borderWidth="1px" borderRadius="md" p={3} minW={0}>
                    <HStack justify="space-between" mb={2}>
                      <Text fontSize="sm" fontWeight="600">{t("Loss by epoch")}</Text>
                      <CopyChartButton chartId="detail-loss" />
                    </HStack>
                    <Chart
                      type="line"
                      height={280}
                      options={{
                        chart: { id: "detail-loss", toolbar: { show: false }, zoom: { enabled: false }, background: chartBg, foreColor: chartForeColor },
                        theme: { mode: chartMode },
                        stroke: { width: 2, curve: "straight" },
                        markers: { size: 0 },
                        grid: { borderColor: gridColor },
                        xaxis: { type: "numeric", title: { text: t("Epoch") } },
                        yaxis: { decimalsInFloat: 4 },
                        legend: { position: "top" },
                        tooltip: { theme: chartMode, shared: true },
                      }}
                      series={epochChart.loss}
                    />
                  </Box>
                )}
                {epochChart.metrics.length > 0 && (
                  <Box borderWidth="1px" borderRadius="md" p={3} minW={0}>
                    <HStack justify="space-between" mb={2}>
                      <Text fontSize="sm" fontWeight="600">{t("Validation metrics by epoch")}</Text>
                      <CopyChartButton chartId="detail-metrics" />
                    </HStack>
                    <Chart
                      type="line"
                      height={280}
                      options={{
                        chart: { id: "detail-metrics", toolbar: { show: false }, zoom: { enabled: false }, background: chartBg, foreColor: chartForeColor },
                        theme: { mode: chartMode },
                        stroke: { width: 2, curve: "straight" },
                        markers: { size: 0 },
                        grid: { borderColor: gridColor },
                        xaxis: { type: "numeric", title: { text: t("Epoch") } },
                        yaxis: { min: 0, max: 1, decimalsInFloat: 3 },
                        legend: { position: "top" },
                        tooltip: { theme: chartMode, shared: true },
                      }}
                      series={epochChart.metrics}
                    />
                  </Box>
                )}
              </SimpleGrid>
            )}

            {(perClassMetrics.length > 0 || confusionAnalysis) && (
              <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
                {perClassMetrics.length > 0 && (
                  <Box borderWidth="1px" borderRadius="md" p={3} overflowX="auto">
                    <HStack spacing={1} mb={2}>
                      <Text fontSize="sm" fontWeight="600">{t("Metrics per class")}</Text>
                      <InfoPopover titleKey="info.results.perClass.title" bodyKey="info.results.perClass.body" helpPage="training/wyniki.html" />
                    </HStack>
                    <Table size="sm">
                      <Thead><Tr><Th>{t("Class")}</Th><Th isNumeric>{t("Precision")}</Th><Th isNumeric>{t("Recall")}</Th><Th isNumeric>mAP50</Th><Th isNumeric>mAP50-95</Th></Tr></Thead>
                      <Tbody>
                        {perClassMetrics.map(([className, metrics]) => (
                          <Tr key={className}>
                            <Td>{className}</Td>
                            <Td isNumeric>{formatMetric(metrics.precision)}</Td>
                            <Td isNumeric>{formatMetric(metrics.recall)}</Td>
                            <Td isNumeric>{formatMetric(metrics.mAP50)}</Td>
                            <Td isNumeric>{formatMetric(metrics["mAP50-95"])}</Td>
                          </Tr>
                        ))}
                      </Tbody>
                    </Table>
                  </Box>
                )}
                {confusionAnalysis && confusionAnalysis.matrix.length > 0 && (
                  <Box minW={0}>
                    <HStack spacing={1} mb={2} justify="flex-end">
                      <InfoPopover titleKey="info.results.confusionMatrix.title" bodyKey="info.results.confusionMatrix.body" helpPage="training/wyniki.html" />
                      {confusionMatrixUrl && (
                        <Button
                          size="xs"
                          variant="outline"
                          leftIcon={<MdDownload />}
                          onClick={() => {
                            const a = document.createElement("a");
                            a.href = confusionMatrixUrl;
                            a.download = "confusion_matrix.png";
                            a.click();
                          }}
                        >
                          {t("Download PNG")}
                        </Button>
                      )}
                    </HStack>
                    <ConfusionMatrix data={confusionAnalysis} />
                  </Box>
                )}
              </SimpleGrid>
            )}

            {confusionAnalysis && confusionAnalysis.class_names.length > 1 && (
              <ConfusionSimilarityMatrix data={confusionAnalysis} />
            )}

            <Divider />
            <HStack spacing={1}>
              <Text fontSize="sm" fontWeight="600">{t("Reproduction context")}</Text>
              <InfoPopover titleKey="info.results.reproduction.title" bodyKey="info.results.reproduction.body" helpPage="training/wyniki.html" />
            </HStack>
            <SimpleGrid columns={{ base: 1, md: 2 }} gap={2}>
              {([
                ["dataset_run_id", t("Dataset")],
                ["dataset_split_seed", t("Split seed")],
                ["base_model_sha256", t("Base weights SHA-256")],
                ["seed", t("Seed")],
                ["imgsz", t("Image size")],
                ["batch", t("Batch")],
                ["torch", "torch"],
                ["ultralytics", "ultralytics"],
                ["cuda", "CUDA"],
                ["app_version", t("Version")],
              ] as const).map(([key, label]) => (
                <HStack key={key} align="start">
                  <Text fontSize="xs" color={mutedColor} minW="150px">{label}</Text>
                  <Text fontSize="xs" fontFamily="mono" noOfLines={1}>
                    {String((detail.manifest ?? detail.job)?.[key] ?? "-")}
                  </Text>
                </HStack>
              ))}
            </SimpleGrid>

            {!detail.manifest && (
              <Alert status="warning" borderRadius="md">
                <AlertIcon />
                {t("This run has no manifest - it did not finish, so its results are not trustworthy.")}
              </Alert>
            )}

            {detail.manifest && (
              <>
                <Divider />
                <HStack justify="space-between" flexWrap="wrap">
                  <HStack spacing={1}>
                    <Text fontSize="sm" fontWeight="600">{t("Final test evaluation")}</Text>
                    <InfoPopover titleKey="info.results.finalTest.title" bodyKey="info.results.finalTest.body" helpPage="training/wyniki.html" />
                  </HStack>
                  {currentModelId === detail.training_run_id && (
                    <Badge colorScheme="green">{t("project model")}</Badge>
                  )}
                </HStack>

                {evaluation?.evaluated && evaluation.evaluation ? (
                  <Box borderWidth="1px" borderColor="green.400" borderRadius="md" p={3}>
                    <HStack mb={2}>
                      <Badge colorScheme="green">{t("final")}</Badge>
                      <Text fontSize="xs" color={mutedColor}>{evaluation.evaluation.evaluated_at}</Text>
                    </HStack>
                    <SimpleGrid columns={{ base: 2, md: 4 }} gap={3}>
                      {Object.entries(evaluation.evaluation.metrics).map(([key, value]) => (
                        <Box key={key}>
                          <Text fontSize="xs" color={mutedColor}>{key}</Text>
                          <Text fontWeight="bold">{formatMetric(value)}</Text>
                        </Box>
                      ))}
                    </SimpleGrid>
                  </Box>
                ) : (
                  <VStack align="stretch" spacing={2}>
                    {evaluation?.error && (
                      <Alert status="error" borderRadius="md">
                        <AlertIcon />{evaluation.error}
                      </Alert>
                    )}
                    <Text fontSize="xs" color={mutedColor}>
                      {t("Evaluating on the test split is allowed once per run. Selecting a model by repeatedly looking at the test set inflates the estimate.")}
                    </Text>
                    <Button
                      size="sm"
                      variant="outline"
                      alignSelf="flex-start"
                      isLoading={busy === "eval"}
                      loadingText={t("Evaluating")}
                      onClick={runTestEvaluation}
                    >
                      {t("Evaluate on test set")}
                    </Button>
                  </VStack>
                )}

                <HStack flexWrap="wrap">
                  <HStack spacing={1}>
                    <Button
                      size="sm"
                      variant="outline"
                      isLoading={busy === "register"}
                      isDisabled={models.some((model) => model.model_id === detail.training_run_id)}
                      onClick={() => registerAndPromote(false)}
                    >
                      {models.some((model) => model.model_id === detail.training_run_id)
                        ? t("Registered")
                        : t("Register model")}
                    </Button>
                    <InfoPopover titleKey="info.results.registerModel.title" bodyKey="info.results.registerModel.body" helpPage="training/wyniki.html" />
                  </HStack>
                  <HStack spacing={1}>
                    <Button
                      size="sm"
                      colorScheme="brand"
                      isLoading={busy === "promote"}
                      isDisabled={currentModelId === detail.training_run_id}
                      onClick={() => registerAndPromote(true)}
                    >
                      {t("Promote to project model")}
                    </Button>
                    <InfoPopover titleKey="info.results.promoteModel.title" bodyKey="info.results.promoteModel.body" helpPage="training/wyniki.html" />
                  </HStack>
                </HStack>

                {lineage && (
                  <Box borderWidth="1px" borderRadius="md" p={3}>
                    <HStack spacing={1} mb={2}>
                      <Text fontSize="sm" fontWeight="600">{t("Lineage")}</Text>
                      <InfoPopover titleKey="info.results.lineage.title" bodyKey="info.results.lineage.body" helpPage="training/wyniki.html" />
                    </HStack>
                    <Text fontSize="xs" color={mutedColor} mb={2}>
                      {t("lineageSummary", {
                        annotations: lineage.annotation_count,
                        scenes: lineage.scene_count,
                        dataset: lineage.dataset_run.label || lineage.dataset_run.dataset_run_id,
                      })}
                    </Text>
                    <SimpleGrid columns={{ base: 1, md: 2 }} gap={2}>
                      <Box>
                        <Text fontSize="xs" fontWeight="600">{t("Annotation authors")}</Text>
                        {Object.entries(lineage.authors).map(([author, count]) => (
                          <Text key={author} fontSize="xs" color={mutedColor}>{author}: {count}</Text>
                        ))}
                      </Box>
                      <Box>
                        <Text fontSize="xs" fontWeight="600">{t("Annotation sources")}</Text>
                        {Object.entries(lineage.annotation_sources).map(([source, count]) => (
                          <Text key={source} fontSize="xs" color={mutedColor}>{source}: {count}</Text>
                        ))}
                      </Box>
                    </SimpleGrid>
                    {lineage.truncated && (
                      <Text fontSize="xs" color="orange.400" mt={2}>
                        {t("The chain could not be followed down to individual annotations - the dataset or its tile catalog is no longer available.")}
                      </Text>
                    )}
                  </Box>
                )}
              </>
            )}

            <Code fontSize="xs" p={2} borderRadius="md" whiteSpace="pre-wrap">
              {detail.training_run_id}
            </Code>
          </VStack>
        </Card>
              )}
            </VStack>
          </GridItem>
        </Grid>
      )}

      <DeleteRunDialog
        isOpen={deleteDisc.isOpen}
        onClose={deleteDisc.onClose}
        kind="training"
        projectId={id}
        runId={deleteRunId}
        runLabel={deleteRunLabel}
        onDeleted={handleRunDeleted}
      />
    </VStack>
  );
}
