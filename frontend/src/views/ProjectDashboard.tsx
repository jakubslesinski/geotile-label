import { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  VStack,
  HStack,
  Text,
  Image,
  Input,
  Select,
  Button,
  Badge,
  Tooltip,
  FormControl,
  FormLabel,
  Textarea,
  Collapse,
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
  Spinner,
  Switch,
  Progress,
  Alert,
  AlertIcon,
  Checkbox,
  Divider,
  Modal,
  ModalOverlay,
  ModalContent,
  ModalHeader,
  ModalCloseButton,
  ModalBody,
  ModalFooter,
  useColorModeValue,
  useToast,
  useDisclosure,
} from "@chakra-ui/react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  MdDownload,
  MdDataset,
  MdAdd,
  MdChevronLeft,
  MdChevronRight,
  MdDelete,
  MdExpandLess,
  MdExpandMore,
  MdFolderOpen,
  MdLink,
  MdRefresh,
  MdUploadFile,
  MdRateReview,
} from "react-icons/md";
import Card from "../components/common/Card";
import InfoPopover from "../components/common/InfoPopover";
import { useJob } from "../hooks/useJob";
import { formatBytes } from "../utils/sceneWorkingFiles";
import ImportProgressModal from "../components/common/ImportProgressModal";
import ClassManager from "../components/sidebar/ClassManager";
import ProjectAnnotationSummaryPanel from "../components/stats/ProjectAnnotationSummaryPanel";
import SceneWorkingFilesPanel from "../components/project/SceneWorkingFilesPanel";
import FolderBrowser from "../components/panels/FolderBrowser";
import * as api from "../api/client";
import type {
  AnnotationImportPreview,
  AnnotationPackagePreview,
  ImportJob,
  ProjectAnnotationSummary,
} from "../api/client";
import type { Project, Scene, LabelClass } from "../types";
import {
  isTauriRuntime,
  openAnnotationPackagesDialog,
  openReviewPackagesDialog,
  saveReviewPackageDialog,
  openClassesFileDialog,
  openSceneFolderDialog,
  saveAnnotationPackageDialog,
  saveSourceAnnotationsGeoParquetDialog,
} from "../desktop/dialogs";

function formatAcquisitionDate(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 10);
}

const REVIEW_STATUS_META: Record<string, { label: string; color: string }> = {
  none: { label: "Not reviewed", color: "gray" },
  accepted: { label: "Accepted", color: "green" },
  needs_fix: { label: "Needs fix", color: "orange" },
  rejected: { label: "Rejected", color: "red" },
};

function reviewStatusLabel(status?: string | null): string {
  return REVIEW_STATUS_META[status || "none"]?.label || REVIEW_STATUS_META.none.label;
}

function reviewStatusColor(status?: string | null): string {
  return REVIEW_STATUS_META[status || "none"]?.color || "gray";
}

// Wybor produktu/assetu — dane jednego "pakietu do decyzji" i wynik decyzji.
type SceneDecisionItem = {
  id: string;
  packageId: string;
  decisionUid?: string;
  label: string;
  alternatives: { label: string; asset_ids: string[] }[];
  needsRgb: boolean;
  allowSkip: boolean;
};
type SceneDecisionResult = {
  package_id: string;
  decision_uid?: string;
  action: "import" | "skip";
  asset_ids: string[];
  rgb_bands?: number[];
};

export default function ProjectDashboard() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [classes, setClasses] = useState<LabelClass[]>([]);
  const [sceneSources, setSceneSources] = useState<any[]>([]);
  const [sceneImportMode, setSceneImportMode] = useState<api.SceneImportMode>("background");
  const [isSavingImportMode, setIsSavingImportMode] = useState(false);
  // Domyslnie OFF — zgodnie z DEFAULT_AUTO_FULLRES_COG_ENABLED po stronie backendu.
  // Budowa COG pelnej rozdzielczosci to dziesiatki minut i kilka GB na scene.
  const [autoFullresCogEnabled, setAutoFullresCogEnabled] = useState(false);
  const [isSavingAutoFullresCog, setIsSavingAutoFullresCog] = useState(false);
  const [newSourceProvider, setNewSourceProvider] = useState<api.SceneProvider>("generic");
  const [decisionRequest, setDecisionRequest] = useState<{
    title: string;
    items: SceneDecisionItem[];
    resolve: (results: SceneDecisionResult[] | null) => void;
  } | null>(null);
  const [sceneMigrationPlan, setSceneMigrationPlan] = useState<api.SceneMigrationPlan | null>(null);
  const [sceneMigrationDecisions, setSceneMigrationDecisions] = useState<Record<string, string>>({});
  const [isCheckingSceneMigration, setIsCheckingSceneMigration] = useState(false);
  const [isApplyingSceneMigration, setIsApplyingSceneMigration] = useState(false);
  const [annotationSummary, setAnnotationSummary] = useState<ProjectAnnotationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [isScanning, setIsScanning] = useState(false);
  const [isExportingAnnotations, setIsExportingAnnotations] = useState(false);
  const [isPreparingPackage, setIsPreparingPackage] = useState(false);
  const [isSavingPackage, setIsSavingPackage] = useState(false);
  // Przygotowanie paczki = policzenie pelnego sha256 zasobow pomiarowych scen, ktore
  // blokuja eksport. Plan jest metadanowy (nic nie czyta), zadanie czyta gigabajty.
  const [preparePlan, setPreparePlan] = useState<api.AnnotationPackagePreparePlan | null>(null);
  const [prepareJobId, setPrepareJobId] = useState<string | null>(null);
  const [isStartingPrepare, setIsStartingPrepare] = useState(false);
  const [isPreviewingImport, setIsPreviewingImport] = useState(false);
  const [isApplyingImport, setIsApplyingImport] = useState(false);
  const [preparingSceneId, setPreparingSceneId] = useState<string | null>(null);
  const [packagePreview, setPackagePreview] = useState<AnnotationPackagePreview | null>(null);
  const [importPreview, setImportPreview] = useState<AnnotationImportPreview | null>(null);
  const [importPackagePaths, setImportPackagePaths] = useState<string[]>([]);
  const [createMissingClasses, setCreateMissingClasses] = useState(false);
  const [acceptedSceneIds, setAcceptedSceneIds] = useState<string[]>([]);
  const [isSavingReview, setIsSavingReview] = useState(false);
  const [isImportingReview, setIsImportingReview] = useState(false);
  const [reviewOwnerEmail, setReviewOwnerEmail] = useState("");
  const [allReviews, setAllReviews] = useState(false);
  const [reviewScene, setReviewScene] = useState<Scene | null>(null);
  const [reviewStatus, setReviewStatus] = useState<api.ReviewStatus>("accepted");
  const [reviewComment, setReviewComment] = useState("");
  const [isSavingVerdict, setIsSavingVerdict] = useState(false);
  const [sceneSearch, setSceneSearch] = useState("");
  const [sceneAuthorFilter, setSceneAuthorFilter] = useState("");
  const [sceneClassFilter, setSceneClassFilter] = useState("");
  const [sceneSourceFilter, setSceneSourceFilter] = useState("");
  const [sceneProvenanceFilter, setSceneProvenanceFilter] = useState("");
  const [debouncedSceneSearch, setDebouncedSceneSearch] = useState("");
  const [scenePageSize, setScenePageSize] = useState(100);
  const [scenePage, setScenePage] = useState(0);
  const [scenePageOffset, setScenePageOffset] = useState(0);
  const [sceneTotalCount, setSceneTotalCount] = useState(0);
  const [sceneFilteredCount, setSceneFilteredCount] = useState(0);
  const [sceneNextCursor, setSceneNextCursor] = useState<string | null>(null);
  const [sceneCursors, setSceneCursors] = useState<Array<string | null>>([null]);
  const [sceneReloadNonce, setSceneReloadNonce] = useState(0);
  const [isScenePageLoading, setIsScenePageLoading] = useState(false);
  const sceneRequestRef = useRef(0);
  const [classesExpanded, setClassesExpanded] = useState(false);
  // Wizualny znacznik "ukonczone" dla analityka przegladajacego dziesiatki scen.
  // Wylacznie po stronie klienta (localStorage per projekt) — nie trafia do
  // backendu, eksportow ani paczek. To notatka robocza, nie stan sceny.
  const [doneSceneIds, setDoneSceneIds] = useState<Set<string>>(new Set());
  const { isOpen: isBrowseOpen, onOpen: onBrowseOpen, onClose: onBrowseClose } = useDisclosure();
  const packageModal = useDisclosure();
  const importModal = useDisclosure();
  const importProgressDisc = useDisclosure();
  const sceneMigrationModal = useDisclosure();
  const [importJob, setImportJob] = useState<ImportJob | null>(null);
  const [importProgressJobId, setImportProgressJobId] = useState<string | null>(null);
  const [isResumingImport, setIsResumingImport] = useState(false);
  const [importPollNonce, setImportPollNonce] = useState(0);
  const importWasRunningRef = useRef(false);
  const verdictModal = useDisclosure();
  // NITF (sensor-geometry) projects are folder-based and deliberately have no scene
  // sources — hide the scene-sources sub-panel so "0 sources" isn't shown as a problem.
  const isNitfProject = project?.profile?.georeferencing === "SENSOR_GEO";

  const textColor = useColorModeValue("navy.700", "white");
  const tableBg = useColorModeValue("white", "navy.800");
  const tableBorderColor = useColorModeValue("gray.100", "whiteAlpha.200");
  const hoverBg = useColorModeValue("gray.50", "whiteAlpha.50");
  const doneRowBg = useColorModeValue("green.50", "rgba(72, 187, 120, 0.10)");
  const stickyBarBg = useColorModeValue("rgba(244, 247, 254, 0.94)", "rgba(28, 28, 30, 0.94)");

  const doneStorageKey = id ? `geotile.doneScenes.${id}` : null;

  // Wczytanie znacznikow "ukonczone" przy zmianie projektu.
  useEffect(() => {
    if (!doneStorageKey) return;
    try {
      const raw = window.localStorage.getItem(doneStorageKey);
      setDoneSceneIds(new Set(raw ? (JSON.parse(raw) as string[]) : []));
    } catch {
      setDoneSceneIds(new Set());
    }
  }, [doneStorageKey]);

  const toggleSceneDone = useCallback(
    (sceneId: string) => {
      setDoneSceneIds((prev) => {
        const next = new Set(prev);
        if (next.has(sceneId)) next.delete(sceneId);
        else next.add(sceneId);
        if (doneStorageKey) {
          try {
            window.localStorage.setItem(doneStorageKey, JSON.stringify([...next]));
          } catch {
            // Brak dostepu do localStorage nie moze wywrocic widoku — znacznik
            // pozostanie wtedy tylko na czas sesji.
          }
        }
        return next;
      });
    },
    [doneStorageKey]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSceneSearch(sceneSearch.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [sceneSearch]);

  // P2.2: filtry i paginacja są częścią zapytania do read modelu JSON. W przeglądarce
  // trzymamy wyłącznie bieżącą stronę oraz kursory stron już odwiedzonych.
  const scenePageCount = Math.max(1, Math.ceil(sceneFilteredCount / scenePageSize));
  useEffect(() => {
    setScenePage(0);
    setSceneCursors([null]);
  }, [
    scenePageSize,
    debouncedSceneSearch,
    sceneAuthorFilter,
    sceneClassFilter,
    sceneSourceFilter,
    sceneProvenanceFilter,
  ]);
  useEffect(() => {
    if (scenePage > scenePageCount - 1) setScenePage(scenePageCount - 1);
  }, [scenePage, scenePageCount]);
  const scenePageStart = scenePageOffset;
  const pagedScenes = scenes;
  const showScenePagination = sceneFilteredCount > 50;
  const currentSceneCursor = sceneCursors[scenePage] || null;

  useEffect(() => {
    if (!id || !project) return;
    const requestId = ++sceneRequestRef.current;
    let cancelled = false;
    setIsScenePageLoading(true);
    const importId = sceneProvenanceFilter.startsWith("import:")
      ? sceneProvenanceFilter.slice(7)
      : undefined;
    const packageId = sceneProvenanceFilter.startsWith("package:")
      ? sceneProvenanceFilter.slice(8)
      : undefined;
    void api.getScenesPage(id, {
      cursor: currentSceneCursor,
      limit: scenePageSize,
      filter: debouncedSceneSearch || undefined,
      author: sceneAuthorFilter || undefined,
      class_id: sceneClassFilter ? Number(sceneClassFilter) : undefined,
      annotation_source: sceneSourceFilter || undefined,
      import_id: importId,
      package_id: packageId,
    }).then((page) => {
      if (cancelled || requestId !== sceneRequestRef.current) return;
      setScenes(page.scenes);
      setScenePageOffset(page.offset);
      setSceneTotalCount(page.total_count);
      setSceneFilteredCount(page.filtered_count);
      setSceneNextCursor(page.next_cursor);
      setSceneCursors((previous) => {
        if (!page.next_cursor || previous[scenePage + 1] === page.next_cursor) return previous;
        const next = previous.slice(0, scenePage + 1);
        next[scenePage + 1] = page.next_cursor;
        return next;
      });
    }).catch((error) => {
      if (cancelled || requestId !== sceneRequestRef.current) return;
      if (error?.response?.status === 409 && scenePage > 0) {
        setScenePage(0);
        setSceneCursors([null]);
        return;
      }
      setScenes([]);
      setSceneFilteredCount(0);
    }).finally(() => {
      if (!cancelled && requestId === sceneRequestRef.current) setIsScenePageLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [
    id,
    project,
    currentSceneCursor,
    scenePage,
    scenePageSize,
    debouncedSceneSearch,
    sceneAuthorFilter,
    sceneClassFilter,
    sceneSourceFilter,
    sceneProvenanceFilter,
    sceneReloadNonce,
  ]);

  // --- Ładowanie widoku: ścieżka krytyczna oddzielona od agregatów ---
  //
  // DESIGN_DECISIONS.md, performance-audit E8a. Wcześniej wszystkie sześć wywołań siedziało w jednym
  // `Promise.all`, więc widok czekał na najwolniejsze — a najwolniejsze są DOKŁADNIE te,
  // bez których widok da się pokazać. Zmierzone na projekcie DOTA (2423 sceny):
  // `annotation_summary` 25,5 s i `legacy_cache_info` 1,7 s, przy ścieżce krytycznej ~10 s.
  // Że są niekrytyczne, było widać już wcześniej po `.catch(() => null)` przy każdym z nich.
  //
  // Reguła, której trzyma się `loadSecondary`: NIGDY nie zerować tych stanów na czas
  // przeładowania, tylko nadpisywać po sukcesie. Lista scen i jej filtry mają osobne,
  // stronicowane żądanie i nie zależą od pobrania pełnego `per_scene` do przeglądarki.
  const secondaryRunningRef = useRef(false);
  const secondaryPendingRef = useRef(false);

  const loadSecondary = useCallback(async () => {
    if (!id) return;
    // Koalescencja: 14 miejsc woła `loadData(false)` po mutacjach, a każdy przebieg to
    // wielosekundowa praca backendu. Przy zbiegu żądań robimy jeden dodatkowy przebieg
    // na końcu, zamiast kolejkować wszystkie.
    if (secondaryRunningRef.current) {
      secondaryPendingRef.current = true;
      return;
    }
    secondaryRunningRef.current = true;
    try {
      do {
        secondaryPendingRef.current = false;
        const summary = await api
          .getProjectAnnotationSummary(id, "compact")
          .catch(() => null);
        if (summary) setAnnotationSummary(summary);
      } while (secondaryPendingRef.current);
    } finally {
      secondaryRunningRef.current = false;
    }
  }, [id]);

  const loadData = useCallback(async (showLoading = true) => {
    if (!id) return;
    if (showLoading) setLoading(true);
    try {
      const [proj, classList] = await Promise.all([
        api.getProject(id),
        api.listClasses(id).catch(() => null),
      ]);
      setProject(proj);
      if (classList) {
        setClasses(classList);
        setClassesExpanded((expanded) => expanded || classList.length === 0);
      }
      const [sources, importConfig] = await Promise.all([
        api.getSceneSources(id).catch(() => null),
        api.getSceneImportConfig(id).catch(() => null),
      ]);
      if (sources) setSceneSources(sources.sources || []);
      if (importConfig) {
        setSceneImportMode(importConfig.import_mode);
        setAutoFullresCogEnabled(importConfig.auto_fullres_cog_enabled ?? false);
      }
      setSceneReloadNonce((value) => value + 1);
    } finally {
      if (showLoading) setLoading(false);
    }
    // Świadomie BEZ `await`: widok jest już gotowy, agregaty dopełniają się w tle.
    void loadSecondary();
  }, [id, loadSecondary]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Śledzenie importu scen w tle (job): banner + wznawianie przerwanego. Polling trwa,
  // dopóki job żyje; po zakończeniu odświeżamy listę scen. `importPollNonce` restartuje
  // pętlę po wznowieniu/zamknięciu paska.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const job = await api.getLatestImportJob(id);
        if (cancelled) return;
        setImportJob(job.state === "none" ? null : job);
        // Aktywne, gdy trwa katalogowanie/tożsamość, ALBO gdy piramidy budują się w tle po "done".
        const active =
          job.state === "running"
            ? !job.stale
            : Boolean(job.live && job.overviews_incomplete);
        if (active) {
          importWasRunningRef.current = true;
          timer = setTimeout(tick, 1500);
        } else if (importWasRunningRef.current) {
          importWasRunningRef.current = false;
          loadData(false);
        }
      } catch {
        if (!cancelled) timer = setTimeout(tick, 4000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, loadData, importPollNonce]);

  const openImportProgress = (jobId: string) => {
    setImportProgressJobId(jobId);
    importProgressDisc.onOpen();
  };

  const handleResumeImport = async (showModal = true) => {
    if (!id) return;
    setIsResumingImport(true);
    try {
      const res = await api.resumeImportJob(id, sceneImportMode);
      setImportProgressJobId(res.import_job_id);
      importWasRunningRef.current = true;
      // „Dokończ piramidy": nie otwieramy pełnego modala (katalog przelatuje od razu) —
      // banner sam pokaże postęp piramid dzięki pollingowi (live && overviews_incomplete).
      if (showModal) importProgressDisc.onOpen();
      setImportPollNonce((n) => n + 1);
    } catch (err: any) {
      toast({
        title: t("Resume import"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
    } finally {
      setIsResumingImport(false);
    }
  };

  const handleSceneImportModeChange = async (mode: api.SceneImportMode) => {
    if (!id) return;
    const previous = sceneImportMode;
    setSceneImportMode(mode);
    setIsSavingImportMode(true);
    try {
      await api.updateSceneImportConfig(id, { import_mode: mode });
    } catch (err: any) {
      setSceneImportMode(previous);
      toast({
        title: t("Cannot update import mode"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
    } finally {
      setIsSavingImportMode(false);
    }
  };

  const handleAutoFullresCogChange = async (enabled: boolean) => {
    if (!id) return;
    const previous = autoFullresCogEnabled;
    setAutoFullresCogEnabled(enabled);
    setIsSavingAutoFullresCog(true);
    try {
      await api.updateSceneImportConfig(id, { auto_fullres_cog_enabled: enabled });
    } catch (err: any) {
      setAutoFullresCogEnabled(previous);
      toast({
        title: t("Cannot update automatic COG generation"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
    } finally {
      setIsSavingAutoFullresCog(false);
    }
  };

  const handleImportProgressClosed = () => {
    importProgressDisc.onClose();
    loadData(false);
    setImportPollNonce((n) => n + 1);
  };

  const handleCancelImport = async (jobId?: string) => {
    if (!id || !jobId) return;
    try {
      await api.cancelImportJob(id, jobId);
      setImportPollNonce((n) => n + 1);
    } catch {
      /* job mógł się właśnie zakończyć — polling odświeży stan */
    }
  };

  // Wspólny job może budować kilka piramid równolegle; starszy kontrakt raportuje jedną
  // bieżącą scenę, dlatego obsługujemy oba warianty podczas migracji.
  const overviewBadge = (scene: Scene): { label: string; color: string; spin?: boolean } => {
    const building =
      importJob?.overview_active_scene_ids?.includes(scene.id) ||
      (importJob?.current?.stage === "overviews" && importJob.current.scene_id === scene.id);
    const status = building ? "building" : scene.overview_status || "native";
    switch (status) {
      case "building":
        return { label: t("Pyramids building"), color: "blue", spin: true };
      case "queued":
        return { label: t("Pyramids queued"), color: "purple" };
      case "pending":
        return { label: t("Pyramids missing"), color: "orange" };
      case "error":
        return { label: t("Pyramids error"), color: "red" };
      default: // ready | native
        return { label: t("Pyramids ready"), color: "green" };
    }
  };

  // Modal wyboru produktu (zastepuje window.prompt): promise rozwiazuje sie decyzjami albo
  // null (anulowanie). Modal jest zawsze zamontowany i sterowany przez decisionRequest.
  const requestSceneDecisions = (title: string, items: SceneDecisionItem[]) =>
    new Promise<SceneDecisionResult[] | null>((resolve) => {
      setDecisionRequest({ title, items, resolve });
    });

  const resolveSceneDecisions = (results: SceneDecisionResult[] | null) => {
    decisionRequest?.resolve(results);
    setDecisionRequest(null);
  };

  const collectSceneSourceDecisions = async (
    preview: api.SceneImportPreview,
  ): Promise<SceneDecisionResult[] | null> => {
    const pending = preview.packages.filter((entry) => entry.selection.status === "decision_required");
    if (!pending.length) return [];
    const items: SceneDecisionItem[] = pending.map((item) => ({
      id: item.decision_uid || item.package_id,
      packageId: item.package_id,
      decisionUid: item.decision_uid,
      label: item.package_root_relative,
      alternatives: item.selection.alternatives || [],
      needsRgb: item.selection.product_type === "MUL+PAN",
      allowSkip: true,
    }));
    return requestSceneDecisions(t("Select product"), items);
  };

  const handleScan = async () => {
    if (!id) return;
    setIsScanning(true);
    try {
      let result: any;
      if (sceneSources.length) {
        const queued = await api.resumeImportJob(id, sceneImportMode);
        setImportProgressJobId(queued.import_job_id);
        importWasRunningRef.current = true;
        importProgressDisc.onOpen();
        setImportPollNonce((value) => value + 1);
        toast({ title: t("Scene catalog refresh started"), status: "info" });
        return;
      } else {
        result = await api.scanProject(id);
      }
      await loadData(false);
      toast({
        title: t("Scene catalog refreshed"),
        description: sceneSources.length
          ? `${t("Added")}: ${result.added}, ${t("Updated")}: ${result.updated}, ${t("Missing")}: ${result.missing}, ${t("Blocked")}: ${result.blocked}`
          : `${t("scanSummary", { added: result.added, removed: result.removed, total: result.total })} ${t("identitySummary", { complete: result.identity_complete ?? 0, errors: result.identity_errors?.length ?? 0 })}`,
        status: result.blocked || result.identity_errors?.length ? "warning" : "success",
        duration: 5000,
      });
    } catch (err: any) {
      toast({ title: t("Scan failed"), description: err.message, status: "error" });
    } finally {
      setIsScanning(false);
    }
  };

  const handleRelinkSource = async (sourceId: string) => {
    if (!id) return;
    const folder = isTauriRuntime() ? await openSceneFolderDialog() : window.prompt(t("Source folder"));
    if (!folder) return;
    try {
      const preview = await api.relinkSceneSource(id, sourceId, folder, true);
      if (!preview.report.compatible) {
        const issueCount = preview.report.missing_packages.length
          + preview.report.changed_products.length
          + preview.report.missing_assets.length
          + preview.report.changed_assets.length;
        toast({
          title: t("Relink failed"),
          description: `${t("Source does not match the registered delivery")} (${issueCount})`,
          status: "error",
        });
        return;
      }
      const result = await api.relinkSceneSource(id, sourceId, folder, false);
      if (result.import_job_id) {
        setImportProgressJobId(result.import_job_id);
        importWasRunningRef.current = true;
        importProgressDisc.onOpen();
        setImportPollNonce((value) => value + 1);
        toast({ title: t("Scene source relinked; verification started"), status: "info" });
      } else {
        await loadData(false);
        toast({ title: t("Scene source relinked"), status: "success" });
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      toast({
        title: t("Relink failed"),
        description: typeof detail === "string" ? detail : detail?.message || err.message,
        status: "error",
      });
    }
  };

  const handleAddSceneSource = async () => {
    if (!id) return;
    const folder = isTauriRuntime() ? await openSceneFolderDialog() : window.prompt(t("Source folder"));
    if (!folder) return;
    try {
      const preview = await api.addSceneSource(id, {
        provider: newSourceProvider,
        root_path: folder,
        enabled: true,
      });
      const decisions = await collectSceneSourceDecisions(preview);
      if (decisions === null) return;
      const applied = await api.applySceneSourcePreview(
        id,
        preview.preview_id,
        decisions,
        sceneImportMode,
      );
      if (applied.import_job_id) {
        setImportProgressJobId(applied.import_job_id);
        importWasRunningRef.current = true;
        importProgressDisc.onOpen();
        setImportPollNonce((value) => value + 1);
        toast({ title: t("Scene source import started"), status: "info" });
      } else {
        await loadData(false);
        toast({ title: t("Scene source added"), status: "success" });
      }
    } catch (err: any) {
      toast({
        title: t("Adding scene source failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleCheckSceneMigration = async () => {
    if (!id) return;
    setIsCheckingSceneMigration(true);
    try {
      const plan = await api.getSceneImportMigrationPlan(id);
      setSceneMigrationPlan(plan);
      setSceneMigrationDecisions({});
      sceneMigrationModal.onOpen();
    } catch (err: any) {
      toast({
        title: t("Migration check failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setIsCheckingSceneMigration(false);
    }
  };

  const handleApplySceneMigration = async () => {
    if (!id || !sceneMigrationPlan) return;
    setIsApplyingSceneMigration(true);
    try {
      const result = await api.applySceneImportMigration(id, true, sceneMigrationDecisions);
      sceneMigrationModal.onClose();
      setSceneMigrationPlan(null);
      setSceneMigrationDecisions({});
      await loadData(false);
      toast({
        title: t("Scene migration completed"),
        description: t("Scene migration result", {
          migrated: result.migrated.length,
          flagged: result.flagged.length,
        }),
        status: result.flagged.length ? "warning" : "success",
        duration: 7000,
      });
    } catch (err: any) {
      toast({
        title: t("Scene migration failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setIsApplyingSceneMigration(false);
    }
  };

  const handleRemoveSceneSource = async (source: any) => {
    if (!id) return;
    const confirmed = window.confirm(
      t("Remove scene source confirmation", { provider: source.provider, path: source.root_path }),
    );
    if (!confirmed) return;
    try {
      const result = await api.removeSceneSource(id, source.source_id);
      await loadData(false);
      toast({
        title: t("Scene source removed"),
        description: t("Removed scenes count", { count: result.removed_scenes }),
        status: "success",
      });
    } catch (err: any) {
      toast({
        title: t("Removing scene source failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handlePrepareScene = async (scene: Scene) => {
    if (!id) return;
    setPreparingSceneId(scene.id);
    try {
      const started = await api.prepareScene(id, scene.id);
      if (started?.job_id) {
        while (true) {
          const detail = await api.getJob(id, started.job_id);
          const status = detail.state.status;
          if (status === "completed") break;
          if (status === "cancelled") {
            toast({ title: t("Scene preparation cancelled"), status: "info" });
            return;
          }
          if (status === "failed" || status === "interrupted") {
            throw new Error(detail.state.error || t("Scene preparation failed"));
          }
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
      }
      await loadData(false);
      toast({ title: t("Scene preparation complete"), status: "success" });
    } catch (err: any) {
      toast({
        title: t("Scene preparation failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    } finally {
      setPreparingSceneId(null);
    }
  };

  const handleRemoveManagedScene = async (scene: Scene) => {
    if (!id) return;
    const confirmed = window.confirm(
      t("Remove scene from project confirmation", { name: scene.display_name || scene.filename }),
    );
    if (!confirmed) return;
    try {
      await api.removeManagedScene(id, scene.id);
      await loadData(false);
      toast({ title: t("Scene removed from project"), status: "success" });
    } catch (err: any) {
      toast({
        title: t("Could not remove scene"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleCancelPreparation = async (scene: Scene) => {
    if (!id) return;
    try {
      await api.cancelScenePreparation(id, scene.id);
      toast({ title: t("Cancelling scene preparation"), status: "info" });
    } catch (err: any) {
      toast({ title: t("Cancellation failed"), description: err.response?.data?.detail || err.message, status: "error" });
    }
  };

  const handleResolveSceneProduct = async (scene: Scene) => {
    if (!id) return;
    try {
      const manifest = await api.getSceneManifest(id, scene.id);
      const selection = manifest.source_package?.selection || {};
      const alternatives = selection.alternatives || [];
      if (!alternatives.length) {
        throw new Error(t("No selectable product alternatives were found"));
      }
      const results = await requestSceneDecisions(t("Select product"), [{
        id: scene.id,
        packageId: scene.id,
        label: scene.display_name || scene.filename,
        alternatives,
        needsRgb: selection.product_type === "MUL+PAN",
        allowSkip: false,
      }]);
      if (results === null) return;
      const decision = results[0];
      if (!decision || decision.action === "skip") return;
      await api.selectSceneAsset(id, scene.id, decision.asset_ids, decision.rgb_bands);
      await loadData(false);
      toast({ title: t("Scene product selected"), status: "success" });
    } catch (err: any) {
      toast({
        title: t("Product selection failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleAddClass = async (name: string, color: string) => {
    if (!id) return;
    const cls = await api.createClass(id, { name, color });
    setClasses((prev) => [...prev, cls]);
  };

  // Kolor klasy jest zapisywany do pliku klas projektu, więc zmiana idzie na backend od
  // razu po zatwierdzeniu w pickerze. Lista wraca do poprzedniego stanu, gdy zapis padnie -
  // inaczej ramki na scenie byłyby w innym kolorze niż to, co pokazuje panel.
  const handleChangeClassColor = async (classId: number, color: string) => {
    if (!id) return;
    const previous = classes;
    setClasses((prev) => prev.map((c) => (c.id === classId ? { ...c, color } : c)));
    try {
      await api.updateClass(id, classId, { color });
    } catch (err: any) {
      setClasses(previous);
      toast({
        title: t("Could not change the class color"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleDeleteClass = async (classId: number) => {
    if (!id) return;
    await api.deleteClass(id, classId);
    setClasses((prev) => prev.filter((c) => c.id !== classId));
  };

  const handleImportFromBrowser = async (_path: string, classesPath?: string, _displayPath?: string) => {
    if (!id || !classesPath) return;
    await importClassesFromPath(classesPath);
  };

  const importClassesFromPath = async (classesPath: string) => {
    if (!id) return;
    try {
      const imported = await api.importClasses(id, classesPath);
      setClasses(imported);
      toast({ title: t("Classes imported"), status: "success", duration: 2000 });
    } catch (err: any) {
      toast({
        title: t("Import failed"),
        description: err.response?.data?.detail || err.message,
        status: "error",
      });
    }
  };

  const handleImportClick = async () => {
    if (!isTauriRuntime()) {
      onBrowseOpen();
      return;
    }

    try {
      const selected = await openClassesFileDialog();
      if (!selected) return;
      await importClassesFromPath(selected);
    } catch (err: any) {
      toast({
        title: t("Import failed"),
        description: err?.response?.data?.detail || err?.message || String(err),
        status: "error",
      });
    }
  };

  const handleBackupProject = async () => {
    if (!id) return;
    try {
      const submitted = await api.startProjectBackupJob(id);
      toast({
        title: t("Project backup queued"),
        description:
          t("The archive will be available in Background jobs when ready.") +
          ` (${submitted.job.job_id})`,
        status: "info",
      });
    } catch (err: any) {
      toast({
        title: t("Backup failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    }
  };

  const handleOpenProjectFolder = async () => {
    if (!project?.project_root) return;
    try {
      await api.openPathInFileManager(project.project_root);
    } catch (err: any) {
      toast({
        title: t("Cannot open project folder"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    }
  };

  const handleExportSourceAnnotations = async () => {
    if (!id || !project || !isTauriRuntime()) return;
    try {
      const outputPath = await saveSourceAnnotationsGeoParquetDialog(
        "annotations_wgs84.geoparquet",
      );
      if (!outputPath) return;
      setIsExportingAnnotations(true);
      const result = await api.exportSourceAnnotationsGeoParquet(id, outputPath);
      toast({
        title: t("GeoParquet export complete"),
        description: t("sourceGeoParquetSummary", {
          exported: result.wgs84_annotation_count,
          total: result.source_annotation_count,
        }),
        status: result.omitted_from_wgs84_count ? "warning" : "success",
        duration: 5000,
      });
    } catch (err: any) {
      toast({
        title: t("GeoParquet export failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsExportingAnnotations(false);
    }
  };

  const refreshPackagePreview = useCallback(async (projectId: string) => {
    // Podglad i plan zawsze razem: to ten sam warunek widziany z dwoch stron — co
    // blokuje eksport i ile kosztuje odblokowanie.
    const [preview, plan] = await Promise.all([
      api.getAnnotationPackagePreview(projectId),
      api.getAnnotationPackagePreparePlan(projectId),
    ]);
    setPackagePreview(preview);
    setPreparePlan(plan);
    return preview;
  }, []);

  const handleStartPackagePreparation = async () => {
    if (!id) return;
    setIsStartingPrepare(true);
    try {
      const started = await api.startAnnotationPackagePreparation(id);
      if (started.already_exact || !started.job_id) {
        await refreshPackagePreview(id);
        toast({ title: t("Package is already prepared"), status: "info", duration: 2500 });
        return;
      }
      setPrepareJobId(started.job_id);
    } catch (err: any) {
      toast({
        title: t("Package preparation failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsStartingPrepare(false);
    }
  };

  const handlePrepareAnnotationPackage = async () => {
    if (!id) return;
    setIsPreparingPackage(true);
    try {
      await refreshPackagePreview(id);
      packageModal.onOpen();
    } catch (err: any) {
      toast({
        title: t("Annotation package preview failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsPreparingPackage(false);
    }
  };

  const prepareJob = useJob(id, prepareJobId ?? undefined);
  const prepareState = prepareJob.detail?.state;
  const prepareActive = prepareJobId !== null;

  useEffect(() => {
    if (!prepareJobId || !id || !prepareState) return;
    if (!["completed", "cancelled", "failed", "interrupted"].includes(prepareState.status)) return;
    setPrepareJobId(null);
    // Podglad odswiezamy ZAWSZE, takze po anulowaniu i bledzie: część scen mogła się
    // policzyć, zanim zadanie się zatrzymało, więc stary podgląd byłby nieprawdziwy.
    void refreshPackagePreview(id)
      .then((preview) => {
        if (prepareState.status === "completed") {
          toast({
            title: preview.can_export
              ? t("Package prepared")
              : t("Package preparation finished with unresolved scenes"),
            status: preview.can_export ? "success" : "warning",
            duration: 4000,
          });
        } else if (prepareState.status !== "cancelled") {
          toast({
            title: t("Package preparation failed"),
            description: prepareState.error || undefined,
            status: "error",
          });
        }
      })
      .catch(() => undefined);
  }, [prepareJobId, prepareState, id, refreshPackagePreview, toast, t]);

  const handleSaveAnnotationPackage = async () => {
    if (!id || !project || !packagePreview?.can_export) return;
    try {
      const outputPath = await saveAnnotationPackageDialog(
        packagePreview.suggested_filename,
      );
      if (!outputPath) return;
      setIsSavingPackage(true);
      await api.saveAnnotationPackage(id, outputPath, packagePreview.package_id);
      toast({ title: t("Annotation package saved"), status: "success", duration: 4000 });
      packageModal.onClose();
    } catch (err: any) {
      toast({
        title: t("Annotation package export failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsSavingPackage(false);
    }
  };

  const handleSelectAnnotationPackages = async () => {
    if (!id || !isTauriRuntime()) return;
    try {
      const packagePaths = await openAnnotationPackagesDialog();
      if (!packagePaths.length) return;
      setImportPackagePaths(packagePaths);
      setIsPreviewingImport(true);
      const preview = await api.previewAnnotationImport(id, packagePaths);
      setImportPreview(preview);
      setCreateMissingClasses(false);
      // Pre-select every plan that does something and is safe. Destructive plans start
      // unchecked: accepting them has to be a deliberate act, not the default.
      setAcceptedSceneIds(
        preview.scene_plans
          .filter(
            (plan) =>
              !plan.block_reason &&
              !plan.requires_confirmation &&
              (plan.added_count || plan.changed_count || plan.removed_count)
          )
          .map((plan) => plan.target_scene_id)
      );
      importModal.onOpen();
    } catch (err: any) {
      toast({
        title: t("Annotation import preview failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsPreviewingImport(false);
    }
  };

  const handleAllowProbableIdentityMatches = async () => {
    if (!id || !importPackagePaths.length) return;
    setIsPreviewingImport(true);
    try {
      const preview = await api.previewAnnotationImport(
        id,
        importPackagePaths,
        "allow_probable",
      );
      setImportPreview(preview);
      setAcceptedSceneIds(
        preview.scene_plans
          .filter(
            (plan) =>
              !plan.block_reason &&
              !plan.requires_confirmation &&
              (plan.added_count || plan.changed_count || plan.removed_count)
          )
          .map((plan) => plan.target_scene_id),
      );
    } catch (err: any) {
      toast({
        title: t("Annotation import preview failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsPreviewingImport(false);
    }
  };

  const handleApplyAnnotationImport = async () => {
    if (!id || !importPreview) return;
    setIsApplyingImport(true);
    try {
      const report = await api.applyAnnotationImport(
        id,
        importPreview.preview_id,
        createMissingClasses,
        acceptedSceneIds,
      );
      await loadData(false);
      toast({
        title: t("Annotations imported"),
        description: t("annotationImportApplied", {
          added: report.imported_annotation_count,
          updated: report.updated_annotation_count,
          deleted: report.deleted_annotation_count,
          scenes: report.applied_scene_count,
        }),
        status: report.deleted_annotation_count ? "warning" : "success",
        duration: 6000,
      });
      importModal.onClose();
    } catch (err: any) {
      toast({
        title: t("Annotation import failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsApplyingImport(false);
    }
  };

  const handleSaveReviewPackage = async () => {
    // Tryb „Wszystkie recenzje" (allReviews) eksportuje wszystkie sprawdzone sceny bez
    // filtrowania po analityku, więc wtedy email nie jest wymagany.
    const ownerEmail = allReviews ? "" : reviewOwnerEmail;
    if (!id || (!allReviews && !ownerEmail)) return;
    try {
      setIsSavingReview(true);
      const preview = await api.getReviewPackagePreview(id, ownerEmail);
      if (!preview.can_export) {
        toast({
          title: t("Review package export failed"),
          description: preview.errors.join(" · "),
          status: "error",
        });
        return;
      }
      const outputPath = await saveReviewPackageDialog(preview.suggested_filename);
      if (!outputPath) return;
      const saved = await api.saveReviewPackage(id, outputPath, ownerEmail, preview.package_id);
      toast({
        title: t("Review package saved"),
        description: t("reviewPackageSummary", { scenes: saved.scene_count, pins: saved.pin_count }),
        status: "success",
        duration: 5000,
      });
    } catch (err: any) {
      toast({
        title: t("Review package export failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsSavingReview(false);
    }
  };

  const handleImportReview = async () => {
    if (!id || !isTauriRuntime()) return;
    try {
      const paths = await openReviewPackagesDialog();
      if (!paths.length) return;
      setIsImportingReview(true);
      const report = await api.applyReviewImport(id, paths);
      await loadData(false);
      toast({
        title: t("Review imported"),
        description: t("reviewImportSummary", {
          scenes: report.applied_scene_count ?? 0,
          stale: report.stale_verdict_count,
          missing: report.missing_scene_count,
        }),
        status: report.missing_scene_count ? "warning" : "success",
        duration: 6000,
      });
    } catch (err: any) {
      toast({
        title: t("Review import failed"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsImportingReview(false);
    }
  };

  const handleSaveSceneVerdict = async () => {
    if (!id || !reviewScene) return;
    try {
      setIsSavingVerdict(true);
      await api.setSceneReview(id, reviewScene.id, {
        review_status: reviewStatus,
        review_comment: reviewComment.trim() || null,
        // Pins are kept as they were; this dialog only sets the scene verdict.
        pins: reviewScene.review_pins || [],
      });
      await loadData(false);
      verdictModal.onClose();
      setReviewScene(null);
    } catch (err: any) {
      toast({
        title: t("Could not save the verdict"),
        description: err.response?.data?.detail || err.message || String(err),
        status: "error",
      });
    } finally {
      setIsSavingVerdict(false);
    }
  };

  if (!id) return null;
  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" h="200px">
        <Spinner size="lg" color="brand.400" />
      </Box>
    );
  }

  // Team-workflow role gates the two package actions (backend enforces the same).
  const projectRole = project?.profile?.project_role || "labeling";
  const exportDisabledByRole = projectRole === "review";
  const importDisabledByRole = projectRole === "labeling";
  const missingMigrationDecisions = sceneMigrationPlan?.scenes.filter(
    (entry) => entry.classification === "needs_selection"
      && Boolean(entry.alternatives?.length)
      && !sceneMigrationDecisions[entry.scene_id],
  ).length || 0;

  return (
    <VStack align="stretch" spacing={6} p={2}>
      <ImportProgressModal
        projectId={id ?? null}
        jobId={importProgressJobId}
        isOpen={importProgressDisc.isOpen}
        onClose={handleImportProgressClosed}
        onDone={handleImportProgressClosed}
      />

      {/* Import scen w tle: postęp / wznawianie przerwanego / dokańczanie piramid */}
      {importJob &&
        importJob.state !== "none" &&
        (importJob.state !== "done" || importJob.overviews_incomplete) &&
        (() => {
          const running = importJob.state === "running" && !importJob.stale;
          const pyramidsBg = importJob.state === "done" && !!importJob.overviews_incomplete && !!importJob.live;
          const pyramidsAbandoned =
            importJob.state === "done" && !!importJob.overviews_incomplete && !importJob.live;
          const showOverviewCount = importJob.state === "done";
          return (
            <Card>
              <HStack justify="space-between" flexWrap="wrap" spacing={3}>
                <VStack align="start" spacing={1} flex="1" minW="260px">
                  <HStack>
                    {(running || pyramidsBg) && <Spinner size="sm" />}
                    <Text fontWeight="semibold" color={textColor}>
                      {importJob.stale
                        ? t("Import interrupted")
                        : importJob.state === "running"
                        ? t("Importing scenes")
                        : importJob.state === "error"
                        ? t("Import failed")
                        : importJob.state === "cancelled"
                        ? t("Import cancelled")
                        : pyramidsBg
                        ? t("Building display pyramids")
                        : t("Pyramids incomplete")}
                    </Text>
                    <Badge
                      colorScheme={
                        pyramidsBg
                          ? "teal"
                          : importJob.stale || importJob.state === "cancelled" || pyramidsAbandoned
                          ? "orange"
                          : importJob.state === "error"
                          ? "red"
                          : "blue"
                      }
                    >
                      {showOverviewCount
                        ? `${importJob.overviews_done ?? 0} / ${importJob.overviews_total ?? "?"}`
                        : `${importJob.done ?? 0} / ${importJob.total ?? "?"}`}
                    </Badge>
                  </HStack>
                  {(running || pyramidsBg) && (
                    <Progress
                      w="100%"
                      value={
                        pyramidsBg
                          ? Math.round(((importJob.overviews_done ?? 0) / (importJob.overviews_total || 1)) * 100)
                          : importJob.total
                          ? Math.round(((importJob.done ?? 0) / importJob.total) * 100)
                          : 0
                      }
                      size="xs"
                      colorScheme={pyramidsBg ? "teal" : "brand"}
                      borderRadius="full"
                      hasStripe
                      isAnimated
                    />
                  )}
                  {(importJob.failed ?? 0) > 0 && (
                    <Text fontSize="xs" color="red.400">
                      {t("Failed scenes")}: {importJob.failed}
                    </Text>
                  )}
                </VStack>
                <HStack>
                  {running && importJob.job_id && (
                    <Button size="sm" variant="outline" onClick={() => openImportProgress(importJob.job_id!)}>
                      {t("Show progress")}
                    </Button>
                  )}
                  {(importJob.stale || importJob.state === "error" || importJob.state === "cancelled") && (
                    <Button size="sm" colorScheme="brand" isLoading={isResumingImport} onClick={() => handleResumeImport(true)}>
                      {t("Resume import")}
                    </Button>
                  )}
                  {pyramidsAbandoned && (
                    <Button size="sm" colorScheme="brand" isLoading={isResumingImport} onClick={() => handleResumeImport(false)}>
                      {t("Finish pyramids")}
                    </Button>
                  )}
                  {pyramidsBg && importJob.job_id && (
                    <Button size="sm" variant="outline" colorScheme="orange" onClick={() => handleCancelImport(importJob.job_id)}>
                      {t("Cancel import")}
                    </Button>
                  )}
                </HStack>
              </HStack>
            </Card>
          );
        })()}

      {/* Project header */}
      <Card>
        <HStack justify="space-between" align="start" spacing={4} flexWrap="wrap">
          <VStack align="start" spacing={1} flex="1" minW="280px">
            <Text fontSize="2xl" fontWeight="bold" color={textColor}>
              {project?.name}
            </Text>
            <Text fontSize="sm" color="secondaryGray.600">
              {t("Folder")}: {project?.scene_folder_display || project?.scene_folder || "/"}
            </Text>
            {project?.project_root && (
              <Text fontSize="xs" color="secondaryGray.500" noOfLines={1}>
                {t("Project location")}: {project.project_root}
              </Text>
            )}
            <Badge colorScheme="brand">
              {t("sceneCount", { count: project?.scene_count || 0 })}
            </Badge>
            {project?.profile && (
              <HStack spacing={2} flexWrap="wrap">
                <Badge colorScheme={projectRole === "review" ? "red" : "green"}>
                  {projectRole === "review" ? t("Review role") : t("Labeling role")}
                </Badge>
                <Badge colorScheme="purple">{project.profile.modality}</Badge>
                <Badge colorScheme={project.profile.georeferencing === "GEO" ? "green" : "blue"}>
                  {project.profile.georeferencing}
                </Badge>
                <Badge colorScheme="gray">{t(project.profile.annotation_mode)}</Badge>
                <Badge colorScheme="teal">{t(project.profile.default_split_strategy)}</Badge>
                {project.profile.labeling_author_email && (
                  <Badge colorScheme="cyan">{project.profile.labeling_author_email}</Badge>
                )}
                {project.profile.sensors?.map((sensor) => (
                  <Badge key={sensor} colorScheme="orange">
                    {sensor}
                  </Badge>
                ))}
              </HStack>
            )}
          </VStack>
          <HStack flexWrap="wrap" justify="flex-end">
            <HStack spacing={1}>
              <Tooltip
                label={t("Annotation-package export is disabled in review projects")}
                isDisabled={!exportDisabledByRole}
                placement="top"
                hasArrow
              >
                <Button
                  size="sm"
                  leftIcon={<MdDownload />}
                  onClick={handlePrepareAnnotationPackage}
                  variant="outline"
                  isLoading={isPreparingPackage}
                  loadingText={t("Preparing")}
                  isDisabled={!isTauriRuntime() || exportDisabledByRole}
                >
                  {t("Export annotation package")}
                </Button>
              </Tooltip>
              <InfoPopover titleKey="info.project.exportAnnotations.title" bodyKey="info.project.exportAnnotations.body" />
            </HStack>
            <HStack spacing={1}>
              <Tooltip
                label={t("Package import is disabled in labeling projects")}
                isDisabled={!importDisabledByRole}
                placement="top"
                hasArrow
              >
                <Button
                  size="sm"
                  leftIcon={<MdUploadFile />}
                  onClick={handleSelectAnnotationPackages}
                  variant="outline"
                  isLoading={isPreviewingImport}
                  loadingText={t("Analyzing")}
                  isDisabled={!isTauriRuntime() || importDisabledByRole}
                >
                  {t("Import annotations into project")}
                </Button>
              </Tooltip>
              <InfoPopover titleKey="info.project.importAnnotations.title" bodyKey="info.project.importAnnotations.body" />
            </HStack>
            {/* Review channel: the manager ships verdicts, the analyst takes them back. */}
            {projectRole === "review" ? (
              <HStack spacing={1}>
                <Input
                  size="sm"
                  maxW="190px"
                  type="email"
                  placeholder={t("Analyst email")}
                  value={reviewOwnerEmail}
                  onChange={(event) => setReviewOwnerEmail(event.target.value.trim().toLowerCase())}
                  isDisabled={allReviews}
                />
                <Checkbox
                  size="sm"
                  isChecked={allReviews}
                  onChange={(event) => setAllReviews(event.target.checked)}
                  whiteSpace="nowrap"
                >
                  {t("All reviews")}
                </Checkbox>
                <InfoPopover titleKey="info.review.allReviews.title" bodyKey="info.review.allReviews.body" />
                <Button
                  size="sm"
                  leftIcon={<MdRateReview />}
                  onClick={handleSaveReviewPackage}
                  variant="outline"
                  isLoading={isSavingReview}
                  loadingText={t("Preparing")}
                  isDisabled={!isTauriRuntime() || (!allReviews && !reviewOwnerEmail)}
                >
                  {t("Export review")}
                </Button>
              </HStack>
            ) : (
              <Button
                size="sm"
                leftIcon={<MdRateReview />}
                onClick={handleImportReview}
                variant="outline"
                isLoading={isImportingReview}
                loadingText={t("Importing")}
                isDisabled={!isTauriRuntime()}
              >
                {t("Import review")}
              </Button>
            )}
            <HStack spacing={1}>
              <Button
                size="sm"
                leftIcon={<MdFolderOpen />}
                onClick={handleOpenProjectFolder}
                variant="outline"
                isDisabled={!project?.project_root || !isTauriRuntime()}
              >
                {t("Open folder")}
              </Button>
              <InfoPopover titleKey="info.project.openFolder.title" bodyKey="info.project.openFolder.body" />
            </HStack>
            <HStack spacing={1}>
              <Button
                size="sm"
                leftIcon={<MdDownload />}
                onClick={handleBackupProject}
                variant="outline"
              >
                {t("Backup project")}
              </Button>
              <InfoPopover titleKey="info.project.backup.title" bodyKey="info.project.backup.body" />
            </HStack>
          </HStack>
        </HStack>
      </Card>

      {/* Classes section */}
      <Card>
        <VStack align="stretch" spacing={3}>
          <HStack justify="space-between">
            <HStack spacing={2}>
              <Text fontWeight="bold" color={textColor}>{t("Classes")}</Text>
              <Badge colorScheme="brand">{classes.length}</Badge>
            </HStack>
            <HStack>
              <Button
                size="xs"
                leftIcon={<MdUploadFile />}
                variant="outline"
                onClick={handleImportClick}
              >
                {t("Import from file")}
              </Button>
              <Button
                size="xs"
                variant="ghost"
                rightIcon={classesExpanded ? <MdExpandLess /> : <MdExpandMore />}
                onClick={() => setClassesExpanded((expanded) => !expanded)}
                aria-expanded={classesExpanded}
              >
                {classesExpanded ? t("Hide") : t("Show")}
              </Button>
            </HStack>
          </HStack>
          <Collapse in={classesExpanded} animateOpacity>
            <ClassManager
              classes={classes}
              onAdd={handleAddClass}
              onDelete={handleDeleteClass}
              onChangeColor={handleChangeClassColor}
            />
          </Collapse>
        </VStack>
      </Card>

      {annotationSummary && (
        <ProjectAnnotationSummaryPanel
          summary={annotationSummary}
          onExportGeoParquet={handleExportSourceAnnotations}
          isExporting={isExportingAnnotations}
          exportDisabled={!isTauriRuntime()}
        />
      )}

      <SceneWorkingFilesPanel projectId={id} isDesktop={isTauriRuntime()} />

      {/* Scene table */}
      <Card p={0} overflow="hidden">
        {!isNitfProject && (
          <VStack align="stretch" spacing={3} px={4} py={3} borderBottomWidth="1px" borderColor={tableBorderColor}>
            <HStack justify="space-between" flexWrap="wrap" gap={2}>
              <HStack spacing={2} minW={0}>
                <Text fontWeight="bold" color={textColor}>{t("Scene sources")}</Text>
                <Badge>{sceneSources.length}</Badge>
              </HStack>
              <HStack spacing={2} flexWrap="wrap" justify={{ base: "flex-start", md: "flex-end" }}>
                <Text fontSize="xs" color="secondaryGray.600" display={{ base: "none", sm: "block" }}>
                  {t("Provider")}
                </Text>
                <Select
                  size="sm"
                  maxW="150px"
                  flexShrink={0}
                  value={newSourceProvider}
                  onChange={(event) => setNewSourceProvider(event.target.value as api.SceneProvider)}
                >
                  {(project?.profile?.modality === "SAR"
                    ? ["iceye", "capella", "umbra", "generic"]
                    : ["pleiades_neo", "worldview", "blacksky", "generic"]
                  ).map((provider) => <option key={provider} value={provider}>{provider}</option>)}
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<MdRefresh />}
                  onClick={handleCheckSceneMigration}
                  isLoading={isCheckingSceneMigration}
                  loadingText={t("Checking migration")}
                  flexShrink={0}
                  whiteSpace="nowrap"
                >
                  {t("Check migration")}
                </Button>
                <Button
                  size="sm"
                  colorScheme="brand"
                  leftIcon={<MdAdd />}
                  onClick={handleAddSceneSource}
                  flexShrink={0}
                  whiteSpace="nowrap"
                >
                  {t("Add source")}
                </Button>
              </HStack>
            </HStack>
            <HStack spacing={2} flexWrap="wrap">
              <Text fontSize="xs" color="secondaryGray.600">{t("Import mode")}</Text>
              <Select
                size="xs"
                maxW="280px"
                value={sceneImportMode}
                isDisabled={isSavingImportMode || Boolean(importJob?.live)}
                onChange={(event) => void handleSceneImportModeChange(event.target.value as api.SceneImportMode)}
              >
                <option value="on_demand">{t("Quick import (on demand)")}</option>
                <option value="background">{t("Prepare pyramids in background")}</option>
                <option value="prepare_all">{t("Prepare everything before opening")}</option>
              </Select>
              {isSavingImportMode && <Spinner size="xs" />}
              <Text fontSize="xs" color="secondaryGray.600">
                {sceneImportMode === "on_demand"
                  ? t("Pyramids are prepared when a scene is opened.")
                  : sceneImportMode === "prepare_all"
                    ? t("New imports wait for all display pyramids.")
                    : t("New imports prepare pyramids with a responsiveness-friendly background profile.")}
              </Text>
            </HStack>
            <HStack spacing={2} flexWrap="wrap">
              <Text fontSize="xs" color="secondaryGray.600">
                {t("Automatic full-resolution COG")}
              </Text>
              <Switch
                size="sm"
                colorScheme="purple"
                isChecked={autoFullresCogEnabled}
                isDisabled={isSavingAutoFullresCog}
                aria-label={t("Automatic full-resolution COG")}
                onChange={(event) => void handleAutoFullresCogChange(event.target.checked)}
              />
              <Badge colorScheme={autoFullresCogEnabled ? "green" : "gray"}>
                {autoFullresCogEnabled ? "ON" : "OFF"}
              </Badge>
              {isSavingAutoFullresCog && <Spinner size="xs" />}
              <Text fontSize="xs" color="secondaryGray.600">
                {autoFullresCogEnabled
                  ? t("A 1x COG is prepared in the background after the JP2 preview becomes ready.")
                  : t("JP2 scenes stay on the overview preview and a 1x COG is not generated automatically.")}
              </Text>
            </HStack>
            {sceneSources.length === 0 ? (
              <Text fontSize="xs" color="secondaryGray.600">{t("No scene sources yet. Add one to catalog scenes.")}</Text>
            ) : (
              <VStack align="stretch" spacing={2}>
                {sceneSources.map((source) => (
                  <HStack
                    key={source.source_id}
                    justify="space-between"
                    spacing={3}
                    borderWidth="1px"
                    borderColor={tableBorderColor}
                    borderRadius="md"
                    px={3}
                    py={2}
                  >
                    <HStack minW={0} flex="1" spacing={2}>
                      <Badge colorScheme="blue" flexShrink={0}>{source.provider}</Badge>
                      <Text fontSize="xs" color="secondaryGray.600" noOfLines={1} title={source.root_path} flex="1">
                        {source.root_path}
                      </Text>
                      <Badge colorScheme={source.available === false ? "red" : "green"} flexShrink={0}>
                        {source.available === false ? t("Source unavailable") : t("Connected")}
                      </Badge>
                    </HStack>
                    <HStack spacing={1} flexShrink={0}>
                      <Button size="xs" variant="ghost" leftIcon={<MdLink />} onClick={() => handleRelinkSource(source.source_id)}>
                        {t("Relink")}
                      </Button>
                      <Button size="xs" variant="ghost" colorScheme="red" leftIcon={<MdDelete />} onClick={() => handleRemoveSceneSource(source)}>
                        {t("Remove")}
                      </Button>
                    </HStack>
                  </HStack>
                ))}
              </VStack>
            )}
          </VStack>
        )}
        <HStack
          justify="space-between"
          px={4}
          py={3}
          bg={tableBg}
          borderBottomWidth="1px"
          borderColor={tableBorderColor}
        >
          <HStack spacing={2}>
            <Text fontWeight="bold" color={textColor}>{t("Scene catalog")}</Text>
            <Badge colorScheme="brand">{sceneFilteredCount}/{sceneTotalCount}</Badge>
            {isScenePageLoading && <Spinner size="xs" />}
          </HStack>
          <Button
            size="sm"
            leftIcon={<MdRefresh />}
            onClick={handleScan}
            variant="outline"
            isLoading={isScanning}
            loadingText={t("Rescanning")}
          >
            {t("Rescan")}
          </Button>
        </HStack>
        <HStack
          px={4}
          py={3}
          spacing={2}
          flexWrap="wrap"
          borderBottomWidth="1px"
          borderColor={tableBorderColor}
          bg={tableBg}
        >
          <Input
            size="sm"
            maxW="260px"
            value={sceneSearch}
            onChange={(event) => setSceneSearch(event.target.value)}
            placeholder={t("Filter scenes")}
          />
          <Select
            size="sm"
            maxW="220px"
            value={sceneAuthorFilter}
            onChange={(event) => setSceneAuthorFilter(event.target.value)}
          >
            <option value="">{t("All authors")}</option>
            {annotationSummary?.per_author.map((item) => (
              <option key={item.annotator_email} value={item.annotator_email}>{item.annotator_email}</option>
            ))}
          </Select>
          <Select
            size="sm"
            maxW="200px"
            value={sceneClassFilter}
            onChange={(event) => setSceneClassFilter(event.target.value)}
          >
            <option value="">{t("All classes")}</option>
            {classes.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </Select>
          <Select
            size="sm"
            maxW="190px"
            value={sceneSourceFilter}
            onChange={(event) => setSceneSourceFilter(event.target.value)}
          >
            <option value="">{t("All sources")}</option>
            {annotationSummary?.per_source.map((item) => (
              <option key={item.annotation_source} value={item.annotation_source}>{item.annotation_source}</option>
            ))}
          </Select>
          <Select
            size="sm"
            maxW="250px"
            value={sceneProvenanceFilter}
            onChange={(event) => setSceneProvenanceFilter(event.target.value)}
          >
            <option value="">{t("All imports and packages")}</option>
            {annotationSummary?.per_import.map((item) => (
              <option key={`import:${item.import_id}`} value={`import:${item.import_id}`}>
                {t("Import")} {item.import_id.slice(0, 8)}
              </option>
            ))}
            {annotationSummary?.per_package.map((item) => (
              <option key={`package:${item.package_id}`} value={`package:${item.package_id}`}>
                {t("Package")} {item.package_id.slice(0, 8)}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSceneSearch("");
              setSceneAuthorFilter("");
              setSceneClassFilter("");
              setSceneSourceFilter("");
              setSceneProvenanceFilter("");
            }}
          >
            {t("Clear filters")}
          </Button>
        </HStack>
        <Box overflowX="auto">
          <Table variant="simple" size="sm" bg={tableBg}>
            <Thead>
              <Tr>
                <Th w="70px" textAlign="center">#</Th>
                <Th></Th>
                <Th>{t("Filename")}</Th>
                <Th>{t("Dimensions")}</Th>
                <Th>{t("Type")}</Th>
                <Th>{t("Metadata")}</Th>
                <Th isNumeric>{t("Status")}</Th>
                <Th textAlign="center">{t("Pyramids")}</Th>
                <Th isNumeric>{t("Annotations")}</Th>
                <Th>{t("Review")}</Th>
                <Th isNumeric>{t("Tiles")}</Th>
                <Th></Th>
              </Tr>
            </Thead>
            <Tbody>
              {!isScenePageLoading && scenes.length === 0 && (
                <Tr>
                  <Td colSpan={12} py={8} textAlign="center">
                    <Text color="secondaryGray.600">
                      {sceneTotalCount
                        ? t("No scenes match filters")
                        : t("No scenes found. Check the source folder or rescan the catalog.")}
                    </Text>
                  </Td>
                </Tr>
              )}
              {pagedScenes.map((scene, index) => (
                <Tr
                  key={scene.id}
                  _hover={{ bg: hoverBg }}
                  bg={doneSceneIds.has(scene.id) ? doneRowBg : undefined}
                  cursor={["prepare_required", "decision_required", "migration_required", "invalid", "runtime_unsupported", "missing_source", "source_changed"].includes(scene.preparation_status || "") ? "default" : "pointer"}
                  onClick={() => {
                    if (!["prepare_required", "decision_required", "migration_required", "invalid", "runtime_unsupported", "missing_source", "source_changed"].includes(scene.preparation_status || "")) {
                      navigate(`/projects/${id}/scenes/${scene.id}/label`);
                    }
                  }}
                >
                  <Td
                    w="70px"
                    onClick={(event) => event.stopPropagation()}
                    cursor="default"
                  >
                    <HStack spacing={2} justify="center">
                      {/*
                        Dymek MUSI opakowywać element, który sam trzyma swój ref.
                        `Tooltip` wiesza nasłuch `pointerleave` na węźle z refa
                        (use-tooltip: useEventListener(() => ref.current, "pointerleave")),
                        a `Checkbox` przekazuje ref do UKRYTEGO `<input>` (1×1 px, clip).
                        Kursor nigdy nad nim nie jest, więc dymek się nie zamykał; drugą
                        drogę wyjścia — `onBlur` — `useCheckbox` wycina z propsów roota.
                        Ten sam wzorzec dotyczy `Switch` i `Radio`.
                      */}
                      <Tooltip label={t("Mark scene as done (personal note)")} openDelay={400}>
                        <Box as="span" display="inline-flex">
                          <Checkbox
                            isChecked={doneSceneIds.has(scene.id)}
                            onChange={() => toggleSceneDone(scene.id)}
                            colorScheme="green"
                          />
                        </Box>
                      </Tooltip>
                      <Text fontSize="xs" color="secondaryGray.600" minW="20px" textAlign="right">
                        {scenePageStart + index + 1}
                      </Text>
                    </HStack>
                  </Td>
                  <Td w="60px" p={1}>
                    <Image
                      src={api.sceneThumbnailUrl(id, scene.id, scene.overview_fingerprint)}
                      w="50px"
                      h="35px"
                      objectFit="cover"
                      borderRadius="4px"
                      loading="lazy"
                      fallback={
                        <Box w="50px" h="35px" bg="secondaryGray.400" borderRadius="4px" />
                      }
                    />
                  </Td>
                  <Td>
                    <Text fontSize="sm" fontWeight="500" color={textColor} noOfLines={1}>
                      {scene.display_name || scene.filename}
                    </Text>
                    <HStack spacing={1} mt={1} flexWrap="wrap">
                      {scene.sensor && (
                        <Badge colorScheme="orange" fontSize="xx-small">
                          {scene.sensor}
                        </Badge>
                      )}
                      {scene.provider && <Badge fontSize="xx-small">{scene.provider}</Badge>}
                      {scene.product_type && <Badge colorScheme="blue" fontSize="xx-small">{scene.product_type}</Badge>}
                      {scene.raster_format && <Badge variant="outline" fontSize="xx-small">{scene.raster_format.toUpperCase()}</Badge>}
                      {!!scene.raster_part_count && scene.raster_part_count > 1 && (
                        <Badge variant="outline" fontSize="xx-small">{t("Parts")}: {scene.raster_part_count}</Badge>
                      )}
                      {!!scene.gsd_m && <Badge colorScheme="cyan" fontSize="xx-small">GSD {scene.gsd_m.toFixed(2)} m</Badge>}
                      {scene.preparation_status && scene.preparation_status !== "ready" && (
                        <Badge colorScheme="orange" fontSize="xx-small">{t(scene.preparation_status)}</Badge>
                      )}
                      {formatAcquisitionDate(scene.acquisition_datetime_utc) && (
                        <Badge colorScheme="cyan" fontSize="xx-small">
                          {formatAcquisitionDate(scene.acquisition_datetime_utc)}
                        </Badge>
                      )}
                    </HStack>
                  </Td>
                  <Td>
                    {scene.scene_info && (
                      <Text fontSize="xs" color="secondaryGray.600">
                        {scene.scene_info.width}x{scene.scene_info.height}
                      </Text>
                    )}
                  </Td>
                  <Td>
                    {scene.modality && (
                      <Badge mr={1} colorScheme={scene.modality === "SAR" ? "purple" : "yellow"} fontSize="xx-small">
                        {scene.modality}
                      </Badge>
                    )}
                    {scene.scene_info?.has_geo && (
                      <Badge colorScheme="green" fontSize="xx-small">GEO</Badge>
                    )}
                    {scene.scene_info && !scene.scene_info.has_geo && (
                      <Badge colorScheme="blue" variant="outline" fontSize="xx-small">
                        NO GEO
                      </Badge>
                    )}
                  </Td>
                  <Td>
                    {scene.metadata_status && (
                      <Badge
                        colorScheme={scene.metadata_status === "ok" ? "green" : "yellow"}
                        fontSize="xx-small"
                        mr={scene.profile_warning_count ? 1 : 0}
                      >
                        {scene.metadata_status}
                      </Badge>
                    )}
                    {!!scene.profile_warning_count && (
                      <Badge colorScheme="orange" fontSize="xx-small">
                        {t("profileWarnings", { count: scene.profile_warning_count })}
                      </Badge>
                    )}
                  </Td>
                  <Td isNumeric>
                    <Badge
                      colorScheme={["tiled", "cataloged"].includes(scene.status) ? "green" : "gray"}
                      fontSize="xx-small"
                    >
                      {t(scene.status)}
                    </Badge>
                  </Td>
                  <Td textAlign="center" onClick={(event) => event.stopPropagation()}>
                    {(() => {
                      const b = overviewBadge(scene);
                      const overviewDetails = [
                        scene.overview_type || "unknown",
                        scene.overview_factors?.length
                          ? `levels: ${scene.overview_factors.join(", ")}`
                          : null,
                      ].filter(Boolean).join("; ");
                      return (
                        <Badge
                          colorScheme={b.color}
                          fontSize="xx-small"
                          display="inline-flex"
                          alignItems="center"
                          gap={1}
                          title={overviewDetails}
                        >
                          {b.spin && <Spinner size="xs" />}
                          {b.label}
                        </Badge>
                      );
                    })()}
                  </Td>
                  <Td isNumeric>
                    <Text fontSize="sm">{scene.annotation_count}</Text>
                  </Td>
                  <Td onClick={(event) => event.stopPropagation()}>
                    {projectRole === "review" ? (
                      <Button
                        size="xs"
                        variant={scene.review_status && scene.review_status !== "none" ? "solid" : "outline"}
                        colorScheme={reviewStatusColor(scene.review_status)}
                        onClick={() => {
                          setReviewScene(scene);
                          setReviewStatus(
                            scene.review_status && scene.review_status !== "none"
                              ? scene.review_status
                              : "accepted"
                          );
                          setReviewComment(scene.review_comment || "");
                          verdictModal.onOpen();
                        }}
                      >
                        {t(reviewStatusLabel(scene.review_status))}
                      </Button>
                    ) : (
                      <Badge colorScheme={reviewStatusColor(scene.review_status)}>
                        {t(reviewStatusLabel(scene.review_status))}
                      </Badge>
                    )}
                    {scene.review_comment && (
                      // Komentarz recenzenta bywa dłuższy (np. instrukcja ze współrzędnymi),
                      // a w tabeli mieści się tylko jedna linia — pełną treść pokazujemy w tooltipie.
                      <Tooltip
                        label={<Box whiteSpace="pre-wrap">{scene.review_comment}</Box>}
                        hasArrow
                        placement="top"
                        openDelay={300}
                        maxW="360px"
                      >
                        <Text
                          fontSize="xs"
                          color="secondaryGray.600"
                          noOfLines={1}
                          maxW="180px"
                          cursor="help"
                        >
                          {scene.review_comment}
                        </Text>
                      </Tooltip>
                    )}
                  </Td>
                  <Td isNumeric>
                    <Text fontSize="sm">{scene.tile_count}</Text>
                  </Td>
                  <Td>
                    <HStack spacing={1}>
                      <Button
                        size="xs"
                        colorScheme="brand"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/projects/${id}/scenes/${scene.id}/label`);
                        }}
                        isDisabled={["prepare_required", "decision_required", "migration_required", "invalid", "runtime_unsupported", "missing_source", "source_changed"].includes(scene.preparation_status || "")}
                      >
                        {t("Label")}
                      </Button>
                      {scene.preparation_status === "prepare_required" && (
                        <>
                          <Button
                            size="xs"
                            colorScheme="teal"
                            isLoading={preparingSceneId === scene.id}
                            onClick={(event) => {
                              event.stopPropagation();
                              handlePrepareScene(scene);
                            }}
                          >
                            {t("Prepare")}
                          </Button>
                          {preparingSceneId === scene.id && (
                            <Button
                              size="xs"
                              colorScheme="red"
                              variant="outline"
                              onClick={(event) => {
                                event.stopPropagation();
                                handleCancelPreparation(scene);
                              }}
                            >
                              {t("Cancel")}
                            </Button>
                          )}
                        </>
                      )}
                      {scene.preparation_status === "decision_required" && (
                        <Button
                          size="xs"
                          colorScheme="orange"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleResolveSceneProduct(scene);
                          }}
                        >
                          {t("Select product")}
                        </Button>
                      )}
                      {scene.source_id && scene.package_id && (
                        <Button
                          size="xs"
                          variant="ghost"
                          colorScheme="red"
                          aria-label={t("Remove scene from project")}
                          title={t("Remove scene from project")}
                          onClick={(event) => {
                            event.stopPropagation();
                            void handleRemoveManagedScene(scene);
                          }}
                        >
                          <MdDelete />
                        </Button>
                      )}
                    </HStack>
                  </Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        </Box>
        {showScenePagination && (
          <HStack
            justify="space-between"
            flexWrap="wrap"
            gap={2}
            px={4}
            py={2}
            borderTopWidth="1px"
            borderColor={tableBorderColor}
          >
            <HStack spacing={2}>
              <Text fontSize="xs" color="secondaryGray.600">{t("Per page")}</Text>
              <Select
                size="xs"
                maxW="90px"
                value={String(scenePageSize)}
                onChange={(e) => setScenePageSize(Number(e.target.value))}
              >
                <option value="50">50</option>
                <option value="100">100</option>
                <option value="250">250</option>
              </Select>
              <Text fontSize="xs" color="secondaryGray.600">
                {t("Showing {{from}}–{{to}} of {{total}}", {
                  from: sceneFilteredCount ? scenePageStart + 1 : 0,
                  to: scenePageStart + pagedScenes.length,
                  total: sceneFilteredCount,
                })}
              </Text>
            </HStack>
            <HStack spacing={1}>
                <Button
                  size="xs"
                  variant="outline"
                  leftIcon={<MdChevronLeft />}
                  isDisabled={scenePage <= 0}
                  onClick={() => setScenePage((p) => Math.max(0, p - 1))}
                >
                  {t("Previous")}
                </Button>
                <Text fontSize="xs" color="secondaryGray.600" px={1}>
                  {t("Page {{page}} of {{count}}", { page: scenePage + 1, count: scenePageCount })}
                </Text>
                <Button
                  size="xs"
                  variant="outline"
                  rightIcon={<MdChevronRight />}
                  isDisabled={!sceneNextCursor || scenePage >= scenePageCount - 1}
                  onClick={() => setScenePage((p) => Math.min(scenePageCount - 1, p + 1))}
                >
                  {t("Next")}
                </Button>
            </HStack>
          </HStack>
        )}
      </Card>

      <Box
        position="sticky"
        bottom={0}
        zIndex={10}
        mx={-2}
        pl={2}
        pr={2}
        py={3}
        bg={stickyBarBg}
        backdropFilter="blur(10px)"
        borderTopWidth="1px"
        borderColor={tableBorderColor}
      >
        <Button
          as={Link}
          to={`/projects/${id}/dataset`}
          w="100%"
          size="lg"
          leftIcon={<MdDataset />}
          colorScheme="brand"
        >
          {t("Open Dataset Workspace")}
        </Button>
      </Box>

      <Modal isOpen={packageModal.isOpen} onClose={packageModal.onClose} size="lg" scrollBehavior="inside">
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>{t("Annotation package preview")}</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            {packagePreview && (
              <VStack align="stretch" spacing={4}>
                <HStack spacing={2} flexWrap="wrap">
                  <Badge colorScheme="blue">{t("sceneCount", { count: packagePreview.scene_count })}</Badge>
                  <Badge colorScheme="purple">
                    {t("annotationCount", { count: packagePreview.annotation_count })}
                  </Badge>
                  <Badge colorScheme="teal">{t("classCount", { count: packagePreview.class_count })}</Badge>
                  <Badge colorScheme={packagePreview.no_geo_scene_count ? "orange" : "green"}>
                    GEO {packagePreview.geo_scene_count} / NO GEO {packagePreview.no_geo_scene_count}
                  </Badge>
                </HStack>
                <Text fontSize="sm">
                  {t("Labeling author email")}: {packagePreview.annotator_email || t("Missing")}
                </Text>
                {prepareActive && (
                  <Box borderWidth="1px" borderRadius="md" p={3}>
                    <Text fontSize="sm" fontWeight="semibold" mb={1}>
                      {t("Preparing package")}
                    </Text>
                    <Text fontSize="xs" color="secondaryGray.600" mb={2} noOfLines={1}>
                      {prepareState?.filename
                        ? t("prepareSceneProgress", {
                            index: prepareState?.scene_index ?? 1,
                            count: prepareState?.scene_count ?? preparePlan?.scene_count ?? 1,
                            filename: prepareState.filename,
                          })
                        : t("Waiting for a free slot")}
                    </Text>
                    <Progress
                      size="sm"
                      borderRadius="md"
                      isIndeterminate={!prepareState?.total_bytes}
                      value={
                        prepareState?.total_bytes
                          ? ((prepareState.done_bytes ?? 0) / prepareState.total_bytes) * 100
                          : 0
                      }
                    />
                    <HStack justify="space-between" mt={2}>
                      <Text fontSize="xs" color="secondaryGray.600">
                        {formatBytes(prepareState?.done_bytes ?? 0)} /{" "}
                        {formatBytes(prepareState?.total_bytes ?? preparePlan?.total_bytes ?? 0)}
                      </Text>
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => void prepareJob.cancel()}
                        isDisabled={prepareState?.cancel_requested}
                      >
                        {prepareState?.cancel_requested ? t("Cancelling") : t("Cancel")}
                      </Button>
                    </HStack>
                  </Box>
                )}
                {!prepareActive && !!preparePlan?.scene_count && (
                  <Alert status="info" borderRadius="md" alignItems="flex-start">
                    <AlertIcon />
                    <VStack align="stretch" spacing={2} flex="1">
                      <Text fontSize="sm">
                        {t("prepareNeeded", {
                          count: preparePlan.scene_count,
                          size: formatBytes(preparePlan.total_bytes),
                        })}
                      </Text>
                      <Button
                        size="sm"
                        alignSelf="flex-start"
                        colorScheme="brand"
                        variant="outline"
                        onClick={handleStartPackagePreparation}
                        isLoading={isStartingPrepare}
                        loadingText={t("Starting")}
                      >
                        {t("Prepare package")}
                      </Button>
                    </VStack>
                  </Alert>
                )}
                {packagePreview.errors.map((message) => (
                  <Alert key={message} status="error" borderRadius="md">
                    <AlertIcon />{message}
                  </Alert>
                ))}
                {packagePreview.warnings.map((message) => (
                  <Alert key={message} status="warning" borderRadius="md">
                    <AlertIcon />{message}
                  </Alert>
                ))}
                <Divider />
                <Text fontSize="sm" color="secondaryGray.600">
                  {t("Annotation package contents description")}
                </Text>
              </VStack>
            )}
          </ModalBody>
          <ModalFooter gap={3}>
            <Button variant="ghost" onClick={packageModal.onClose}>{t("Cancel")}</Button>
            <Button
              colorScheme="brand"
              onClick={handleSaveAnnotationPackage}
              isLoading={isSavingPackage}
              loadingText={t("Saving")}
              isDisabled={!packagePreview?.can_export}
            >
              {t("Save annotation package")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal isOpen={importModal.isOpen} onClose={importModal.onClose} size="xl" scrollBehavior="inside">
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>{t("Annotation import preview")}</ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            {importPreview && (
              <VStack align="stretch" spacing={4}>
                <HStack spacing={2} flexWrap="wrap">
                  <Badge colorScheme="blue">
                    {t("packageCount", { count: importPreview.valid_package_count })}
                  </Badge>
                  <Badge colorScheme="green">
                    {t("matchedSceneCount", { count: importPreview.matched_scene_count })}
                  </Badge>
                  <Badge colorScheme="purple">
                    {t("newAnnotationCount", { count: importPreview.new_annotation_count })}
                  </Badge>
                  <Badge colorScheme="gray">
                    {t("duplicateCount", { count: importPreview.duplicate_annotation_count })}
                  </Badge>
                  <Badge colorScheme={importPreview.changed_annotation_count ? "blue" : "gray"}>
                    {t("changedCount", { count: importPreview.changed_annotation_count })}
                  </Badge>
                  <Badge colorScheme={importPreview.removed_annotation_count ? "red" : "gray"}>
                    {t("removedCount", { count: importPreview.removed_annotation_count })}
                  </Badge>
                </HStack>
                <Text fontSize="sm">
                  {t("Missing scenes")}: {importPreview.missing_scene_count} · {t("Blocked annotations")}: {importPreview.blocked_annotation_count}
                </Text>
                {(importPreview.incompatible_scene_count > 0 || importPreview.approval_required_scene_count > 0) && (
                  <Text fontSize="sm" color="orange.500">
                    {t("Different working view")}: {importPreview.incompatible_scene_count} · {t("Needs identity decision")}: {importPreview.approval_required_scene_count}
                  </Text>
                )}
                {importPreview.identity_policy === "allow_probable" && (
                  <Alert status="warning" borderRadius="md">
                    <AlertIcon />{t("Heuristic identity matching is enabled for this import")}
                  </Alert>
                )}
                {importPreview.identity_policy === "exact_only" &&
                  importPreview.approval_required_scene_count > 0 && (
                    <Box>
                      <Text fontSize="sm" color="secondaryGray.600" mb={2}>
                        {t("Sampled signatures can miss source changes. Enable heuristic matching only after reviewing the packages and target project.")}
                      </Text>
                      <Button
                        size="sm"
                        variant="outline"
                        colorScheme="orange"
                        onClick={handleAllowProbableIdentityMatches}
                        isLoading={isPreviewingImport}
                      >
                        {t("Allow unambiguous heuristic matches")}
                      </Button>
                    </Box>
                  )}
                {Object.keys(importPreview.authors).length > 0 && (
                  <Text fontSize="sm">
                    {t("Authors")}: {Object.entries(importPreview.authors).map(([author, count]) => `${author}: ${count}`).join(", ")}
                  </Text>
                )}

                {importPreview.scene_plans.length > 0 && (
                  <VStack align="stretch" spacing={1}>
                    <Text fontSize="sm" fontWeight="bold">{t("Scenes to accept")}</Text>
                    {importPreview.scene_plans.map((plan) => {
                      const key = `${plan.target_scene_id}:${plan.owner_email}`;
                      const noEffect = !plan.added_count && !plan.changed_count && !plan.removed_count;
                      const disabled = !!plan.block_reason || noEffect;
                      return (
                        <Box
                          key={key}
                          borderWidth="1px"
                          borderRadius="md"
                          borderColor={plan.requires_confirmation ? "orange.400" : "transparent"}
                          bg={plan.requires_confirmation ? "orange.50" : "transparent"}
                          _dark={{ bg: plan.requires_confirmation ? "rgba(251,146,60,0.12)" : "transparent" }}
                          px={2}
                          py={1}
                        >
                          <Checkbox
                            isChecked={acceptedSceneIds.includes(plan.target_scene_id)}
                            isDisabled={disabled}
                            onChange={(event) =>
                              setAcceptedSceneIds((current) =>
                                event.target.checked
                                  ? [...new Set([...current, plan.target_scene_id])]
                                  : current.filter((value) => value !== plan.target_scene_id)
                              )
                            }
                          >
                            <Text fontSize="sm">
                              {plan.source_filename || plan.target_scene_id}
                              {" · "}
                              <Text as="span" color="secondaryGray.600">{plan.owner_email || t("unknown owner")}</Text>
                            </Text>
                          </Checkbox>
                          <Text fontSize="xs" pl={6} color="secondaryGray.600">
                            {plan.before_count} → {plan.after_count}
                            {"  "}
                            <Text as="span" color="green.500">+{plan.added_count}</Text>{" "}
                            <Text as="span" color="blue.400">~{plan.changed_count}</Text>{" "}
                            <Text as="span" color={plan.removed_count ? "red.500" : "secondaryGray.600"}>
                              −{plan.removed_count}
                            </Text>
                            {plan.mode === "append" && `  · ${t("additive package (no removals)")}`}
                            {plan.taken_over_count > 0 && `  · ${t("takenOverCount", { count: plan.taken_over_count })}`}
                          </Text>
                          {plan.requires_confirmation && (
                            <Text fontSize="xs" pl={6} color="orange.500" fontWeight="bold">
                              {t("Removes most of this owner's annotations in the scene - confirm explicitly")}
                            </Text>
                          )}
                          {plan.block_reason && (
                            <Text fontSize="xs" pl={6} color="red.500">
                              {t("Cannot be applied")}: {plan.block_reason}
                            </Text>
                          )}
                        </Box>
                      );
                    })}
                  </VStack>
                )}
                {importPreview.errors.map((message) => (
                  <Alert key={message} status="error" borderRadius="md">
                    <AlertIcon />{message}
                  </Alert>
                ))}
                {importPreview.warnings.map((message) => (
                  <Alert key={message} status="warning" borderRadius="md">
                    <AlertIcon />{message}
                  </Alert>
                ))}
                {!!importPreview.missing_classes.length && (
                  <Checkbox
                    isChecked={createMissingClasses}
                    onChange={(event) => setCreateMissingClasses(event.target.checked)}
                  >
                    {t("Create missing classes")}: {importPreview.missing_classes.join(", ")}
                  </Checkbox>
                )}
                <Divider />
                <Text fontSize="sm" color="secondaryGray.600">
                  {t("Accepting a scene replaces that owner's annotations in it. Other owners' annotations are never touched.")}
                </Text>
              </VStack>
            )}
          </ModalBody>
          <ModalFooter gap={3}>
            <Button variant="ghost" onClick={importModal.onClose}>{t("Cancel")}</Button>
            <Button
              colorScheme="brand"
              onClick={handleApplyAnnotationImport}
              isLoading={isApplyingImport}
              loadingText={t("Importing")}
              isDisabled={
                !importPreview ||
                acceptedSceneIds.length === 0 ||
                (!importPreview.can_apply && !(createMissingClasses && importPreview.can_apply_with_class_creation))
              }
            >
              {t("Apply annotation import")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      {!isTauriRuntime() && (
        <FolderBrowser
          isOpen={isBrowseOpen}
          onClose={onBrowseClose}
          onSelect={handleImportFromBrowser}
          title={t("Select classes.json file")}
          mode="classes_file"
        />
      )}

      {/* Scene verdict — the review unit is the scene, never the single annotation. */}
      <Modal isOpen={verdictModal.isOpen} onClose={verdictModal.onClose} size="lg" isCentered>
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>
            {t("Scene verdict")}: {reviewScene?.display_name || reviewScene?.filename}
          </ModalHeader>
          <ModalCloseButton />
          <ModalBody>
            <VStack align="stretch" spacing={3}>
              <HStack spacing={2} flexWrap="wrap">
                {(["accepted", "needs_fix", "rejected", "none"] as api.ReviewStatus[]).map((status) => (
                  <Button
                    key={status}
                    size="sm"
                    variant={reviewStatus === status ? "solid" : "outline"}
                    colorScheme={reviewStatusColor(status)}
                    onClick={() => setReviewStatus(status)}
                  >
                    {t(reviewStatusLabel(status))}
                  </Button>
                ))}
              </HStack>
              <FormControl>
                <FormLabel fontSize="sm">{t("Comment for the analyst")}</FormLabel>
                <Textarea
                  size="sm"
                  rows={4}
                  value={reviewComment}
                  onChange={(event) => setReviewComment(event.target.value)}
                  placeholder={t("What should be corrected on this scene?")}
                />
              </FormControl>
              {reviewStatus === "needs_fix" && !reviewComment.trim() && (
                <Alert status="warning" borderRadius="md">
                  <AlertIcon />
                  {t("A needs-fix verdict without a comment is hard to act on.")}
                </Alert>
              )}
              {!!reviewScene?.review_pins?.length && (
                <Text fontSize="xs" color="secondaryGray.600">
                  {t("pinnedCount", { count: reviewScene.review_pins.length })}
                </Text>
              )}
              <Text fontSize="xs" color="secondaryGray.600">
                {t("The verdict travels in a review package. It never changes annotations.")}
              </Text>
            </VStack>
          </ModalBody>
          <ModalFooter gap={3}>
            <Button variant="ghost" onClick={verdictModal.onClose}>{t("Cancel")}</Button>
            <Button
              colorScheme="brand"
              onClick={handleSaveSceneVerdict}
              isLoading={isSavingVerdict}
              loadingText={t("Saving")}
            >
              {t("Save verdict")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <ProductDecisionModal request={decisionRequest} onResolve={resolveSceneDecisions} />

      <Modal
        isOpen={sceneMigrationModal.isOpen}
        onClose={isApplyingSceneMigration ? () => undefined : sceneMigrationModal.onClose}
        size="4xl"
        isCentered
        scrollBehavior="inside"
      >
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>{t("Scene package migration")}</ModalHeader>
          <ModalCloseButton isDisabled={isApplyingSceneMigration} />
          <ModalBody>
            {sceneMigrationPlan && (
              <VStack align="stretch" spacing={4}>
                <Alert status={sceneMigrationPlan.summary.needs_selection || sceneMigrationPlan.summary.source_unavailable ? "warning" : "info"}>
                  <AlertIcon />
                  <Box>
                    <Text fontWeight="600">{t("Migration dry-run only")}</Text>
                    <Text fontSize="sm">{t("Migration explanation")}</Text>
                  </Box>
                </Alert>
                <HStack spacing={2} flexWrap="wrap">
                  <Badge colorScheme="gray">{t("Unchanged")}: {sceneMigrationPlan.summary.unchanged}</Badge>
                  <Badge colorScheme="green">{t("Automatic migration")}: {sceneMigrationPlan.summary.auto}</Badge>
                  <Badge colorScheme="orange">{t("Needs selection")}: {sceneMigrationPlan.summary.needs_selection}</Badge>
                  <Badge colorScheme="red">{t("Source unavailable")}: {sceneMigrationPlan.summary.source_unavailable}</Badge>
                </HStack>
                <Box overflowX="auto" borderWidth="1px" borderRadius="md">
                  <Table size="sm">
                    <Thead>
                      <Tr>
                        <Th>{t("Scene")}</Th>
                        <Th>{t("Status")}</Th>
                        <Th>{t("Successor")}</Th>
                        <Th>{t("Changes")}</Th>
                        <Th>{t("Reason")}</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {sceneMigrationPlan.scenes
                        .filter((entry) => entry.classification !== "unchanged")
                        .map((entry) => (
                          <Tr key={entry.scene_id}>
                            <Td maxW="240px">
                              <Text noOfLines={1} title={entry.filename || entry.scene_id}>
                                {entry.filename || entry.scene_id}
                              </Text>
                            </Td>
                            <Td>
                              <Badge colorScheme={
                                entry.classification === "auto"
                                  ? "green"
                                  : entry.classification === "source_unavailable"
                                    ? "red"
                                    : "orange"
                              }>
                                {t(entry.classification)}
                              </Badge>
                            </Td>
                            <Td minW="260px">
                              {entry.classification === "needs_selection" && entry.alternatives?.length ? (
                                <VStack align="stretch" spacing={1}>
                                  <Select
                                    size="sm"
                                    value={sceneMigrationDecisions[entry.scene_id] || ""}
                                    placeholder={t("Select successor")}
                                    onChange={(event) => setSceneMigrationDecisions((current) => ({
                                      ...current,
                                      [entry.scene_id]: event.target.value,
                                    }))}
                                  >
                                    {entry.alternatives.map((alternative) => (
                                      <option key={alternative.package_id} value={alternative.package_id}>
                                        {alternative.label || alternative.package_root_relative || alternative.package_id}
                                        {alternative.product_type ? ` · ${alternative.product_type}` : ""}
                                      </option>
                                    ))}
                                  </Select>
                                  {!sceneMigrationDecisions[entry.scene_id] && (
                                    <Text fontSize="xs" color="orange.400">
                                      {t("Successor selection is required")}
                                    </Text>
                                  )}
                                </VStack>
                              ) : (
                                <Text fontSize="xs" color="secondaryGray.600">-</Text>
                              )}
                            </Td>
                            <Td fontSize="xs">
                              {(
                                entry.alternatives?.find(
                                  (alternative) => alternative.package_id === sceneMigrationDecisions[entry.scene_id],
                                )?.changes
                                || entry.changes
                              ).join(", ") || "-"}
                            </Td>
                            <Td fontSize="xs">{entry.reason}</Td>
                          </Tr>
                        ))}
                    </Tbody>
                  </Table>
                </Box>
                {sceneMigrationPlan.scenes.every((entry) => entry.classification === "unchanged") && (
                  <Text color="secondaryGray.600">{t("No scene migration is required")}</Text>
                )}
                {missingMigrationDecisions > 0 && (
                  <Alert status="warning">
                    <AlertIcon />
                    <Text fontSize="sm">
                      {t("Choose migration successors before applying", { count: missingMigrationDecisions })}
                    </Text>
                  </Alert>
                )}
              </VStack>
            )}
          </ModalBody>
          <ModalFooter gap={2}>
            <Button variant="ghost" onClick={sceneMigrationModal.onClose} isDisabled={isApplyingSceneMigration}>
              {t("Cancel")}
            </Button>
            <Button
              colorScheme="brand"
              onClick={handleApplySceneMigration}
              isLoading={isApplyingSceneMigration}
              loadingText={t("Applying migration")}
              isDisabled={
                !sceneMigrationPlan
                || missingMigrationDecisions > 0
                || sceneMigrationPlan.scenes.every((entry) => entry.classification === "unchanged")
              }
            >
              {t("Apply migration with backup")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </VStack>
  );
}

function ProductDecisionModal({
  request,
  onResolve,
}: {
  request: { title: string; items: SceneDecisionItem[] } | null;
  onResolve: (results: SceneDecisionResult[] | null) => void;
}) {
  const { t } = useTranslation();
  const border = useColorModeValue("gray.200", "whiteAlpha.300");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [rgb, setRgb] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!request) return;
    const nextChoices: Record<string, string> = {};
    const nextRgb: Record<string, string> = {};
    for (const item of request.items) {
      nextChoices[item.id] = item.alternatives.length ? "0" : "skip";
      nextRgb[item.id] = "3,2,1";
    }
    setChoices(nextChoices);
    setRgb(nextRgb);
    setError(null);
  }, [request]);

  if (!request) return null;

  const confirm = () => {
    const results: SceneDecisionResult[] = [];
    for (const item of request.items) {
      const choice = choices[item.id] ?? (item.alternatives.length ? "0" : "skip");
      if (choice === "skip") {
        results.push({ package_id: item.packageId, decision_uid: item.decisionUid, action: "skip", asset_ids: [] });
        continue;
      }
      const alternative = item.alternatives[Number(choice)];
      if (!alternative) {
        setError(t("Invalid product selection"));
        return;
      }
      let rgbBands: number[] | undefined;
      if (item.needsRgb) {
        const parsed = (rgb[item.id] || "")
          .split(/[,;\s]+/)
          .map(Number)
          .filter((value) => Number.isInteger(value) && value > 0);
        if (parsed.length !== 3) {
          setError(t("Provide exactly three RGB band indexes"));
          return;
        }
        rgbBands = parsed;
      }
      results.push({
        package_id: item.packageId,
        decision_uid: item.decisionUid,
        action: "import",
        asset_ids: alternative.asset_ids,
        rgb_bands: rgbBands,
      });
    }
    onResolve(results);
  };

  return (
    <Modal isOpen onClose={() => onResolve(null)} size="xl" isCentered scrollBehavior="inside">
      <ModalOverlay />
      <ModalContent>
        <ModalHeader>{request.title}</ModalHeader>
        <ModalCloseButton />
        <ModalBody>
          <VStack align="stretch" spacing={3}>
            {request.items.map((item) => (
              <Box key={item.id} borderWidth="1px" borderColor={border} borderRadius="md" p={3}>
                <Text fontSize="sm" fontWeight="600" noOfLines={2} title={item.label}>{item.label}</Text>
                <Select
                  mt={2}
                  size="sm"
                  value={choices[item.id] ?? (item.alternatives.length ? "0" : "skip")}
                  onChange={(event) => setChoices((prev) => ({ ...prev, [item.id]: event.target.value }))}
                >
                  {item.alternatives.map((alternative, index) => (
                    <option key={index} value={String(index)}>{alternative.label}</option>
                  ))}
                  {item.allowSkip && <option value="skip">{t("Skip")}</option>}
                </Select>
                {item.needsRgb && choices[item.id] !== "skip" && (
                  <HStack mt={2} spacing={2}>
                    <Text fontSize="xs" color="secondaryGray.600">{t("RGB bands, e.g. 3,2,1")}</Text>
                    <Input
                      size="xs"
                      maxW="120px"
                      value={rgb[item.id] ?? "3,2,1"}
                      onChange={(event) => setRgb((prev) => ({ ...prev, [item.id]: event.target.value }))}
                    />
                  </HStack>
                )}
                {choices[item.id] !== "skip" && (
                  <Alert status="warning" mt={2} borderRadius="md" py={1} px={2} fontSize="xs">
                    <AlertIcon boxSize={3} />
                    {t("Manual product selection consequences")}
                  </Alert>
                )}
              </Box>
            ))}
            {error && <Text color="red.400" fontSize="sm">{error}</Text>}
          </VStack>
        </ModalBody>
        <ModalFooter>
          <Button variant="ghost" mr={2} onClick={() => onResolve(null)}>{t("Cancel")}</Button>
          <Button colorScheme="brand" onClick={confirm}>{t("Confirm")}</Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
