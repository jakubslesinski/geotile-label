export type ProjectModality = "SAR" | "EO" | "AERIAL_EO";
export type ProjectGeoreferencing = "GEO" | "NO_GEO" | "SENSOR_GEO";
export type AnnotationMode = "bbox" | "rotated_bbox" | "mask";
export type MapTool =
  | "select"
  | "draw"
  | "multi_select"
  | "measure"
  | "class_paint"
  | "grid_review"
  | "grid_clear"
  | "grid_exclude"
  | "sam_click";
export type GeometryType = "bbox" | "rotated_bbox" | "polygon" | "mask";
export type SplitMode =
  | "random_tile"
  | "scene_split"
  | "image_block_split"
  | "spatial_block_split"
  | "class_balanced_spatial";

export type ProjectRole = "labeling" | "review";

export interface ProjectProfile {
  modality: ProjectModality;
  allowed_modalities?: ProjectModality[];
  georeferencing: ProjectGeoreferencing;
  allowed_georeferencing?: ProjectGeoreferencing[];
  allow_mixed_scenes?: boolean;
  sensors: string[];
  annotation_mode: AnnotationMode;
  labeling_author_email?: string | null;
  project_role?: ProjectRole;
  default_preprocessing_profile: string;
  default_split_strategy: SplitMode;
}

export interface SourceOverviewState {
  schema_version: number;
  type: string;
  driver?: string | null;
  usable: boolean;
  factors: number[];
  factors_by_band: number[][];
  sidecar_present: boolean;
  sidecar_fingerprint: string;
  fingerprint: string;
  artifacts: Array<{
    type: string;
    path: string;
    filename: string;
    size: number;
    mtime_ns: number;
    ctime_ns: number;
    file_id: string;
  }>;
  read_error?: string | null;
}

export interface SceneInfo {
  width: number;
  height: number;
  channels: number;
  dtype: string;
  has_geo: boolean;
  crs: string | null;
  crs_proj4: string | null;
  transform: number[] | null; // [a, b, c, d, e, f]
  bounds: [number, number, number, number] | null; // [west, south, east, north]
  source_overviews?: SourceOverviewState | null;
  file_size: number;
  filename: string;
}

// "nitf_sensor" is a frontend-only UI marker for the airborne-NITF creation
// flow; the created project's backend source_type stays "local_scenes" and is
// distinguished by profile.georeferencing === "SENSOR_GEO".
export type ProjectSourceType = "local_scenes" | "nitf_sensor";

export interface Project {
  id: string;
  name: string;
  schema_version?: number;
  app_version?: string;
  created_at: string;
  updated_at?: string;
  scene_folder: string;
  scene_folder_display?: string;
  project_root?: string;
  storage_mode?: string;
  created_in_appdata?: boolean;
  profile?: ProjectProfile;
  scene_count: number;
  source_type?: ProjectSourceType;
}

export interface Scene {
  id: string;
  filename: string;
  display_name?: string | null;
  source_id?: string | null;
  package_id?: string | null;
  provider_scene_id?: string | null;
  product_type?: string | null;
  raster_format?: string | null;
  raster_part_count?: number | null;
  gsd_m?: number | null;
  raster_kind?: string | null;
  working_variant_id?: string | null;
  working_variant_fingerprint?: string | null;
  working_grid_uid?: string | null;
  working_asset_locked?: boolean;
  preparation_status?: string | null;
  overview_status?: string | null; // ready | native | pending | queued | building | error
  // Kontrakt R0.4/R0.1 — patrz utils/sceneOverviews.ts.
  preview_status?: string | null;
  fullres_derivative_status?: string | null; // missing | queued | building | validating | ready | error | stale
  available_native_zoom?: number | null;
  source_max_zoom?: number | null;
  overview_type?: string | null;
  overview_factors?: number[];
  overview_fingerprint?: string | null;
  overview_sidecar_fingerprint?: string | null;
  overview_changed_at?: string | null;
  scene_info: SceneInfo | null;
  manifest_schema_version?: number | null;
  source_scene_uid?: string | null;
  source_scene_candidate_uid?: string | null;
  source_identity_status?: string | null;
  source_identity_method?: string | null;
  source_identity_strength?: "heuristic" | "exact" | null;
  modality?: ProjectModality | string | null;
  georeferencing?: ProjectGeoreferencing | string | null;
  sensor?: string | null;
  provider?: string | null;
  acquisition_datetime_utc?: string | null;
  metadata_status?: string | null;
  profile_warning_count?: number;
  status: string;
  /** Scene-level review verdict (team workflow); independent of tiling `status`. */
  review_status?: "none" | "accepted" | "rejected" | "needs_fix";
  review_comment?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  review_round?: number;
  review_pins?: Array<{ source_annotation_id: string; comment?: string | null }>;
  annotation_count: number;
  tile_count: number;
  created_at: string;
}

export interface SceneIndexEntry {
  scene_id: string;
  source_scene_uid: string | null;
  source_scene_candidate_uid?: string | null;
  source_identity_status: string | null;
  source_identity_method?: string | null;
  source_identity_strength?: "heuristic" | "exact" | null;
  source_file_sha256: string | null;
  source_file_content_signature?: string | null;
  source_file_content_signature_method?: string | null;
  source_file_size: number | null;
  filename: string;
  modality: string | null;
  georeferencing: string | null;
  sensor: string | null;
  provider?: string | null;
  acquisition_datetime_utc: string | null;
  metadata_status: string | null;
  profile_warning_count?: number;
  width: number | null;
  height: number | null;
  channels: number | null;
  dtype: string | null;
  has_geo: boolean | null;
  crs: string | null;
  bbox_lonlat: [number, number, number, number] | null;
  status: string | null;
  overview_status?: string | null;
  overview_type?: string | null;
  overview_factors?: number[];
  overview_fingerprint?: string | null;
  overview_sidecar_fingerprint?: string | null;
  overview_changed_at?: string | null;
  annotation_count: number;
  tile_count: number;
}

export interface ScenesIndex {
  schema_name: string;
  schema_version: number;
  project_id: string;
  updated_at: string;
  scene_count: number;
  scenes: SceneIndexEntry[];
}

export interface SceneManifestWarning {
  code: string;
  severity: "info" | "warning" | "error" | string;
  message: string;
}

export interface SceneManifest {
  schema_name: string;
  schema_version: number;
  app_version?: string;
  created_at: string;
  project_id: string;
  scene_id: string;
  filename: string;
  source_path: string | null;
  source_exists: boolean;
  source_scene_uid: string | null;
  source_scene_candidate_uid?: string | null;
  source_file_sha256: string | null;
  source_file_content_signature?: string | null;
  source_file_content_signature_method?: string | null;
  source_file_size: number | null;
  source_file_mtime_ns?: number | null;
  source_filename: string;
  source_identity_version: number;
  source_identity_method: string | null;
  source_identity_strength?: "heuristic" | "exact" | null;
  source_identity_status: "pending" | "complete" | "missing" | "error" | string;
  source_identity_computed_at: string | null;
  source_identity_error?: string | null;
  modality: string | null;
  georeferencing: string | null;
  sensor: string | null;
  provider?: string | null;
  acquisition_datetime_utc: string | null;
  metadata_status: string | null;
  metadata_files: string[];
  metadata_sources?: Array<{ path: string; parser: string | null }>;
  parser?: {
    name: string | null;
    version: number | null;
    diagnostics?: {
      warnings?: string[];
      errors?: string[];
    };
  };
  acquisition?: {
    datetime_utc: string | null;
    source: string | null;
  };
  sar?: Record<string, unknown> | null;
  eo?: Record<string, unknown> | null;
  image?: Record<string, unknown>;
  geospatial?: {
    has_geo?: boolean;
    crs?: string | null;
    crs_proj4?: string | null;
    transform?: number[] | null;
    bounds_wgs84?: [number, number, number, number] | null;
  };
  display?: Record<string, unknown>;
  profile_validation?: {
    status: "ok" | "warning" | string;
    warnings: SceneManifestWarning[];
  };
  source_package?: Record<string, any> | null;
  source_identity?: Record<string, any> | null;
  working_view?: Record<string, any> | null;
}

export interface SceneIdentityRefreshResult {
  project_id: string;
  scene_count: number;
  complete: number;
  exact: number;
  errors: number;
  force: boolean;
  scenes: Array<{
    scene_id: string;
    filename: string;
    source_scene_uid: string | null;
    source_scene_candidate_uid?: string | null;
    identity_method?: string | null;
    identity_strength?: "heuristic" | "exact" | null;
    status: string;
    error: string | null;
  }>;
}

export interface LabelClass {
  id: number;
  name: string;
  color: string;
  hotkey: number | null;
}

export interface RotatedBBox {
  cx: number;
  cy: number;
  width: number;
  height: number;
  angle_deg: number;
}

export interface AnnotationGeometryPayload {
  geometry_type?: GeometryType;
  bbox?: [number, number, number, number];
  rotated_bbox?: RotatedBBox | null;
  polygon_scene_px?: [number, number][] | null;
  front_edge_scene_px?: [number, number][] | null;
  front_vector_scene_px?: [number, number] | null;
  orientation_angle_deg?: number | null;
}

export interface AnnotationAttributes {
  attribute_version: number;
  attribute_status: "computed" | "partial" | "unavailable" | "error" | string;
  attribute_computed_at: string;
  attribute_errors: string[];
  attribute_warnings: string[];
  attribute_input_hash?: string;
  identification?: Record<string, unknown>;
  geometry?: Record<string, unknown>;
  orientation?: Record<string, unknown>;
  geospatial?: Record<string, unknown>;
  scene_metadata?: Record<string, unknown>;
}

export interface Annotation {
  id: string;
  source_annotation_id?: string;
  scene_id?: string;
  class_id: number;
  geometry_type: GeometryType;
  bbox: [number, number, number, number];
  rotated_bbox?: RotatedBBox | null;
  polygon_scene_px?: [number, number][] | null;
  front_edge_scene_px?: [number, number][] | null;
  front_vector_scene_px?: [number, number] | null;
  orientation_angle_deg?: number | null;
  is_negative: boolean;
  annotation_source?: string;
  annotator_email?: string | null;
  created_by?: string | null;
  updated_by?: string | null;
  source_model?: string | null;
  import_id?: string | null;
  source_package_id?: string | null;
  attributes?: AnnotationAttributes | null;
  created_at: string;
  updated_at?: string;
}

export interface TilingConfig {
  tile_size: number;
  buffer: number;
}

export interface TilingPreview {
  num_cols: number;
  num_rows: number;
  total_tiles: number;
  tile_size: number;
  buffer: number;
  stride: number;
  grid_lines_x: number[];
  grid_lines_y: number[];
  tile_rects: [number, number, number, number][];
}

export interface TileInfo {
  tile_id?: string;
  grid_cell_id?: string;
  scene_id?: string;
  filename: string;
  col: number;
  row: number;
  x0: number;
  y0: number;
  x1?: number;
  y1?: number;
  geometry_px?: [number, number][];
  geometry_scene_px?: [number, number][];
  geometry_wgs84?: [number, number][];
  review_status?: "unreviewed" | "reviewed";
  exclude_from_dataset?: boolean;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  reviewed: boolean;
  excluded: boolean;
}

export interface TileCatalogManifest {
  catalog_id: string;
  status: string;
  semantic_role?: "review_grid" | string;
  materialization: "metadata_only" | string;
  migrated_from_legacy: boolean;
  scene_count: number;
  tile_count: number;
  annotation_link_count: number;
  tiling_config: TilingConfig;
}

export interface TileProgress {
  total_all: number;
  total: number;
  reviewed: number;
  positive: number;
  negative: number;
  excluded: number;
}

export interface DatasetConfig {
  train_ratio: number;
  val_ratio: number;
  test_ratio: number;
  min_box_fraction: number;
  negative_ratio: number;
  tile_selection?: 'reviewed_sampled' | 'all_reviewed' | 'all';
  split_mode: SplitMode;
  split_seed: number;
  block_size_tiles: number;
  preprocessing_profile_id?: string | null;
  target_gsd_m?: number | null;
  scene_ids?: string[];
  class_ids?: number[];
  annotator_emails?: string[];
  annotation_sources?: string[];
  gsd_min_m?: number | null;
  gsd_max_m?: number | null;
  incidence_min_deg?: number | null;
  incidence_max_deg?: number | null;
  acquired_after?: string | null;
  acquired_before?: string | null;
  class_merges?: ClassMerge[];
  /** P1.5: świadoma zgoda na dataset ze scen o różnej semantyce wartości pikseli
   *  (np. nieskalibrowana amplituda ICEYE razem ze skalibrowanym sigma0 Capelli).
   *  Bez niej backend odrzuca taki build — mieszanie nie może zajść niejawnie. */
  allow_mixed_radiometry?: boolean;
}

export interface ClassMerge {
  /** Klasa wiodąca (zachowana). */
  into: number;
  /** Klasy wcielane w klasę wiodącą. */
  members: number[];
}

export interface MetadataNumericRange {
  min: number;
  max: number;
  count: number;
}

export interface MetadataDateRange {
  min: string;
  max: string;
  count: number;
}

export interface DatasetFilterOptions {
  catalog_id: string | null;
  scenes: Array<{
    scene_id: string;
    filename: string;
    tile_count: number;
    annotation_link_count: number;
    gsd_m?: number | null;
    incidence_angle_deg?: number | null;
    acquisition_datetime_utc?: string | null;
  }>;
  classes: Array<{ class_id: number; name: string; annotation_link_count: number }>;
  authors: Array<{ email: string; annotation_link_count: number }>;
  annotation_sources: Array<{ source: string; annotation_link_count: number }>;
  metadata_ranges?: {
    gsd_m?: MetadataNumericRange | null;
    incidence_angle_deg?: MetadataNumericRange | null;
    acquisition_datetime_utc?: MetadataDateRange | null;
  };
}

export interface PreprocessingProfile {
  profile_id: string;
  name: string;
  description: string;
  profile_version: number;
  processor_version: number;
  profile_hash: string;
  modality: ProjectModality;
  input_quantity: string;
  radiometric_transform: "none" | "linear" | "log1p" | "db10";
  percentile_stretch: boolean;
  percentile_scope: "tile";
  stretch_low: number;
  stretch_high: number;
  gamma: number;
  brightness: number;
  contrast: number;
  rgb_conversion: "native_rgb" | "first_three_bands" | "grayscale_rgb";
  output_dtype: "uint8";
  builtin: boolean;
}

export interface PreprocessingProfilesFile {
  schema_name: string;
  schema_version: number;
  updated_at: string;
  profiles: PreprocessingProfile[];
}

export interface DatasetStats {
  run_id?: string | null;
  run_dir?: string;
  storage_mode?: string;
  is_latest?: boolean;
  preprocessing_profile_id?: string | null;
  preprocessing_profile_hash?: string | null;
  total_tiles: number;
  positive_tiles: number;
  negative_tiles: number;
  total_annotations: number;
  largest_class_count?: number;
  per_class: Record<string, number>;
  per_split: Record<string, {
    images: number;
    positive_tiles?: number;
    negative_tiles?: number;
    annotations: number;
    per_class: Record<string, number>;
  }>;
  dataset_dir?: string;
  generated_at?: string;
  split_mode?: DatasetConfig["split_mode"];
  split_seed?: number;
  tile_summary?: {
    total_all: number;
    active: number;
    reviewed?: number;
    reviewed_empty?: number;
    unreviewed?: number;
    used: number;
    positive: number;
    negative: number;
    excluded: number;
    omitted: number;
  };
  class_stats?: Array<{
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
  }>;
  split_stats?: Array<{
    split: string;
    images: number;
    positive_tiles: number;
    negative_tiles: number;
    annotations: number;
    per_class: Record<string, number>;
  }>;
  scene_stats?: Array<{
    scene_id: string;
    filename: string;
    status: string;
    tiles: number;
    reviewed_tiles?: number;
    used_tiles: number;
    excluded_tiles: number;
    annotations: number;
    classes: string[];
    without_annotations: boolean;
    without_dataset_classes: boolean;
  }>;
  unused_classes?: string[];
  source_only_classes?: string[];
  scenes_without_annotations?: string[];
  scenes_without_dataset_classes?: string[];
  geometry_stats?: GeometryStats | null;
  co_occurrence?: CoOccurrence | null;
  acquisition_stats?: AcquisitionStats | null;
  validation_report?: DatasetValidationReport;
}

export interface MetaDist {
  overall: Record<string, number>;
  train: Record<string, number>;
  val: Record<string, number>;
  test: Record<string, number>;
}

export interface SplitHistogram {
  bins: number[];
  counts: number[];
  train: number[];
  val: number[];
  test: number[];
}

export interface AcquisitionStats {
  weighting: string;
  total_tiles: number;
  gsd_hist?: SplitHistogram | null;
  incidence_hist?: SplitHistogram | null;
  sensor: MetaDist;
  modality: MetaDist;
  season: MetaDist;
  missing: Record<string, number>;
}

export interface CoOccurrence {
  classes: string[];
  counts: number[][];
  tiles_total: number;
  truncated: boolean;
}

export interface GeometryHistogram {
  bins: number[];
  counts: number[];
}

export interface GeometryClassStats {
  class_id: number;
  name: string;
  count: number;
  w_px_median: number;
  h_px_median: number;
  area_px_median: number;
  area_px_p90: number;
  area_frac_median: number;
  aspect_median: number;
  size_small: number;
  size_medium: number;
  size_large: number;
  w_m_median?: number | null;
  h_m_median?: number | null;
  area_m2_median?: number | null;
  obb_count: number;
  angle_median?: number | null;
  short_side_median?: number | null;
  long_side_median?: number | null;
  near_square_count: number;
}

export interface GeometryStats {
  mode: string; // bbox | rotated_bbox
  tile_size: number;
  size_thresholds_px: Record<string, number>;
  gsd_coverage_frac: number;
  total_annotations: number;
  per_class: GeometryClassStats[];
  area_px_hist: GeometryHistogram;
  aspect_hist: GeometryHistogram;
  angle_hist?: GeometryHistogram | null;
}

export interface DatasetValidationIssue {
  code: string;
  severity: "info" | "warning" | "error" | string;
  message: string;
  count?: number;
  values?: string[];
}

export interface DatasetValidationReport {
  schema_name: string;
  schema_version: number;
  status: "ok" | "warning" | "error" | string;
  split_mode: SplitMode;
  split_seed: number;
  split_counts: Record<"train" | "val" | "test", number>;
  empty_splits: string[];
  cross_split_source_annotation_count: number;
  cross_split_source_annotation_ids: string[];
  issues: DatasetValidationIssue[];
  errors: string[];
  warnings: string[];
  checks: Record<string, string>;
}

export type DatasetAuditCheckStatus =
  | "passed"
  | "warning"
  | "error"
  | "info"
  | "not_available"
  | string;

export interface DatasetAuditCheck {
  check_id: string;
  category: string;
  status: DatasetAuditCheckStatus;
  title: string;
  message: string;
  count?: number | null;
  details: string[];
  data?: Record<string, unknown>;
}

export interface DatasetAuditSummary {
  status: "ok" | "warning" | "error" | string;
  readiness: "ready" | "ready_with_warnings" | "not_ready" | string;
  quality_score: number;
  total_checks: number;
  passed: number;
  warnings: number;
  errors: number;
  info: number;
  not_available: number;
}

export interface DatasetAuditReport {
  schema_name: string;
  schema_version: number;
  generated_at: string;
  app_version?: string;
  project_id: string;
  run_id?: string | null;
  status: DatasetAuditSummary["status"];
  readiness: DatasetAuditSummary["readiness"];
  quality_score: number;
  summary: DatasetAuditSummary;
  dataset_summary: Record<string, number | string | null>;
  annotation_summary: Record<string, number | Record<string, number>>;
  metadata_summary: Record<string, number>;
  sidecar_summary: {
    generated: boolean;
    complete: boolean;
    files: Record<string, boolean>;
    validation: Record<string, unknown>;
  };
  distributions: {
    by_sensor: Array<Record<string, string | number>>;
    by_modality: Array<Record<string, string | number>>;
    by_georeferencing: Array<Record<string, string | number>>;
    by_preprocessing: Array<Record<string, string | number | null>>;
    by_split: Array<Record<string, unknown>>;
    by_scene: Array<Record<string, unknown>>;
    locations: Array<Record<string, unknown>>;
  };
  checks: DatasetAuditCheck[];
  issues: DatasetAuditCheck[];
  recommendations: string[];
}

export interface DatasetRunSummary {
  run_id: string;
  created_at: string;
  completed_at?: string | null;
  status: "complete" | "failed" | string;
  storage_mode: string;
  input_hash?: string | null;
  split_mode?: SplitMode;
  split_seed?: number;
  tile_size?: number;
  preprocessing_profile_id?: string | null;
  preprocessing_profile_hash?: string | null;
  validation_status?: string | null;
  validation_warning_count?: number;
  validation_error_count?: number;
  audit_status?: string;
  audit_readiness?: string;
  audit_quality_score?: number;
  total_tiles: number;
  positive_tiles: number;
  negative_tiles: number;
  total_annotations: number;
  /** Cykl życia wersji datasetu; brak pola = wersja robocza (projekty sprzed M1). */
  publication_status?: DatasetPublicationStatus;
  publication_label?: string | null;
  published_at?: string | null;
  published_by?: string | null;
}

export type DatasetPublicationStatus = "draft" | "published" | "deprecated";

export interface DatasetPublication {
  run_id: string;
  status: DatasetPublicationStatus;
  label?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
  /** Przebiegi treningowe zależne od tej wersji (puste do czasu M3). */
  dependent_training_runs?: string[];
}

export interface DatasetRunsIndex {
  schema_name: string;
  schema_version: number;
  project_id: string;
  latest_run_id: string | null;
  updated_at: string;
  runs: DatasetRunSummary[];
}

export interface DatasetRunManifest {
  schema_name: string;
  schema_version: number;
  app_version?: string;
  run_id: string;
  project_id: string;
  project_name?: string;
  created_at: string;
  completed_at?: string;
  status: string;
  storage_mode: string;
  input_hash: string;
  tile_catalog_id?: string;
  tile_catalog?: Record<string, unknown>;
  selection?: Record<string, unknown>;
  project_profile: ProjectProfile;
  tiling_config: TilingConfig;
  dataset_config: DatasetConfig;
  preprocessing_profile?: PreprocessingProfile;
  preprocessing_warnings?: string[];
  audit_summary?: DatasetAuditSummary;
  classes: LabelClass[];
  scenes: Array<Record<string, unknown>>;
  statistics: DatasetStats;
  artifacts?: Record<string, string>;
  error?: string;
}

// --- Display Enhancement ---

export interface DisplayParams {
  brightness: number;
  contrast: number;
  gamma: number;
  stretch_low: number;
  stretch_high: number;
}

export const DEFAULT_DISPLAY_PARAMS: DisplayParams = {
  brightness: 1.0,
  contrast: 1.0,
  gamma: 1.0,
  stretch_low: 0,
  stretch_high: 100,
};

/**
 * Domyślne wyświetlanie SAR na mapie (DESIGN_DECISIONS.md, display-stretch B, C). Górny próg 99,8%,
 * bo w SAR górne 2% sceny to właśnie cele — nastawa 2–98% wypalała je do bieli. Jasność,
 * kontrast i gamma neutralne: dawne 0,75–0,85 i 1,1–1,2 dobrano, gdy rozciąganie było
 * praktycznie wyłączone, a po jego włączeniu kumulowały się z nim. Dotyczy tylko mapy —
 * profil przetwarzania datasetu pozostaje bez zmian.
 */
export const SAR_DISPLAY_PARAMS: DisplayParams = {
  brightness: 1.0,
  contrast: 1.0,
  gamma: 1.0,
  stretch_low: 1,
  stretch_high: 99.8,
};

/** Jedna krzywa histogramu, binowana we wspólnym zakresie osi sceny. */
export interface SceneHistogramCurve {
  index?: number; // numer pasma; brak dla krzywej luminancji
  bins: number[];
  min: number;
  max: number;
  mean: number;
  std: number;
  percentiles: Record<string, number>;
  sample_count: number;
}

export interface SceneHistogram {
  /**
   * Krzywa zlana ze wszystkich pasm. To z niej renderer bierze progi rozciągnięcia,
   * więc pozostaje źródłem liczb dla uchwytów i etykiet — niezależnie od tego, którą
   * krzywą pokazuje wykres.
   */
  bins: number[];
  bin_low: number;
  bin_high: number;
  min: number;
  max: number;
  mean: number;
  std: number;
  percentiles: Record<string, number>; // p0, p1, p2, p5, ... p100
  sample_count: number;
  band_count?: number;
  bands?: SceneHistogramCurve[];
  /** Jasność postrzegana (Rec. 601); `null` dla scen jedno- i dwupasmowych. */
  luminance?: SceneHistogramCurve | null;
  /** `display` = wartości są już po przekształceniu `display_mode` (SAR: log). */
  domain?: "source" | "display";
  display_mode?: string | null;
}

/**
 * Statystyki bieżącego widoku mapy — źródło progów zakresu rozciągnięcia „Widok”
 * (DESIGN_DECISIONS.md, display-stretch D). Kształt histogramu sceny plus opis próbki.
 */
export interface SceneViewStats extends SceneHistogram {
  scope: "view";
  /** Okno po przycięciu do sceny i przyciągnięciu do siatki: [x0, y0, x1, y1]. */
  window: [number, number, number, number] | null;
  valid_fraction: number;
  /** Za mało danych w widoku — frontend zostaje przy poprzednich progach. */
  insufficient: boolean;
  reason?: string | null;
  display_revision?: string;
}

/** Zakres statystyk rozciągnięcia: cała scena albo bieżący widok (jak QGIS). */
export type StretchScope = "scene" | "view";

/** Jawne okno rozciągnięcia zakresu „Widok” — w dziedzinie wyświetlania sceny. */
export interface ViewStretchWindow {
  min: number;
  max: number;
}

// --- YOLO Prediction ---

export interface Prediction {
  id: string;
  class_id: number;
  class_name: string;
  bbox: [number, number, number, number];
  confidence: number;
  source_model: string;
  status: "pending" | "accepted" | "rejected";
  created_at: string;
  // AI-assist oriented-box proposals (SAM click in rotated_bbox mode)
  geometry_type?: GeometryType;
  rotated_bbox?: RotatedBBox | null;
  /**
   * Kształt wierny mapie, liczony przy tworzeniu propozycji. Ma pierwszeństwo przed
   * `rotated_bbox`, który opisuje prostokąt W PIKSELACH i na scenach niekonforemnych
   * rysuje się jako równoległobok.
   */
  polygon_scene_px?: [number, number][] | null;
  front_vector_scene_px?: [number, number] | null;
  needs_front_direction?: boolean;
}

export interface PredictionConfig {
  model_path: string;
  conf: number;
  iou: number;
  tile_size: number;
  buffer: number;
  device: string;
  img_size: number | null;
  batch_size: "auto" | number;
  prefetch_batches: number;
  merge_method: "nms";
  progress_interval_ms: number;
  preprocess_mode: "auto" | "linear" | "log";
  stretch_low: number;
  stretch_high: number;
  gamma: number;
  brightness: number;
  contrast: number;
  sam_checkpoint: string | null;
  sam_models_dir: string | null;
  sar_exemplar_backbone: string | null;
  exemplar_engine: "template" | "dino";
  dino_models_dir: string | null;
  dino_checkpoint: string | null;
}

export interface ModelInfo {
  name: string;
  path: string;
  size: number;
  modified: string;
}

export interface SamModelInfo extends ModelInfo {
  family: "sam1" | "sam2" | "sam3" | null;
  supported: boolean;
  reason: string | null;
}
