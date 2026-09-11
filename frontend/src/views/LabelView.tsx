import { useState, useEffect, useCallback, useRef } from "react";
import {
  Accordion,
  AccordionButton,
  AccordionIcon,
  AccordionItem,
  AccordionPanel,
  Flex,
  Box,
  VStack,
  Divider,
  Text,
  Switch,
  HStack,
  Spinner,
  Button,
  Progress,
  Tabs,
  TabList,
  TabPanels,
  TabPanel,
  Tab,
  Badge,
  Select,
  useColorModeValue,
  useToast,
  IconButton,
  Tooltip,
} from "@chakra-ui/react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { MdArrowBack, MdChevronRight, MdChevronLeft, MdRateReview, MdPushPin } from "react-icons/md";
import SceneMapCanvas from "../components/canvas/SceneMapCanvas";
import {
  deferDisplayOverviewUntilRasterReady,
  sceneResolutionPhase,
  type SceneOverviewState,
  type SceneResolutionPhase,
} from "../utils/sceneOverviews";
import LayerControl from "../components/canvas/LayerControl";
import MapToolControl from "../components/canvas/MapToolControl";
import AiToolControl from "../components/canvas/AiToolControl";
import HotkeyBar from "../components/common/HotkeyBar";
import InfoPopover from "../components/common/InfoPopover";
import AnnotationList from "../components/sidebar/AnnotationList";
import ComputedAttributesPanel from "../components/sidebar/ComputedAttributesPanel";
import TilingPanel from "../components/panels/TilingPanel";
import DisplayPanel from "../components/panels/DisplayPanel";
import BandSelector from "../components/panels/BandSelector";
import PredictionPanel from "../components/panels/PredictionPanel";
import PredictionResultsPanel from "../components/panels/PredictionResultsPanel";
import SceneMetadataPanel from "../components/panels/SceneMetadataPanel";
import { useAnnotations } from "../hooks/useAnnotations";
import { useHotkeys } from "../hooks/useHotkeys";
import { useTileReview } from "../hooks/useTileReview";
import { usePredictions } from "../hooks/usePredictions";
import { useViewStretch, type SceneBounds } from "../hooks/useViewStretch";
import { readStoredStretchScope, resolveViewWindow, storeStretchScope } from "../utils/viewStretch";
import * as api from "../api/client";
import type {
  AnnotationGeometryPayload,
  AnnotationMode,
  DisplayParams,
  LabelClass,
  MapTool,
  Project,
  PreprocessingProfile,
  SceneHistogram,
  SceneManifest,
  SceneInfo,
  StretchScope,
  TilingConfig,
  TilingPreview,
  ViewStretchWindow,
} from "../types";
import { DEFAULT_DISPLAY_PARAMS, SAR_DISPLAY_PARAMS } from "../types";
import { BASEMAPS } from "../config/basemaps";

const TAB_DRAWING = 0;
// Configuration and review are two phases of one object — the review grid — so they
// share a tab. Splitting them meant the S hotkey had to guess which grid layer the
// user meant from the active tab, and the review tab's empty state could only point
// back at its sibling.
const TAB_GRID = 1;
const TAB_PREDICT = 2;
const TAB_METADATA = 3;

// Werdykt recenzji menedżera — te same statusy co backend (review_package.REVIEW_STATUSES).
const REVIEW_META: Record<string, { label: string; color: string }> = {
  accepted: { label: "Accepted", color: "green" },
  needs_fix: { label: "Needs fix", color: "orange" },
  rejected: { label: "Rejected", color: "red" },
};

// Sections inside the grid tab. The open section also decides which grid layer the
// map shows, so there is no separate layer picker to keep in sync.
const GRID_SECTION_CONFIG = 0;
const GRID_SECTION_REVIEW = 1;

const SCENE_RESOLUTION_META: Record<SceneResolutionPhase, {
  label: string;
  description: string;
  color: string;
  spin?: boolean;
}> = {
  native_preview_loading: {
    label: "Loading native preview",
    description: "GeoTile Label is loading the first view from native JPEG2000 levels before starting background preparation.",
    color: "blue",
    spin: true,
  },
  native_preview_ready: {
    label: "Native preview ready",
    description: "The scene is usable at overview scale. High-resolution optimization will start in the background.",
    color: "cyan",
  },
  high_resolution_queued: {
    label: "High-resolution view queued",
    description: "The native preview remains available while optimized random access waits for an I/O slot.",
    color: "purple",
  },
  high_resolution_building: {
    label: "Preparing high-resolution view",
    description: "The native preview remains available while GeoTile Label prepares faster fine-zoom access.",
    color: "blue",
    spin: true,
  },
  high_resolution_validating: {
    label: "Validating high-resolution view",
    description: "The full-resolution derivative is built and is being checked against the source before it goes live.",
    color: "blue",
    spin: true,
  },
  high_resolution_ready: {
    label: "High-resolution view ready",
    description: "Full source resolution is available for fine zoom levels.",
    color: "green",
  },
  preview_ready_fullres_missing: {
    label: "Preview ready, full resolution pending",
    description: "The scene is usable at overview scale. Full source resolution is not available yet for this source.",
    color: "orange",
  },
  native_ready: {
    label: "Native pyramid ready",
    description: "The source provides display-ready resolution levels; no project pyramid is required.",
    color: "green",
  },
  display_pyramid_pending: {
    label: "Display pyramid pending",
    description: "GeoTile Label will prepare a display pyramid for this source.",
    color: "orange",
  },
  error: {
    label: "High-resolution preparation failed",
    description: "The source preview remains available where possible. Check the background job details and retry preparation.",
    color: "red",
  },
};

const isSingleBandUint16 = (sceneInfo: SceneInfo | null | undefined): boolean => {
  if (!sceneInfo) return false;
  return sceneInfo.channels === 1 && String(sceneInfo.dtype).toLowerCase() === "uint16";
};

const toolRequiresActiveClass = (tool: MapTool): boolean => tool === "draw" || tool === "class_paint";
const ASK_CLASS_AFTER_DRAW_STORAGE_KEY = "geotile.askClassAfterDraw";
const EXEMPLAR_SEARCH_MODE_STORAGE_KEY = "geotile.exemplarSearchMode";
const EXEMPLAR_MATCHING_SETTINGS_STORAGE_KEY = "geotile.exemplarMatchingSettings";
const LAYER_PREFERENCES_STORAGE_KEY = "geotile.label.layerPreferences.v1";
const RIGHT_PANEL_WIDTH_STORAGE_KEY = "geotile.label.rightPanelWidth.v1";

function loadRightPanelWidth(fallback: number, min: number, max: number): number {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(RIGHT_PANEL_WIDTH_STORAGE_KEY);
    const value = raw === null ? NaN : parseInt(raw, 10);
    if (!Number.isFinite(value)) return fallback;
    return Math.min(max, Math.max(min, value));  // klamp do dozwolonego zakresu resize
  } catch {
    return fallback;
  }
}

interface LayerPreferences {
  showAnnotationBoxes: boolean;
  showAnnotationLabels: boolean;
  showSceneRaster: boolean;
  activeBasemapId?: string | null;
}

function loadLayerPreferences(): Partial<LayerPreferences> {
  if (typeof window === "undefined") return {};
  try {
    const value = JSON.parse(window.localStorage.getItem(LAYER_PREFERENCES_STORAGE_KEY) || "{}");
    return typeof value === "object" && value !== null ? value : {};
  } catch {
    return {};
  }
}
const DEFAULT_EXEMPLAR_MATCHING_SETTINGS: api.ExemplarMatchingSettings = {
  scaleTolerance: 0.1,
  rotationToleranceDeg: 20,
  threshold: 0.7,
  useEdges: true,
  dinoThreshold: 0.45,
};

function loadExemplarMatchingSettings(): api.ExemplarMatchingSettings {
  if (typeof window === "undefined") return DEFAULT_EXEMPLAR_MATCHING_SETTINGS;
  try {
    const stored = JSON.parse(window.localStorage.getItem(EXEMPLAR_MATCHING_SETTINGS_STORAGE_KEY) || "{}");
    return {
      scaleTolerance: stored.scaleTolerance === 0 || stored.scaleTolerance === 0.2 ? stored.scaleTolerance : 0.1,
      rotationToleranceDeg: stored.rotationToleranceDeg === 0 || stored.rotationToleranceDeg === 45
        ? stored.rotationToleranceDeg
        : 20,
      threshold: Math.max(0.5, Math.min(0.9, Number(stored.threshold) || 0.7)),
      useEdges: stored.useEdges !== false,
      dinoThreshold: Math.max(0.2, Math.min(1.0, Number(stored.dinoThreshold) || 0.45)),
    };
  } catch {
    return DEFAULT_EXEMPLAR_MATCHING_SETTINGS;
  }
}

export default function LabelView() {
  const { t } = useTranslation();
  const { id, sceneId } = useParams<{ id: string; sceneId: string }>();
  const [searchParams] = useSearchParams();
  const toast = useToast();
  const layerPreferencesRef = useRef(loadLayerPreferences());

  // --- Drawing state ---
  const [classes, setClasses] = useState<LabelClass[]>([]);
  const [activeClassId, setActiveClassId] = useState<number | null>(null);
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null);
  const [multiSelectedAnnotationIds, setMultiSelectedAnnotationIds] = useState<string[]>([]);
  const [selectedPredictionId, setSelectedPredictionId] = useState<string | null>(null);
  const [multiSelectedPredictionIds, setMultiSelectedPredictionIds] = useState<string[]>([]);
  const predictionAnchorRef = useRef<string | null>(null);  // kotwica dla Shift-range w tabeli propozycji
  const [activeMapTool, setActiveMapTool] = useState<MapTool>("draw");
  const [annotationMode, setAnnotationMode] = useState<AnnotationMode>("bbox");
  const [showAnnotationBoxes, setShowAnnotationBoxes] = useState(
    layerPreferencesRef.current.showAnnotationBoxes ?? true
  );
  const [showAnnotationLabels, setShowAnnotationLabels] = useState(
    layerPreferencesRef.current.showAnnotationLabels ?? true
  );
  const [isClassSelectorOpen, setIsClassSelectorOpen] = useState(false);
  const [classSelectorAnnotationId, setClassSelectorAnnotationId] = useState<string | null>(null);
  const [askClassAfterDraw, setAskClassAfterDraw] = useState(() => (
    typeof window !== "undefined" &&
    window.localStorage.getItem(ASK_CLASS_AFTER_DRAW_STORAGE_KEY) === "1"
  ));
  const [focusBBoxRequest, setFocusBBoxRequest] = useState<{
    bbox: [number, number, number, number];
    token: number;
  } | null>(null);

  // --- Scene state ---
  const [project, setProject] = useState<Project | null>(null);
  // Kontrakt zoomu (R0.1): `max_zoom`/`source_max_zoom` to STAŁY poziom referencyjny
  // układu współrzędnych — względem niego liczona jest geometria i adnotacje.
  // `available_native_zoom` to osobna, zmienna wartość: najwyższy poziom, który backend
  // potrafi w tej chwili obsłużyć. Mieszanie ich przesunęłoby adnotacje.
  const [tileInfo, setTileInfo] = useState<{
    width: number;
    height: number;
    max_zoom: number;
    source_max_zoom?: number;
    available_native_zoom?: number;
    available_native_xyz_zoom?: number | null;
    display_asset_revision?: string | null;
    overview_type?: string | null;
    overview_factors?: number[];
    overview_fingerprint?: string | null;
    fullres_eligible?: boolean;
    fullres_auto_start_enabled?: boolean;
    fullres_derivative_status?: string | null;
    fullres_job_id?: string | null;
    fullres_error_code?: string | null;
    fullres_error_message?: string | null;
    fullres_retryable?: boolean;
    preview_factor?: number | null;
    /** Backend obsługuje zakres rozciągnięcia „Widok” (wyłącznik GEOTILE_VIEW_STRETCH). */
    view_stretch_available?: boolean;
  } | null>(null);
  const [sceneInfo, setSceneInfo] = useState<SceneInfo | null>(null);
  const [sceneOverviewState, setSceneOverviewState] = useState<(
    SceneOverviewState & { sceneKey: string }
  ) | null>(null);
  const [sceneManifest, setSceneManifest] = useState<SceneManifest | null>(null);
  const [sceneStatus, setSceneStatus] = useState<string>("");
  // Recenzja menedżera zaimportowana do projektu — pokazujemy ją analitykowi TU, gdzie pracuje.
  const [sceneReview, setSceneReview] = useState<{
    status?: string | null;
    comment?: string | null;
    pins?: Array<{ source_annotation_id: string; comment?: string | null }>;
  } | null>(null);
  const [sceneFilename, setSceneFilename] = useState<string>("");

  // --- Geo mode state ---
  const [activeBasemapId, setActiveBasemapId] = useState<string | null>(
    Object.prototype.hasOwnProperty.call(layerPreferencesRef.current, "activeBasemapId")
      ? layerPreferencesRef.current.activeBasemapId ?? null
      : BASEMAPS[0]?.id ?? null
  );
  const [sceneOpacity, setSceneOpacity] = useState(100);
  const [showSceneRaster, setShowSceneRaster] = useState(
    layerPreferencesRef.current.showSceneRaster ?? true
  );

  // --- Display params state ---
  const [displayParams, setDisplayParams] = useState<DisplayParams>({ ...DEFAULT_DISPLAY_PARAMS });
  const [sceneHistogram, setSceneHistogram] = useState<SceneHistogram | null>(null);

  // --- Zakres statystyk rozciągnięcia: scena albo bieżący widok (DESIGN_DECISIONS.md, display-stretch D) ---
  const [stretchScope, setStretchScope] = useState<StretchScope>(() => readStoredStretchScope(id));
  const [viewFrozen, setViewFrozen] = useState(false);
  const [viewportBounds, setViewportBounds] = useState<SceneBounds | null>(null);
  const [viewWindowState, setViewWindowState] = useState<{
    key: string;
    window: ViewStretchWindow | null;
  }>({ key: "", window: null });
  const viewWindowInputsRef = useRef("");
  const viewStretchAvailable = tileInfo?.view_stretch_available === true;
  const viewScopeActive = stretchScope === "view" && viewStretchAvailable;
  const {
    key: viewKey,
    stats: viewStats,
    status: viewStatus,
  } = useViewStretch({
    projectId: id,
    sceneId,
    revision: tileInfo?.display_asset_revision ?? null,
    enabled: viewScopeActive,
    frozen: viewFrozen,
    bounds: viewportBounds,
  });

  useEffect(() => {
    setStretchScope(readStoredStretchScope(id));
    setViewFrozen(false);
  }, [id]);

  useEffect(() => {
    setViewportBounds(null);
    setViewFrozen(false);
  }, [id, sceneId]);

  // Okno progów dla wszystkich kafli widoku. Histereza tylko dla ruchu mapy — zmiana
  // nastawy percentyli ma zadziałać zawsze, nawet gdy przesuwa progi o włos.
  useEffect(() => {
    const low = displayParams.stretch_low;
    const high = displayParams.stretch_high;
    if (!viewScopeActive || !viewStats) {
      viewWindowInputsRef.current = "";
      setViewWindowState({ key: viewKey, window: null });
      return;
    }
    const inputs = `${viewKey}|${low}|${high}`;
    const sameInputs = viewWindowInputsRef.current === inputs;
    viewWindowInputsRef.current = inputs;
    setViewWindowState((current) => ({
      key: viewKey,
      window: resolveViewWindow({
        view: viewStats,
        scene: sceneHistogram,
        stretchLow: low,
        stretchHigh: high,
        previous: sameInputs && current.key === viewKey ? current.window : null,
      }),
    }));
  }, [
    viewScopeActive,
    viewStats,
    viewKey,
    sceneHistogram,
    displayParams.stretch_low,
    displayParams.stretch_high,
  ]);
  const activeViewWindow = viewScopeActive && viewWindowState.key === viewKey
    ? viewWindowState.window
    : null;

  const handleStretchScopeChange = useCallback((scope: StretchScope) => {
    setStretchScope(scope);
    setViewFrozen(false);
    storeStretchScope(id, scope);
  }, [id]);
  // RGB band selection for local multiband scenes; bandVersion forces a scene reload.
  const [sceneBandsSelection, setSceneBandsSelection] = useState<number[] | undefined>(undefined);
  // Wybór pasm buduje VRT nad plikiem źródłowym i przestawia widok roboczy sceny.
  // Dla scen z paczki dostawcy widok roboczy należy do paczki (warianty, kolejność
  // części, powiązanie z derywatem pełnej rozdzielczości), więc backend takiej sceny
  // nie przyjmuje. Bez tego panel oferował kontrolkę, która zawsze kończyła się
  // błędem „Band selection applies only to local multiband scenes”.
  const [scenePackageId, setScenePackageId] = useState<string | null>(null);
  const [bandVersion, setBandVersion] = useState(0);
  const [bandBusy, setBandBusy] = useState(false);
  const activeSceneRasterKey = `${id || ""}:${sceneId || ""}`;
  const sceneRasterReadyKeyRef = useRef<string | null>(null);
  const fullresStartKeyRef = useRef<string | null>(null);
  const deferredOverviewSceneKeyRef = useRef<string | null>(null);
  const [deferredOverviewReleaseKey, setDeferredOverviewReleaseKey] = useState<string | null>(null);
  const [preprocessingProfiles, setPreprocessingProfiles] = useState<PreprocessingProfile[]>([]);
  const [displayProfileId, setDisplayProfileId] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const preferences: LayerPreferences = {
      showAnnotationBoxes,
      showAnnotationLabels,
      showSceneRaster,
      activeBasemapId,
    };
    window.localStorage.setItem(LAYER_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
  }, [activeBasemapId, showAnnotationBoxes, showAnnotationLabels, showSceneRaster]);

  // --- Tiling state ---
  const [tilingConfig, setTilingConfig] = useState<TilingConfig>({ tile_size: 640, buffer: 0 });
  const [tilingPreview, setTilingPreview] = useState<TilingPreview | null>(null);
  const [showPreviewGrid, setShowPreviewGrid] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [tilingProgress, setTilingProgress] = useState<{ done: number; total: number } | null>(null);

  // --- Review / tile grid state ---
  const [showTileGrid, setShowTileGrid] = useState(false);
  const [tileActionMode, setTileActionMode] = useState<"review" | "clear" | "exclude">("review");
  const hoveredTileRef = useRef<number | null>(null);
  const getVisibleTilesRef = useRef<() => number[]>(() => []);
  const getVisibleSceneBoundsRef = useRef<() => [number, number, number, number] | null>(() => null);
  const focusTokenRef = useRef(0);
  // SAM3-tekst „narysuj obszar": gdy ustawione, następny narysowany prostokąt jest obszarem
  // wyszukiwania (nie adnotacją). runSamTextRef pozwala wywołać run z handlera rysowania,
  // który jest zdefiniowany wcześniej niż runSamText.
  const samTextRegionRef = useRef<{ prompt: string; conf: number } | null>(null);
  const runSamTextRef = useRef<
    ((bbox: [number, number, number, number], prompt: string, conf: number) => void) | null
  >(null);

  // --- Tab state ---
  const initialTab = searchParams.get("tab") === "tiling" ? TAB_GRID : TAB_DRAWING;
  const [tabIndex, setTabIndex] = useState(initialTab);
  const [gridSection, setGridSection] = useState<number>(GRID_SECTION_CONFIG);

  const { annotations, refresh: refreshAnnotations, addAnnotation, removeAnnotation, updateAnnotation } = useAnnotations(id, sceneId);
  const {
    tiles,
    progress,
    refresh: refreshTiles,
    toggleReview,
    markReviewed,
    clearReviewed,
    toggleExclude,
    markExcluded,
  } = useTileReview(id, sceneId);

  const {
    predictions,
    isRunning: isPredicting,
    progress: predProgress,
    pendingCount,
    acceptedCount,
    load: loadPredictions,
    run: runPrediction,
    cancel: cancelPrediction,
    acceptPreds,
    deletePreds,
    acceptAll: acceptAllPreds,
    clearAll: clearAllPreds,
  } = usePredictions(id, sceneId);

  const [showPredictions, setShowPredictions] = useState(true);
  const [samCheckpoint, setSamCheckpoint] = useState<string | null>(null);
  const [samModels, setSamModels] = useState<api.SamModelInfo[]>([]);
  const [samModelsDirPath, setSamModelsDirPath] = useState<string | null>(null);
  const samBusyRef = useRef(false);
  const [findSimilarBusy, setFindSimilarBusy] = useState(false);
  const [exemplarEngine, setExemplarEngine] = useState<api.ExemplarEngine>("template");
  const [exemplarSearchMode, setExemplarSearchMode] = useState<api.ExemplarSearchMode>(() => {
    if (typeof window === "undefined") return "local";
    const stored = window.localStorage.getItem(EXEMPLAR_SEARCH_MODE_STORAGE_KEY);
    return stored === "viewport" || stored === "scene" ? stored : "local";
  });
  const [exemplarMatchingSettings, setExemplarMatchingSettings] = useState<api.ExemplarMatchingSettings>(
    loadExemplarMatchingSettings,
  );

  const textColor = useColorModeValue("navy.700", "white");
  const panelBg = useColorModeValue("white", "navy.800");
  const mutedColor = useColorModeValue("gray.500", "whiteAlpha.600");
  const borderColor = useColorModeValue("secondaryGray.200", "whiteAlpha.300");
  const resizeHandleBg = useColorModeValue("gray.200", "whiteAlpha.200");
  const resizeHandleHoverBg = useColorModeValue("brand.200", "brand.400");
  const panelBorderColor = useColorModeValue("gray.200", "whiteAlpha.100");
  // Cień rzucany w stronę mapy — to on odkleja panel od jej płaszczyzny.
  const panelShadow = useColorModeValue(
    "-8px 0 24px rgba(112, 144, 176, 0.20)",
    "-8px 0 24px rgba(0, 0, 0, 0.35)"
  );

  const handleOpenClassSelector = useCallback(() => {
    setClassSelectorAnnotationId(null);
    setIsClassSelectorOpen(true);
  }, []);

  const handleCloseClassSelector = useCallback(() => {
    setClassSelectorAnnotationId(null);
    setIsClassSelectorOpen(false);
  }, []);

  const handleAskClassAfterDrawChange = useCallback((enabled: boolean) => {
    setAskClassAfterDraw(enabled);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(ASK_CLASS_AFTER_DRAW_STORAGE_KEY, enabled ? "1" : "0");
    }
  }, []);

  const handleSelectClass = useCallback(async (classId: number) => {
    setActiveClassId(classId);
    if (classSelectorAnnotationId) {
      try {
        await updateAnnotation(classSelectorAnnotationId, { class_id: classId });
      } catch (err: any) {
        toast({
          title: t("Failed to update annotation"),
          description: err?.response?.data?.detail || err?.message || "Unknown error",
          status: "error",
          duration: 3000,
        });
      }
    }
    setClassSelectorAnnotationId(null);
    setIsClassSelectorOpen(false);
  }, [classSelectorAnnotationId, updateAnnotation, toast, t]);

  // --- Right panel resize state ---
  const RIGHT_PANEL_MIN = 280;
  const RIGHT_PANEL_MAX = 600;
  const RIGHT_PANEL_DEFAULT = 340;
  // Szerokość prawego panelu zapamiętana z poprzedniej sesji (jak warstwy/podpisy boxów).
  const [rightPanelWidth, setRightPanelWidth] = useState(() =>
    loadRightPanelWidth(RIGHT_PANEL_DEFAULT, RIGHT_PANEL_MIN, RIGHT_PANEL_MAX),
  );
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const isResizingRef = useRef(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingRef.current = true;
    startXRef.current = e.clientX;
    startWidthRef.current = rightPanelWidth;

    const handleMouseMove = (ev: MouseEvent) => {
      if (!isResizingRef.current) return;
      const delta = startXRef.current - ev.clientX;
      const newWidth = Math.min(RIGHT_PANEL_MAX, Math.max(RIGHT_PANEL_MIN, startWidthRef.current + delta));
      setRightPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      isResizingRef.current = false;
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, [rightPanelWidth]);

  // Zapamiętaj szerokość prawego panelu między sesjami.
  useEffect(() => {
    try {
      window.localStorage.setItem(RIGHT_PANEL_WIDTH_STORAGE_KEY, String(rightPanelWidth));
    } catch {
      /* localStorage niedostępny — pomiń */
    }
  }, [rightPanelWidth]);

  // Airborne NITF (SENSOR_GEO): label in native sensor pixel geometry, never on a
  // map. The working raster carries GCPs, so sceneInfo.has_geo is true, but the
  // canonical geometry is non-linear TPS (manifest.geometry), not an affine map —
  // force the pixel canvas and hide the basemap. Decide by PROFILE, not scene_info.
  const isSensorProject = project?.profile?.georeferencing === "SENSOR_GEO";
  const geoMode = !isSensorProject && !!(sceneInfo?.has_geo && sceneInfo?.bounds);
  const currentSceneOverview = sceneOverviewState?.sceneKey === activeSceneRasterKey
    ? sceneOverviewState
    : null;
  const nativeRasterPreviewReady = (
    sceneRasterReadyKeyRef.current === activeSceneRasterKey ||
    deferredOverviewReleaseKey === activeSceneRasterKey
  );
  const resolutionPhase = currentSceneOverview
    ? sceneResolutionPhase(currentSceneOverview, nativeRasterPreviewReady)
    : null;
  const resolutionMeta = resolutionPhase ? SCENE_RESOLUTION_META[resolutionPhase] : null;
  const activeBasemapUrl = BASEMAPS.find((item) => item.id === activeBasemapId)?.url ?? null;
  const isTiled = sceneStatus === "tiled" || sceneStatus === "cataloged";
  const tileSize = tilingConfig.tile_size;

  // Once the grid exists the configuration phase is over, so hand the tab to review
  // rather than making the user expand a second section to see what they just built.
  // Scoped to the grid tab so it can never switch a map layer on under someone who is
  // drawing. It does not depend on the open section, so a deliberate return to
  // configuration is left alone.
  useEffect(() => {
    if (!isTiled || tabIndex !== TAB_GRID) return;
    setGridSection(GRID_SECTION_REVIEW);
    setShowPreviewGrid(false);
    setShowTileGrid(true);
  }, [isTiled, tabIndex]);
  const metadataWarningCount = sceneManifest?.profile_validation?.warnings?.length || 0;
  const drawingMode = activeMapTool === "draw";

  // â”€â”€â”€ Data loading â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  // Load classes once â€” use ref to avoid re-fetching when activeClassId changes
  const activeClassIdRef = useRef(activeClassId);
  activeClassIdRef.current = activeClassId;

  const loadClasses = useCallback(async () => {
    if (!id) return;
    const data = await api.listClasses(id);
    setClasses(data);
    if (data.length > 0 && activeClassIdRef.current === null) {
      setActiveClassId(data[0].id);
    }
  }, [id]);

  useEffect(() => {
    loadClasses();
  }, [loadClasses]);

  const refreshSceneTileInfo = useCallback(async () => {
    if (!id || !sceneId) return null;
    const next = await api.getSceneTileInfo(id, sceneId);
    setTileInfo(next);
    setSceneOverviewState((current) => ({
      sceneKey: activeSceneRasterKey,
      overview_status: next.preview_status ?? current?.overview_status,
      overview_type: next.overview_type ?? current?.overview_type,
      preview_status: next.preview_status,
      fullres_derivative_status: next.fullres_derivative_status,
      available_native_zoom: next.available_native_zoom,
      source_max_zoom: next.source_max_zoom,
    }));
    return next;
  }, [activeSceneRasterKey, id, sceneId]);

  const requestFullresDerivative = useCallback(async (retryFailed = false) => {
    if (!id || !sceneId || !tileInfo?.fullres_eligible) return;
    if (!retryFailed && tileInfo.fullres_auto_start_enabled === false) return;
    const status = String(tileInfo.fullres_derivative_status || "missing");
    if (!retryFailed && !["missing", "stale"].includes(status)) return;
    const startKey = `${activeSceneRasterKey}:${tileInfo.display_asset_revision || "none"}`;
    if (!retryFailed && fullresStartKeyRef.current === startKey) return;
    fullresStartKeyRef.current = startKey;
    try {
      const result = await api.startSceneFullresDerivative(id, sceneId, retryFailed);
      setTileInfo((current) => current ? {
        ...current,
        fullres_derivative_status: result.fullres_derivative_status || "queued",
        fullres_job_id: result.fullres_job_id || current.fullres_job_id,
        fullres_error_code: null,
        fullres_error_message: null,
      } : current);
      setSceneOverviewState((current) => current ? {
        ...current,
        fullres_derivative_status: result.fullres_derivative_status || "queued",
      } : current);
    } catch (error: any) {
      fullresStartKeyRef.current = null;
      if (retryFailed) {
        toast({
          title: t("Could not start high-resolution preparation"),
          description: error?.response?.data?.detail?.error || error?.response?.data?.detail || error?.message,
          status: "error",
        });
      }
    }
  }, [activeSceneRasterKey, id, sceneId, t, tileInfo, toast]);

  const handleSceneRasterReady = useCallback(() => {
    sceneRasterReadyKeyRef.current = activeSceneRasterKey;
    if (deferredOverviewSceneKeyRef.current === activeSceneRasterKey) {
      setDeferredOverviewReleaseKey(activeSceneRasterKey);
    }
    void requestFullresDerivative(false);
  }, [activeSceneRasterKey, requestFullresDerivative]);

  useEffect(() => {
    const jobId = tileInfo?.fullres_job_id;
    const status = String(tileInfo?.fullres_derivative_status || "");
    if (!id || !sceneId || !jobId || !["queued", "building", "validating"].includes(status)) {
      return;
    }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const detail = await api.getJob(id, jobId);
        if (disposed) return;
        const jobStatus = String(detail.state.status || "");
        const phase = String(detail.state.phase || "");
        if (jobStatus === "completed") {
          const before = tileInfo?.display_asset_revision;
          const next = await refreshSceneTileInfo();
          if (!disposed && next?.display_asset_revision !== before) {
            setBandVersion((value) => value + 1);
          }
          return;
        }
        if (["failed", "interrupted", "cancelled"].includes(jobStatus)) {
          await refreshSceneTileInfo();
          return;
        }
        const nextStatus = phase.includes("validat")
          ? "validating"
          : jobStatus === "queued" ? "queued" : "building";
        setTileInfo((current) => current ? {
          ...current,
          fullres_derivative_status: nextStatus,
        } : current);
        setSceneOverviewState((current) => current ? {
          ...current,
          fullres_derivative_status: nextStatus,
        } : current);
      } catch {
        // Durable state remains on disk; a transient API failure is retried.
      }
      if (!disposed) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, refreshSceneTileInfo, sceneId, tileInfo?.display_asset_revision,
    tileInfo?.fullres_derivative_status, tileInfo?.fullres_job_id]);

  const handleCancelFullres = useCallback(async () => {
    if (!id || !tileInfo?.fullres_job_id) return;
    try {
      await api.cancelJob(id, tileInfo.fullres_job_id);
      await refreshSceneTileInfo();
    } catch (error: any) {
      toast({
        title: t("Could not cancel high-resolution preparation"),
        description: error?.response?.data?.detail || error?.message,
        status: "error",
      });
    }
  }, [id, refreshSceneTileInfo, t, tileInfo?.fullres_job_id, toast]);

  // Opening a scene gives its display pyramid interactive priority. Large JPEG2000
  // sources are the exception: their native levels first render the fitted viewport,
  // and only then do we start the competing full-raster overview read.
  useEffect(() => {
    if (!id || !sceneId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const pollUntilReady = async () => {
      if (disposed) return;
      try {
        const scene = await api.getScene(id, sceneId);
        const status = String(scene?.overview_status || "pending");
        if (!disposed) {
          setSceneOverviewState({
            sceneKey: activeSceneRasterKey,
            overview_status: status,
            overview_type: scene?.overview_type,
          });
        }
        if (status === "ready" || status === "native") {
          if (!disposed) setBandVersion((value) => value + 1);
          return;
        }
        if (status === "error") return;
      } catch {
        // A transient read failure should not block the labeling view.
      }
      if (!disposed) timer = setTimeout(pollUntilReady, 1500);
    };

    const requestWhenPreviewAllows = async () => {
      try {
        const scene = await api.getScene(id, sceneId);
        if (disposed) return;
        const status = String(scene?.overview_status || "pending");
        setSceneOverviewState({
          sceneKey: activeSceneRasterKey,
          overview_status: status,
          overview_type: scene?.overview_type,
        });
        if (status === "ready" || status === "native") return;

        if (deferDisplayOverviewUntilRasterReady(scene)) {
          deferredOverviewSceneKeyRef.current = activeSceneRasterKey;
          const previewReady = (
            sceneRasterReadyKeyRef.current === activeSceneRasterKey ||
            deferredOverviewReleaseKey === activeSceneRasterKey
          );
          if (!previewReady) return;
        } else if (deferredOverviewSceneKeyRef.current === activeSceneRasterKey) {
          deferredOverviewSceneKeyRef.current = null;
        }

        const result = await api.requestSceneDisplayOverview(id, sceneId);
        if (!disposed) {
          setSceneOverviewState({
            sceneKey: activeSceneRasterKey,
            overview_status: result.status,
            overview_type: scene?.overview_type,
          });
        }
        if (!disposed && result.action !== "already_ready") void pollUntilReady();
      } catch {
        // The source remains the display fallback; preparation can be retried later.
      }
    };

    void requestWhenPreviewAllows();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeSceneRasterKey, deferredOverviewReleaseKey, id, sceneId]);

  // Load tile info + scene info + tiling config
  useEffect(() => {
    if (!id || !sceneId) return;
    Promise.all([
      api.getProject(id),
      api.getScene(id, sceneId),
      api.getPreprocessingProfiles(id),
    ]).then(([loadedProject, sceneData, profilesFile]: [Project, any, any]) => {
      setProject(loadedProject);
      const mode = loadedProject.profile?.annotation_mode;
      setAnnotationMode(mode === "rotated_bbox" ? "rotated_bbox" : "bbox");

      // Zapisana preferencja moze wskazywac warstwe, ktorej juz nie ma (np. podklad
      // usunietego projektu Basemap GEO) — wtedy wracamy do pierwszego znanego podkladu.
      const storedBasemapId = layerPreferencesRef.current.activeBasemapId;
      if (
        storedBasemapId !== null && storedBasemapId !== undefined &&
        !BASEMAPS.some((item) => item.id === storedBasemapId)
      ) {
        setActiveBasemapId(BASEMAPS[0]?.id ?? null);
      }

      const info = sceneData.scene_info ?? null;
      setSceneInfo(info);
      setSceneOverviewState({
        sceneKey: activeSceneRasterKey,
        overview_status: sceneData.overview_status,
        overview_type: sceneData.overview_type,
      });
      setSceneBandsSelection(sceneData.rgb_bands);
      setScenePackageId(sceneData.package_id ?? null);
      setSceneStatus(sceneData.status || "");
      setSceneReview({
        status: sceneData.review_status,
        comment: sceneData.review_comment,
        pins: sceneData.review_pins || [],
      });
      setSceneFilename(sceneData.filename || sceneData.display_name || sceneId);

      const profiles = (profilesFile.profiles || []) as PreprocessingProfile[];
      setPreprocessingProfiles(profiles);
      const preferredId = loadedProject.profile?.default_preprocessing_profile;
      const selected = profiles.find((item) => item.profile_id === preferredId)
        || profiles.find((item) => item.modality === loadedProject.profile?.modality);
      // SAR dostaje na mapie własne ustawienia zamiast nastaw profilu datasetu — profil
      // nadal steruje generowaniem datasetu (DESIGN_DECISIONS.md, display-stretch C).
      const sceneIsSar = String(
        sceneData.modality || loadedProject.profile?.modality || "",
      ).toUpperCase() === "SAR";
      if (selected) {
        setDisplayProfileId(selected.profile_id);
        setDisplayParams(sceneIsSar ? { ...SAR_DISPLAY_PARAMS } : {
          brightness: selected.brightness,
          contrast: selected.contrast,
          gamma: selected.gamma,
          stretch_low: selected.stretch_low,
          stretch_high: selected.stretch_high,
        });
      } else {
        setDisplayParams({
          ...(sceneIsSar || isSingleBandUint16(info) ? SAR_DISPLAY_PARAMS : DEFAULT_DISPLAY_PARAMS),
        });
      }
    }).catch((error) => {
      toast({
        title: t("Cannot load scene configuration"),
        description: error?.message || String(error),
        status: "error",
      });
    });
    void refreshSceneTileInfo();
    setSceneHistogram(null);
    api.getSceneHistogram(id, sceneId)
      .then(setSceneHistogram)
      .catch(() => setSceneHistogram(null));
    api.getSceneManifest(id, sceneId)
      .then(setSceneManifest)
      .catch(() => setSceneManifest(null));
    api.getTilingConfig(id).then(setTilingConfig);
    api.getPredictionConfig(id)
      .then(async (cfg) => {
        setExemplarEngine(cfg?.exemplar_engine === "dino" ? "dino" : "template");
        try {
          const result = await api.listSamModels(id);
          setSamModels((result.models || []).filter((model) => model.supported));
          setSamModelsDirPath(result.models_dir_path || null);
          setSamCheckpoint(cfg?.sam_checkpoint || result.default_checkpoint || null);
        } catch {
          setSamModels([]);
          setSamModelsDirPath(null);
          setSamCheckpoint(cfg?.sam_checkpoint || null);
        }
      })
      .catch(() => {
        setSamCheckpoint(null);
        setSamModels([]);
        setSamModelsDirPath(null);
      });
    loadPredictions();
  }, [id, sceneId, loadPredictions, bandVersion, refreshSceneTileInfo]);

  // Deep-link „kafel → scena" z podglądu opublikowanego datasetu (DI-F): ?focus=x0,y0,x1,y1
  // w pikselach sceny. Odpalamy fokus raz, gdy scena jest gotowa (tileInfo załadowane),
  // reużywając mechanizmu focusBBox używanego przez dwuklik adnotacji.
  const appliedFocusRef = useRef<string | null>(null);
  useEffect(() => {
    const focus = searchParams.get("focus");
    if (!focus || !tileInfo) return;
    const key = `${sceneId}:${focus}`;
    if (appliedFocusRef.current === key) return;
    const parts = focus.split(",").map(Number);
    if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return;
    appliedFocusRef.current = key;
    focusTokenRef.current += 1;
    setFocusBBoxRequest({
      bbox: parts as [number, number, number, number],
      token: focusTokenRef.current,
    });
  }, [searchParams, tileInfo, sceneId]);

  // Load tiling preview â€” use ref for tilingConfig to avoid re-fetch loops
  const tilingConfigRef = useRef(tilingConfig);
  tilingConfigRef.current = tilingConfig;

  const loadPreview = useCallback(async () => {
    if (!id || !sceneId) return;
    try {
      const p = await api.getTilingPreview(id, sceneId);
      setTilingPreview(p);
    } catch {
      setTilingPreview(null);
    }
  }, [id, sceneId]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  // Load tile metadata for legacy tiled scenes and metadata-only catalogs.
  useEffect(() => {
    if (!id || !sceneId || !["tiled", "cataloged"].includes(sceneStatus)) return;
    refreshTiles();
  }, [id, sceneId, sceneStatus, refreshTiles]);

  useEffect(() => {
    const annotationIds = new Set(annotations.map((ann) => ann.id));
    if (selectedAnnotationId && !annotationIds.has(selectedAnnotationId)) {
      setSelectedAnnotationId(null);
    }
    setMultiSelectedAnnotationIds((prev) => prev.filter((annId) => annotationIds.has(annId)));
  }, [annotations, selectedAnnotationId]);

  useEffect(() => {
    const predictionIds = new Set(
      predictions.filter((prediction) => prediction.status === "pending").map((prediction) => prediction.id)
    );
    if (selectedPredictionId && !predictionIds.has(selectedPredictionId)) {
      setSelectedPredictionId(null);
    }
    setMultiSelectedPredictionIds((previous) => previous.filter((predictionId) => predictionIds.has(predictionId)));
  }, [predictions, selectedPredictionId]);

  // â”€â”€â”€ Tiling handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const handleConfigChange = async (newConfig: TilingConfig) => {
    if (!id) return;
    setTilingConfig(newConfig);
    await api.updateTilingConfig(id, newConfig);
    loadPreview();
  };

  const handleApplyBands = async (rgbBands: number[]) => {
    if (!id || !sceneId) return;
    setBandBusy(true);
    try {
      await api.setSceneRgbBands(id, sceneId, rgbBands);
      setSceneBandsSelection(rgbBands);
      setBandVersion((version) => version + 1); // reload scene + remount tiles
      toast({ title: t("RGB bands updated"), status: "success", duration: 1500 });
    } catch (err: any) {
      toast({
        title: t("Band selection failed"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
      });
    } finally {
      setBandBusy(false);
    }
  };

  const handleCopyDisplayToDataset = async (
    params: DisplayParams,
    sourceProfileId?: string | null,
  ) => {
    if (!id) return;
    const source = preprocessingProfiles.find((item) => item.profile_id === sourceProfileId)
      || preprocessingProfiles.find((item) => item.modality === project?.profile?.modality);
    if (!source) {
    toast({ title: t("No preprocessing profile available"), status: "warning" });
      return;
    }

    try {
      const existing = preprocessingProfiles.find((item) => item.profile_id === "display_copy");
      const saved = await api.putPreprocessingProfile(id, {
        ...source,
        profile_id: "display_copy",
        name: "Display settings copy",
        description: t("Dataset profile copied from the Display panel."),
        profile_version: existing?.profile_version || 1,
        profile_hash: "",
        brightness: params.brightness,
        contrast: params.contrast,
        gamma: params.gamma,
        stretch_low: params.stretch_low,
        stretch_high: params.stretch_high,
        builtin: false,
      });
      setPreprocessingProfiles((current) => [
        ...current.filter((item) => item.profile_id !== saved.profile_id),
        saved,
      ]);
      const datasetConfig = await api.getDatasetConfig(id);
      await api.updateDatasetConfig(id, {
        ...datasetConfig,
        preprocessing_profile_id: saved.profile_id,
      });
      toast({
        title: t("Dataset profile updated"),
        description: `${saved.name} v${saved.profile_version} (${saved.profile_hash.slice(0, 12)})`,
        status: "success",
      });
    } catch (error: any) {
      toast({
        title: t("Cannot copy display settings"),
        description: error.response?.data?.detail || error.message,
        status: "error",
      });
    }
  };

  const handleExecute = async () => {
    if (!id || !sceneId) return;
    setIsExecuting(true);
    setTilingProgress(null);

    try {
      const response = await fetch(api.tilingExecuteUrl(id), {
        method: "POST",
        headers: api.authHeaders(),
      });
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
              let data: any;
              try {
                data = JSON.parse(line.slice(5).trim());
              } catch {
                continue;
              }
              if (data.error) {
                throw new Error(data.error);
              }
              if (data.done !== undefined && data.total !== undefined) {
                setTilingProgress({ done: data.done, total: data.total });
              }
            }
          }
        }
      }

      // Refresh scene status after tiling
      const sceneData = await api.getScene(id, sceneId);
      setSceneStatus(sceneData.status || "");
      refreshTiles();
      toast({ title: t("Review grid created"), status: "success", duration: 3000 });
    } catch (err: any) {
      toast({ title: t("Review grid creation failed"), description: err.message, status: "error" });
    } finally {
      setIsExecuting(false);
      setTilingProgress(null);
    }
  };

  // â”€â”€â”€ Drawing handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const getGeometryBbox = (
    geometry: AnnotationGeometryPayload
  ): [number, number, number, number] | null => {
    return geometry.bbox ?? null;
  };

  const handleAnnotationCreate = useCallback(
    async (geometry: AnnotationGeometryPayload, classIdOverride?: number) => {
      // Tryb „narysuj obszar" dla SAM3-tekst: narysowany prostokąt idzie jako obszar
      // wyszukiwania, nie tworzy adnotacji.
      if (samTextRegionRef.current) {
        const region = getGeometryBbox(geometry);
        const pending = samTextRegionRef.current;
        samTextRegionRef.current = null;
        if (region) runSamTextRef.current?.(region, pending.prompt, pending.conf);
        return;
      }
      const classId = classIdOverride ?? activeClassId;
      if (classId === null) return;
      const bbox = getGeometryBbox(geometry);
      if (!bbox) return;
      try {
        const ann = await addAnnotation(classId, geometry);
        if (ann?.id) {
          setSelectedAnnotationId(ann.id);
          if (askClassAfterDraw) {
            setClassSelectorAnnotationId(ann.id);
            setIsClassSelectorOpen(true);
          }
        }

        // Auto-mark overlapping tiles as reviewed
        if (tiles.length > 0) {
          const overlapping: number[] = [];
          for (let i = 0; i < tiles.length; i++) {
            const t = tiles[i];
            const tx1 = t.x0 + tileSize;
            const ty1 = t.y0 + tileSize;
            if (bbox[0] < tx1 && bbox[2] > t.x0 && bbox[1] < ty1 && bbox[3] > t.y0) {
              if (!t.reviewed) overlapping.push(i);
            }
          }
          if (overlapping.length > 0) {
            await markReviewed(overlapping);
          }
        }
      } catch (err: any) {
        toast({
          title: t("Failed to create annotation"),
          description: err?.response?.data?.detail || err?.message || "Unknown error",
          status: "error",
          duration: 3000,
        });
      }
    },
    [activeClassId, addAnnotation, askClassAfterDraw, tiles, tileSize, markReviewed, toast]
  );

  const handleAnnotationUpdate = useCallback(
    async (annId: string, geometry: AnnotationGeometryPayload) => {
      try {
        await updateAnnotation(annId, geometry);
      } catch (err: any) {
        toast({
          title: t("Failed to update annotation"),
          description: err?.response?.data?.detail || err?.message || "Unknown error",
          status: "error",
          duration: 3000,
        });
      }
    },
    [updateAnnotation, toast]
  );

  const handleAnnotationClassChange = useCallback(
    async (annId: string, classId: number) => {
      try {
        await updateAnnotation(annId, { class_id: classId });
        const className = classes.find((c) => c.id === classId)?.name ?? String(classId);
        toast({
          title: `${t("Class changed to")}: ${className}`,
          status: "success",
          duration: 1800,
        });
      } catch (err: any) {
        toast({
          title: t("Failed to update annotation"),
          description: err?.response?.data?.detail || err?.message || "Unknown error",
          status: "error",
          duration: 3000,
        });
      }
    },
    [updateAnnotation, toast, t, classes]
  );

  const handleAnnotationMultiSelect = useCallback((annIds: string[]) => {
    setMultiSelectedAnnotationIds(annIds);
    setSelectedAnnotationId(annIds.length === 1 ? annIds[0] : null);
  }, []);

  const handlePredictionMultiSelect = useCallback((predictionIds: string[]) => {
    setMultiSelectedPredictionIds(predictionIds);
    setSelectedPredictionId(predictionIds.length === 1 ? predictionIds[0] : null);
  }, []);

  const handleApplyActiveClassToSelection = useCallback(async () => {
    if (activeClassId === null || multiSelectedAnnotationIds.length === 0) return;
    try {
      await Promise.all(
        multiSelectedAnnotationIds.map((annId) => updateAnnotation(annId, { class_id: activeClassId }))
      );
    } catch (err: any) {
      toast({
        title: t("Failed to update annotation"),
        description: err?.response?.data?.detail || err?.message || "Unknown error",
        status: "error",
        duration: 3000,
      });
    }
  }, [activeClassId, multiSelectedAnnotationIds, updateAnnotation, toast, t]);

  const handleDeleteAnnotation = useCallback(
    async (annId: string) => {
      await removeAnnotation(annId);
      if (selectedAnnotationId === annId) {
        setSelectedAnnotationId(null);
      }
      setMultiSelectedAnnotationIds((prev) => prev.filter((id) => id !== annId));
    },
    [removeAnnotation, selectedAnnotationId]
  );

  const handleDeletePredictions = useCallback(async (predictionIds: string[]) => {
    if (predictionIds.length === 0) return;
    try {
      await deletePreds(predictionIds);
      const deletedIds = new Set(predictionIds);
      setMultiSelectedPredictionIds((previous) => previous.filter((id) => !deletedIds.has(id)));
      setSelectedPredictionId((previous) => previous && deletedIds.has(previous) ? null : previous);
    } catch (err: any) {
      toast({
        title: t("Failed to delete AI proposals"),
        description: err?.response?.data?.detail || err?.message || "Unknown error",
        status: "error",
        duration: 3000,
      });
    }
  }, [deletePreds, toast, t]);

  const handleDeleteSelectedObjects = useCallback(() => {
    const annotationIds = multiSelectedAnnotationIds.length > 0
      ? multiSelectedAnnotationIds
      : (selectedAnnotationId ? [selectedAnnotationId] : []);
    const predictionIds = multiSelectedPredictionIds.length > 0
      ? multiSelectedPredictionIds
      : (selectedPredictionId ? [selectedPredictionId] : []);
    if (drawingMode && predictionIds.length === 0) return;
    if (annotationIds.length === 0 && predictionIds.length === 0) return;

    void Promise.all([
      ...annotationIds.map((annId) => removeAnnotation(annId)),
      handleDeletePredictions(predictionIds),
    ]).then(() => {
        setMultiSelectedAnnotationIds([]);
        setSelectedAnnotationId(null);
        setMultiSelectedPredictionIds([]);
        setSelectedPredictionId(null);
      });
  }, [
    drawingMode,
    multiSelectedAnnotationIds,
    selectedAnnotationId,
    multiSelectedPredictionIds,
    selectedPredictionId,
    removeAnnotation,
    handleDeletePredictions,
  ]);

  const handleToggleDrawingMode = useCallback(() => {
    if (activeMapTool === "draw") {
      setActiveMapTool("select");
      return;
    }
    setTabIndex(TAB_DRAWING);
    setShowPreviewGrid(false);
    setShowTileGrid(false);
    setActiveMapTool("draw");
  }, [activeMapTool]);

  const handleMapToolChange = useCallback((tool: MapTool) => {
    if (tool === "draw") {
      setTabIndex(TAB_DRAWING);
      setShowPreviewGrid(false);
      setShowTileGrid(false);
    } else if (tool === "grid_review" || tool === "grid_clear" || tool === "grid_exclude") {
      setTabIndex(TAB_GRID);
      setGridSection(GRID_SECTION_REVIEW);
      setShowPreviewGrid(false);
      setShowTileGrid(true);
      if (tool === "grid_review") setTileActionMode("review");
      if (tool === "grid_clear") setTileActionMode("clear");
      if (tool === "grid_exclude") setTileActionMode("exclude");
    } else if (tool !== "select") {
      setShowPreviewGrid(false);
      setShowTileGrid(false);
    }
    setActiveMapTool(tool);
  }, []);

  const exemplarSupported = true;
  const exemplarReady = true;
  // A path alone is not enough: the desktop runtime must also contain the SAM
  // dependencies. This prevents presenting an enabled tool that can only fail.
  const samCapabilities = api.getCachedCapabilities().sam;
  const samRuntimeReady =
    samCapabilities?.runtime_available ?? !!api.getCachedCapabilities().ultralytics;
  const samReady = !!samCapabilities?.mock || (
    samRuntimeReady && (!!samCheckpoint || !!samCapabilities?.bundled_checkpoint)
  );

  const handleSamClick = useCallback(async (x: number, y: number) => {
    if (!id || !sceneId || samBusyRef.current) return;
    if (activeClassId === null) {
      toast({ title: t("Select an active class first"), status: "warning", duration: 2500 });
      return;
    }
    samBusyRef.current = true;
    try {
      // SB3: zaznaczona adnotacja tej klasy zadaje rozmiar odniesienia (prior rozmiaru SB2).
      const reference = annotations.find(
        (a) => a.id === selectedAnnotationId && a.class_id === activeClassId && a.bbox
      );
      const result = await api.samClick(id, sceneId, {
        x, y,
        geometry_type: annotationMode === "rotated_bbox" ? "rotated_bbox" : "bbox",
        class_id: activeClassId,
        size_prior_bbox: reference?.bbox ?? null,
      });
      setShowPredictions(true);
      await loadPredictions();
      if (result.needs_front_direction) {
        toast({
          title: t("Oriented box proposed"),
          description: t("Confirm the front direction before accepting."),
          status: "info",
          duration: 3500,
        });
      }
    } catch (err: any) {
      const status = err?.response?.status;
      toast({
        title: status === 422 ? t("SAM found nothing here") : t("SAM click failed"),
        description: err?.response?.data?.detail || err?.message,
        status: status === 422 ? "warning" : "error",
        duration: 3500,
      });
    } finally {
      samBusyRef.current = false;
    }
  }, [id, sceneId, activeClassId, annotationMode, annotations, selectedAnnotationId, loadPredictions, toast, t]);

  const [samTextBusy, setSamTextBusy] = useState(false);
  // Tryb tekstowy wymaga SAM3 (z CLIP). Mock zawsze OK; inaczej po nazwie checkpointu.
  const sam3Ready = !!samCapabilities?.mock || (samCheckpoint?.toLowerCase().includes("sam3") ?? false);

  const [samTextPrompt, setSamTextPrompt] = useState("");
  const [samTextConf, setSamTextConf] = useState(0.25);

  const runSamText = useCallback(async (
    searchBbox: [number, number, number, number], prompt: string, conf: number,
  ) => {
    if (!id || !sceneId || activeClassId === null) return;
    setSamTextBusy(true);
    try {
      const result = await api.samText(id, sceneId, {
        class_id: activeClassId,
        search_bbox: searchBbox,
        text: prompt.trim() || undefined,
        confidence_threshold: conf,
        geometry_type: annotationMode === "rotated_bbox" ? "rotated_bbox" : "bbox",
      });
      setShowPredictions(true);
      await loadPredictions();
      toast({
        title: result.found > 0 ? t("similarFound", { count: result.found }) : t("SAM3 found nothing in view"),
        status: result.found > 0 ? "success" : "info",
        duration: 3000,
      });
    } catch (err: any) {
      const status = err?.response?.status;
      toast({
        title: status === 422 ? t("SAM3 found nothing in view") : t("SAM3 text failed"),
        description: err?.response?.data?.detail || err?.message,
        status: status === 422 ? "warning" : "error",
        duration: 4000,
      });
    } finally {
      setSamTextBusy(false);
    }
  }, [id, sceneId, activeClassId, annotationMode, loadPredictions, toast, t]);
  runSamTextRef.current = runSamText;  // dostępne dla handlera rysowania regionu

  // Otwarcie popovera SAM3-tekst → prefill promptu nazwą aktywnej klasy (edytowalny).
  const handleSamText = useCallback(() => {
    const activeName = classes.find((c) => c.id === activeClassId)?.name ?? "";
    setSamTextPrompt((prev) => prev || activeName);
  }, [activeClassId, classes]);

  const runSamTextCurrentView = useCallback(() => {
    const bounds = getVisibleSceneBoundsRef.current();
    if (!bounds) {
      toast({ title: t("The current map view does not intersect the scene"), status: "warning", duration: 3000 });
      return;
    }
    runSamText(bounds, samTextPrompt, samTextConf);
  }, [runSamText, samTextPrompt, samTextConf, toast, t]);

  const startSamTextRegionDraw = useCallback(() => {
    samTextRegionRef.current = { prompt: samTextPrompt, conf: samTextConf };
    setActiveMapTool("draw");
    toast({ title: t("Draw a rectangle for the search area"), status: "info", duration: 3500 });
  }, [samTextPrompt, samTextConf, toast, t]);

  const handleSamModelSelect = useCallback(async (checkpoint: string | null) => {
    if (!id) return;
    try {
      const config = await api.getPredictionConfig(id);
      const saved = await api.updatePredictionConfig(id, { ...config, sam_checkpoint: checkpoint });
      setSamCheckpoint(saved.sam_checkpoint || null);
      toast({
        title: t("SAM model selected"),
        description: checkpoint?.split(/[/\\]/).pop() || t("Automatic model selection"),
        status: "success",
        duration: 2500,
      });
    } catch (error: any) {
      toast({
        title: t("Failed to save config"),
        description: error?.response?.data?.detail || error?.message,
        status: "error",
      });
    }
  }, [id, toast, t]);

  const handleSamModelsChange = useCallback((result: api.SamModelsResult) => {
    setSamModels((result.models || []).filter((model) => model.supported));
    setSamModelsDirPath(result.models_dir_path || null);
  }, []);

  // Wybór silnika egzemplarza jest trwały (config projektu) — mapa i panel Predykcja
  // czytają/zapisują tę samą wartość, więc się nie rozjeżdżają.
  const handleExemplarEngineChange = useCallback(async (engine: api.ExemplarEngine) => {
    setExemplarEngine(engine);  // optymistycznie
    if (!id) return;
    try {
      const config = await api.getPredictionConfig(id);
      const saved = await api.updatePredictionConfig(id, { ...config, exemplar_engine: engine });
      setExemplarEngine(saved.exemplar_engine === "dino" ? "dino" : "template");
    } catch {
      /* zostaje wartość optymistyczna; następne wejście odczyta config */
    }
  }, [id]);

  const handleFindSimilar = useCallback(async () => {
    if (!id || !sceneId) return;
    // Zbiór wzorców: zaznaczona ramka + multi-selekcja (bez duplikatów). Główny = pierwszy.
    const orderedIds = selectedAnnotationId
      ? [selectedAnnotationId, ...multiSelectedAnnotationIds.filter((x) => x !== selectedAnnotationId)]
      : multiSelectedAnnotationIds;
    const exemplarAnns = orderedIds
      .map((x) => annotations.find((a) => a.id === x))
      .filter((a): a is NonNullable<typeof a> => !!a && Array.isArray(a.bbox));
    if (exemplarAnns.length === 0) return;
    const exemplar = exemplarAnns[0];
    // Few-shot (silnik DINO): dodatkowe wzorce TYLKO tej samej klasy co główny — mieszanie klas
    // rozmyłoby prototyp. Szablon NCC używa jednego wzorca, więc dla niego pomijamy.
    const extraBboxes = exemplarEngine === "dino"
      ? exemplarAnns.slice(1).filter((a) => a.class_id === exemplar.class_id).map((a) => a.bbox)
      : [];

    const searchMode = exemplarSearchMode;
    const visibleBounds = searchMode === "viewport" ? getVisibleSceneBoundsRef.current() : null;
    if (searchMode === "viewport" && !visibleBounds) {
      toast({
        title: t("The current map view does not intersect the scene"),
        status: "warning",
        duration: 3000,
      });
      return;
    }
    setFindSimilarBusy(true);
    try {
      const result = await api.findSimilar(id, sceneId, {
        exemplar_bbox: exemplar.bbox,
        class_id: exemplar.class_id,
        geometry_type: exemplar.geometry_type === "rotated_bbox" ? "rotated_bbox" : "bbox",
        exemplar_rotated_bbox: exemplar.rotated_bbox || null,
        exemplar_polygon_scene_px: exemplar.polygon_scene_px || null,
        exemplar_front_vector: exemplar.front_vector_scene_px || null,
        engine: exemplarEngine,
        extra_exemplar_bboxes: extraBboxes.length > 0 ? extraBboxes : undefined,
        search_mode: searchMode,
        search_bbox: visibleBounds,
        threshold: exemplarEngine === "dino"
          ? exemplarMatchingSettings.dinoThreshold
          : exemplarMatchingSettings.threshold,
        scale_tolerance: exemplarMatchingSettings.scaleTolerance,
        rotation_tolerance_deg: exemplarMatchingSettings.rotationToleranceDeg,
        use_edges: exemplarMatchingSettings.useEdges,
      });
      setShowPredictions(true);
      await loadPredictions();
      const shots = 1 + extraBboxes.length;
      toast({
        title: result.found > 0 ? t("similarFound", { count: result.found }) : t("No similar objects found"),
        description: shots > 1 ? t("Prototype from {{count}} examples", { count: shots }) : undefined,
        status: result.found > 0 ? "success" : "info",
        duration: 3000,
      });
    } catch (err: any) {
      toast({
        title: t("Find similar failed"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
        duration: 3500,
      });
    } finally {
      setFindSimilarBusy(false);
    }
  }, [
    id,
    sceneId,
    selectedAnnotationId,
    multiSelectedAnnotationIds,
    annotations,
    exemplarEngine,
    exemplarSearchMode,
    exemplarMatchingSettings,
    loadPredictions,
    toast,
    t,
  ]);

  const handleExemplarSearchModeChange = useCallback((mode: api.ExemplarSearchMode) => {
    setExemplarSearchMode(mode);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(EXEMPLAR_SEARCH_MODE_STORAGE_KEY, mode);
    }
  }, []);

  const handleExemplarMatchingSettingsChange = useCallback((settings: api.ExemplarMatchingSettings) => {
    setExemplarMatchingSettings(settings);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(EXEMPLAR_MATCHING_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
    }
  }, []);

  const handleRotateFront = useCallback(async (proposalId: string) => {
    if (!id || !sceneId) return;
    try {
      await api.rotateProposalFront(id, sceneId, proposalId);
      await loadPredictions();
    } catch (err: any) {
      toast({
        title: t("Could not rotate front direction"),
        description: err?.response?.data?.detail || err?.message,
        status: "error",
        duration: 3000,
      });
    }
  }, [id, sceneId, loadPredictions, toast, t]);

  // Exactly one grid layer is ever visible: the configuration preview while the grid
  // is being set up, the review grid once it exists. Both the switch and the S hotkey
  // route through this, so they cannot disagree about which layer they mean.
  const usesPreviewLayer = !isTiled || gridSection === GRID_SECTION_CONFIG;
  const gridVisible = usesPreviewLayer ? showPreviewGrid : showTileGrid;

  const handleToggleGridVisibility = useCallback(() => {
    if (usesPreviewLayer) {
      setShowPreviewGrid((current) => !current);
      setShowTileGrid(false);
      return;
    }
    setShowTileGrid((current) => !current);
    setShowPreviewGrid(false);
  }, [usesPreviewLayer]);

  const handleToggleSceneRaster = useCallback(() => {
    setShowSceneRaster((current) => !current);
  }, []);

  const handleDecreaseSceneOpacity = useCallback(() => {
    setSceneOpacity((current) => Math.max(0, current - 10));
  }, []);

  const handleIncreaseSceneOpacity = useCallback(() => {
    setSceneOpacity((current) => Math.min(100, current + 10));
  }, []);

  const handleDecreaseDisplayGamma = useCallback(() => {
    setDisplayParams((current) => ({
      ...current,
      gamma: Number(Math.max(0.1, current.gamma - 0.05).toFixed(2)),
    }));
  }, []);

  const handleIncreaseDisplayGamma = useCallback(() => {
    setDisplayParams((current) => ({
      ...current,
      gamma: Number(Math.min(5, current.gamma + 0.05).toFixed(2)),
    }));
  }, []);

  const handleAnnotationSelect = useCallback((annId: string | null) => {
    setSelectedAnnotationId(annId);
    setMultiSelectedAnnotationIds([]);
    setSelectedPredictionId(null);
    setMultiSelectedPredictionIds([]);
  }, []);

  // Pin recenzji wskazuje adnotację po source_annotation_id (lub id) — zaznacz ją analitykowi.
  const handleReviewPinClick = useCallback((sourceAnnotationId: string) => {
    const match = annotations.find(
      (a) => a.source_annotation_id === sourceAnnotationId || a.id === sourceAnnotationId,
    );
    if (match) handleAnnotationSelect(match.id);
  }, [annotations, handleAnnotationSelect]);

  const handleAnnotationDoubleClick = useCallback((annId: string) => {
    const ann = annotations.find((item) => item.id === annId);
    if (!ann) return;
    setSelectedAnnotationId(annId);
    setMultiSelectedAnnotationIds([]);
    setSelectedPredictionId(null);
    setMultiSelectedPredictionIds([]);
    focusTokenRef.current += 1;
    setFocusBBoxRequest({
      bbox: [...ann.bbox] as [number, number, number, number],
      token: focusTokenRef.current,
    });
  }, [annotations]);

  const handlePredictionSelect = useCallback((
    predictionId: string | null, modifiers?: { ctrl: boolean; shift: boolean },
  ) => {
    // Wybór propozycji zawsze czyści selekcję adnotacji.
    setSelectedAnnotationId(null);
    setMultiSelectedAnnotationIds([]);

    // Zwykły klik (albo z mapy, bez modyfikatorów) → pojedynczy wybór.
    if (predictionId === null || !modifiers || (!modifiers.ctrl && !modifiers.shift)) {
      setSelectedPredictionId(predictionId);
      setMultiSelectedPredictionIds([]);
      predictionAnchorRef.current = predictionId;
      return;
    }

    // Bieżący zbiór zaznaczonych = multi ∪ pojedynczy.
    const current = new Set(multiSelectedPredictionIds);
    if (selectedPredictionId) current.add(selectedPredictionId);

    if (modifiers.shift) {
      // Zakres od kotwicy do klikniętego, w kolejności listy oczekujących.
      const pendingIds = predictions.filter((p) => p.status === "pending").map((p) => p.id);
      const anchor = predictionAnchorRef.current ?? selectedPredictionId ?? predictionId;
      const i0 = pendingIds.indexOf(anchor);
      const i1 = pendingIds.indexOf(predictionId);
      if (i0 !== -1 && i1 !== -1) {
        const [lo, hi] = i0 <= i1 ? [i0, i1] : [i1, i0];
        for (let i = lo; i <= hi; i++) current.add(pendingIds[i]);
      } else {
        current.add(predictionId);
      }
    } else {
      // Ctrl → przełącz pojedynczą propozycję.
      if (current.has(predictionId)) current.delete(predictionId);
      else current.add(predictionId);
      predictionAnchorRef.current = predictionId;
    }

    // Trzymamy cały zbiór w multi (pojedynczy „przenosimy" do zbioru), żeby stan był spójny.
    setSelectedPredictionId(null);
    setMultiSelectedPredictionIds([...current]);
  }, [predictions, selectedPredictionId, multiSelectedPredictionIds]);

  const handlePredictionDoubleClick = useCallback((predictionId: string) => {
    const prediction = predictions.find((item) => item.id === predictionId);
    if (!prediction || prediction.bbox.length !== 4) return;
    setSelectedPredictionId(predictionId);
    setMultiSelectedPredictionIds([]);
    setSelectedAnnotationId(null);
    setMultiSelectedAnnotationIds([]);
    focusTokenRef.current += 1;
    setFocusBBoxRequest({
      bbox: [...prediction.bbox] as [number, number, number, number],
      token: focusTokenRef.current,
    });
  }, [predictions]);

  // â”€â”€â”€ Review handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  const handleTileClick = useCallback(
    (index: number) => {
      if (tileActionMode === "exclude") {
        if (activeMapTool === "grid_exclude") {
          markExcluded([index]);
        } else {
          toggleExclude(index);
        }
      } else if (tileActionMode === "clear") {
        clearReviewed([index]);
      } else if (activeMapTool === "grid_review") {
        markReviewed([index]);
      } else {
        toggleReview(index);
      }
    },
    [activeMapTool, clearReviewed, markExcluded, markReviewed, tileActionMode, toggleExclude, toggleReview]
  );

  // A paint stroke arrives as one batch. The per-tile endpoints were already
  // array-based, so this only stops the canvas from calling them one tile at a time.
  const handleTilePaintCommit = useCallback(
    (indices: number[]) => {
      if (indices.length === 0) return;
      if (tileActionMode === "exclude") {
        markExcluded(indices);
      } else if (tileActionMode === "clear") {
        clearReviewed(indices);
      } else {
        markReviewed(indices);
      }
    },
    [clearReviewed, markExcluded, markReviewed, tileActionMode]
  );

  const handleMarkVisibleReviewed = useCallback(() => {
    const indices = getVisibleTilesRef.current();
    const unreviewedIndices = indices.filter(
      (i) => tiles[i] && !tiles[i].excluded && !tiles[i].reviewed
    );
    if (unreviewedIndices.length > 0) {
      markReviewed(unreviewedIndices);
    }
  }, [tiles, markReviewed]);

  const handleMarkVisibleExcluded = useCallback(() => {
    const indices = getVisibleTilesRef.current();
    const includableIndices = indices.filter(
      (i) => tiles[i] && !tiles[i].excluded
    );
    if (includableIndices.length > 0) {
      markExcluded(includableIndices);
    }
  }, [tiles, markExcluded]);

  const handleMarkVisibleUnreviewed = useCallback(() => {
    const indices = getVisibleTilesRef.current();
    const reviewedIndices = indices.filter(
      (i) => tiles[i] && !tiles[i].excluded && tiles[i].reviewed
    );
    if (reviewedIndices.length > 0) {
      clearReviewed(reviewedIndices);
    }
  }, [clearReviewed, tiles]);

  const handleUndo = useCallback(() => {
    if (annotations.length > 0) {
      const lastId = annotations[annotations.length - 1].id;
      removeAnnotation(lastId);
      if (selectedAnnotationId === lastId) {
        setSelectedAnnotationId(null);
      }
    }
  }, [annotations, removeAnnotation, selectedAnnotationId]);

  const handleReviewHotkey = useCallback(() => {
    if (hoveredTileRef.current !== null) {
      toggleReview(hoveredTileRef.current);
    }
  }, [toggleReview]);

  const handleReviewAllHotkey = useCallback(() => {
    handleMarkVisibleReviewed();
  }, [handleMarkVisibleReviewed]);

  const handleExcludeHotkey = useCallback(() => {
    if (hoveredTileRef.current !== null) {
      toggleExclude(hoveredTileRef.current);
    }
  }, [toggleExclude]);

  const handleExcludeAllHotkey = useCallback(() => {
    handleMarkVisibleExcluded();
  }, [handleMarkVisibleExcluded]);

  useHotkeys({
    classes,
    onSelectClass: setActiveClassId,
    onUndo: handleUndo,
    onEscape: () => {
      setActiveClassId(null);
      setMultiSelectedAnnotationIds([]);
      setSelectedAnnotationId(null);
      setMultiSelectedPredictionIds([]);
      setSelectedPredictionId(null);
    },
    onToggleDrawing: handleToggleDrawingMode,
    onOpenClassSelector: handleOpenClassSelector,
    onSelectMapTool: handleMapToolChange,
    onToggleGrid: handleToggleGridVisibility,
    onToggleSceneRaster: handleToggleSceneRaster,
    onDecreaseSceneOpacity: handleDecreaseSceneOpacity,
    onIncreaseSceneOpacity: handleIncreaseSceneOpacity,
    onDecreaseDisplayGamma: handleDecreaseDisplayGamma,
    onIncreaseDisplayGamma: handleIncreaseDisplayGamma,
    onDeleteSelected: selectedPredictionId !== null || multiSelectedPredictionIds.length > 0 ||
      activeMapTool === "select" || activeMapTool === "multi_select"
      ? handleDeleteSelectedObjects
      : undefined,
    onReviewTile: showTileGrid ? handleReviewHotkey : undefined,
    onReviewAllVisible: showTileGrid ? handleReviewAllHotkey : undefined,
    onExcludeTile: showTileGrid ? handleExcludeHotkey : undefined,
    onExcludeAllVisible: showTileGrid ? handleExcludeAllHotkey : undefined,
  });

  // --- Prediction handlers (wrap to refresh annotations after accept) ---

  const handleAcceptPreds = useCallback(
    async (ids: string[]) => {
      const result = await acceptPreds(ids);
      const resolvedIds = new Set(ids);
      setMultiSelectedPredictionIds((previous) => previous.filter((id) => !resolvedIds.has(id)));
      setSelectedPredictionId((previous) => previous && resolvedIds.has(previous) ? null : previous);
      if (result?.missing_classes?.length) {
        toast({
          title: t("Some predictions were not accepted"),
          description: `${t("Missing project classes")}: ${result.missing_classes.join(", ")}`,
          status: "warning",
          duration: 6000,
        });
      }
      refreshAnnotations();
    },
    [acceptPreds, refreshAnnotations, toast]
  );

  const handleAcceptAllPreds = useCallback(async () => {
    const result = await acceptAllPreds();
    setSelectedPredictionId(null);
    setMultiSelectedPredictionIds([]);
    if (result?.missing_classes?.length) {
      toast({
        title: t("Some predictions were not accepted"),
        description: `${t("Missing project classes")}: ${result.missing_classes.join(", ")}`,
        status: "warning",
        duration: 6000,
      });
    }
    refreshAnnotations();
  }, [acceptAllPreds, refreshAnnotations, toast]);

  const handleClearAllPreds = useCallback(async () => {
    await clearAllPreds();
    setSelectedPredictionId(null);
    setMultiSelectedPredictionIds([]);
  }, [clearAllPreds]);

  // â"€â"€â"€ Tab change logic â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

  // Opening a grid section picks the matching layer. Kept next to the tab handler so
  // the two entry points into the grid tab stay visibly consistent.
  const handleGridSectionChange = (index: number) => {
    setGridSection(index);
    if (index === GRID_SECTION_REVIEW && isTiled) {
      setShowPreviewGrid(false);
      setShowTileGrid(true);
    } else if (index === GRID_SECTION_CONFIG) {
      setShowPreviewGrid(true);
      setShowTileGrid(false);
    }
    // index === -1 (everything collapsed) leaves the map untouched on purpose:
    // collapsing a section to read the map should not blank the layer.
  };

  const handleTabChange = (index: number) => {
    setTabIndex(index);
    if (index === TAB_GRID) {
      // An existing grid means the review phase is the useful one; otherwise there is
      // nothing to review yet and configuration is where the user has to start.
      const section = isTiled ? GRID_SECTION_REVIEW : GRID_SECTION_CONFIG;
      setGridSection(section);
      setShowPreviewGrid(!isTiled);
      setShowTileGrid(isTiled);
      setActiveMapTool("select");
    } else if (index === TAB_PREDICT) {
      setShowPreviewGrid(false);
      setShowTileGrid(false);
      setShowPredictions(true);
      setActiveMapTool("select");
    } else if (index === TAB_METADATA) {
      setShowPreviewGrid(false);
      setShowTileGrid(false);
      setActiveMapTool("select");
    } else {
      // Drawing tab
      setShowPreviewGrid(false);
      setShowTileGrid(false);
      setActiveMapTool("draw");
    }
  };

  if (!id || !sceneId) return null;

  const progressPct = progress.total > 0 ? Math.round((progress.reviewed / progress.total) * 100) : 0;
  const selectedAnnotation = annotations.find((annotation) => annotation.id === selectedAnnotationId) || null;

  return (
    <Flex h="calc(100vh - 32px)" gap={0}>
      {/* â”€â”€â”€ Main canvas area â”€â”€â”€ */}
      <Flex direction="column" flex="1" minW={0} overflow="hidden">
        <HStack px={4} py={1} spacing={4}>
          <Button
            as={Link}
            to={`/projects/${id}`}
            size="xs"
            variant="ghost"
            leftIcon={<MdArrowBack />}
          >
            {t("Dashboard")}
          </Button>
          <HotkeyBar
            classes={classes}
            activeClassId={activeClassId}
            isOpen={isClassSelectorOpen}
            onOpen={handleOpenClassSelector}
            onClose={handleCloseClassSelector}
            onSelect={handleSelectClass}
          />
          {sceneInfo && (
            <Text fontSize="xs" color={mutedColor} ml="auto" flexShrink={0}>
              {sceneInfo.width}x{sceneInfo.height}
              {sceneInfo.has_geo && (
                <Badge ml={2} colorScheme="green" fontSize="xx-small">GEO</Badge>
              )}
              {isTiled && (
                <Badge ml={1} colorScheme="blue" fontSize="xx-small">{t("TILED")}</Badge>
              )}
            </Text>
          )}
          {resolutionMeta && (
            <Tooltip label={t(resolutionMeta.description)} hasArrow>
              <Badge
                colorScheme={resolutionMeta.color}
                fontSize="xx-small"
                display="inline-flex"
                alignItems="center"
                gap={1}
                flexShrink={0}
              >
                {resolutionMeta.spin && <Spinner size="xs" thickness="2px" />}
                {t(resolutionMeta.label)}
              </Badge>
            </Tooltip>
          )}
          {tileInfo?.fullres_derivative_status === "error" && tileInfo.fullres_retryable && (
            <Tooltip label={tileInfo.fullres_error_message || t("High-resolution preparation failed")} hasArrow>
              <Button size="xs" variant="outline" onClick={() => void requestFullresDerivative(true)}>
                {t("Retry")}
              </Button>
            </Tooltip>
          )}
          {["queued", "building", "validating"].includes(
            String(tileInfo?.fullres_derivative_status || ""),
          ) && tileInfo?.fullres_job_id && (
            <Button size="xs" variant="ghost" onClick={() => void handleCancelFullres()}>
              {t("Cancel")}
            </Button>
          )}
        </HStack>
        <Box flex="1" position="relative" overflow="hidden">
          {tileInfo ? (
            <>
              <SceneMapCanvas
                key={`${id}-${sceneId}-${geoMode}-${tileInfo.display_asset_revision || tileInfo.overview_fingerprint || "none"}-${bandVersion}`}
                projectId={id}
                tileUrlTemplate={api.sceneTileUrl(
                  id,
                  sceneId,
                  displayParams,
                  `${tileInfo.display_asset_revision || tileInfo.overview_fingerprint || "none"}-${bandVersion}`,
                  activeViewWindow,
                )}
                sceneWidth={tileInfo.width}
                sceneHeight={tileInfo.height}
                maxZoom={tileInfo.source_max_zoom ?? tileInfo.max_zoom}
                availableNativeZoom={
                  tileInfo.available_native_zoom ?? tileInfo.source_max_zoom ?? tileInfo.max_zoom
                }
                availableNativeXyzZoom={tileInfo.available_native_xyz_zoom ?? null}
                annotations={annotations}
                classes={classes}
                activeClassId={activeClassId}
                annotationMode={annotationMode}
                drawingMode={drawingMode}
                activeMapTool={activeMapTool}
                onAnnotationCreate={handleAnnotationCreate}
                selectedAnnotationId={selectedAnnotationId}
                selectedAnnotationIds={multiSelectedAnnotationIds}
                onAnnotationSelect={handleAnnotationSelect}
                onAnnotationMultiSelect={handleAnnotationMultiSelect}
                onAnnotationUpdate={handleAnnotationUpdate}
                onAnnotationClassChange={handleAnnotationClassChange}
                showAnnotationBoxes={showAnnotationBoxes}
                showAnnotationLabels={showAnnotationLabels}
                focusBBox={focusBBoxRequest?.bbox}
                focusBBoxToken={focusBBoxRequest?.token}
                // Preview grid (tiling tab)
                tilingPreview={tilingPreview}
                showGrid={showPreviewGrid}
                // Geo mode
                geoMode={geoMode}
                geoBounds={sceneInfo?.bounds}
                geoTileUrlTemplate={geoMode
                  ? api.sceneGeoTileUrl(
                      id,
                      sceneId,
                      displayParams,
                      `${tileInfo.display_asset_revision || tileInfo.overview_fingerprint || "none"}-${bandVersion}`,
                      activeViewWindow,
                    )
                  : undefined}
                geoCrs={sceneInfo?.crs}
                geoProj4={sceneInfo?.crs_proj4}
                geoTransform={sceneInfo?.transform}
                basemapUrl={activeBasemapUrl}
                onSceneRasterReady={handleSceneRasterReady}
                sceneOpacity={showSceneRaster ? sceneOpacity : 0}
                // Interactive tile grid (review tab â€” uses tiles if tiled, preview rects otherwise)
                tiles={isTiled ? tiles : undefined}
                showTileGrid={showTileGrid}
                onTileClick={isTiled ? handleTileClick : undefined}
                onTilePaintCommit={isTiled ? handleTilePaintCommit : undefined}
                tileSize={tileSize}
                hoveredTileRef={hoveredTileRef}
                getVisibleTilesRef={getVisibleTilesRef}
                getVisibleSceneBoundsRef={getVisibleSceneBoundsRef}
                // Tylko w zakresie „Widok” i bez zamrożenia — inaczej każde przesunięcie
                // mapy przerysowywałoby cały widok etykietowania bez potrzeby.
                onViewportSettled={viewScopeActive && !viewFrozen ? setViewportBounds : undefined}
                layoutVersion={`${rightPanelCollapsed ? "collapsed" : "expanded"}-${rightPanelWidth}`}
                // Predictions layer
                predictions={predictions}
                showPredictions={showPredictions}
                selectedPredictionId={selectedPredictionId}
                selectedPredictionIds={multiSelectedPredictionIds}
                onPredictionSelect={handlePredictionSelect}
                onPredictionMultiSelect={handlePredictionMultiSelect}
                onSamClick={activeMapTool === "sam_click" ? handleSamClick : undefined}
                sceneName={sceneFilename || sceneId}
              />
              {viewScopeActive && (
                // Adaptacyjność ma być jawna: widać, że progi pochodzą z bieżącego widoku.
                <HStack
                  position="absolute"
                  bottom="10px"
                  right="10px"
                  zIndex={1000}
                  spacing={1}
                  px={2}
                  py={0.5}
                  borderRadius="md"
                  bg="rgba(19,19,22,0.85)"
                  color="white"
                  pointerEvents="none"
                >
                  {viewStatus === "loading" && <Spinner size="xs" />}
                  <Text fontSize="xs">
                    {viewFrozen ? t("Stretch: view (frozen)") : t("Stretch: view")}
                  </Text>
                </HStack>
              )}
              <LayerControl
                geoMode={geoMode}
                activeBasemapId={activeBasemapId}
                onBasemapChange={setActiveBasemapId}
                sceneOpacity={sceneOpacity}
                onOpacityChange={setSceneOpacity}
                showSceneVisibilityToggle
                sceneVisible={showSceneRaster}
                onSceneVisibleChange={setShowSceneRaster}
                showAnnotationBoxes={showAnnotationBoxes}
                onShowAnnotationBoxesChange={setShowAnnotationBoxes}
                showAnnotationLabels={showAnnotationLabels}
                onShowAnnotationLabelsChange={setShowAnnotationLabels}
                showSceneOpacity
                showDisplayControl
                displayContent={(
                  <VStack align="stretch" spacing={2}>
                    {(sceneInfo?.channels ?? 0) > 3 && !scenePackageId && (
                      <BandSelector
                        channels={sceneInfo!.channels}
                        initial={sceneBandsSelection}
                        isBusy={bandBusy}
                        onApply={handleApplyBands}
                      />
                    )}
                    {(sceneInfo?.channels ?? 0) > 3 && !!scenePackageId && (
                      <Text fontSize="xs" color={mutedColor}>
                        {t("Band selection is fixed by the delivery package for this scene.")}
                      </Text>
                    )}
                    <DisplayPanel
                      compact
                      params={displayParams}
                      onChange={setDisplayParams}
                      profiles={preprocessingProfiles}
                      selectedProfileId={displayProfileId}
                      onProfileSelect={setDisplayProfileId}
                      onCopyToDataset={handleCopyDisplayToDataset}
                      projectModality={project?.profile?.modality}
                      sceneModality={sceneManifest?.modality}
                      histogram={sceneHistogram}
                      stretchScope={stretchScope}
                      onStretchScopeChange={handleStretchScopeChange}
                      viewStretchAvailable={viewStretchAvailable}
                      viewFrozen={viewFrozen}
                      onViewFrozenChange={setViewFrozen}
                      viewStatus={viewStatus}
                      viewHistogram={viewStats}
                    />
                  </VStack>
                )}
              />
              <MapToolControl
                activeTool={activeMapTool}
                onToolChange={handleMapToolChange}
              />
              <AiToolControl
                visible
                activeTool={activeMapTool}
                onToolChange={handleMapToolChange}
                exemplarSupported={exemplarSupported}
                exemplarReady={exemplarReady}
                samReady={samReady}
                hasActiveClass={activeClassId !== null}
                hasSelection={selectedAnnotationId !== null || multiSelectedAnnotationIds.length > 0}
                findSimilarBusy={findSimilarBusy}
                onFindSimilar={handleFindSimilar}
                  exemplarSearchMode={exemplarSearchMode}
                  onExemplarSearchModeChange={handleExemplarSearchModeChange}
                exemplarEngine={exemplarEngine}
                onExemplarEngineChange={handleExemplarEngineChange}
                sam3Ready={sam3Ready}
                samTextBusy={samTextBusy}
                onSamText={handleSamText}
                samTextPrompt={samTextPrompt}
                onSamTextPromptChange={setSamTextPrompt}
                samTextConf={samTextConf}
                onSamTextConfChange={setSamTextConf}
                onSamTextRun={runSamTextCurrentView}
                onSamTextDrawRegion={startSamTextRegionDraw}
                samModels={samModels}
                samModelsDirPath={samModelsDirPath}
                selectedSamCheckpoint={samCheckpoint}
                onSamCheckpointChange={handleSamModelSelect}
              />
            </>
          ) : (
            <Flex flex="1" h="100%" align="center" justify="center">
              <Spinner size="lg" color="brand.400" />
            </Flex>
          )}
        </Box>
      </Flex>

      {/* â"€â"€â"€ Sidebar with tabs â"€â"€â"€ */}
      <Flex
        flexShrink={0}
        position="relative"
        zIndex={1200}
        overflow="visible"
      >
        {/* Resize handle */}
        {!rightPanelCollapsed && (
          <Box
            position="absolute"
            // Wsunięty o margines panelu (m={2} = 8px) ORAZ jego promień zaokrąglenia
            // (16px). Sama krawędź panelu jest prosta dopiero poniżej łuku narożnika,
            // więc bez tych 16px góra i dół uchwytu wychodziły w obszar zaokrąglenia.
            left={2}
            top="24px"
            bottom="24px"
            w="5px"
            borderRadius="full"
            cursor="col-resize"
            bg={resizeHandleBg}
            _hover={{ bg: resizeHandleHoverBg }}
            transition="background 0.15s"
            zIndex={10}
            onMouseDown={handleResizeStart}
          />
        )}

        {/* Collapse toggle */}
        <Tooltip label={rightPanelCollapsed ? t("Expand panel") : t("Collapse panel")} placement="left">
          <IconButton
            aria-label={rightPanelCollapsed ? t("Expand panel") : t("Collapse panel")}
            icon={rightPanelCollapsed ? <MdChevronLeft /> : <MdChevronRight />}
            size="xs"
            variant="ghost"
            position="absolute"
            // Panel odsunął się o margines, więc przycisk idzie za nim, żeby
            // zachować dotychczasowy odstęp od jego krawędzi.
            left={rightPanelCollapsed ? "-24px" : "-16px"}
            top="50%"
            transform="translateY(-50%)"
            zIndex={1300}
            onClick={() => setRightPanelCollapsed((prev) => !prev)}
            borderRadius="full"
            bg={panelBg}
            color={textColor}
            borderWidth="1px"
            borderColor={panelBorderColor}
            // Mocniejszy cień niż wcześniej: przy uniesionym panelu przycisk z
            // cieniem "sm" wyglądał, jakby leżał na mapie, a nie należał do panelu.
            boxShadow="md"
            pointerEvents="auto"
            _hover={{ bg: resizeHandleHoverBg }}
          />
        </Tooltip>

        <Box
          w={rightPanelCollapsed ? "0px" : `${rightPanelWidth}px`}
          minW={rightPanelCollapsed ? "0px" : `${RIGHT_PANEL_MIN}px`}
          maxW={`${RIGHT_PANEL_MAX}px`}
          bg={panelBg}
          // Marginesy tworzą górną krawędź, której panel wcześniej nie miał — bez nich
          // zaokrąglenie wyglądałoby na doklejone do paska tytułu okna. W stanie
          // zwiniętym znikają razem z obramowaniem, żeby nie został pasek tła.
          m={rightPanelCollapsed ? 0 : 2}
          border={rightPanelCollapsed ? "none" : "1px"}
          borderColor={panelBorderColor}
          borderRadius={rightPanelCollapsed ? "0" : "16px"}
          boxShadow={rightPanelCollapsed ? "none" : panelShadow}
          display="flex"
          flexDirection="column"
          overflow="hidden"
          transition={isResizingRef.current ? "none" : "width 0.2s ease"}
        >
          <Tabs
            index={tabIndex}
            onChange={handleTabChange}
            variant="soft-rounded"
            colorScheme="brand"
            size="sm"
            display="flex"
            flexDirection="column"
            flex="1"
            overflow="hidden"
          >
            <TabList px={3} pt={3} pb={1} gap={1} flexShrink={0} flexWrap="wrap">
              <Tab fontSize="xs" py={1}>{t("Drawing")}</Tab>
              <Tab fontSize="xs" py={1}>
                {t("Review grid")}
                {isTiled && progress.total > 0 && (
                  <Badge ml={1} fontSize="xx-small" colorScheme="green">
                    {progressPct}%
                  </Badge>
                )}
              </Tab>
              <Tab fontSize="xs" py={1}>
                {t("Predict")}
                {pendingCount > 0 && (
                  <Badge ml={1} fontSize="xx-small" colorScheme="orange">
                    {pendingCount}
                  </Badge>
                )}
              </Tab>
              <Tab fontSize="xs" py={1}>
                {t("Metadata")}
                {metadataWarningCount > 0 && (
                  <Badge ml={1} fontSize="xx-small" colorScheme="orange">
                    {metadataWarningCount}
                  </Badge>
                )}
              </Tab>
            </TabList>

            <TabPanels flex="1" overflowY="auto" px={3} pb={3}>
              {/* â"€â"€â"€ Drawing tab â"€â"€â"€ */}
              <TabPanel p={0} pt={2}>
                <VStack align="stretch" spacing={4}>
                  <HStack justify="space-between">
                    <HStack spacing={1}>
                      <Text fontSize="sm" fontWeight="bold" color={textColor}>
                        {t("Active map tool")}
                      </Text>
                      <InfoPopover
                        titleKey="info.annotation.drawingMode.title"
                        bodyKey="info.annotation.drawingMode.body"
                      />
                    </HStack>
                    <Badge colorScheme={annotationMode === "rotated_bbox" ? "purple" : "blue"}>
                      {t(annotationMode)}
                    </Badge>
                    <Badge colorScheme={activeMapTool === "draw" ? "green" : "gray"}>
                      {t(activeMapTool)}
                    </Badge>
                  </HStack>
                  {toolRequiresActiveClass(activeMapTool) && activeClassId === null && (
                    <Text fontSize="xs" color="orange.500" fontWeight="semibold">
                      {t("Select an active class to use this tool.")}
                    </Text>
                  )}
                  {activeMapTool === "multi_select" && multiSelectedAnnotationIds.length > 0 && (
                    <Text fontSize="xs" color={mutedColor}>
                      {t("Selected annotations")}: {multiSelectedAnnotationIds.length}. {t("Use Delete to remove selected annotations.")}
                    </Text>
                  )}
                  <HStack justify="space-between">
                    <Text fontSize="sm" color={textColor}>{t("Ask for class after drawing")}</Text>
                    <Switch
                      isChecked={askClassAfterDraw}
                      onChange={(event) => handleAskClassAfterDrawChange(event.target.checked)}
                      colorScheme="brand"
                      size="sm"
                    />
                  </HStack>
                  <HStack spacing={1}>
                    <Text fontSize="xs" color={mutedColor}>{t("Project geometry type")}</Text>
                    <InfoPopover
                      titleKey={annotationMode === "rotated_bbox"
                        ? "info.annotation.rotatedBbox.title"
                        : "info.annotation.bbox.title"}
                      bodyKey={annotationMode === "rotated_bbox"
                        ? "info.annotation.rotatedBbox.body"
                        : "info.annotation.bbox.body"}
                    />
                  </HStack>
                  <Badge alignSelf="flex-start" colorScheme={annotationMode === "rotated_bbox" ? "purple" : "blue"}>
                    {annotationMode === "rotated_bbox" ? t("Rotated bbox") : t("Axis-aligned bbox")}
                  </Badge>

                  {multiSelectedAnnotationIds.length > 0 && (
                    <Box p={3} borderWidth="1px" borderRadius="lg" borderColor={borderColor}>
                      <VStack align="stretch" spacing={2}>
                        <HStack justify="space-between">
                          <Text fontSize="sm" fontWeight="bold" color={textColor}>
                            {t("Selected annotations")}
                          </Text>
                          <Badge colorScheme="blue">{multiSelectedAnnotationIds.length}</Badge>
                        </HStack>
                        <HStack>
                          <Button
                            size="sm"
                            flex="1"
                            onClick={handleApplyActiveClassToSelection}
                            isDisabled={activeClassId === null}
                          >
                            {t("Apply active class")}
                          </Button>
                          <Button
                            size="sm"
                            colorScheme="red"
                            variant="outline"
                            onClick={handleDeleteSelectedObjects}
                          >
                            {t("Delete selected")}
                          </Button>
                        </HStack>
                      </VStack>
                    </Box>
                  )}

                  <Divider />

                  {sceneReview?.status && REVIEW_META[sceneReview.status] && (
                    <Box
                      borderWidth="1px"
                      borderRadius="md"
                      p={3}
                      borderColor={`${REVIEW_META[sceneReview.status].color}.300`}
                    >
                      <HStack
                        justify="space-between"
                        mb={sceneReview.comment || sceneReview.pins?.length ? 2 : 0}
                      >
                        <HStack spacing={2}>
                          <MdRateReview />
                          <Text fontWeight="bold" fontSize="sm">
                            {t("Manager review")}
                          </Text>
                        </HStack>
                        <Badge colorScheme={REVIEW_META[sceneReview.status].color}>
                          {t(REVIEW_META[sceneReview.status].label)}
                        </Badge>
                      </HStack>
                      {sceneReview.comment && (
                        <Text
                          fontSize="sm"
                          whiteSpace="pre-wrap"
                          mb={sceneReview.pins?.length ? 2 : 0}
                        >
                          {sceneReview.comment}
                        </Text>
                      )}
                      {!!sceneReview.pins?.length && (
                        <VStack align="stretch" spacing={1}>
                          <Text fontSize="xs" color="gray.500">
                            {t("Flagged objects", { count: sceneReview.pins.length })}
                          </Text>
                          {sceneReview.pins.map((pin, index) => (
                            <Button
                              key={index}
                              size="xs"
                              variant="ghost"
                              justifyContent="flex-start"
                              leftIcon={<MdPushPin />}
                              onClick={() => handleReviewPinClick(pin.source_annotation_id)}
                            >
                              <Text fontSize="xs" noOfLines={1}>
                                {pin.comment || pin.source_annotation_id}
                              </Text>
                            </Button>
                          ))}
                        </VStack>
                      )}
                    </Box>
                  )}

                  <PredictionResultsPanel
                    predictions={predictions}
                    pendingCount={pendingCount}
                    acceptedCount={acceptedCount}
                    onAcceptAll={handleAcceptAllPreds}
                    onClearAll={handleClearAllPreds}
                    onAccept={handleAcceptPreds}
                    onDelete={handleDeletePredictions}
                    onFlipFront={handleRotateFront}
                    selectedPredictionId={selectedPredictionId}
                    selectedPredictionIds={multiSelectedPredictionIds}
                    onSelect={handlePredictionSelect}
                    onDoubleClick={handlePredictionDoubleClick}
                  />

                  <AnnotationList
                    annotations={annotations}
                    classes={classes}
                    onDelete={handleDeleteAnnotation}
                    selectedAnnotationId={selectedAnnotationId}
                    selectedAnnotationIds={multiSelectedAnnotationIds}
                    onSelect={handleAnnotationSelect}
                    onDoubleClick={handleAnnotationDoubleClick}
                    onClassChange={handleAnnotationClassChange}
                  />

                  <Divider />

                  <ComputedAttributesPanel annotation={selectedAnnotation} />
                </VStack>
              </TabPanel>

              {/* â"€â"€â"€ Review grid tab: configuration and review, one workflow â"€â"€â"€ */}
              <TabPanel p={0} pt={2}>
                <VStack align="stretch" spacing={3}>
                  {/* One visibility switch for whichever layer the open section means. */}
                  <HStack justify="space-between">
                    <HStack spacing={1}>
                      <Text fontSize="sm" fontWeight="bold" color={textColor}>
                        {t("Grid visibility")}
                      </Text>
                      <Badge fontSize="xx-small" colorScheme="purple">S</Badge>
                      <InfoPopover
                        titleKey="info.annotation.tileReview.title"
                        bodyKey="info.annotation.tileReview.body"
                      />
                    </HStack>
                    <Switch
                      isChecked={gridVisible}
                      onChange={handleToggleGridVisibility}
                      colorScheme="green"
                      size="sm"
                    />
                  </HStack>

                  <Accordion
                    allowToggle
                    index={gridSection}
                    onChange={(value) => handleGridSectionChange(value as number)}
                  >
                    <AccordionItem border="none">
                      <AccordionButton px={0} py={2}>
                        <Text fontSize="xs" fontWeight="bold" flex="1" textAlign="left" color={textColor}>
                          {t("Grid configuration")}
                        </Text>
                        <AccordionIcon />
                      </AccordionButton>
                      <AccordionPanel px={0} pb={3}>
                        <TilingPanel
                          config={tilingConfig}
                          preview={tilingPreview}
                          onConfigChange={handleConfigChange}
                          onExecute={handleExecute}
                          isExecuting={isExecuting}
                          progress={tilingProgress}
                          reviewProgress={progress}
                        />
                      </AccordionPanel>
                    </AccordionItem>

                    <AccordionItem border="none" isDisabled={!isTiled}>
                      <AccordionButton px={0} py={2}>
                        <Text fontSize="xs" fontWeight="bold" flex="1" textAlign="left" color={textColor}>
                          {t("Progress and review")}
                        </Text>
                        {isTiled && progress.total > 0 && (
                          <Badge mr={2} fontSize="xx-small" colorScheme="green">
                            {progressPct}%
                          </Badge>
                        )}
                        <AccordionIcon />
                      </AccordionButton>
                      <AccordionPanel px={0} pb={3}>
                        <VStack align="stretch" spacing={4}>
                          {isTiled && (
                            <>
                              <Progress
                                value={progressPct}
                                colorScheme="green"
                                size="sm"
                                borderRadius="full"
                              />
                              <Text fontSize="xs" color={textColor}>
                                {t("Reviewed")}: {progress.reviewed}/{progress.total} ({progressPct}%)
                              </Text>
                              <HStack spacing={3} flexWrap="wrap">
                                <Text fontSize="xs" color={mutedColor}>
                                  {t("Active")}: {progress.total}/{progress.total_all}
                                </Text>
                                <Text fontSize="xs" color={mutedColor}>
                                  {t("Positive")}: {progress.positive}
                                </Text>
                                <Text fontSize="xs" color={mutedColor}>
                                  {t("Negative")}: {progress.negative}
                                </Text>
                                <Text fontSize="xs" color={mutedColor}>
                                  {t("Unchecked")}: {progress.total - progress.reviewed}
                                </Text>
                                <Text fontSize="xs" color={mutedColor}>
                                  {t("Excluded")}: {progress.excluded}
                                </Text>
                              </HStack>

                              <HStack spacing={2}>
                                <Button
                                  size="xs"
                                  colorScheme="green"
                                  variant={tileActionMode === "review" ? "solid" : "outline"}
                                  onClick={() => {
                                    setTileActionMode("review");
                                    setActiveMapTool("grid_review");
                                  }}
                                >
                                  {t("Mark reviewed")}
                                </Button>
                                <Button
                                  size="xs"
                                  colorScheme="gray"
                                  variant={tileActionMode === "clear" ? "solid" : "outline"}
                                  onClick={() => {
                                    setTileActionMode("clear");
                                    setActiveMapTool("grid_clear");
                                  }}
                                >
                                  {t("Clear reviewed")}
                                </Button>
                                <Button
                                  size="xs"
                                  colorScheme="red"
                                  variant={tileActionMode === "exclude" ? "solid" : "outline"}
                                  onClick={() => {
                                    setTileActionMode("exclude");
                                    setActiveMapTool("grid_exclude");
                                  }}
                                >
                                  {t("Exclude")}
                                </Button>
                              </HStack>

                              <Button
                                size="xs"
                                variant="outline"
                                colorScheme={
                                  tileActionMode === "exclude"
                                    ? "red"
                                    : tileActionMode === "clear"
                                      ? "gray"
                                      : "green"
                                }
                                onClick={
                                  tileActionMode === "exclude"
                                    ? handleMarkVisibleExcluded
                                    : tileActionMode === "clear"
                                      ? handleMarkVisibleUnreviewed
                                    : handleMarkVisibleReviewed
                                }
                              >
                                {tileActionMode === "exclude"
                                  ? t("Exclude visible tiles")
                                  : tileActionMode === "clear"
                                    ? t("Clear visible reviewed")
                                  : t("Mark visible as reviewed")}
                              </Button>
                            </>
                          )}

                          <Divider />

                          <Text fontSize="xs" color={mutedColor}>
                            <strong>S</strong> - {t("toggle grid visibility")}
                            <br />
                            {t("Click a tile to apply current mode.")}
                            <br />
                            <strong>R</strong> - {t("toggle reviewed")} &bull; <strong>Shift+R</strong> - {t("review visible")}
                            <br />
                            <strong>E</strong> - {t("toggle excluded")} &bull; <strong>Shift+E</strong> - {t("exclude visible")}
                          </Text>
                        </VStack>
                      </AccordionPanel>
                    </AccordionItem>
                  </Accordion>

                  {!isTiled && (
                    <Text fontSize="xs" color={mutedColor}>
                      {t("Create the review grid above to start tracking labelling progress.")}
                    </Text>
                  )}
                </VStack>
              </TabPanel>

              {/* --- Predict tab --- */}
              <TabPanel p={0} pt={2}>
                {id && sceneId && (
                  <PredictionPanel
                    projectId={id}
                    sceneId={sceneId}
                    classes={classes}
                    predictions={predictions}
                    isRunning={isPredicting}
                    progress={predProgress}
                    pendingCount={pendingCount}
                    acceptedCount={acceptedCount}
                    onRun={runPrediction}
                    onCancel={cancelPrediction}
                    onAcceptAll={handleAcceptAllPreds}
                    onClearAll={handleClearAllPreds}
                    onAccept={handleAcceptPreds}
                    onDelete={handleDeletePredictions}
                    onFlipFront={handleRotateFront}
                    selectedSamCheckpoint={samCheckpoint}
                    onSamCheckpointChange={setSamCheckpoint}
                    onSamModelsChange={handleSamModelsChange}
                    exemplarMatchingSettings={exemplarMatchingSettings}
                    onExemplarMatchingSettingsChange={handleExemplarMatchingSettingsChange}
                    showResults={false}
                  />
                )}
              </TabPanel>

              {/* --- Metadata tab --- */}
              <TabPanel p={0} pt={2}>
                <SceneMetadataPanel
                  project={project}
                  sceneInfo={sceneInfo}
                  manifest={sceneManifest}
                />
              </TabPanel>
            </TabPanels>
          </Tabs>
        </Box>
      </Flex>

    </Flex>
  );
}
