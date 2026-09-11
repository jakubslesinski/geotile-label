import axios from "axios";
import type {
  DatasetAuditReport,
  DatasetFilterOptions,
  DatasetPublication,
  DatasetPublicationStatus,
  DatasetRunManifest,
  DatasetRunsIndex,
  DatasetStats,
  DisplayParams,
  PreprocessingProfile,
  PreprocessingProfilesFile,
  Project,
  ProjectProfile,
  Prediction,
  PredictionConfig,
  SceneIdentityRefreshResult,
  SceneManifest,
  SceneViewStats,
  Scene,
  ScenesIndex,
  ViewStretchWindow,
} from "../types";

const DEFAULT_API_BASE = "/api";

let apiBase = DEFAULT_API_BASE;
let serverBase = "";
let authToken = "";
let capabilitiesCache: BackendCapabilities = {
  desktop: false,
  build_variant: "yolo",
  scene_package_graph_v2: false,
  feature_flags: {},
  yolo: true,
  rasterio: false,
  cuda_available: false,
  device: "cpu",
};

const api = axios.create({ baseURL: apiBase });
let backendRecoveryPromise: Promise<void> | null = null;

api.interceptors.request.use((config) => {
  if (authToken) {
    config.headers = config.headers || {};
    (config.headers as any)["X-GeoTile-Token"] = authToken;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error?.config as (typeof error.config & { __backendRetry?: boolean }) | undefined;
    if (!isTauriRuntime() || error?.response || !config || config.__backendRetry) {
      return Promise.reject(error);
    }

    config.__backendRetry = true;
    try {
      await recoverBackendConnection();
      config.baseURL = api.defaults.baseURL;
      return api.request(config);
    } catch {
      return Promise.reject(error);
    }
  },
);

export interface BackendCapabilities {
  desktop: boolean;
  build_variant?: string;
  buildVariant?: string;
  scene_package_graph_v2?: boolean;
  scenePackageGraphV2?: boolean;
  feature_flags?: Record<string, boolean>;
  featureFlags?: Record<string, boolean>;
  yolo: boolean;
  rasterio: boolean;
  torch?: string | null;
  ultralytics?: string | null;
  cuda_available?: boolean;
  cudaAvailable?: boolean;
  device?: string;
  jp2?: boolean;
  jp2_driver?: string | null;
  sam?: {
    available: boolean;
    mock: boolean;
    bundled_checkpoint?: string | null;
    runtime_available?: boolean;
    reason?: "runtime_missing" | "checkpoint_missing" | null;
  };
  sar_exemplar?: {
    available: boolean;
    mock: boolean;
    bundled_backbone?: string | null;
  };
  /** Whether training is usable, and — when it is not — the remedy. */
  training?: {
    available: boolean;
    reason?: string | null;
    cpu_fallback?: boolean;
    device?: "cpu" | "cuda";
    gpu_name?: string | null;
    vram_free_gb?: number | null;
    vram_total_gb?: number | null;
  };
}

export type SceneProvider = "generic" | "iceye" | "capella" | "umbra" | "pleiades_neo" | "worldview" | "blacksky";

export interface SceneSourceInput {
  provider: SceneProvider;
  root_path: string;
  enabled: boolean;
}

export interface SceneImportAsset {
  asset_id: string;
  role: string;
  asset_role?: string;
  relative_path: string;
  package_relative_path: string;
  format: string;
  part_id?: string | null;
  size: number;
  mtime_ns: number;
}

export interface SceneImportPackage {
  package_id: string;
  decision_uid?: string;
  source_id: string;
  provider: SceneProvider;
  modality?: string | null;
  package_root_relative: string;
  /** `archive` oznacza pakiet-archiwum (P1.3a): jest widoczny, ale nie jest scena. */
  package_kind?: "archive";
  assets: SceneImportAsset[];
  selection: {
    status: string;
    product_type?: string | null;
    provider_scene_id?: string | null;
    display_name?: string | null;
    asset_ids: string[];
    identity_asset_ids?: string[];
    rgb_bands?: number[] | null;
    raster_kind?: string | null;
    mosaic_parts_order?: string[];
    metadata_asset_ids?: string[];
    source_components?: Record<string, string[]>;
    component_metadata_asset_ids?: Record<string, string[]>;
    multispectral_asset_ids?: string[];
    panchromatic_asset_ids?: string[];
    primary_component?: string | null;
    declared_parts?: number | null;
    completeness?: "complete" | "partial" | "unknown";
    missing_parts?: string[];
    missing_parts_unknown_count?: number;
    archive?: {
      entry_count: number;
      uncompressed_bytes: number;
      compressed_bytes: number;
      delivery_root?: string | null;
      unsafe_entry_count: number;
      truncated: boolean;
      extracted_root_relative?: string | null;
      extracted_present: number;
      extracted_total: number;
      missing_count: number;
      missing_paths: string[];
    };
    selected_by?: "resolver" | "user" | string;
    alternatives?: Array<{ asset_ids: string[]; label: string }>;
    diagnostics?: {
      warnings?: Array<{ code: string; message: string }>;
      errors?: Array<{ code: string; message: string }>;
      metadata_conflicts?: Array<Record<string, unknown>>;
    };
  };
}

/** Hierarchiczny podgląd importu (P2.1): źródło → dostawa → akwizycja → produkt.
 *  Wiersz niesie tylko to, co pozwala wychwycić problem; `detail` rozwija się na żądanie,
 *  a pełne dane (ścieżki, rozmiary per plik, identity) są w raporcie importu. */
export interface ScenePreviewProduct {
  package_id: string;
  decision_uid?: string;
  package_kind: string;
  label: string;
  status: string;
  product_type?: string | null;
  raster_kind?: string | null;
  part_count: number;
  declared_parts?: number | null;
  incomplete: boolean;
  warning_count: number;
  blocking_count: number;
  detail: {
    provider?: string | null;
    processing_level?: string | null;
    polarization_or_bands?: string | null;
    completeness?: string | null;
    missing_parts: string[];
    missing_parts_total: number;
    parts_source?: string | null;
    assets: { measurement: number; metadata: number; browse: number; auxiliary: number };
    selection_reason: { code: string; message: string };
    diagnostics: {
      blocking: Array<{ code: string; message: string }>;
      warnings: Array<{ code: string; message: string }>;
      metadata_conflicts: number;
    };
    archive: ScenePreviewArchive[];
    preparation?: {
      estimated_output_bytes: number;
      free_bytes?: number | null;
      fits?: boolean | null;
      method: string;
    } | null;
    alternatives: Array<{ label?: string; asset_ids: string[] }>;
  };
}

export interface ScenePreviewArchive {
  package_id: string;
  name: string;
  status: string;
  extracted_present?: number | null;
  extracted_total?: number | null;
  compressed_bytes?: number | null;
}

export interface ScenePreviewTree {
  schema_name: string;
  schema_version: number;
  free_bytes?: number | null;
  sources: Array<{
    source_id: string;
    provider?: string | null;
    root_path?: string | null;
    deliveries: Array<{
      delivery_id: string;
      label: string;
      archives: ScenePreviewArchive[];
      acquisitions: Array<{ acquisition_id: string; products: ScenePreviewProduct[] }>;
    }>;
  }>;
}

export interface SceneImportPreview {
  preview_id: string;
  created_at: string;
  expires_at: string;
  sources: Array<SceneSourceInput & { source_id: string }>;
  packages: SceneImportPackage[];
  diagnostics: Array<{ source_id: string; level: string; code: string; message: string }>;
  tree?: ScenePreviewTree;
  detected_modalities: string[];
  scan_job_id?: string;
  scan_status?: "complete";
  scan_cache?: Array<{
    source_id: string;
    status: "hit" | "partial" | "miss";
    hits: number;
    misses: number;
  }>;
}

export interface SceneScanJob {
  scan_job_id: string;
  status: "queued" | "starting" | "running" | "cancelling" | "completed" | "cancelled" | "failed" | "interrupted";
  phase?: string | null;
  current?: number | null;
  total?: number | null;
  source_index?: number | null;
  source_total?: number | null;
  error?: string | null;
  preview?: SceneImportPreview;
}

export interface BackendInfo {
  baseUrl: string;
  token: string;
  capabilities: BackendCapabilities;
}

export interface DiagnosticsInfo {
  appVersion: string;
  os: string;
  arch: string;
  appDataDir: string;
  dataDir: string;
  logsDir: string;
  runtimeDir: string;
  backendRunning: boolean;
  backendBaseUrl: string | null;
  capabilities: BackendCapabilities;
  lastBackendError: string | null;
}

export interface ImportBackupResult {
  project_id: string;
  name: string;
  imported_scenes: number;
  missing_scenes: string[];
  total_scenes: number;
}

export interface SourceAnnotationsGeoParquetResult {
  status: string;
  source_annotation_count: number;
  wgs84_annotation_count: number;
  native_annotation_count: number;
  omitted_from_wgs84_count: number;
  files: {
    annotations_wgs84: string;
    annotations_native: string;
    annotation_summary: string;
  };
}

export interface AnnotationPackagePreview {
  package_id: string;
  suggested_filename: string;
  can_export: boolean;
  annotator_email?: string | null;
  scene_count: number;
  annotation_count: number;
  class_count: number;
  geo_scene_count: number;
  no_geo_scene_count: number;
  class_counts: Record<string, number>;
  author_counts: Record<string, number>;
  errors: string[];
  warnings: string[];
}

export type ReviewStatus = "none" | "accepted" | "rejected" | "needs_fix";

export interface ScenePin {
  source_annotation_id: string;
  comment?: string | null;
}

export interface ReviewPackagePreview {
  package_id: string;
  suggested_filename: string;
  can_export: boolean;
  reviewer_email?: string | null;
  owner_email: string;
  scene_count: number;
  pin_count: number;
  status_counts: Record<string, number>;
  errors: string[];
  warnings: string[];
  scenes: Array<{
    scene_id: string;
    source_scene_uid: string;
    filename?: string | null;
    review_status: ReviewStatus;
    review_comment?: string | null;
    review_round: number;
    pins: ScenePin[];
  }>;
}

export interface ReviewImportReport {
  package_count: number;
  valid_package_count: number;
  can_apply: boolean;
  update_count: number;
  missing_scene_count: number;
  /** Verdicts from an older round than the scene already holds — skipped. */
  stale_verdict_count: number;
  pin_count: number;
  status_counts: Record<string, number>;
  errors: string[];
  warnings: string[];
  status?: string;
  applied_scene_count?: number;
}

export interface AnnotationImportScenePlan {
  target_scene_id: string;
  owner_email: string;
  package_id?: string | null;
  source_scene_uid?: string | null;
  source_filename?: string | null;
  /** "replace" = scoped v2 package (may update and remove); "append" = additive v1. */
  mode: "replace" | "append";
  before_count: number;
  after_count: number;
  added_count: number;
  changed_count: number;
  removed_count: number;
  unchanged_count: number;
  taken_over_count: number;
  out_of_scope_count: number;
  /** Would remove a large share of this owner's work — needs an explicit decision. */
  requires_confirmation: boolean;
  /** Non-empty when the plan cannot be applied at all (e.g. a class is missing). */
  block_reason: string;
}

export interface AnnotationImportPreview {
  preview_id: string;
  identity_policy: "exact_only" | "allow_probable";
  can_apply: boolean;
  can_apply_with_class_creation: boolean;
  package_count: number;
  valid_package_count: number;
  matched_scene_count: number;
  missing_scene_count: number;
  ambiguous_scene_count: number;
  incompatible_scene_count: number;
  approval_required_scene_count: number;
  missing_classes: string[];
  ambiguous_classes: string[];
  new_annotation_count: number;
  duplicate_annotation_count: number;
  changed_annotation_count: number;
  removed_annotation_count: number;
  taken_over_annotation_count: number;
  out_of_scope_annotation_count: number;
  blocked_annotation_count: number;
  scene_plans: AnnotationImportScenePlan[];
  confirmation_required_scene_ids: string[];
  blocked_scene_ids: string[];
  authors: Record<string, number>;
  requires_missing_class_decision: boolean;
  errors: string[];
  warnings: string[];
}

export interface AnnotationImportReport extends AnnotationImportPreview {
  import_id: string;
  status: string;
  imported_annotation_count: number;
  updated_annotation_count: number;
  deleted_annotation_count: number;
  applied_scene_count: number;
  skipped_scene_plans: Array<{
    target_scene_id: string;
    owner_email: string;
    reason: string;
  }>;
  applied_at: string;
}

export interface ProjectAnnotationSummary {
  project_id: string;
  scene_count: number;
  scenes_with_annotations: number;
  scenes_without_annotations: string[];
  annotation_count: number;
  missing_author_count: number;
  missing_author_annotation_ids?: string[];
  per_scene?: Array<{
    scene_id: string;
    source_scene_uid?: string | null;
    filename: string;
    annotation_count: number;
    class_ids: number[];
    class_names: string[];
    authors: string[];
    sources: string[];
    import_ids: string[];
    package_ids: string[];
  }>;
  per_class: Array<{ class_id: number; class_name: string; annotation_count: number }>;
  per_author: Array<{ annotator_email: string; annotation_count: number }>;
  per_source: Array<{ annotation_source: string; annotation_count: number }>;
  per_import: Array<{ import_id: string; annotation_count: number }>;
  per_package: Array<{ package_id: string; annotation_count: number }>;
  import_count: number;
  import_totals: {
    imported_annotation_count: number;
    duplicate_annotation_count: number;
    changed_annotation_count: number;
    blocked_annotation_count: number;
  };
  last_import_report?: ProjectImportSummary | null;
  recent_imports: ProjectImportSummary[];
  warnings: string[];
}

export interface ProjectImportSummary {
  import_id: string;
  applied_at?: string | null;
  status?: string;
  package_count: number;
  imported_annotation_count: number;
  duplicate_annotation_count: number;
  changed_annotation_count: number;
  blocked_annotation_count: number;
  authors: Record<string, number>;
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function normalizeServerBase(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, "");
}

export function configureApi(info: BackendInfo) {
  serverBase = normalizeServerBase(info.baseUrl);
  apiBase = `${serverBase}/api`;
  authToken = info.token || "";
  capabilitiesCache = info.capabilities || capabilitiesCache;
  api.defaults.baseURL = apiBase;
}

async function recoverBackendConnection(): Promise<void> {
  if (!backendRecoveryPromise) {
    backendRecoveryPromise = (async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      const info = await invoke<BackendInfo>("get_backend_info");
      configureApi(info);
    })().finally(() => {
      backendRecoveryPromise = null;
    });
  }
  return backendRecoveryPromise;
}

export async function initializeApi() {
  if (!isTauriRuntime()) {
    try {
      capabilitiesCache = await getCapabilities();
    } catch {
      // Browser/dev mode can run while the backend is still starting.
    }
    return;
  }

  const { invoke } = await import("@tauri-apps/api/core");
  const info = await invoke<BackendInfo>("get_backend_info");
  configureApi(info);
  try {
    capabilitiesCache = await getCapabilities();
  } catch {
    capabilitiesCache = info.capabilities;
  }
}

async function invokeTauri<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauriRuntime()) {
    throw new Error("Tauri runtime is not available");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

export async function getAppDiagnostics(): Promise<DiagnosticsInfo> {
  return invokeTauri<DiagnosticsInfo>("get_app_diagnostics");
}

export async function openLogsDir(): Promise<void> {
  await invokeTauri<void>("open_logs_dir");
}

export async function quitApplication(): Promise<void> {
  await invokeTauri<void>("quit_app");
}

export async function openPathInFileManager(path: string): Promise<void> {
  await invokeTauri<void>("open_path_in_file_manager", { path });
}

export async function exportDiagnosticsZip(): Promise<string> {
  return invokeTauri<string>("export_diagnostics_zip");
}

export interface CudaPackStatus {
  installed: boolean;
  usable: boolean;
  /** Wynik sondy ("12.6 True") albo powód, dla którego pakiet jest nieużywalny. */
  detail: string;
  path: string;
}

export async function getCudaPackStatus(): Promise<CudaPackStatus> {
  return invokeTauri<CudaPackStatus>("get_cuda_pack_status");
}

/** Instaluje ręcznie wskazany pakiet CUDA (jeden plik albo części archiwum). */
export async function installCudaPack(archivePaths: string[]): Promise<CudaPackStatus> {
  return invokeTauri<CudaPackStatus>("install_cuda_pack", { archivePaths });
}

export async function removeCudaPack(): Promise<void> {
  await invokeTauri<void>("remove_cuda_pack");
}

export async function restartBackend(): Promise<BackendInfo> {
  const info = await invokeTauri<BackendInfo>("restart_backend");
  configureApi(info);
  try {
    capabilitiesCache = await getCapabilities();
  } catch {
    capabilitiesCache = info.capabilities;
  }
  return info;
}

export async function clearRuntimeCache(): Promise<void> {
  await invokeTauri<void>("clear_runtime_cache");
  await initializeApi();
}

export function getCachedCapabilities(): BackendCapabilities {
  return capabilitiesCache;
}

export function authHeaders(): HeadersInit {
  return authToken ? { "X-GeoTile-Token": authToken } : {};
}

function apiUrl(path: string): string {
  return `${apiBase}${path}`;
}

function dataUrl(path: string): string {
  return `${serverBase || ""}/data${path}`;
}

function appendQueryParams(url: string, params: URLSearchParams): string {
  const qs = params.toString();
  if (!qs) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${qs}`;
}

/**
 * Wersja algorytmu renderu kafli. Podbijana, gdy te same parametry wyświetlania dają inne
 * piksele — kafle mają `max-age=3600`, więc bez tego przeglądarka pokazywałaby stare PNG.
 * 2: okno rozciągnięcia wchodzi do konwersji do uint8 (DESIGN_DECISIONS.md, display-stretch A).
 * Nazwa `_rv`, nie `_r`: `_r` należy do ponawiania kafli (`tileLayerQueue.ts`), które
 * obcina adres na `&_r=`.
 */
const DISPLAY_RENDER_VERSION = "2";

export function displayQueryString(
  display?: DisplayParams,
  cacheVersion?: string | number | null,
  viewWindow?: ViewStretchWindow | null,
): string {
  const params = new URLSearchParams();
  if (authToken) params.set("token", authToken);
  if (cacheVersion !== undefined && cacheVersion !== null) params.set("_v", String(cacheVersion));
  if (display) {
    params.set("_rv", DISPLAY_RENDER_VERSION);
    if (display.brightness !== 1.0) params.set("brightness", String(display.brightness));
    if (display.contrast !== 1.0) params.set("contrast", String(display.contrast));
    if (display.gamma !== 1.0) params.set("gamma", String(display.gamma));
    if (viewWindow) {
      // Okno widoku zastępuje percentyle — wysyłanie obu tylko rozdrabniałoby cache.
      params.set("stretch_min", String(viewWindow.min));
      params.set("stretch_max", String(viewWindow.max));
    } else {
      if (display.stretch_low > 0) params.set("stretch_low", String(display.stretch_low));
      if (display.stretch_high < 100) params.set("stretch_high", String(display.stretch_high));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// --- Browse ---
export interface BrowseBreadcrumb {
  label: string;
  path: string;
}

export interface BrowseDirEntry {
  name: string;
  path: string;
  image_count: number;
  is_root?: boolean;
}

export interface BrowseFileEntry {
  name: string;
  path: string;
  size: number;
  type: string;
}

export interface BrowseResult {
  path: string;
  parent_path: string;
  breadcrumbs: BrowseBreadcrumb[];
  dirs: BrowseDirEntry[];
  files: BrowseFileEntry[];
  image_count: number;
}
export const browse = (path = "", scope: "scene" | "class" = "scene"): Promise<BrowseResult> =>
  api.get("/browse/", { params: { path, scope } }).then((r) => r.data);

// --- Projects ---
export const listProjects = () => api.get("/projects/").then((r) => r.data);
export const getProject = (id: string) =>
  api.get(`/projects/${id}`).then((r) => r.data);
export const createProject = (data: {
  name: string;
  scene_folder: string;
  classes_file?: string;
  project_location?: string;
  profile?: ProjectProfile;
  tile_size?: number;
  buffer?: number;
}) => api.post("/projects/", data).then((r) => r.data);
export const createNitfProject = (data: {
  name: string;
  scene_folder: string;
  classes_file?: string;
  project_location?: string;
  annotation_mode?: string;
}) => api.post("/projects/nitf", data).then((r) => r.data);
const waitForSceneScan = async (scanJobId: string): Promise<SceneImportPreview> => {
  for (;;) {
    const scan = await getSceneScanJob(scanJobId);
    if (scan.status === "completed" && scan.preview) return scan.preview;
    if (["cancelled", "failed", "interrupted"].includes(scan.status)) {
      throw new Error(scan.error || `Scene source scan ${scan.status}`);
    }
    await new Promise((resolve) => globalThis.setTimeout(resolve, 500));
  }
};

const resolveSceneScanResponse = async (
  response: SceneImportPreview | { status: string; scan_job_id: string },
): Promise<SceneImportPreview> => (
  "preview_id" in response ? response : waitForSceneScan(response.scan_job_id)
);

export const getSceneScanJob = (scanJobId: string): Promise<SceneScanJob> =>
  api.get(`/scene-import/scan-jobs/${scanJobId}`).then((response) => response.data);
export const cancelSceneScanJob = (scanJobId: string) =>
  api.post(`/scene-import/scan-jobs/${scanJobId}/cancel`).then((response) => response.data);
export const resumeSceneScanJob = (scanJobId: string): Promise<{ status: string; scan_job_id: string }> =>
  api.post(`/scene-import/scan-jobs/${scanJobId}/resume`).then((response) => response.data);

export const previewSceneSources = async (data: {
  sources: SceneSourceInput[];
  modality?: "SAR" | "EO";
}): Promise<SceneImportPreview> => {
  const response = await api.post("/scene-import/previews", data);
  return resolveSceneScanResponse(response.data);
};
export interface ImportJobRecent {
  filename: string;
  status: "ok" | "error" | "blocked" | "skipped";
  ms: number;
}
export type SceneImportMode = "on_demand" | "background" | "prepare_all";
export interface ImportJob {
  state: "running" | "done" | "error" | "cancelled" | "none";
  phase?: "catalogue" | "identity" | "overviews" | "complete";
  job_id?: string;
  project_id?: string;
  total?: number;
  done?: number;
  added?: number;
  updated?: number;
  blocked?: number;
  failed?: number;
  identity_total?: number;
  identity_done?: number;
  overviews_total?: number;
  overviews_done?: number;
  overview_active_scene_ids?: string[];
  overview_profile?: {
    workers?: number;
    compression?: string;
    predictor?: string | number;
    worker_selection?: string;
  };
  current?: { filename: string; scene_id?: string; stage: string; started_at?: string } | null;
  recent?: ImportJobRecent[];
  errors?: Array<{ filename: string; message: string }>;
  started_at?: string;
  updated_at?: string;
  error?: string;
  live?: boolean;
  stale?: boolean;
  overviews_incomplete?: boolean;
  overviews_deferred?: boolean;
  import_mode?: SceneImportMode;
  report?: {
    scan_id?: string;
    added: number;
    updated: number;
    missing: number;
    blocked: number;
    failed: number;
    archived?: number;
    considered: number;
    total: number;
    cancelled?: boolean;
  };
}

/** Nagłówek zapisanego raportu importu (P1.6). */
export interface SceneImportReportHeader {
  scan_id: string;
  created_at?: string;
  updated_at?: string;
  added?: number;
  updated?: number;
  blocked?: number;
  failed?: number;
  archived?: number;
  missing?: number;
  cancelled?: boolean;
  scene_count: number;
  error_count: number;
}

export type SceneMigrationClassification =
  | "unchanged"
  | "auto"
  | "needs_selection"
  | "source_unavailable";

export interface SceneMigrationSummary {
  scenes: number;
  unchanged: number;
  auto: number;
  needs_selection: number;
  source_unavailable: number;
  changed_fields: Record<string, number>;
  rebuild: Record<string, number>;
}

export interface SceneMigrationPlan {
  schema_name: string;
  schema_version: number;
  project_id: string;
  created_at: string;
  summary: SceneMigrationSummary;
  scenes: Array<{
    scene_id: string;
    filename?: string | null;
    source_id: string;
    classification: SceneMigrationClassification;
    current: Record<string, unknown>;
    proposed?: Record<string, unknown> | null;
    changes: string[];
    rebuild: string[];
    reason: string;
    scan_error?: string;
    alternatives?: Array<{
      package_id: string;
      label?: string | null;
      package_root_relative?: string | null;
      product_type?: string | null;
      status?: string | null;
      raster_kind?: string | null;
      asset_count: number;
      changes: string[];
      rebuild: string[];
    }>;
  }>;
}

export interface SceneMigrationApplyResult {
  project_id: string;
  backup_path?: string | null;
  migrated: string[];
  flagged: string[];
  summary: SceneMigrationSummary;
}

export const listSceneImportReports = (
  projectId: string,
  limit = 50,
): Promise<{ reports: SceneImportReportHeader[] }> =>
  api.get(`/projects/${projectId}/import-reports`, { params: { limit } }).then((r) => r.data);

/** Pełny raport importu. `anonymize` zwraca kopię bez ścieżek i nazw plików —
 *  do przekazania dalej; skróty są stabilne, więc scen da się nadal rozróżnić. */
export const getSceneImportReport = (
  projectId: string,
  scanId: string,
  anonymize = false,
): Promise<Record<string, unknown>> =>
  api
    .get(`/projects/${projectId}/import-reports/${scanId}`, { params: { anonymize } })
    .then((r) => r.data);
export const createProjectFromSources = (data: {
  name: string;
  preview_id: string;
  sources: SceneSourceInput[];
  decisions?: Array<{ package_id: string; decision_uid?: string; action?: "import" | "skip"; asset_ids: string[]; rgb_bands?: number[] }>;
  classes_file?: string;
  project_location?: string;
  profile?: ProjectProfile;
  tile_size?: number;
  buffer?: number;
  import_mode?: SceneImportMode;
}): Promise<Project & { import_job_id: string; import_state: string; total: number }> =>
  api.post("/projects/from-sources", data).then((r) => r.data);
export const getImportJob = (pid: string, jobId: string): Promise<ImportJob> =>
  api.get(`/projects/${pid}/import-jobs/${jobId}`).then((r) => r.data);
export const getLatestImportJob = (pid: string): Promise<ImportJob> =>
  api.get(`/projects/${pid}/import-jobs`).then((r) => r.data);
export interface SceneImportConfig {
  schema_name?: string;
  schema_version?: number;
  import_mode: SceneImportMode;
  auto_fullres_cog_enabled: boolean;
}

export const getSceneImportConfig = (pid: string): Promise<SceneImportConfig> =>
  api.get(`/projects/${pid}/scene-import/config`).then((response) => response.data);
export const updateSceneImportConfig = (
  pid: string,
  patch: Partial<Pick<SceneImportConfig, "import_mode" | "auto_fullres_cog_enabled">>,
): Promise<SceneImportConfig> =>
  api.put(`/projects/${pid}/scene-import/config`, patch).then((response) => response.data);
export const cancelImportJob = (pid: string, jobId: string): Promise<{ status: string }> =>
  api.post(`/projects/${pid}/import-jobs/${jobId}/cancel`).then((r) => r.data);
export const resumeImportJob = (
  pid: string,
  importMode?: SceneImportMode,
): Promise<{ import_job_id: string; import_state: string; total: number }> =>
  api.post(`/projects/${pid}/import-jobs/resume`, importMode ? { import_mode: importMode } : {}).then((r) => r.data);
export const getSceneSources = (pid: string) =>
  api.get(`/projects/${pid}/scene-sources`).then((r) => r.data);
export const getSceneImportMigrationPlan = (pid: string): Promise<SceneMigrationPlan> =>
  api.get(`/projects/${pid}/scene-import/migration/dry-run`).then((r) => r.data);
export const applySceneImportMigration = (
  pid: string,
  backup = true,
  decisions: Record<string, string> = {},
): Promise<SceneMigrationApplyResult> =>
  api.post(`/projects/${pid}/scene-import/migration/apply`, { backup, decisions }).then((r) => r.data);
export const addSceneSource = async (pid: string, source: SceneSourceInput): Promise<SceneImportPreview> => {
  const response = await api.post(`/projects/${pid}/scene-sources`, { source });
  return resolveSceneScanResponse(response.data);
};
export const rescanSceneSource = async (pid: string, sourceId: string): Promise<SceneImportPreview> => {
  const response = await api.post(`/projects/${pid}/scene-sources/${sourceId}/rescan`);
  return resolveSceneScanResponse(response.data);
};
export const applySceneSourcePreview = (
  pid: string,
  previewId: string,
  decisions: Array<{ package_id: string; decision_uid?: string; action?: "import" | "skip"; asset_ids: string[]; rgb_bands?: number[] }> = [],
  importMode?: SceneImportMode,
): Promise<{
  accepted?: boolean;
  import_job_id?: string;
  import_state?: string;
  added?: number;
  updated?: number;
  missing?: number;
  blocked?: number;
  /** Pakiety-archiwa pominiete przy katalogowaniu (P1.3a); licznik rozlaczny z reszta. */
  archived?: number;
  total: number;
}> => api.post(`/projects/${pid}/scene-sources/apply`, {
  preview_id: previewId,
  decisions,
  ...(importMode ? { import_mode: importMode } : {}),
}).then((r) => r.data);

export interface SceneOverviewRequestResult {
  status: string;
  action: "already_ready" | "prioritized" | "already_queued" | "queued";
  job_id?: string | null;
  job_type?: "scene_import" | "scene_overview";
}

export const requestSceneDisplayOverview = (
  pid: string,
  sceneId: string,
): Promise<SceneOverviewRequestResult> =>
  api.post(`/projects/${pid}/scenes/${sceneId}/display-overview`).then((response) => response.data);
export interface SceneRelinkReport {
  compatible: boolean;
  expected_packages: number;
  found_packages: number;
  missing_packages: string[];
  added_packages: string[];
  changed_products: Array<Record<string, unknown>>;
  missing_assets: Array<Record<string, unknown>>;
  changed_assets: Array<Record<string, unknown>>;
}
export const relinkSceneSource = (pid: string, sourceId: string, rootPath: string, dryRun = false): Promise<{
  status: "preview" | "relinked";
  report: SceneRelinkReport;
  import_job_id?: string;
  affected_scenes?: number;
}> => api.post(`/projects/${pid}/scene-sources/${sourceId}/relink`, {
  root_path: rootPath,
  dry_run: dryRun,
}).then((r) => r.data);
export const removeSceneSource = (
  pid: string,
  sourceId: string
): Promise<{ status: string; source_id: string; removed_scenes: number; scene_count: number }> =>
  api.delete(`/projects/${pid}/scene-sources/${sourceId}`).then((r) => r.data);
export const deleteProject = (id: string) =>
  api.delete(`/projects/${id}`).then((r) => r.data);
export const scanProject = (id: string) =>
  api.post(`/projects/${id}/scan`).then((r) => r.data);
export const getCapabilities = (): Promise<BackendCapabilities> =>
  api.get("/capabilities").then((r) => r.data);
export const downloadProjectBackup = (id: string): Promise<Blob> =>
  api.get(`/projects/${id}/backup/download`, { responseType: "blob" }).then((r) => r.data);
export const startProjectBackupJob = (id: string): Promise<DurableJobItem> =>
  api.post(`/projects/${id}/backup/jobs`).then((r) => r.data);
export const importProjectBackup = (data: {
  backup_file: string;
  scene_folder: string;
  name?: string;
}): Promise<ImportBackupResult> =>
  api.post("/projects/import-backup", data).then((r) => r.data);
export const importProjectFolder = (data: {
  project_folder: string;
  scene_folder: string;
  name?: string;
}): Promise<ImportBackupResult> =>
  api.post("/projects/import-folder", data).then((r) => r.data);
export const getAnnotationPackagePreview = (pid: string): Promise<AnnotationPackagePreview> =>
  api.get(`/projects/${pid}/annotation-package/preview`).then((r) => r.data);

/** Ile kosztuje odblokowanie eksportu — czytane z manifestów, bez dotykania rastrów. */
export interface AnnotationPackagePreparePlan {
  project_id: string;
  scene_count: number;
  total_bytes: number;
  scenes: Array<{ scene_id: string; filename: string; bytes: number }>;
}

export interface AnnotationPackagePrepareStart {
  job_id: string | null;
  plan: AnnotationPackagePreparePlan;
  /** true = nie było czego liczyć; zadania nie zlecono. */
  already_exact: boolean;
}

export const getAnnotationPackagePreparePlan = (
  pid: string,
): Promise<AnnotationPackagePreparePlan> =>
  api.get(`/projects/${pid}/annotation-package/prepare-plan`).then((r) => r.data);

export const startAnnotationPackagePreparation = (
  pid: string,
): Promise<AnnotationPackagePrepareStart> =>
  api.post(`/projects/${pid}/annotation-package/prepare`).then((r) => r.data);
export const saveAnnotationPackage = (pid: string, outputPath: string, packageId?: string) =>
  api.post(`/projects/${pid}/annotation-package/save`, {
    output_path: outputPath,
    package_id: packageId,
  }).then((r) => r.data);
export const previewAnnotationImport = (
  pid: string,
  packagePaths: string[],
  identityPolicy: "exact_only" | "allow_probable" = "exact_only"
): Promise<AnnotationImportPreview> =>
  api.post(`/projects/${pid}/annotation-import/preview`, {
    package_paths: packagePaths,
    identity_policy: identityPolicy,
  }).then((r) => r.data);
export const applyAnnotationImport = (
  pid: string,
  previewId: string,
  createMissingClasses = false,
  acceptedSceneIds?: string[]
): Promise<AnnotationImportReport> =>
  api.post(`/projects/${pid}/annotation-import/apply`, {
    preview_id: previewId,
    create_missing_classes: createMissingClasses,
    // Omitted => every safe plan. Listing a scene is the confirmation that lets a
    // destructive plan through.
    accepted_scene_ids: acceptedSceneIds ?? null,
  }).then((r) => r.data);
export const getAnnotationImportReport = (pid: string, reportId: string): Promise<AnnotationImportReport> =>
  api.get(`/projects/${pid}/annotation-import/reports/${reportId}`).then((r) => r.data);
export const getProjectAnnotationSummary = (
  pid: string,
  detail: "full" | "compact" = "full",
): Promise<ProjectAnnotationSummary> =>
  api.get(`/projects/${pid}/annotation-summary`, { params: { detail } }).then((r) => r.data);

// --- Training workbench ---
export interface BaseModelInfo {
  file: string;
  name: string;
  task: "detect" | "obb";
  params_m: number;
  /** false = architecture trained from random init; no local weights involved. */
  pretrained: boolean;
  path: string | null;
  present: boolean;
  size: number | null;
  sha256: string | null;
  license: string;
  ultralytics_min_version: string;
  available: boolean;
  reason: string | null;
  recommended: boolean;
  estimated_vram_gb: number;
}

export interface BaseModelCatalog {
  directory: string;
  annotation_mode: string | null;
  required_task: "detect" | "obb" | null;
  available_count: number;
  models: BaseModelInfo[];
}

export interface TrainingConfig {
  dataset_run_id: string;
  base_model: string;
  epochs: number;
  imgsz: number;
  batch: number;
  seed: number;
  patience: number;
  device: "auto" | "cpu" | "cuda";
  advanced_options?: Record<string, TrainingOptionValue>;
}

export type TrainingOptionValue = string | number | boolean | Array<string | number | boolean>;

export interface PreflightCheck {
  name: string;
  status: "ok" | "warning" | "error";
  detail: string;
}

export interface TrainingResourceOptions {
  batch: number;
  workers: number;
  cache: false | "ram" | "disk";
}

export interface TrainingResourceRecommendation {
  schema_name: "geotile_training_resource_recommendation";
  schema_version: number;
  bottleneck: {
    kind: "io" | "cpu_decode" | "gpu_compute_likely" | "balanced";
    confidence: "low" | "medium" | "high";
  };
  recommended_options: TrainingResourceOptions;
  current_options: TrainingResourceOptions & { workers_explicit: boolean };
  overrides: Record<string, { current: TrainingOptionValue; recommended: TrainingOptionValue }>;
  is_recommended_applied: boolean;
  batch_mode: string;
  thread_budget: {
    data_loader_workers: number;
    blas_threads_per_process: number;
    opencv_threads_per_process: number;
  };
  memory_safety: {
    ram_cache_safe: boolean;
    current_ram_cache_safe: boolean;
    estimated_ram_cache_bytes: number;
    effective_ram_cache_bytes: number;
    current_effective_ram_cache_bytes: number;
    cache_copy_factor: number;
    current_cache_copy_factor: number;
    multiprocessing_start_method: string;
    available_memory_bytes: number;
    required_headroom_bytes: number;
    remaining_after_cache_bytes: number;
  };
  warnings: string[];
  reasons: string[];
  loader_benchmark: {
    sample_count: number;
    decoded_count: number;
    failure_count: number;
    image_count: number;
    train_image_count: number;
    benchmark_seconds: number;
    images_per_second: number | null;
    read_mib_per_second: number | null;
    estimated_ram_cache_bytes: number;
  };
  system: {
    logical_cpu_count: number;
    physical_cpu_count: number;
    memory_total_bytes: number;
    memory_available_bytes: number;
    gpu_name?: string | null;
    vram_total_bytes?: number | null;
    vram_free_bytes?: number | null;
  };
}

export interface TrainingRunSummary {
  training_run_id: string;
  config_fingerprint: string | null;
  attempt: number | null;
  status: "queued" | "running" | "completed" | "cancelled" | "failed" | "interrupted" | "unknown";
  dataset_run_id: string | null;
  base_model: string | null;
  task: string | null;
  device: string | null;
  epochs_total: number | null;
  epochs_done: number;
  started_at: string | null;
  ended_at: string | null;
  error: string | null;
  has_manifest: boolean;
  metrics: Record<string, number | null> | null;
  /** Per-class metrics {className: {precision, recall, mAP50, mAP50-95}} for the per-class radar. */
  metrics_per_class?: Record<string, Record<string, number | null>> | null;
}

export interface TrainingPreflight {
  can_start: boolean;
  checks: PreflightCheck[];
  error_count: number;
  warning_count: number;
  duration_hint: string;
  config_fingerprint: string;
  device: string;
  task: "detect" | "obb";
  resource_recommendation: TrainingResourceRecommendation | null;
  /** Repeats are allowed on purpose — they show run-to-run variance. */
  previous_runs: TrainingRunSummary[];
  next_attempt: number;
}

export interface TrainingRunDetail extends TrainingRunSummary {
  run_dir?: string;
  job: Record<string, any>;
  manifest: Record<string, any> | null;
  state: Record<string, any>;
  metrics_detail?: {
    summary?: Record<string, number | null>;
    per_class?: Record<string, Record<string, number | null>>;
    source?: string;
  };
}

export interface TrainingHistory {
  columns: string[];
  rows: Array<Record<string, number | string>>;
}

export type JobStatus =
  | "queued"
  | "starting"
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "failed"
  | "interrupted";

export interface DurableJobSpec {
  job_id: string;
  job_type:
    | "training"
    | "dataset_build"
    | "scene_scan"
    | "scene_import"
    | "scene_preparation"
    | "scene_overview"
    | "scene_inference"
    | "embedding_analysis"
    | "dataset_export"
    | "project_backup"
    | "artifact_cleanup"
    | "annotation_package_prepare";
  project_id: string;
  resource_class: "gpu_exclusive" | "cpu_heavy" | "io_heavy" | "io_metadata";
  priority_class: "interactive" | "user_background" | "maintenance";
  priority: number;
  payload: Record<string, unknown>;
  dedupe_key?: string | null;
  retry_of?: string | null;
  attempt: number;
  created_at: string;
}

export interface DurableJobState {
  job_id: string;
  status: JobStatus;
  phase: string;
  current?: number | null;
  total?: number | null;
  // Postep bajtowy przygotowania paczki adnotacji. Licznik scen stoi w miejscu przez
  // caly odczyt jednego wielogigabajtowego rastra, wiec sam nie wystarcza.
  done_bytes?: number | null;
  total_bytes?: number | null;
  scene_index?: number | null;
  scene_count?: number | null;
  filename?: string | null;
  heartbeat?: string | null;
  cancel_requested: boolean;
  error?: string | null;
  artifacts: Record<string, unknown>;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  updated_at: string;
  revision: number;
}

export interface DurableJobItem {
  job: DurableJobSpec;
  state: DurableJobState;
}

export interface DurableJobsIndex {
  job_count: number;
  jobs: DurableJobItem[];
  resource_limits: Record<string, number>;
  leases: Array<Record<string, unknown>>;
}

export interface DurableJobDetail extends DurableJobItem {
  artifacts: Record<string, unknown>;
  performance: Record<string, unknown>;
}

export const getJobs = (pid: string): Promise<DurableJobsIndex> =>
  api.get(`/projects/${pid}/jobs`).then((response) => response.data);

export const getJob = (pid: string, jobId: string): Promise<DurableJobDetail> =>
  api.get(`/projects/${pid}/jobs/${jobId}`).then((response) => response.data);

export const getJobEvents = (pid: string, jobId: string, after = 0) =>
  api.get(`/projects/${pid}/jobs/${jobId}/events`, { params: { after } }).then((response) => response.data);

export const cancelJob = (pid: string, jobId: string): Promise<DurableJobState> =>
  api.post(`/projects/${pid}/jobs/${jobId}/cancel`).then((response) => response.data);

export const retryJob = (pid: string, jobId: string): Promise<DurableJobItem> =>
  api.post(`/projects/${pid}/jobs/${jobId}/retry`).then((response) => response.data);

export const downloadJobArtifactUrl = (pid: string, jobId: string) => {
  const params = new URLSearchParams(authToken ? { token: authToken } : {});
  return appendQueryParams(apiUrl(`/projects/${pid}/jobs/${jobId}/artifact`), params);
};

export const getBaseModels = (pid: string): Promise<BaseModelCatalog> =>
  api.get(`/projects/${pid}/training/base-models`).then((r) => r.data);

export const postTrainingPreflight = (pid: string, config: TrainingConfig): Promise<TrainingPreflight> =>
  api.post(`/projects/${pid}/training/preflight`, config).then((r) => r.data);

export const startTrainingRun = (
  pid: string,
  config: TrainingConfig
): Promise<{ training_run_id: string; config_fingerprint: string; attempt: number }> =>
  api.post(`/projects/${pid}/training/runs`, config).then((r) => r.data);

export const getTrainingRuns = (
  pid: string,
  datasetRunId?: string | null
): Promise<{ run_count: number; runs: TrainingRunSummary[] }> =>
  api.get(`/projects/${pid}/training/runs`, {
    params: datasetRunId ? { dataset_run_id: datasetRunId } : {},
  }).then((r) => r.data);

export const getTrainingRun = (pid: string, runId: string): Promise<TrainingRunDetail> =>
  api.get(`/projects/${pid}/training/runs/${runId}`).then((r) => r.data);

export const getTrainingHistory = (pid: string, runId: string): Promise<TrainingHistory> =>
  api.get(`/projects/${pid}/training/runs/${runId}/history`).then((r) => r.data);

export const getTrainingConfusionMatrix = (pid: string, runId: string): Promise<Blob> =>
  api.get(`/projects/${pid}/training/runs/${runId}/artifacts/confusion-matrix`, {
    responseType: "blob",
  }).then((r) => r.data as Blob);

export interface ConfusionAnalysis {
  class_names: string[];
  matrix: number[][];
  orientation: string | null;
  /** Symetryczne podobieństwo klas (0..1), N×N. */
  similarity: number[][];
  /** Kolejność klas z klasteryzacji — mylone pary sąsiadują. */
  leaf_order: number[];
  confused_pairs: { class_a: string; class_b: string; similarity: number }[];
}

export const getTrainingConfusionAnalysis = (
  pid: string,
  runId: string,
): Promise<ConfusionAnalysis> =>
  api
    .get(`/projects/${pid}/training/runs/${runId}/artifacts/confusion-analysis`)
    .then((r) => r.data as ConfusionAnalysis);

export const cancelTrainingRun = (pid: string, runId: string) =>
  api.post(`/projects/${pid}/training/runs/${runId}/cancel`).then((r) => r.data);

// --- Dataset Intelligence (DI2): analiza embeddingów (DINO) ---

/** Pochodzenie obiektu — wspólne dla wszystkich list analizy; wraca do edytora. */
export interface AnalysisObject {
  scene_id: string;
  annotation_id: string | null;
  class_id: number;
  class_name?: string;
  bbox: [number, number, number, number];
}

export interface ClassSimilarity {
  class_ids: number[];
  class_names: string[];
  similarity: number[][];
  leaf_order: number[];
  similar_pairs: { class_a: string; class_b: string; similarity: number }[];
}

export interface ClassCohesion {
  class_id: number;
  name: string;
  count: number;
  cohesion: number;
  spread: number;
  min_cohesion: number;
}

export interface SuspectedMislabel extends AnalysisObject {
  suggested_class_id: number;
  suggested_class_name: string;
  own_similarity: number;
  suggested_similarity: number;
  gap: number;
}

export interface NearDuplicatePair {
  similarity: number;
  a: AnalysisObject;
  b: AnalysisObject;
  same_class: boolean;
  split_a?: string | null;
  split_b?: string | null;
  cross_split?: boolean;
}

export interface ClassExample {
  annotation_id: string;
  scene_id: string;
  thumb: string;
  kind: "proto" | "boundary";
  own_similarity: number;
  other_class_id: number | null;
  other_similarity: number | null;
}

export interface EmbeddingAnalysisResult {
  schema_name: string;
  schema_version: number;
  project_id: string;
  backbone: string | null;
  n_objects: number;
  per_class: { class_id: number; name: string; count: number }[];
  class_similarity: ClassSimilarity;
  /** Miniatury przykładów per klasa (klucz = class_id jako string). */
  class_examples?: Record<string, ClassExample[]>;
  class_cohesion: ClassCohesion[];
  suspected_mislabels: SuspectedMislabel[];
  class_outliers: (AnalysisObject & { own_similarity: number })[];
  near_duplicates: NearDuplicatePair[];
  n_near_duplicates: number;
  /** Run, z którego wzięto split do wykrywania przecieku (null = analiza bez splitu). */
  split_dataset_run_id: string | null;
  n_split_tagged: number;
  n_leaks: number;
  split_error: string | null;
  generated_at: string;
}

export interface EmbeddingAnalysisState {
  project_id?: string;
  backbone?: string | null;
  status: "queued" | "running" | "completed" | "failed";
  stage?: string;
  n_objects?: number;
  error?: string;
  created_at?: string;
  heartbeat?: string;
  ended_at?: string;
}

export const startEmbeddingAnalysis = (
  pid: string,
  body?: { backbone?: string | null; mislabel_margin?: number; dup_threshold?: number; dataset_run_id?: string | null },
): Promise<{ status: string; backbone: string | null }> =>
  api.post(`/projects/${pid}/analysis/embedding`, body || {}).then((r) => r.data);

export const getEmbeddingAnalysis = (
  pid: string,
): Promise<{ state: EmbeddingAnalysisState | null; result: EmbeddingAnalysisResult | null }> =>
  api.get(`/projects/${pid}/analysis/embedding`).then((r) => r.data);

export const embeddingChipUrl = (pid: string, filename: string): string =>
  `${apiUrl(`/projects/${pid}/analysis/embedding/chip/${filename}`)}${displayQueryString()}`;

export const getNearestObjects = (
  pid: string,
  annotationId: string,
  k = 20,
): Promise<{ query_annotation_id: string; neighbors: (AnalysisObject & { similarity: number })[] }> =>
  api
    .get(`/projects/${pid}/analysis/embedding/nearest`, { params: { annotation_id: annotationId, k } })
    .then((r) => r.data);

export interface TestEvaluation {
  evaluated: boolean;
  evaluation: {
    split: string;
    /** Written once; re-running would turn the test split into a second val set. */
    final: boolean;
    evaluated_at: string;
    metrics: Record<string, number | null>;
  } | null;
  error?: string | null;
}

export interface RegisteredModel {
  model_id: string;
  training_run_id: string;
  dataset_run_id: string | null;
  dataset_label: string | null;
  task: string | null;
  base_model: string | null;
  checkpoint_path: string;
  attempt: number | null;
  validation_metrics: Record<string, number | null> | null;
  test_metrics: Record<string, number | null> | null;
  registered_at: string;
  status: "current" | "archived";
}

export interface ModelLineage {
  model_id: string;
  task: string | null;
  training_run: Record<string, any>;
  dataset_run: Record<string, any>;
  tile_catalog_id: string | null;
  scene_count: number;
  annotation_count: number;
  authors: Record<string, number>;
  annotation_sources: Record<string, number>;
  classes: Record<string, number>;
  /** True when the chain could not be walked down to annotation level. */
  truncated: boolean;
}

export const getTestEvaluation = (pid: string, runId: string): Promise<TestEvaluation> =>
  api.get(`/projects/${pid}/training/runs/${runId}/test-evaluation`).then((r) => r.data);

export const startTestEvaluation = (pid: string, runId: string) =>
  api.post(`/projects/${pid}/training/runs/${runId}/test-evaluation`).then((r) => r.data);

export const getRegisteredModels = (
  pid: string
): Promise<{ current_model_id: string | null; model_count: number; models: RegisteredModel[]; promotions: any[] }> =>
  api.get(`/projects/${pid}/training/models`).then((r) => r.data);

export const registerModel = (pid: string, trainingRunId: string): Promise<RegisteredModel> =>
  api.post(`/projects/${pid}/training/models/${trainingRunId}/register`).then((r) => r.data);

export const promoteModel = (pid: string, modelId: string) =>
  api.post(`/projects/${pid}/training/models/${modelId}/promote`).then((r) => r.data);

export const getModelLineage = (pid: string, modelId: string): Promise<ModelLineage> =>
  api.get(`/projects/${pid}/training/models/${modelId}/lineage`).then((r) => r.data);

// --- Review channel (manager -> analyst) ---
export const setSceneReview = (
  pid: string,
  sceneId: string,
  body: { review_status: ReviewStatus; review_comment?: string | null; pins?: ScenePin[] }
) => api.put(`/projects/${pid}/scene-review/${sceneId}`, body).then((r) => r.data);

export const getReviewPackagePreview = (pid: string, ownerEmail: string): Promise<ReviewPackagePreview> =>
  api.get(`/projects/${pid}/review-package/preview`, { params: { owner_email: ownerEmail } })
    .then((r) => r.data);

export const saveReviewPackage = (
  pid: string,
  outputPath: string,
  ownerEmail: string,
  packageId?: string
) =>
  api.post(`/projects/${pid}/review-package/save`, {
    output_path: outputPath,
    owner_email: ownerEmail,
    package_id: packageId,
  }).then((r) => r.data);

export const previewReviewImport = (pid: string, packagePaths: string[]): Promise<ReviewImportReport> =>
  api.post(`/projects/${pid}/review-import/preview`, { package_paths: packagePaths }).then((r) => r.data);

export const applyReviewImport = (pid: string, packagePaths: string[]): Promise<ReviewImportReport> =>
  api.post(`/projects/${pid}/review-import/apply`, { package_paths: packagePaths }).then((r) => r.data);

// --- Scenes ---
export const listScenes = (pid: string) =>
  api.get(`/projects/${pid}/scenes/`).then((r) => r.data);
export const getScenesIndex = (pid: string): Promise<ScenesIndex> =>
  api.get(`/projects/${pid}/scenes/index`).then((r) => r.data);
export interface ScenesPage {
  schema_name: "geotile_scenes_page";
  schema_version: number;
  project_id: string;
  source_revision: number;
  total_count: number;
  filtered_count: number;
  offset: number;
  limit: number;
  sort: string;
  order: "asc" | "desc";
  next_cursor: string | null;
  scenes: Scene[];
}
export interface ScenesPageQuery {
  cursor?: string | null;
  limit?: number;
  sort?: "filename" | "acquisition_datetime_utc" | "annotation_count" | "tile_count" | "gsd_m" | "status" | "scene_id";
  order?: "asc" | "desc";
  filter?: string;
  author?: string;
  class_id?: number;
  annotation_source?: string;
  import_id?: string;
  package_id?: string;
}
export const getScenesPage = (pid: string, query: ScenesPageQuery = {}): Promise<ScenesPage> =>
  api.get(`/projects/${pid}/scenes/index`, {
    params: { limit: 100, sort: "filename", order: "asc", ...query },
  }).then((r) => r.data);
export const refreshSceneIdentities = (pid: string, force = false): Promise<SceneIdentityRefreshResult> =>
  api.post(`/projects/${pid}/scenes/identities/refresh`, { force }).then((r) => r.data);
export const getScene = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/scenes/${sceneId}`).then((r) => r.data);
export const getSceneManifest = (pid: string, sceneId: string): Promise<SceneManifest> =>
  api.get(`/projects/${pid}/scenes/${sceneId}/manifest`).then((r) => r.data);
export const prepareScene = (pid: string, sceneId: string, rgbBands?: number[]) =>
  api.post(`/projects/${pid}/scenes/${sceneId}/prepare`, { rgb_bands: rgbBands }).then((r) => {
    if (r.data && typeof r.data === "object") return r.data;
    const body = String(r.data || "");
    const match = body.match(/event:\s*error[\s\S]*?data:\s*([^\r\n]+)/);
    if (match) {
      try {
        throw new Error(JSON.parse(match[1]).error || "Scene preparation failed");
      } catch (error) {
        if (error instanceof SyntaxError) throw new Error(match[1]);
        throw error;
      }
    }
    return body;
  });
export const cancelScenePreparation = (pid: string, sceneId: string) =>
  api.post(`/projects/${pid}/scenes/${sceneId}/prepare/cancel`).then((r) => r.data);
export const selectSceneAsset = (pid: string, sceneId: string, assetIds: string[], rgbBands?: number[]) =>
  api.post(`/projects/${pid}/scenes/${sceneId}/select-asset`, {
    asset_ids: assetIds,
    rgb_bands: rgbBands,
  }).then((r) => r.data);
export const removeManagedScene = (pid: string, sceneId: string) =>
  api.delete(`/projects/${pid}/scenes/${sceneId}`).then((r) => r.data);
export const sceneThumbnailUrl = (pid: string, sceneId: string, cacheVersion?: string | null) =>
  `${apiUrl(`/projects/${pid}/scenes/${sceneId}/thumbnail`)}${displayQueryString(undefined, cacheVersion)}`;
export const sceneTileUrl = (
  pid: string,
  sceneId: string,
  display?: DisplayParams,
  cacheVersion?: string | number | null,
  viewWindow?: ViewStretchWindow | null,
) =>
  `${apiUrl(`/projects/${pid}/scenes/${sceneId}/scene-tiles/{z}/{x}/{y}.png`)}${displayQueryString(display, cacheVersion, viewWindow)}`;
export const getSceneTileInfo = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/scenes/${sceneId}/scene-tiles/info`).then((r) => r.data);
/**
 * Statystyki bieżącego widoku (zakres rozciągnięcia „Widok”). `bounds` w pikselach siatki
 * sceny: [x0, y0, x1, y1], jak `getVisibleSceneBounds`.
 */
export const getSceneViewStats = (
  pid: string,
  sceneId: string,
  bounds: [number, number, number, number],
  signal?: AbortSignal,
): Promise<SceneViewStats> =>
  api.get(`/projects/${pid}/scenes/${sceneId}/view-stats`, {
    params: { x0: bounds[0], y0: bounds[1], x1: bounds[2], y1: bounds[3] },
    signal,
  }).then((r) => r.data);
export const startSceneFullresDerivative = (
  pid: string,
  sceneId: string,
  retryFailed = false,
) => api.post(`/projects/${pid}/scenes/${sceneId}/fullres-derivative`, {
  retry_failed: retryFailed,
}).then((r) => r.data);
export const getSceneHistogram = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/scenes/${sceneId}/histogram`).then((r) => r.data);
export const setSceneRgbBands = (pid: string, sceneId: string, rgb_bands: number[]) =>
  api.post(`/projects/${pid}/scenes/${sceneId}/rgb-bands`, { rgb_bands }).then((r) => r.data);

// --- Annotations (scene-level) ---
export const listAnnotations = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/scenes/${sceneId}/annotations/`).then((r) => r.data);
export const createAnnotation = (pid: string, sceneId: string, data: any) =>
  api.post(`/projects/${pid}/scenes/${sceneId}/annotations/`, data).then((r) => r.data);
export const updateAnnotation = (pid: string, sceneId: string, annId: string, data: any) =>
  api.put(`/projects/${pid}/scenes/${sceneId}/annotations/${annId}`, data).then((r) => r.data);
export const deleteAnnotation = (pid: string, sceneId: string, annId: string) =>
  api.delete(`/projects/${pid}/scenes/${sceneId}/annotations/${annId}`).then((r) => r.data);

// --- Classes (project-level) ---
export const listClasses = (pid: string) =>
  api.get(`/projects/${pid}/classes/`).then((r) => r.data);
export const createClass = (pid: string, data: any) =>
  api.post(`/projects/${pid}/classes/`, data).then((r) => r.data);
export const importClasses = (pid: string, filePath: string) =>
  api.post(`/projects/${pid}/classes/import`, { file_path: filePath }).then((r) => r.data);
export const updateClass = (pid: string, classId: number, data: any) =>
  api.put(`/projects/${pid}/classes/${classId}`, data).then((r) => r.data);
export const deleteClass = (pid: string, classId: number) =>
  api.delete(`/projects/${pid}/classes/${classId}`).then((r) => r.data);

// --- Tiling ---
export const getTilingConfig = (pid: string) =>
  api.get(`/projects/${pid}/tiling/config`).then((r) => r.data);
export const updateTilingConfig = (pid: string, data: any) =>
  api.put(`/projects/${pid}/tiling/config`, data).then((r) => r.data);
export const getTilingPreview = (pid: string, sceneId?: string) =>
  api.get(`/projects/${pid}/tiling/preview`, { params: sceneId ? { scene_id: sceneId } : {} }).then((r) => r.data);
export const listTiles = (pid: string, sceneId?: string) =>
  api.get(`/projects/${pid}/tiling/tiles`, { params: sceneId ? { scene_id: sceneId } : {} }).then((r) => r.data);
export const getActiveTileCatalog = (pid: string) =>
  api.get(`/projects/${pid}/tiling/catalog`).then((r) => r.data);
export const migrateLegacyTileCatalog = (pid: string) =>
  api.post(`/projects/${pid}/tiling/catalog/migrate`).then((r) => r.data);
export const tilePreviewUrl = (pid: string, tileId: string) => {
  const params = new URLSearchParams(authToken ? { token: authToken } : {});
  return appendQueryParams(apiUrl(`/projects/${pid}/tiling/tiles/${tileId}/preview`), params);
};
export type SceneWorkingStorageCategory = {
  bytes: number;
  file_count: number;
  scene_count: number;
};

/**
 * Rozmiar `derived_scenes` projektu: piramidy, VRT i COG przygotowane do wyświetlania
 * scen. Suma obejmuje WYLACZNIE ten katalog, zeby zgadzala sie z rozmiarem folderu,
 * ktory otwiera przycisk w panelu — zrodla, adnotacje i katalogi kafli sa poza nia.
 */
export type SceneWorkingStorageSummary = {
  schema_name: "geotile_scene_working_storage";
  schema_version: 1;
  project_id: string;
  generated_at: string;
  scan_duration_ms: number;
  working_files_dir: string;
  directory_exists: boolean;
  total: SceneWorkingStorageCategory;
  categories: Record<string, SceneWorkingStorageCategory>;
  largest_scenes: Array<{
    scene_id: string;
    display_name?: string | null;
    bytes: number;
    file_count: number;
    categories: Record<string, number>;
  }>;
  scan_errors: number;
};

export const getSceneWorkingStorage = (
  pid: string,
  largestLimit = 10,
): Promise<SceneWorkingStorageSummary> =>
  api
    .get(`/projects/${pid}/scene-working-storage`, { params: { largest_limit: largestLimit } })
    .then((r) => r.data);


// --- Geo tiles ---
export const sceneGeoTileUrl = (
  pid: string,
  sceneId: string,
  display?: DisplayParams,
  cacheVersion?: string | number | null,
  viewWindow?: ViewStretchWindow | null,
) =>
  `${apiUrl(`/projects/${pid}/scenes/${sceneId}/geo-tiles/{z}/{x}/{y}.png`)}${displayQueryString(display, cacheVersion, viewWindow)}`;

// --- Tile review ---
export const listTilesForScene = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/tiling/tiles`, { params: { scene_id: sceneId } }).then((r) => r.data);

export const reviewTiles = (pid: string, sceneId: string, indices: number[], reviewed: boolean) =>
  api.patch(`/projects/${pid}/tiling/tiles/review`, { tile_indices: indices, reviewed }, { params: { scene_id: sceneId } }).then((r) => r.data);

export const excludeTiles = (pid: string, sceneId: string, indices: number[], excluded: boolean) =>
  api.patch(`/projects/${pid}/tiling/tiles/exclude`, { tile_indices: indices, excluded }, { params: { scene_id: sceneId } }).then((r) => r.data);

export const getTileProgress = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/tiling/tiles/progress`, { params: { scene_id: sceneId } }).then((r) => r.data);

// SSE endpoints (use EventSource directly)
export const tilingExecuteUrl = (pid: string, sceneId?: string) => {
  const params = new URLSearchParams();
  if (authToken) params.set("token", authToken);
  if (sceneId) params.set("scene_id", sceneId);
  return appendQueryParams(apiUrl(`/projects/${pid}/tiling/execute`), params);
};
export const datasetGenerateUrl = (pid: string) =>
  appendQueryParams(apiUrl(`/projects/${pid}/dataset/generate`), new URLSearchParams(authToken ? { token: authToken } : {}));

// --- Dataset ---
export const getDatasetConfig = (pid: string) =>
  api.get(`/projects/${pid}/dataset/config`).then((r) => r.data);
export const updateDatasetConfig = (pid: string, data: any) =>
  api.put(`/projects/${pid}/dataset/config`, data).then((r) => r.data);
export const getDatasetFilterOptions = (pid: string): Promise<DatasetFilterOptions> =>
  api.get(`/projects/${pid}/dataset/filter-options`).then((r) => r.data);
export const getPreprocessingProfiles = (pid: string): Promise<PreprocessingProfilesFile> =>
  api.get(`/projects/${pid}/dataset/preprocessing-profiles`).then((r) => r.data);
export const putPreprocessingProfile = (
  pid: string,
  profile: PreprocessingProfile
): Promise<PreprocessingProfile> =>
  api.put(`/projects/${pid}/dataset/preprocessing-profiles/${profile.profile_id}`, profile).then((r) => r.data);
export const getDatasetStats = (pid: string, runId?: string | null): Promise<DatasetStats> =>
  api.get(`/projects/${pid}/dataset/stats`, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const getDatasetRuns = (pid: string): Promise<DatasetRunsIndex> =>
  api.get(`/projects/${pid}/dataset/runs`).then((r) => r.data);
export const getDatasetRun = (pid: string, runId: string): Promise<DatasetRunManifest> =>
  api.get(`/projects/${pid}/dataset/runs/${runId}`).then((r) => r.data);
export const getDatasetRunStats = (pid: string, runId: string): Promise<DatasetStats> =>
  api.get(`/projects/${pid}/dataset/runs/${runId}/stats`).then((r) => r.data);
export const getDatasetRunPublication = (pid: string, runId: string): Promise<DatasetPublication> =>
  api.get(`/projects/${pid}/dataset/runs/${runId}/publication`).then((r) => r.data);
export const setDatasetRunPublication = (
  pid: string,
  runId: string,
  body: { status: DatasetPublicationStatus; label?: string | null; acknowledge_issues?: boolean }
): Promise<DatasetPublication> =>
  api.put(`/projects/${pid}/dataset/runs/${runId}/publication`, body).then((r) => r.data);

// --- Kasowanie runów (datasetu / treningu) z bramką zależności lineage ---
export interface RunDeleteInfo {
  run_id?: string;
  training_run_id?: string;
  training_runs?: Array<{
    training_run_id: string;
    base_model?: string | null;
    attempt?: number | null;
    status?: string | null;
  }>;
  models?: Array<{ model_id: string; training_run_id?: string; is_current: boolean }>;
  published?: boolean;
  is_latest?: boolean;
  is_current_model?: boolean;
  active?: boolean;
  status?: string | null;
  blocked: boolean;
}
export const getDatasetRunDeleteInfo = (pid: string, runId: string): Promise<RunDeleteInfo> =>
  api.get(`/projects/${pid}/dataset/runs/${runId}/delete-info`).then((r) => r.data);
export const deleteDatasetRun = (
  pid: string,
  runId: string,
  force = false,
): Promise<{ deleted: boolean }> =>
  api.delete(`/projects/${pid}/dataset/runs/${runId}`, { params: { force } }).then((r) => r.data);
export const getTrainingRunDeleteInfo = (pid: string, runId: string): Promise<RunDeleteInfo> =>
  api.get(`/projects/${pid}/training/runs/${runId}/delete-info`).then((r) => r.data);
export const deleteTrainingRun = (
  pid: string,
  runId: string,
  force = false,
): Promise<{ deleted: boolean }> =>
  api.delete(`/projects/${pid}/training/runs/${runId}`, { params: { force } }).then((r) => r.data);

// --- Przegląd zawartości opublikowanego datasetu (DI-F) ---
export interface DatasetContentBox {
  class_id: number;
  name: string;
  color: string | null;
  cx: number;
  cy: number;
  w: number;
  h: number;
}
export interface DatasetContentSample {
  filename: string;
  split: string;
  boxes: DatasetContentBox[];
  scene_id: string | null;
  tile_x0: number | null;
  tile_y0: number | null;
  tile_size: number;
}
export interface DatasetContentSummary {
  run_id: string;
  tile_size: number;
  splits: { split: string; tiles: number }[];
  classes: {
    class_id: number;
    name: string;
    color: string | null;
    tiles: number;
    per_split: Record<string, number>;
  }[];
}
export interface DatasetContentSamples {
  run_id: string;
  split: string;
  class_id: number | null;
  offset: number;
  limit: number;
  total: number;
  tile_size: number;
  items: DatasetContentSample[];
}

export const getDatasetContentSummary = (pid: string, runId: string): Promise<DatasetContentSummary> =>
  api.get(`/projects/${pid}/dataset/runs/${runId}/content`).then((r) => r.data);

export const getDatasetContentSamples = (
  pid: string,
  runId: string,
  params: { split: string; class_id?: number | null; offset?: number; limit?: number }
): Promise<DatasetContentSamples> =>
  api
    .get(`/projects/${pid}/dataset/runs/${runId}/content/samples`, {
      params: {
        split: params.split,
        ...(params.class_id != null ? { class_id: params.class_id } : {}),
        offset: params.offset ?? 0,
        limit: params.limit ?? 60,
      },
    })
    .then((r) => r.data);

export const datasetSampleImageUrl = (
  pid: string,
  runId: string,
  split: string,
  filename: string
): string => {
  const path = apiUrl(
    `/projects/${pid}/dataset/runs/${runId}/content/samples/${split}/${encodeURIComponent(filename)}/image`
  );
  return authToken
    ? appendQueryParams(path, new URLSearchParams({ token: authToken }))
    : path;
};

export const getDatasetAudit = (pid: string, runId?: string | null): Promise<DatasetAuditReport> =>
  api.get(`/projects/${pid}/dataset/audit`, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const refreshDatasetAudit = (pid: string, runId?: string | null): Promise<DatasetAuditReport> =>
  api.post(`/projects/${pid}/dataset/audit/refresh`, undefined, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const datasetAuditExportUrl = (
  pid: string,
  format: "json" | "csv",
  runId?: string | null
) => {
  const params = new URLSearchParams(authToken ? { token: authToken } : {});
  params.set("format", format);
  if (runId) params.set("run_id", runId);
  return appendQueryParams(apiUrl(`/projects/${pid}/dataset/audit/export`), params);
};

export const datasetStatsExportUrl = (
  pid: string,
  runId?: string | null,
  section: "classes" | "geometry" | "cooccurrence" | "acquisition" = "classes"
) => {
  const params = new URLSearchParams(authToken ? { token: authToken } : {});
  params.set("format", "csv");
  params.set("section", section);
  if (runId) params.set("run_id", runId);
  return appendQueryParams(apiUrl(`/projects/${pid}/dataset/stats/export`), params);
};

export const saveDatasetStats = (
  pid: string,
  outputPath: string,
  runId?: string | null,
  section: "classes" | "geometry" | "cooccurrence" | "acquisition" = "classes"
): Promise<{ status: string; output_path: string }> =>
  api.post(`/projects/${pid}/dataset/stats/save`, {
    output_path: outputPath,
    run_id: runId || null,
    section,
  }).then((r) => r.data);
export const saveDatasetAudit = (
  pid: string,
  outputPath: string,
  format: "json" | "csv",
  runId?: string | null
): Promise<{ status: string; run_id?: string | null; format: string; output_path: string }> =>
  api.post(`/projects/${pid}/dataset/audit/save`, {
    output_path: outputPath,
    format,
    run_id: runId || null,
  }).then((r) => r.data);
export const getDatasetLocation = (
  pid: string,
  runId?: string | null
): Promise<{ exists: boolean; dataset_dir: string; run_id?: string | null }> =>
  api.get(`/projects/${pid}/dataset/location`, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);

// --- Export ---
export const exportYolo = (pid: string, runId?: string | null) =>
  api.post(`/projects/${pid}/export/yolo`, undefined, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const exportCoco = (pid: string, runId?: string | null) =>
  api.post(`/projects/${pid}/export/coco`, undefined, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const exportVoc = (pid: string, runId?: string | null) =>
  api.post(`/projects/${pid}/export/voc`, undefined, { params: runId ? { run_id: runId } : {} }).then((r) => r.data);
export const downloadDatasetUrl = (pid: string, runId?: string | null) => {
  const params = new URLSearchParams(authToken ? { token: authToken } : {});
  if (runId) params.set("run_id", runId);
  return appendQueryParams(apiUrl(`/projects/${pid}/export/download`), params);
};
export const saveDatasetZip = (
  pid: string,
  outputPath: string,
  runId?: string | null
): Promise<{ status: string; run_id?: string | null; output_path: string }> =>
  api.post(`/projects/${pid}/export/save-zip`, { output_path: outputPath, run_id: runId || null }).then((r) => r.data);
export type DatasetExportFormat = "yolo" | "coco" | "voc";
export const startDatasetExportJob = (
  pid: string,
  formats: DatasetExportFormat[],
  runId?: string | null,
  outputPath?: string | null,
): Promise<DurableJobItem & { disk_estimate: Record<string, number>; cache_key: string }> =>
  api.post(`/projects/${pid}/export/jobs`, {
    run_id: runId || null,
    formats,
    output_path: outputPath || null,
  }).then((r) => r.data);
export const exportSourceAnnotationsGeoParquet = (
  pid: string,
  outputPath: string
): Promise<SourceAnnotationsGeoParquetResult> =>
  api.post(`/projects/${pid}/export/source-annotations/geoparquet`, { output_path: outputPath }).then((r) => r.data);

// --- Predictions ---
export const listModels = (pid: string) =>
  api.get(`/projects/${pid}/predictions/models`).then((r) => r.data);
export interface PredictionBrowseResult {
  path: string;
  parent_path: string;
  breadcrumbs: BrowseBreadcrumb[];
  dirs: { name: string; path: string; is_root?: boolean }[];
  files: { name: string; path: string; size: number }[];
}
export const browsePredictionModels = (pid: string, path = ""): Promise<PredictionBrowseResult> =>
  api.post(`/projects/${pid}/predictions/models/browse`, { path }).then((r) => r.data);
export interface ModelInspectResult {
  name: string;
  path: string;
  classes: { id: number; name: string }[];
}
export const inspectPredictionModel = (pid: string, model_path: string): Promise<ModelInspectResult> =>
  api.post(`/projects/${pid}/predictions/models/inspect`, { model_path }).then((r) => r.data);
export interface SamModelInfo {
  name: string;
  path: string;
  size: number;
  modified: string;
  family: "sam1" | "sam2" | "sam3" | null;
  supported: boolean;
  reason: string | null;
}
export interface SamModelsResult {
  models: SamModelInfo[];
  models_dir_path: string;
  models_dir_available: boolean;
  custom_models_dir: boolean;
  default_checkpoint: string | null;
}
export const listSamModels = (pid: string): Promise<SamModelsResult> =>
  api.get(`/projects/${pid}/predictions/sam/models`).then((r) => r.data);

export interface DinoModelInfo {
  name: string;
  path: string;
  family: "dinov2" | "dinov3" | null;
  arch: string | null;
  label: string;
  supported: boolean;
}
export interface DinoModelsResult {
  models: DinoModelInfo[];
  models_dir_path: string;
  models_dir_available: boolean;
  custom_models_dir: boolean;
  default_checkpoint: string | null;
  selected_checkpoint: string | null;
  runtime_available: boolean;
}
export const listDinoModels = (pid: string): Promise<DinoModelsResult> =>
  api.get(`/projects/${pid}/predictions/dino/models`).then((r) => r.data);
export const inspectSamModel = (pid: string, model_path: string): Promise<SamModelInfo> =>
  api.post(`/projects/${pid}/predictions/sam/models/inspect`, { model_path }).then((r) => r.data);
export const getPredictionConfig = (pid: string): Promise<PredictionConfig> =>
  api.get(`/projects/${pid}/predictions/config`).then((r) => r.data);
export const updatePredictionConfig = (pid: string, data: PredictionConfig): Promise<PredictionConfig> =>
  api.put(`/projects/${pid}/predictions/config`, data).then((r) => r.data);
export const predictionRunUrl = (pid: string, sceneId: string) =>
  appendQueryParams(apiUrl(`/projects/${pid}/predictions/${sceneId}/run`), new URLSearchParams(authToken ? { token: authToken } : {}));

export const cancelPrediction = (pid: string, sceneId: string) =>
  api.post(`/projects/${pid}/predictions/${sceneId}/cancel`).then((r) => r.data);
export const listPredictions = (pid: string, sceneId: string) =>
  api.get(`/projects/${pid}/predictions/${sceneId}/predictions`).then((r) => r.data);
export const acceptPredictions = (pid: string, sceneId: string, ids: string[]) =>
  api.patch(`/projects/${pid}/predictions/${sceneId}/predictions/accept`, { prediction_ids: ids }).then((r) => r.data);
export const deletePredictions = (pid: string, sceneId: string, ids: string[]) =>
  api.post(`/projects/${pid}/predictions/${sceneId}/predictions/delete`, { prediction_ids: ids }).then((r) => r.data);
export const clearPredictions = (pid: string, sceneId: string) =>
  api.delete(`/projects/${pid}/predictions/${sceneId}/predictions`).then((r) => r.data);
export const acceptAllPredictions = (pid: string, sceneId: string) =>
  api.patch(`/projects/${pid}/predictions/${sceneId}/predictions/accept`, { prediction_ids: [], all: true }).then((r) => r.data);

// --- AI assist (SAM click-to-box) ---
export interface SamClickResult {
  session_id: string;
  proposal: Prediction;
  rotated_bbox: { cx: number; cy: number; width: number; height: number; angle_deg: number } | null;
  source_window: [number, number, number, number];
  needs_front_direction: boolean;
}
export const samClick = (
  pid: string,
  sceneId: string,
  data: {
    x: number;
    y: number;
    geometry_type?: "bbox" | "rotated_bbox";
    class_id?: number | null;
    positive_points?: [number, number][];
    negative_points?: [number, number][];
    /** SB3: rozmiar odniesienia [x0,y0,x1,y1] px sceny (np. z zaznaczonej adnotacji). */
    size_prior_bbox?: number[] | null;
  }
): Promise<SamClickResult> =>
  api.post(`/projects/${pid}/scenes/${sceneId}/assist/sam/click`, data).then((r) => r.data);

export interface SamTextResult {
  session_id: string;
  found: number;
  proposals: Prediction[];
}
/** ST: SAM3 tekstowy — segmentacja instancji aktywnej klasy w oknie widoku. */
export const samText = (
  pid: string,
  sceneId: string,
  data: {
    class_id: number;
    search_bbox: number[]; // [x0,y0,x1,y1] px sceny (widok / narysowany box)
    text?: string; // prompt SAM3 (open-vocab, EN); puste → nazwa klasy
    geometry_type?: "bbox" | "rotated_bbox";
    confidence_threshold?: number;
  }
): Promise<SamTextResult> =>
  api.post(`/projects/${pid}/scenes/${sceneId}/assist/sam/text`, data).then((r) => r.data);
export const rotateProposalFront = (
  pid: string,
  sceneId: string,
  proposalId: string
): Promise<{ proposal: Prediction }> =>
  api.post(`/projects/${pid}/scenes/${sceneId}/assist/proposals/${proposalId}/rotate-front`).then((r) => r.data);
export const flipProposalFront = rotateProposalFront;
export type ExemplarSearchMode = "local" | "viewport" | "scene";
export type ExemplarEngine = "template" | "dino";
export interface ExemplarMatchingSettings {
  scaleTolerance: 0 | 0.1 | 0.2;
  rotationToleranceDeg: 0 | 20 | 45;
  threshold: number;
  useEdges: boolean;
  /** Osobny próg dla silnika DINO (centrowany cosine — inna skala niż NCC). */
  dinoThreshold: number;
}
export const findSimilar = (
  pid: string,
  sceneId: string,
  data: {
    exemplar_bbox: [number, number, number, number];
    class_id?: number | null;
    threshold?: number;
    geometry_type?: "bbox" | "rotated_bbox";
    exemplar_polygon_scene_px?: [number, number][] | null;
    exemplar_rotated_bbox?: {
      cx: number;
      cy: number;
      width: number;
      height: number;
      angle_deg: number;
    } | null;
      exemplar_front_vector?: [number, number] | null;
      search_mode?: ExemplarSearchMode;
      search_bbox?: [number, number, number, number] | null;
      engine?: ExemplarEngine;
      extra_exemplar_bboxes?: number[][] | null;
      scale_tolerance?: number;
      rotation_tolerance_deg?: number;
      use_edges?: boolean;
    }
): Promise<{ session_id: string; found: number; proposals: Prediction[] }> =>
  api.post(`/projects/${pid}/scenes/${sceneId}/assist/exemplar/find-similar`, data).then((r) => r.data);
