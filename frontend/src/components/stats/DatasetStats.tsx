import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  HStack,
  Input,
  Select,
  SimpleGrid,
  Spinner,
  Table,
  TableContainer,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  Wrap,
  WrapItem,
  useColorModeValue,
  useDisclosure,
  useToast,
} from "@chakra-ui/react";
import Chart from "react-apexcharts";
import { MdDownload } from "react-icons/md";
import { useMemo, useState, type ReactNode } from "react";
import MiniStatistics from "../common/MiniStatistics";
import Card from "../common/Card";
import InfoPopover from "../common/InfoPopover";
import CopyChartButton from "../results/CopyChartButton";
import CoOccurrenceMatrix from "./CoOccurrenceMatrix";
import type { AcquisitionStats, CoOccurrence, DatasetStats as Stats, GeometryStats, MetaDist, SplitHistogram } from "../../types";
import { useTranslation } from "react-i18next";
import { translateDatasetIssue } from "../../utils/datasetMessages";
import * as api from "../../api/client";
import { isTauriRuntime } from "../../desktop/dialogs";

interface Props {
  stats: Stats | null;
  loading?: boolean;
  projectId?: string;
}

// Powyzej tego progu widok przelacza sie w tryb "wiele klas": wykresy klasowe na pelna
// szerokosc i poziome, ostrzezenia i tabela klas ze zwijaniem/filtrem.
const MANY_CLASSES_THRESHOLD = 25;
const CLASS_LIST_PREVIEW = 8;

type ClassRow = {
  class_id: number;
  name: string;
  source_annotations: number;
  dataset_annotations: number;
  train: number;
  val: number;
  test: number;
  used: boolean;
  source_only: boolean;
  tiles?: number;
  scenes?: number;
  share_pct?: number;
  rel_to_largest?: number;
  train_pct?: number;
  val_pct?: number;
  test_pct?: number;
  missing_in_splits?: string[];
};

export default function DatasetStatsPanel({ stats, loading, projectId }: Props) {
  const { t } = useTranslation();
  const toast = useToast();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.500");
  // Tło eksportu PNG: solidne (motyw), by treść była czytelna na jasnym i ciemnym po wklejeniu.
  const chartExportBg = useColorModeValue("#FFFFFF", "#242428");
  const chartTextColor = useColorModeValue("#1B2559", "#D6D6D8");
  const chartMutedTextColor = useColorModeValue("#707EAE", "#A7A7AD");
  const gridColor = useColorModeValue("#E9EDF7", "rgba(255, 255, 255, 0.08)");
  const chartMode = useColorModeValue("light", "dark") as "light" | "dark";
  const tooltipTheme = useColorModeValue("light", "dark") as "light" | "dark";

  if (!stats || stats.total_tiles === 0) {
    return (
      <Card>
        {loading ? (
          <HStack spacing={3} py={2}>
            <Spinner size="sm" color="brand.500" />
            <Text color="secondaryGray.600" fontSize="sm">
              {t("Loading dataset statistics…")}
            </Text>
          </HStack>
        ) : (
          <Text color="secondaryGray.600" fontSize="sm">
            {t("No dataset stats yet. Generate a dataset first.")}
          </Text>
        )}
      </Card>
    );
  }

  const classRows = stats.class_stats || legacyClassRows(stats);
  const splitRows = stats.split_stats || legacySplitRows(stats);
  const sceneRows = stats.scene_stats || [];
  const tileSummary = stats.tile_summary || {
    total_all: stats.total_tiles,
    active: stats.total_tiles,
    reviewed: 0,
    reviewed_empty: stats.negative_tiles,
    unreviewed: 0,
    used: stats.total_tiles,
    positive: stats.positive_tiles,
    negative: stats.negative_tiles,
    excluded: 0,
    omitted: 0,
  };

  const handleExportCsv = async (section: "classes" | "geometry" | "cooccurrence" | "acquisition" = "classes") => {
    if (!projectId) return;
    const runId = stats.run_id || undefined;
    try {
      if (!isTauriRuntime()) {
        window.open(api.datasetStatsExportUrl(projectId, runId, section), "_blank");
        return;
      }
      const { save } = await import("@tauri-apps/plugin-dialog");
      const selected = await save({
        title: t("Export class statistics CSV"),
        defaultPath: `GeoTileLabel_${section}_stats_${runId || "latest"}.csv`,
        filters: [{ name: "CSV", extensions: ["csv"] }],
      });
      if (!selected) return;
      const res = await api.saveDatasetStats(projectId, selected, runId, section);
      toast({ title: t("Statistics CSV saved"), description: res.output_path, status: "success", duration: 3000 });
    } catch (error: any) {
      toast({
        title: t("Cannot save statistics CSV"),
        description: error?.response?.data?.detail || error?.message,
        status: "error",
      });
    }
  };

  const classNames = classRows.map((row) => row.name);
  const classDatasetCounts = classRows.map((row) => row.dataset_annotations);
  const sceneNames = sceneRows.map((row) => row.filename);
  const sceneTileCounts = sceneRows.map((row) => row.used_tiles);
  const sceneAnnotationCounts = sceneRows.map((row) => row.annotations);
  const chartBase = {
    chart: {
      toolbar: { show: false },
      foreColor: chartTextColor,
      // Solidne tło trafia do eksportu PNG (na ekranie CSS wymusza przezroczystość nad kartą).
      background: chartExportBg,
    },
    dataLabels: { enabled: false },
    grid: { borderColor: gridColor, strokeDashArray: 3 },
    legend: {
      labels: { colors: chartTextColor },
    },
    xaxis: {
      labels: { style: { colors: chartMutedTextColor, fontSize: "12px", fontWeight: 600 } },
      axisBorder: { color: chartTextColor },
      axisTicks: { color: chartTextColor },
      title: { style: { color: chartTextColor } },
    },
    yaxis: {
      labels: { style: { colors: chartTextColor, fontSize: "12px", fontWeight: 600 } },
      axisBorder: { color: chartTextColor },
      axisTicks: { color: chartTextColor },
      title: { style: { color: chartTextColor } },
    },
    theme: { mode: chartMode },
    tooltip: { theme: tooltipTheme },
  };

  // Przy wielu klasach wykresy klasowe robimy poziome (nazwy na osi Y, pełne), rosnące
  // wysokoscia i przewijane; a uklad przenosi je na pelna szerokosc (jeden pod drugim).
  const manyClasses = classRows.length > MANY_CLASSES_THRESHOLD;
  const classChartHeight = manyClasses ? Math.max(320, classRows.length * 22) : 260;

  const classBarOptions = (colors: string[], stacked: boolean) => {
    const shared = {
      ...chartBase,
      chart: { ...chartBase.chart, stacked },
      colors,
      tooltip: {
        ...chartBase.tooltip,
        x: { formatter: (_value: number, opts: any) => classNames[opts.dataPointIndex] || String(_value) },
      },
    };
    if (manyClasses) {
      return {
        ...shared,
        plotOptions: { bar: { horizontal: true, barHeight: "75%" } },
        xaxis: { ...chartBase.xaxis, categories: classNames },
        yaxis: {
          ...chartBase.yaxis,
          labels: {
            ...chartBase.yaxis.labels,
            maxWidth: 220,
            formatter: (value: string | number) => abbreviateChartLabel(String(value), 30),
          },
        },
      };
    }
    return {
      ...shared,
      xaxis: {
        ...chartBase.xaxis,
        categories: classNames,
        labels: { ...chartBase.xaxis.labels, formatter: (value: string) => abbreviateChartLabel(value, 18) },
      },
      yaxis: { ...chartBase.yaxis, min: 0, forceNiceScale: true },
    };
  };

  const withId = (options: any, id: string) => ({ ...options, chart: { ...(options.chart || {}), id } });

  const annotationsPerClassChart = (
    <ChartCard
      title={t("Annotations per class")}
      chartId="ds-ann-per-class"
      infoTitleKey="info.statistics.annotationsPerClass.title"
      infoBodyKey="info.statistics.annotationsPerClass.body"
    >
      <ScrollableChart scrollable={manyClasses}>
        <Chart
          type="bar"
          height={classChartHeight}
          options={withId(classBarOptions(["#9B6CFF"], false), "ds-ann-per-class")}
          series={[{ name: t("Annotations"), data: classDatasetCounts }]}
        />
      </ScrollableChart>
    </ChartCard>
  );

  const classPerSplitChart = (
    <ChartCard
      title={t("Class distribution per split")}
      chartId="ds-class-per-split"
      infoTitleKey="info.statistics.classPerSplit.title"
      infoBodyKey="info.statistics.classPerSplit.body"
    >
      <ScrollableChart scrollable={manyClasses}>
        <Chart
          type="bar"
          height={classChartHeight}
          options={withId(classBarOptions(["#9B6CFF", "#75A7F7", "#E2B35F"], true), "ds-class-per-split")}
          series={[
            { name: t("Train"), data: classRows.map((row) => row.train) },
            { name: t("Val"), data: classRows.map((row) => row.val) },
            { name: t("Test"), data: classRows.map((row) => row.test) },
          ]}
        />
      </ScrollableChart>
    </ChartCard>
  );

  const tileUsageChart = (
    <ChartCard
      title={t("Tile usage")}
      chartId="ds-tile-usage"
      infoTitleKey="info.statistics.tileUsage.title"
      infoBodyKey="info.statistics.tileUsage.body"
    >
      <Chart
        type="donut"
        height={260}
        options={{
          ...chartBase,
          chart: { ...chartBase.chart, id: "ds-tile-usage" },
          labels: [t("Positive"), t("Used checked empty"), t("Excluded"), t("Omitted / unchecked")],
          colors: ["#9B6CFF", "#75A7F7", "#EB7078", "#8B8B8F"],
          legend: { ...chartBase.legend, position: "bottom" },
        }}
        series={[
          tileSummary.positive,
          tileSummary.negative,
          tileSummary.excluded,
          tileSummary.omitted,
        ]}
      />
    </ChartCard>
  );

  const scenePerTileChart = (
    <ChartCard
      title={t("Tiles and annotations per scene")}
      chartId="ds-scene-tiles"
      infoTitleKey="info.statistics.scenePerScene.title"
      infoBodyKey="info.statistics.scenePerScene.body"
    >
      <Chart
        type="bar"
        height={260}
        options={{
          ...chartBase,
          chart: { ...chartBase.chart, id: "ds-scene-tiles" },
          xaxis: {
            ...chartBase.xaxis,
            categories: sceneNames,
            labels: {
              ...chartBase.xaxis.labels,
              formatter: (value: string) => abbreviateChartLabel(value, 24),
            },
          },
          yaxis: { ...chartBase.yaxis, min: 0, forceNiceScale: true },
          colors: ["#9B6CFF", "#75A7F7"],
          tooltip: {
            ...chartBase.tooltip,
            x: { formatter: (_value: number, opts: any) => sceneNames[opts.dataPointIndex] || String(_value) },
          },
        }}
        series={[
          { name: t("Used tiles"), data: sceneTileCounts },
          { name: t("Source annotations"), data: sceneAnnotationCounts },
        ]}
      />
    </ChartCard>
  );

  const geo = stats.geometry_stats;
  const histCategories = (hist?: { bins: number[] }) =>
    (hist?.bins || []).slice(0, -1).map((b) => (b >= 1000 ? `${Math.round(b / 1000)}k` : `${Math.round(b)}`));
  const histChart = (id: string, title: string, hist: { bins: number[]; counts: number[] } | undefined | null, color: string, xLabel: string) => {
    if (!hist || !hist.counts?.length) return null;
    return (
      <ChartCard title={title} chartId={id}>
        <Chart
          type="bar"
          height={240}
          options={withId({
            ...chartBase,
            colors: [color],
            plotOptions: { bar: { columnWidth: "92%" } },
            xaxis: {
              ...chartBase.xaxis,
              categories: histCategories(hist),
              title: { text: xLabel, style: { color: chartTextColor } },
              tickAmount: Math.min(8, hist.counts.length),
            },
            yaxis: { ...chartBase.yaxis, min: 0, forceNiceScale: true },
          }, id)}
          series={[{ name: t("Count"), data: hist.counts }]}
        />
      </ChartCard>
    );
  };
  const geometrySection = geo && geo.total_annotations > 0 && (
    <Card>
      <HStack spacing={1} mb={3}>
        <Text fontWeight="bold" color={textColor}>{t("Object geometry")}</Text>
        <InfoPopover titleKey="info.statistics.geometry.title" bodyKey="info.statistics.geometry.body" />
        <Badge colorScheme={geo.mode === "rotated_bbox" ? "purple" : "gray"}>
          {geo.mode === "rotated_bbox" ? "OBB" : "AABB"}
        </Badge>
        <Text fontSize="xs" color={mutedColor}>
          {t("tile")} {geo.tile_size}px · {t("GSD coverage")} {Math.round((geo.gsd_coverage_frac || 0) * 100)}%
        </Text>
        {projectId && (
          <Button ml="auto" size="xs" leftIcon={<MdDownload />} variant="outline" onClick={() => handleExportCsv("geometry")}>
            {t("Export CSV")}
          </Button>
        )}
      </HStack>
      <GeometryClassTable geo={geo} />
      <SimpleGrid columns={{ base: 1, xl: geo.angle_hist ? 3 : 2 }} gap={4} mt={4}>
        {histChart("geo-area", t("Object area (px²)"), geo.area_px_hist, "#9B6CFF", t("Area (px²)"))}
        {histChart("geo-aspect", t("Aspect ratio (w/h)"), geo.aspect_hist, "#75A7F7", t("Aspect ratio"))}
        {geo.angle_hist && histChart("geo-angle", t("OBB angle (°)"), geo.angle_hist, "#E2B35F", t("Angle (°)"))}
      </SimpleGrid>
    </Card>
  );

  return (
    <VStack align="stretch" spacing={4}>
      <SimpleGrid columns={{ base: 2, md: 4 }} gap={4}>
        <MiniStatistics label={t("Total Tiles")} value={stats.total_tiles} />
        <MiniStatistics label={t("Positive")} value={stats.positive_tiles} />
        <MiniStatistics label={t("Negative")} value={stats.negative_tiles} />
        <MiniStatistics label={t("Annotations")} value={stats.total_annotations} />
        <MiniStatistics label={t("Reviewed cells")} value={tileSummary.reviewed ?? 0} />
        <MiniStatistics label={t("Reviewed empty candidates")} value={tileSummary.reviewed_empty ?? stats.negative_tiles} />
        <MiniStatistics label={t("Unreviewed cells")} value={tileSummary.unreviewed ?? 0} />
        <MiniStatistics label={t("Excluded")} value={tileSummary.excluded} />
      </SimpleGrid>

      <Card>
        <HStack justify="space-between" align="start" mb={4}>
          <Box>
            <HStack>
              <Text fontWeight="bold" color={textColor}>
                {t("Dataset Statistics")}
              </Text>
              {stats.validation_report?.status && (
                <Badge colorScheme={
                  stats.validation_report.status === "ok"
                    ? "green"
                    : stats.validation_report.status === "error"
                      ? "red"
                      : "orange"
                }>
                  {t("Split")}: {t(stats.validation_report.status)}
                </Badge>
              )}
            </HStack>
            <Text fontSize="xs" color={mutedColor}>
              {t("Split")}: {stats.split_mode || "random_tile"} · {t("Seed")}: {stats.split_seed ?? 42}
            </Text>
            {stats.preprocessing_profile_id && (
              <Text
                fontSize="xs"
                color={mutedColor}
                title={stats.preprocessing_profile_hash || undefined}
              >
                {t("Preprocessing")}: {stats.preprocessing_profile_id}
                {stats.preprocessing_profile_hash ? ` · ${stats.preprocessing_profile_hash.slice(0, 12)}` : ""}
              </Text>
            )}
          </Box>
          <VStack align="end" spacing={2} maxW="50%">
            {projectId && (
              <Button size="xs" leftIcon={<MdDownload />} variant="outline" colorScheme="brand" onClick={() => handleExportCsv("classes")}>
                {t("Export CSV")}
              </Button>
            )}
            {stats.dataset_dir && (
              <Text fontSize="xs" color={mutedColor} textAlign="right" noOfLines={2}>
                {stats.dataset_dir}
              </Text>
            )}
          </VStack>
        </HStack>

        <Warnings stats={stats} />

        <VStack align="stretch" spacing={4} mt={4}>
          {/* Wiele klas: wykresy klasowe na pelna szerokosc, jeden pod drugim. */}
          {manyClasses && (
            <VStack align="stretch" spacing={4}>
              {annotationsPerClassChart}
              {classPerSplitChart}
            </VStack>
          )}
          <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
            {!manyClasses && annotationsPerClassChart}
            {!manyClasses && classPerSplitChart}
            {tileUsageChart}
            {scenePerTileChart}
          </SimpleGrid>
        </VStack>
      </Card>

      <ClassTable rows={classRows} largest={stats.largest_class_count || 0} />

      <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
        <StatsTable
          title={t("Splits")}
          infoTitleKey="info.statistics.splitsTable.title"
          infoBodyKey="info.statistics.splitsTable.body"
        >
          <Thead>
            <Tr>
              <Th>{t("Split")}</Th>
              <Th isNumeric>{t("Images")}</Th>
              <Th isNumeric>{t("Positive")}</Th>
              <Th isNumeric>{t("Used checked empty")}</Th>
              <Th isNumeric>{t("Annotations")}</Th>
            </Tr>
          </Thead>
          <Tbody>
            {splitRows.map((row) => (
              <Tr key={row.split}>
                <Td>{t(row.split)}</Td>
                <Td isNumeric>{row.images}</Td>
                <Td isNumeric>{row.positive_tiles}</Td>
                <Td isNumeric>{row.negative_tiles}</Td>
                <Td isNumeric>{row.annotations}</Td>
              </Tr>
            ))}
          </Tbody>
        </StatsTable>

        <StatsTable
          title={t("Scenes")}
          infoTitleKey="info.statistics.scenesTable.title"
          infoBodyKey="info.statistics.scenesTable.body"
        >
          <Thead>
            <Tr>
              <Th>{t("Scene")}</Th>
              <Th isNumeric>{t("Tiles")}</Th>
              <Th isNumeric>{t("Reviewed")}</Th>
              <Th isNumeric>{t("Used")}</Th>
              <Th isNumeric>{t("Excluded")}</Th>
              <Th isNumeric>{t("Annotations")}</Th>
              <Th>{t("Status")}</Th>
            </Tr>
          </Thead>
          <Tbody>
            {sceneRows.map((row) => (
              <Tr key={row.scene_id}>
                <Td maxW="220px" overflow="hidden" textOverflow="ellipsis" title={row.filename}>
                  {row.filename}
                </Td>
                <Td isNumeric>{row.tiles}</Td>
                <Td isNumeric>{row.reviewed_tiles ?? 0}</Td>
                <Td isNumeric>{row.used_tiles}</Td>
                <Td isNumeric>{row.excluded_tiles}</Td>
                <Td isNumeric>{row.annotations}</Td>
                <Td>
                  <Badge colorScheme={row.without_annotations ? "orange" : "green"}>
                    {row.without_annotations ? t("no labels") : row.status || "ok"}
                  </Badge>
                </Td>
              </Tr>
            ))}
          </Tbody>
        </StatsTable>
      </SimpleGrid>

      {geometrySection}

      {stats.co_occurrence && stats.co_occurrence.classes.length >= 2 && (
        <CoOccurrenceSection
          co={stats.co_occurrence}
          onExport={projectId ? () => handleExportCsv("cooccurrence") : undefined}
        />
      )}

      {stats.acquisition_stats && (
        <AcquisitionSection
          acq={stats.acquisition_stats}
          onExport={projectId ? () => handleExportCsv("acquisition") : undefined}
        />
      )}
    </VStack>
  );
}

function Warnings({ stats }: { stats: Stats }) {
  const { t } = useTranslation();
  // Ostrzezenia bez list nazw — jak dotad, jako proste Alerty.
  const generalWarnings = [
    stats.scenes_without_annotations?.length
      ? t("Scenes without annotations count", { count: stats.scenes_without_annotations.length })
      : null,
    stats.scenes_without_dataset_classes?.length
      ? t("Scenes without dataset classes count", { count: stats.scenes_without_dataset_classes.length })
      : null,
    !stats.validation_report && hasEmptyValidation(stats)
      ? t("Validation or test split is empty. Increase data volume or change split mode.")
      : null,
  ].filter(Boolean) as string[];

  const validationIssues = stats.validation_report?.issues || [];
  const items = [
    ...validationIssues.map((item) => ({
      message: translateDatasetIssue(t, item),
      status: item.severity === "error" ? "error" as const : "warning" as const,
    })),
    ...generalWarnings.map((message) => ({ message, status: "warning" as const })),
  ].filter(
    (item, index, all) => all.findIndex((candidate) => candidate.message === item.message) === index
  );

  const unused = stats.unused_classes || [];
  const sourceOnly = stats.source_only_classes || [];
  if (!items.length && !unused.length && !sourceOnly.length) return null;

  return (
    <VStack align="stretch" spacing={2}>
      {items.map((item) => (
        <Alert key={item.message} status={item.status} borderRadius="12px" py={2}>
          <AlertIcon />
          <Text fontSize="sm">{item.message}</Text>
        </Alert>
      ))}
      {unused.length > 0 && <ClassListAlert label={t("Unused classes")} names={unused} />}
      {sourceOnly.length > 0 && <ClassListAlert label={t("Source-only classes")} names={sourceOnly} />}
    </VStack>
  );
}

// Ostrzezenie z lista nazw klas: licznik + zwijana lista badge'y (zamiast dlugiego stringa).
function ClassListAlert({ label, names }: { label: string; names: string[] }) {
  const { t } = useTranslation();
  const { isOpen, onToggle } = useDisclosure();
  const showToggle = names.length > CLASS_LIST_PREVIEW;
  const shown = isOpen || !showToggle ? names : names.slice(0, CLASS_LIST_PREVIEW);
  return (
    <Alert status="warning" borderRadius="12px" py={2} alignItems="start">
      <AlertIcon />
      <Box flex="1" minW={0}>
        <Text fontSize="sm" fontWeight="600">{label}: {names.length}</Text>
        <Wrap spacing={1} mt={1}>
          {shown.map((name) => (
            <WrapItem key={name}>
              <Badge colorScheme="orange" variant="subtle">{name}</Badge>
            </WrapItem>
          ))}
          {!isOpen && showToggle && (
            <WrapItem>
              <Badge colorScheme="gray" variant="subtle">+{names.length - CLASS_LIST_PREVIEW}</Badge>
            </WrapItem>
          )}
        </Wrap>
        {showToggle && (
          <Button size="xs" variant="link" colorScheme="orange" mt={1} onClick={onToggle}>
            {isOpen ? t("Show less") : t("Show all")}
          </Button>
        )}
      </Box>
    </Alert>
  );
}

// Owija wykres w kontener z przewijaniem w pionie, gdy poziomy wykres klasowy rosnie wysoko.
function ScrollableChart({ scrollable, children }: { scrollable: boolean; children: ReactNode }) {
  if (!scrollable) return <>{children}</>;
  return (
    <Box maxH="460px" overflowY="auto" overflowX="hidden">
      {children}
    </Box>
  );
}

// Raport niezbalansowania: filtr, sort malejaco po Dataset, kolumny udzialu/relacji/scen/kafli,
// rozklad train/val/test (n + %) i ostrzezenia (1 scena, brak w splicie). Scroll poziomy.
function ClassTable({ rows, largest }: { rows: ClassRow[]; largest: number }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const textColor = useColorModeValue("navy.700", "white");
  const headerBg = useColorModeValue("white", "#242428");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.500");
  const barTrack = useColorModeValue("gray.100", "whiteAlpha.200");
  const barFill = useColorModeValue("#9B6CFF", "#B794FF");

  const sorted = useMemo(
    () => [...rows].sort((a, b) => b.dataset_annotations - a.dataset_annotations),
    [rows],
  );
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? sorted.filter((row) => row.name.toLowerCase().includes(q)) : sorted;
  }, [sorted, query]);

  const fmtPct = (v?: number) => `${(v ?? 0).toFixed(1)}%`;
  const nWithPct = (n: number, p?: number) => (
    <>
      {n} <Text as="span" color={mutedColor} fontSize="xs">({fmtPct(p)})</Text>
    </>
  );

  return (
    <Card>
      <HStack spacing={1} mb={3}>
        <Text fontWeight="bold" color={textColor}>{t("Class imbalance")}</Text>
        <InfoPopover
          titleKey="info.statistics.sourceVsDatasetAnnotations.title"
          bodyKey="info.statistics.sourceVsDatasetAnnotations.body"
        />
        <Text fontSize="xs" color={mutedColor} ml="auto">{filtered.length}/{rows.length}</Text>
      </HStack>
      <Input
        size="sm"
        mb={2}
        maxW="320px"
        placeholder={t("Filter classes...")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <Box maxH="460px" overflowY="auto" overflowX="auto">
        <Table size="sm" minW="900px">
          <Thead position="sticky" top={0} zIndex={1} bg={headerBg}>
            <Tr>
              <Th>{t("Class")}</Th>
              <Th isNumeric>Dataset</Th>
              <Th>{t("Share")}</Th>
              <Th isNumeric>{t("vs largest")}</Th>
              <Th isNumeric>{t("Scenes")}</Th>
              <Th isNumeric>{t("Tiles")}</Th>
              <Th isNumeric>{t("Source")}</Th>
              <Th isNumeric>{t("Train")}</Th>
              <Th isNumeric>{t("Val")}</Th>
              <Th isNumeric>{t("Test")}</Th>
              <Th>{t("Status")}</Th>
            </Tr>
          </Thead>
          <Tbody>
            {filtered.map((row) => {
              const singleScene = row.used && (row.scenes ?? 0) <= 1;
              const missing = row.missing_in_splits ?? [];
              return (
                <Tr key={`${row.class_id}-${row.name}`}>
                  <Td>{row.name}</Td>
                  <Td isNumeric fontWeight="600">{row.dataset_annotations}</Td>
                  <Td>
                    <HStack spacing={2} minW="120px">
                      <Box flex="1" h="6px" bg={barTrack} borderRadius="full" overflow="hidden">
                        <Box h="100%" w={`${Math.min(100, row.share_pct ?? 0)}%`} bg={barFill} />
                      </Box>
                      <Text fontSize="xs" color={mutedColor} minW="42px" textAlign="right">{fmtPct(row.share_pct)}</Text>
                    </HStack>
                  </Td>
                  <Td isNumeric>{(row.rel_to_largest ?? 0).toFixed(2)}×</Td>
                  <Td isNumeric>
                    <HStack spacing={1} justify="flex-end">
                      <Text>{row.scenes ?? 0}</Text>
                      {singleScene && <Badge colorScheme="orange" fontSize="xx-small">{t("1 scene")}</Badge>}
                    </HStack>
                  </Td>
                  <Td isNumeric>{row.tiles ?? 0}</Td>
                  <Td isNumeric color={mutedColor}>{row.source_annotations}</Td>
                  <Td isNumeric>{nWithPct(row.train, row.train_pct)}</Td>
                  <Td isNumeric>{nWithPct(row.val, row.val_pct)}</Td>
                  <Td isNumeric>{nWithPct(row.test, row.test_pct)}</Td>
                  <Td>
                    <HStack spacing={1}>
                      <Badge colorScheme={row.used ? "green" : row.source_only ? "orange" : "gray"}>
                        {row.used ? t("used") : row.source_only ? t("source only") : t("unused")}
                      </Badge>
                      {missing.length > 0 && (
                        <Badge colorScheme="red" fontSize="xx-small">
                          {t("missing in")}: {missing.map((s) => t(s)).join(", ")}
                        </Badge>
                      )}
                    </HStack>
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
        {filtered.length === 0 && (
          <Text fontSize="sm" color={mutedColor} py={3}>{t("No matching classes")}</Text>
        )}
      </Box>
      {largest > 0 && (
        <Text fontSize="xs" color={mutedColor} mt={2}>
          {t("Largest class")}: {largest} · {t("vs largest = class annotations ÷ largest class")}
        </Text>
      )}
    </Card>
  );
}

// Tabela rozmiarów geometrii per klasa (AABB + opcjonalnie OBB). Scroll poziomy, sort po liczbie.
function GeometryClassTable({ geo }: { geo: GeometryStats }) {
  const { t } = useTranslation();
  const headerBg = useColorModeValue("white", "#242428");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.500");
  const isObb = geo.mode === "rotated_bbox";
  const rows = useMemo(() => [...geo.per_class].sort((a, b) => b.count - a.count), [geo.per_class]);
  const px = (v: number) => Math.round(v);
  const meters = (v?: number | null) => (v == null ? "-" : `${v < 10 ? v.toFixed(2) : v.toFixed(1)}`);
  const angle = (v?: number | null) => (v == null ? "-" : `${Math.round(v)}°`);

  return (
    <Box maxH="420px" overflowY="auto" overflowX="auto">
      <Table size="sm" minW={isObb ? "1000px" : "820px"}>
        <Thead position="sticky" top={0} zIndex={1} bg={headerBg}>
          <Tr>
            <Th>{t("Class")}</Th>
            <Th isNumeric>{t("Count")}</Th>
            <Th isNumeric>{t("w [px]")}</Th>
            <Th isNumeric>{t("h [px]")}</Th>
            <Th isNumeric>{t("Area [px²]")}</Th>
            <Th isNumeric>{t("Area p90")}</Th>
            <Th isNumeric>{t("Tile %")}</Th>
            <Th isNumeric>{t("Aspect")}</Th>
            <Th>{t("S / M / L")}</Th>
            <Th isNumeric>{t("w [m]")}</Th>
            <Th isNumeric>{t("h [m]")}</Th>
            <Th isNumeric>{t("Area [m²]")}</Th>
            {isObb && <Th isNumeric>{t("Angle")}</Th>}
            {isObb && <Th isNumeric>{t("Short")}</Th>}
            {isObb && <Th isNumeric>{t("Long")}</Th>}
            {isObb && <Th isNumeric>{t("~square")}</Th>}
          </Tr>
        </Thead>
        <Tbody>
          {rows.map((r) => (
            <Tr key={`${r.class_id}-${r.name}`}>
              <Td>{r.name}</Td>
              <Td isNumeric fontWeight="600">{r.count}</Td>
              <Td isNumeric>{px(r.w_px_median)}</Td>
              <Td isNumeric>{px(r.h_px_median)}</Td>
              <Td isNumeric>{px(r.area_px_median)}</Td>
              <Td isNumeric color={mutedColor}>{px(r.area_px_p90)}</Td>
              <Td isNumeric>{(r.area_frac_median * 100).toFixed(2)}%</Td>
              <Td isNumeric>{r.aspect_median.toFixed(2)}</Td>
              <Td>
                <Text as="span" color="green.400">{r.size_small}</Text>
                {" / "}
                <Text as="span" color="blue.400">{r.size_medium}</Text>
                {" / "}
                <Text as="span" color="orange.400">{r.size_large}</Text>
              </Td>
              <Td isNumeric color={mutedColor}>{meters(r.w_m_median)}</Td>
              <Td isNumeric color={mutedColor}>{meters(r.h_m_median)}</Td>
              <Td isNumeric color={mutedColor}>{meters(r.area_m2_median)}</Td>
              {isObb && <Td isNumeric>{angle(r.angle_median)}</Td>}
              {isObb && <Td isNumeric>{r.short_side_median == null ? "-" : px(r.short_side_median)}</Td>}
              {isObb && <Td isNumeric>{r.long_side_median == null ? "-" : px(r.long_side_median)}</Td>}
              {isObb && <Td isNumeric>{r.near_square_count}</Td>}
            </Tr>
          ))}
        </Tbody>
      </Table>
      <Text fontSize="xs" color={mutedColor} mt={2}>
        {t("Sizes are medians (px in tile). S/M/L thresholds by area: <32², 32²–96², >96². OBB angle/sides from source annotations.")}
      </Text>
    </Box>
  );
}

// Heatmapa współwystępowania klas (class × class na kaflu). Top-N + metryka (liczba/lift/Jaccard).
function CoOccurrenceSection({ co, onExport }: { co: CoOccurrence; onExport?: () => void }) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.500");
  const [metric, setMetric] = useState<"counts" | "lift" | "jaccard">("counts");
  const [topN, setTopN] = useState<number>(Math.min(12, co.classes.length));

  const n = Math.min(topN, co.classes.length);
  const total = co.tiles_total || 1;
  const matrix = useMemo(() => {
    const cell = (i: number, j: number): number => {
      const cij = co.counts[i][j];
      if (metric === "counts") return cij;
      if (i === j) return 1;
      const cii = co.counts[i][i];
      const cjj = co.counts[j][j];
      if (metric === "lift") return cii && cjj ? Number(((cij * total) / (cii * cjj)).toFixed(2)) : 0;
      const denom = cii + cjj - cij;
      return denom > 0 ? Number((cij / denom).toFixed(2)) : 0;
    };
    return Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => cell(i, j)));
  }, [co.counts, metric, total, n]);
  const classes = co.classes.slice(0, n);

  return (
    <Card>
      <HStack spacing={2} mb={3} flexWrap="wrap">
        <Text fontWeight="bold" color={textColor}>{t("Class co-occurrence")}</Text>
        <InfoPopover titleKey="info.statistics.coOccurrence.title" bodyKey="info.statistics.coOccurrence.body" />
        {co.truncated && <Badge colorScheme="orange">{t("Top classes only")}</Badge>}
        <HStack ml="auto" spacing={2}>
          <Select size="xs" w="130px" value={metric} onChange={(e) => setMetric(e.target.value as any)}>
            <option value="counts">{t("Tiles (count)")}</option>
            <option value="lift">{t("Lift")}</option>
            <option value="jaccard">{t("Jaccard")}</option>
          </Select>
          <Select size="xs" w="110px" value={String(topN)} onChange={(e) => setTopN(Number(e.target.value))}>
            {[5, 8, 12, 20, co.classes.length].filter((v, i, a) => v <= co.classes.length && a.indexOf(v) === i).map((v) => (
              <option key={v} value={v}>{v === co.classes.length ? t("All") : `Top ${v}`}</option>
            ))}
          </Select>
          {onExport && (
            <Button size="xs" leftIcon={<MdDownload />} variant="outline" onClick={onExport}>
              {t("Export CSV")}
            </Button>
          )}
        </HStack>
      </HStack>
      <CoOccurrenceMatrix classes={classes} matrix={matrix} metric={metric} />
      <Text fontSize="xs" color={mutedColor} mt={2}>
        {metric === "counts"
          ? t("Cell = tiles where both classes appear (diagonal = tiles with the class).")
          : metric === "lift"
          ? t("Lift >1 = classes co-occur more than by chance; <1 = less.")
          : t("Jaccard = shared tiles ÷ tiles with either class (0–1).")}
      </Text>
    </Card>
  );
}

// Sekcja metadanych akwizycji: GSD/sensor/modality/sezon/SAR incidence, ważone kaflami, per split.
function AcquisitionSection({ acq, onExport }: { acq: AcquisitionStats; onExport?: () => void }) {
  const { t } = useTranslation();
  const textColor = useColorModeValue("navy.700", "white");
  const mutedColor = useColorModeValue("secondaryGray.600", "secondaryGray.500");
  const chartTextColor = useColorModeValue("#1B2559", "#D6D6D8");
  const chartMutedTextColor = useColorModeValue("#707EAE", "#A7A7AD");
  const gridColor = useColorModeValue("#E9EDF7", "rgba(255, 255, 255, 0.08)");
  const chartExportBg = useColorModeValue("#FFFFFF", "#242428");
  const chartMode = useColorModeValue("light", "dark") as "light" | "dark";

  const base = (categories: (string | number)[]) => ({
    chart: { toolbar: { show: false }, foreColor: chartTextColor, background: chartExportBg, stacked: true },
    theme: { mode: chartMode },
    dataLabels: { enabled: false },
    grid: { borderColor: gridColor, strokeDashArray: 3 },
    legend: { position: "bottom" as const, labels: { colors: chartTextColor } },
    colors: ["#9B6CFF", "#75A7F7", "#E2B35F"],
    tooltip: { theme: chartMode },
    xaxis: { categories, labels: { style: { colors: chartMutedTextColor, fontSize: "11px" }, rotate: -40, trim: true } },
    yaxis: { min: 0, labels: { style: { colors: chartTextColor, fontSize: "11px" } } },
  });
  const splitSeries = (h: SplitHistogram) => [
    { name: t("Train"), data: h.train }, { name: t("Val"), data: h.val }, { name: t("Test"), data: h.test },
  ];
  const histCats = (h: SplitHistogram) => h.bins.slice(0, -1).map((b) => (b >= 10 ? Math.round(b) : Number(b.toFixed(2))));
  const distKeys = (d: MetaDist) => Object.keys(d.overall).sort((a, b) => (d.overall[b] || 0) - (d.overall[a] || 0));
  const distSeries = (d: MetaDist, keys: string[]) => [
    { name: t("Train"), data: keys.map((k) => d.train[k] || 0) },
    { name: t("Val"), data: keys.map((k) => d.val[k] || 0) },
    { name: t("Test"), data: keys.map((k) => d.test[k] || 0) },
  ];

  const sensorKeys = distKeys(acq.sensor);
  const modalityKeys = distKeys(acq.modality);
  const seasonOrder = ["winter", "spring", "summer", "autumn"].filter((s) => acq.season.overall[s]);
  const hasSensor = sensorKeys.length > 0;
  const hasSeason = seasonOrder.length > 0;

  const barChart = (id: string, title: string, categories: (string | number)[], series: any[]) => (
    <ChartCard title={title} chartId={id}>
      <Chart type="bar" height={240} options={{ ...base(categories), chart: { ...base(categories).chart, id } }} series={series} />
    </ChartCard>
  );

  return (
    <Card>
      <HStack spacing={2} mb={3} flexWrap="wrap">
        <Text fontWeight="bold" color={textColor}>{t("Acquisition metadata")}</Text>
        <InfoPopover titleKey="info.statistics.acquisition.title" bodyKey="info.statistics.acquisition.body" />
        <Text fontSize="xs" color={mutedColor}>{t("weighted by tiles")}</Text>
        {onExport && (
          <Button ml="auto" size="xs" leftIcon={<MdDownload />} variant="outline" onClick={onExport}>
            {t("Export CSV")}
          </Button>
        )}
      </HStack>
      <SimpleGrid columns={{ base: 2, md: 4 }} gap={3} mb={4}>
        <MiniStatistics label={t("Tiles")} value={acq.total_tiles} />
        <MiniStatistics label={t("Missing GSD")} value={acq.missing?.gsd ?? 0} />
        <MiniStatistics label={t("Missing sensor")} value={acq.missing?.sensor ?? 0} />
        <MiniStatistics label={t("Missing date")} value={acq.missing?.date ?? 0} />
      </SimpleGrid>
      <SimpleGrid columns={{ base: 1, xl: 2 }} gap={4}>
        {acq.gsd_hist && barChart("acq-gsd", t("GSD (m/px)"), histCats(acq.gsd_hist), splitSeries(acq.gsd_hist))}
        {hasSensor && barChart("acq-sensor", t("Sensor"), sensorKeys, distSeries(acq.sensor, sensorKeys))}
        {modalityKeys.length > 0 && barChart("acq-modality", t("Modality"), modalityKeys, distSeries(acq.modality, modalityKeys))}
        {hasSeason && barChart("acq-season", t("Season"), seasonOrder.map((s) => t(s)), distSeries(acq.season, seasonOrder))}
        {acq.incidence_hist && barChart("acq-incidence", t("SAR incidence (°)"), histCats(acq.incidence_hist), splitSeries(acq.incidence_hist))}
      </SimpleGrid>
    </Card>
  );
}

function ChartCard({
  title,
  children,
  chartId,
  infoTitleKey,
  infoBodyKey,
}: {
  title: string;
  children: ReactNode;
  chartId?: string;
  infoTitleKey?: string;
  infoBodyKey?: string;
}) {
  const textColor = useColorModeValue("navy.700", "#D6D6D8");
  const chartBg = useColorModeValue("white", "#242428");
  return (
    <Card p={4}>
      <HStack spacing={1} mb={2}>
        <Text fontWeight="bold" fontSize="sm" color={textColor}>{title}</Text>
        {infoTitleKey && infoBodyKey && (
          <InfoPopover titleKey={infoTitleKey} bodyKey={infoBodyKey} />
        )}
        {chartId && (
          <Box ml="auto">
            <CopyChartButton chartId={chartId} />
          </Box>
        )}
      </HStack>
      <Box
        bg={chartBg}
        borderRadius="14px"
        p={2}
        sx={{
          ".apexcharts-canvas, .apexcharts-svg": {
            background: "transparent !important",
          },
          ".apexcharts-tooltip": {
            border: "none !important",
            boxShadow: "0 10px 30px rgba(0, 0, 0, 0.25) !important",
          },
          ".apexcharts-text, .apexcharts-legend-text": {
            fill: `${textColor} !important`,
            color: `${textColor} !important`,
          },
        }}
      >
        {children}
      </Box>
    </Card>
  );
}

function StatsTable({
  title,
  children,
  infoTitleKey,
  infoBodyKey,
}: {
  title: string;
  children: ReactNode;
  infoTitleKey?: string;
  infoBodyKey?: string;
}) {
  const textColor = useColorModeValue("navy.700", "white");
  return (
    <Card>
      <HStack spacing={1} mb={3}>
        <Text fontWeight="bold" color={textColor}>{title}</Text>
        {infoTitleKey && infoBodyKey && (
          <InfoPopover titleKey={infoTitleKey} bodyKey={infoBodyKey} />
        )}
      </HStack>
      <TableContainer>
        <Table size="sm">
          {children}
        </Table>
      </TableContainer>
    </Card>
  );
}

function legacyClassRows(stats: Stats) {
  return Object.entries(stats.per_class || {}).map(([name, count], index) => ({
    class_id: index,
    name,
    source_annotations: 0,
    dataset_annotations: count,
    train: Number(stats.per_split?.train?.per_class?.[name] || 0),
    val: Number(stats.per_split?.val?.per_class?.[name] || 0),
    test: Number(stats.per_split?.test?.per_class?.[name] || 0),
    used: count > 0,
    source_only: false,
  }));
}

function legacySplitRows(stats: Stats) {
  return Object.entries(stats.per_split || {}).map(([split, data]) => ({
    split,
    images: data.images,
    positive_tiles: data.positive_tiles ?? data.images,
    negative_tiles: data.negative_tiles ?? 0,
    annotations: data.annotations,
    per_class: data.per_class,
  }));
}

function hasEmptyValidation(stats: Stats) {
  const splitRows = stats.split_stats || legacySplitRows(stats);
  const val = splitRows.find((row) => row.split === "val");
  const test = splitRows.find((row) => row.split === "test");
  return !val?.images || !test?.images;
}

function abbreviateChartLabel(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(1, maxLength - 1))}…`;
}
