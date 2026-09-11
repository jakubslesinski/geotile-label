import { useState, useEffect, useCallback } from "react";
import {
  VStack,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Text,
  useToast,
} from "@chakra-ui/react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import DatasetPanel from "../components/panels/DatasetPanel";
import ExportPanel from "../components/panels/ExportPanel";
import DatasetStatsPanel from "../components/stats/DatasetStats";
import DatasetRunsPanel from "../components/stats/DatasetRunsPanel";
import DatasetAuditPanel from "../components/stats/DatasetAuditPanel";
import DatasetContentPanel from "../components/stats/DatasetContentPanel";
import * as api from "../api/client";
import type {
  DatasetConfig,
  DatasetFilterOptions,
  DatasetRunsIndex,
  DatasetStats,
  DatasetAuditReport,
  PreprocessingProfile,
  ProjectGeoreferencing,
  ProjectModality,
} from "../types";

// Cache wyników per run (run jest niezmienny) — ponowne otwarcie projektu pokazuje
// statystyki/audyt natychmiast z cache, a w tle rewaliduje. Klucz: `${projectId}:${runId}`.
const datasetResultCache = new Map<
  string,
  { stats: DatasetStats | null; audit: DatasetAuditReport | null }
>();

export default function DatasetView() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const [config, setConfig] = useState<DatasetConfig>({
    train_ratio: 0.7,
    val_ratio: 0.2,
    test_ratio: 0.1,
    min_box_fraction: 0.3,
    negative_ratio: 0.1,
    tile_selection: "reviewed_sampled",
    split_mode: "random_tile",
    split_seed: 42,
    block_size_tiles: 5,
    preprocessing_profile_id: null,
    scene_ids: [],
    class_ids: [],
    annotator_emails: [],
    annotation_sources: [],
  });
  const [preprocessingProfiles, setPreprocessingProfiles] = useState<PreprocessingProfile[]>([]);
  const [filterOptions, setFilterOptions] = useState<DatasetFilterOptions>({
    catalog_id: null,
    scenes: [],
    classes: [],
    authors: [],
    annotation_sources: [],
  });
  const [projectModality, setProjectModality] = useState<ProjectModality | undefined>();
  const [projectGeoreferencing, setProjectGeoreferencing] = useState<ProjectGeoreferencing | undefined>();
  const [stats, setStats] = useState<DatasetStats | null>(null);
  const [audit, setAudit] = useState<DatasetAuditReport | null>(null);
  const [runsIndex, setRunsIndex] = useState<DatasetRunsIndex | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isRefreshingAudit, setIsRefreshingAudit] = useState(false);
  // true, dopóki trwa pierwsze pobieranie stats/audytu dla danego runu (bez cache).
  const [resultsLoading, setResultsLoading] = useState(true);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [tiling, setTiling] = useState<{ tile_size: number; buffer: number } | null>(null);
  const toast = useToast();

  const loadData = useCallback(async () => {
    if (!id) return;
    const [cfg, runs, profilesFile, project, availableFilters, tilingCfg] = await Promise.all([
      api.getDatasetConfig(id),
      api.getDatasetRuns(id),
      api.getPreprocessingProfiles(id),
      api.getProject(id),
      api.getDatasetFilterOptions(id),
      api.getTilingConfig(id).catch(() => null),
    ]);
    setConfig(cfg);
    setRunsIndex(runs);
    setPreprocessingProfiles(profilesFile.profiles || []);
    setFilterOptions(availableFilters);
    if (tilingCfg) setTiling({ tile_size: tilingCfg.tile_size, buffer: tilingCfg.buffer ?? 0 });
    setProjectModality(project.profile?.modality);
    setProjectGeoreferencing(project.profile?.georeferencing);
    const selectedExists = selectedRunId && runs.runs.some((run) => run.run_id === selectedRunId);
    const targetRunId = selectedExists ? selectedRunId : runs.latest_run_id;
    setSelectedRunId(targetRunId || null);
    // Cache po run_id: pokaż od razu, jeśli mamy; inaczej pokaż stan ładowania.
    const cacheKey = targetRunId ? `${id}:${targetRunId}` : null;
    const cached = cacheKey ? datasetResultCache.get(cacheKey) : undefined;
    if (cached) {
      setStats(cached.stats);
      setAudit(cached.audit);
      setResultsLoading(false);
    } else {
      setResultsLoading(true);
    }
    const [st, auditReport] = await Promise.all([
      api.getDatasetStats(id, targetRunId).catch(() => null),
      targetRunId ? api.getDatasetAudit(id, targetRunId).catch(() => null) : Promise.resolve(null),
    ]);
    const nextStats = st && Object.keys(st).length > 0 ? (st as DatasetStats) : null;
    setStats(nextStats);
    setAudit(auditReport);
    setResultsLoading(false);
    if (cacheKey) datasetResultCache.set(cacheKey, { stats: nextStats, audit: auditReport });
  }, [id, selectedRunId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleConfigChange = async (newConfig: DatasetConfig) => {
    if (!id) return;
    setConfig(newConfig);
    await api.updateDatasetConfig(id, newConfig);
  };

  const handleGenerate = async () => {
    if (!id) return;
    setIsGenerating(true);
    setProgress(null);
    let completedRunId: string | null = null;
    let streamError: string | null = null;

    try {
      const response = await fetch(api.datasetGenerateUrl(id), {
        method: "POST",
        headers: api.authHeaders(),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Dataset generation failed (${response.status})`);
      }
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (line.startsWith("data:")) {
              try {
                const data = JSON.parse(line.slice(5).trim());
                if (data.done !== undefined && data.total !== undefined) {
                  setProgress({ done: data.done, total: data.total });
                }
                if (data.total_tiles !== undefined) {
                  completedRunId = data.run_id || null;
                  setStats(data as DatasetStats);
                  toast({
                    title: t("Dataset generated"),
                    status: "success",
                    duration: 3000,
                  });
                }
                if (data.error) {
                  streamError = data.error;
                }
              } catch {}
            }
          }
        }
      }

      if (streamError) {
        throw new Error(streamError);
      }

      const runs = await api.getDatasetRuns(id);
      setRunsIndex(runs);
      const targetRunId = completedRunId || runs.latest_run_id;
      setSelectedRunId(targetRunId || null);
      const st = await api.getDatasetStats(id, targetRunId);
      const nextStats = st && Object.keys(st).length > 0 ? (st as DatasetStats) : null;
      setStats(nextStats);
      const nextAudit = targetRunId ? await api.getDatasetAudit(id, targetRunId) : null;
      setAudit(nextAudit);
      if (targetRunId) datasetResultCache.set(`${id}:${targetRunId}`, { stats: nextStats, audit: nextAudit });
    } catch (err: any) {
      toast({
        title: t("Generation failed"),
        description: err.message,
        status: "error",
      });
    } finally {
      setIsGenerating(false);
      setProgress(null);
    }
  };

  const handleSelectRun = async (runId: string) => {
    if (!id) return;
    try {
      const [selectedStats, selectedAudit] = await Promise.all([
        api.getDatasetRunStats(id, runId),
        api.getDatasetAudit(id, runId),
      ]);
      setSelectedRunId(runId);
      setStats(selectedStats);
      setAudit(selectedAudit);
      datasetResultCache.set(`${id}:${runId}`, {
        stats: (selectedStats as DatasetStats) ?? null,
        audit: selectedAudit ?? null,
      });
    } catch (err: any) {
      toast({
        title: t("Cannot load dataset run"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleRefreshAudit = async () => {
    if (!id || !selectedRunId) return;
    setIsRefreshingAudit(true);
    try {
      const refreshed = await api.refreshDatasetAudit(id, selectedRunId);
      setAudit(refreshed);
      const key = `${id}:${selectedRunId}`;
      const prev = datasetResultCache.get(key);
      datasetResultCache.set(key, { stats: prev?.stats ?? stats, audit: refreshed });
      toast({ title: t("Dataset audit refreshed"), status: "success", duration: 2000 });
    } catch (error: any) {
      toast({
        title: t("Dataset audit failed"),
        description: error?.response?.data?.detail || error?.message,
        status: "error",
      });
    } finally {
      setIsRefreshingAudit(false);
    }
  };

  if (!id) return null;

  return (
    <VStack align="stretch" spacing={6} p={2}>
      <DatasetRunsPanel
        runs={runsIndex?.runs || []}
        latestRunId={runsIndex?.latest_run_id || null}
        selectedRunId={selectedRunId}
        onSelect={handleSelectRun}
        projectId={id}
        onPublicationChanged={async () => {
          setRunsIndex(await api.getDatasetRuns(id));
        }}
        onRunDeleted={async (deletedRunId) => {
          const runs = await api.getDatasetRuns(id);
          setRunsIndex(runs);
          if (deletedRunId === selectedRunId) {
            setSelectedRunId(runs.latest_run_id || runs.runs[0]?.run_id || null);
          }
        }}
      />

      <ExportPanel projectId={id} runId={selectedRunId} audit={audit} />

      <Tabs variant="enclosed" colorScheme="brand" isLazy lazyBehavior="keepMounted">
        <TabList overflowX="auto" overflowY="hidden">
          <Tab whiteSpace="nowrap">{t("Build Dataset")}</Tab>
          <Tab whiteSpace="nowrap">{t("Statistics")}</Tab>
          <Tab whiteSpace="nowrap">{t("Content")}</Tab>
          <Tab>
            {t("Audit")}
            {audit?.status && (
              <Text as="span" ml={2} fontSize="xs">
                {audit.status === "ok" ? "✓" : audit.status === "error" ? "!" : "⚠"}
              </Text>
            )}
          </Tab>
        </TabList>
        <TabPanels>
          <TabPanel px={0} pt={4}>
            <DatasetPanel
              config={config}
              onConfigChange={handleConfigChange}
              onGenerate={handleGenerate}
              isGenerating={isGenerating}
              progress={progress}
              preprocessingProfiles={preprocessingProfiles}
              projectModality={projectModality}
              projectGeoreferencing={projectGeoreferencing}
              filterOptions={filterOptions}
              tiling={tiling}
            />
          </TabPanel>
          <TabPanel px={0} pt={4}>
            <DatasetStatsPanel stats={stats} loading={resultsLoading} projectId={id} />
          </TabPanel>
          <TabPanel px={0} pt={4}>
            <DatasetContentPanel projectId={id} runId={selectedRunId} />
          </TabPanel>
          <TabPanel px={0} pt={4}>
            <DatasetAuditPanel
              projectId={id}
              runId={selectedRunId}
              audit={audit}
              isLoading={resultsLoading}
              isRefreshing={isRefreshingAudit}
              onRefresh={handleRefreshAudit}
            />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </VStack>
  );
}
