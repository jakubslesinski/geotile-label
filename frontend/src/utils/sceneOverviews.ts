export interface SceneOverviewState {
  overview_status?: string | null;
  overview_type?: string | null;
  /**
   * Kontrakt R0.4 — rozdzielenie „podgląd gotowy" od „dostępne pełne 1×".
   *
   * Do tej pory `overview_status: "ready"` znaczyło oba naraz i dla dużych JP2 było
   * obietnicą, która łamała się dopiero przy dojechaniu zoomem: piramida od 2× była
   * gotowa, ale pełnej rozdzielczości nie dało się obsłużyć. Gdy backend przyśle nowe
   * pola, to one rozstrzygają; bez nich zachowanie jest dokładnie takie jak wcześniej.
   */
  preview_status?: string | null;
  fullres_derivative_status?: string | null;
  available_native_zoom?: number | null;
  source_max_zoom?: number | null;
}

export type SceneResolutionPhase =
  | "native_preview_loading"
  | "native_preview_ready"
  | "high_resolution_queued"
  | "high_resolution_building"
  | "high_resolution_validating"
  | "high_resolution_ready"
  | "preview_ready_fullres_missing"
  | "native_ready"
  | "display_pyramid_pending"
  | "error";

/**
 * Large JPEG2000 products expose native resolution levels that are good enough
 * for the initial fitted view, even though GeoTile Label still prepares a faster
 * project-local pyramid for fine random reads.  Let that first view finish before
 * starting the competing full-raster background read.
 */
export function deferDisplayOverviewUntilRasterReady(scene: SceneOverviewState): boolean {
  const preview = String(scene.preview_status ?? scene.overview_status ?? "pending");
  return preview === "pending" && String(scene.overview_type || "") === "native_multiresolution";
}

export function sceneResolutionPhase(
  scene: SceneOverviewState,
  rasterReady: boolean,
): SceneResolutionPhase {
  const status = String(scene.preview_status ?? scene.overview_status ?? "pending");
  const nativeMultiresolution = String(scene.overview_type || "") === "native_multiresolution";
  const fullres = scene.fullres_derivative_status
    ? String(scene.fullres_derivative_status)
    : null;

  if (status === "error" || fullres === "error") return "error";

  // Stan derywatu pełnej rozdzielczości ma pierwszeństwo, gdy coś właśnie trwa —
  // to on opisuje pracę, na którą użytkownik czeka.
  if (fullres === "queued") return "high_resolution_queued";
  if (fullres === "building") return "high_resolution_building";
  if (fullres === "validating") return "high_resolution_validating";

  if (status === "queued") return "high_resolution_queued";
  if (status === "building") return "high_resolution_building";

  if (status === "ready") {
    // `ready` na podglądzie NIE wystarcza, żeby obiecać pełną rozdzielczość.
    if (fullres && fullres !== "ready") return "preview_ready_fullres_missing";
    return "high_resolution_ready";
  }
  if (status === "native") {
    if (fullres && fullres !== "ready") return "preview_ready_fullres_missing";
    return "native_ready";
  }
  if (nativeMultiresolution) {
    return rasterReady ? "native_preview_ready" : "native_preview_loading";
  }
  return "display_pyramid_pending";
}
