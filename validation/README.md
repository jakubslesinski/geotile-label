# validation/ - evidence that the workflow closes

One subfolder, one piece of evidence. Each one is a **reproducible script**, not a
screenshot. This is supplementary material for the article; the text itself carries only a
compact table of results.

Every piece of evidence declares three things: the **claim** it supports, the **substrate**
it runs on, and its **reproducibility tier**. The tiers are **public** (a benchmark a
reviewer can rerun), **reported-only** (licensed material such as the Capella SAR scene, so
only the report travels) and **mixed**. NITF and the TPS transform are **outside** this
suite, which covers the affine pipeline.

## Index

| Folder | Claim | Substrate / tier | What it measures |
| --- | --- | --- | --- |
| `v01_compat_matrix/` | S1 | xView3 + FAIR1M + Capella + provider packages, mixed | matrix of provider x format x CRS x **dtype** x **delivery shape** (coverage and status; Float16 called out explicitly) |
| `v02_geometry_roundtrip/` | C3 | FAIR1M / xView3 (+ SAR fixture), mixed | residual of pixel to world to pixel (affine); the OBB travels as `polygon_scene_px` without skew |
| `v03_run_reproducibility/` | C2b, C2c | benchmark, public | same seed and config produce an identical manifest and tile list |
| `v04_split_leakage/` | C1 (mechanism) | xView3 / DOTA, public | overlapping and adjacent tiles across splits: random vs spatial |
| `v05_id_provenance_chain/` | C2a | benchmark + SAR fixture, mixed | integrity of the identifier chain from scene to annotation to tile to export |
| `v06_training_example/` | C4 | FAIR1M / DOTA, public | OBB training: loss and mAP plus a confusion matrix, on a real benchmark |
| `v07_performance/` | S3 | xView3 XXL Float16, public + a 2.63 Gpx JP2 scene, reported-only | time and memory for an XXL scene (ingest, build, export) **and** a single read from the source JP2 against the published COG |
| `v08_offline_egress/` | C5 | any run, public | no outbound connections when the network is cut |
| `v09_class_similarity/` | S2 | FAIR1M (37 subclasses), public | highly similar class pairs, **correlated with the confusion matrix from v06** |
| `v10_import_fidelity/` | C2a | DOTA / DIOR-R / FAIR1M, public | **lossless import**: benchmark OBB to canonical form to GeoParquet to re-parse equals the original |
| `v11_geo_accuracy_external/` | C3 | xView3, public | **geographic accuracy against ground truth**: the application's pixel-to-world against the published `lat/lon`, residual in metres |
| `v12_metadata_stratified_eval/` | C6 | xView3, public | mAP and recall by metadata bin (`distance_from_shore`, incidence angle) |
| `v13_split_leakage_effect/` | C1 (effect) | FAIR1M / DOTA, public | **the mAP gap** between random and spatial splits (a single number; ablations belong to a separate article) |
| `v14_display_asset_invariance/` | C3 | JP2 fixture, public + a real 2.63 Gpx JP2 scene, reported-only | **the read window and the geometry do not depend on the active display asset** (2x pyramid against the published 1x COG) |

## Conventions

- Every folder has a `run.py` whose **docstring is its contract** (Claim / Substrate / Tier /
  Method / Inputs / Outputs / Pass criterion) and a `main()` returning a `ValidationResult`
  with `substrate=[...]` and `tier=...` set.
- The result is written to `results/result.json` (the contract lives in `_common/result.py`)
  along with any artifacts (CSV, PNG) in the same `results/`.
- Every result carries, in `environment`, an **attribution to the state of the product
  code**: `app_rev` (the git revision, or `nogit` when the application directory is not a
  repository), `app_version` (from `frontend/src-tauri/tauri.conf.json`) and
  `app_fingerprint`, a sha256 over the contents of `backend/**/*.py` formatted as
  `<16 hex>:<file count>`. The fingerprint is computed from content, not modification times,
  so the same code on another machine yields the same value. One fingerprint shared across
  all results confirms that the whole run describes a single state of the code.
- The scripts reach the **application backend** by importing from `backend/` in this
  repository, or through the public API. They never duplicate product logic.
- Running the backend directly requires `PROJ_LIB` and `GDAL_DATA` to point at the backend
  environment (see the application's `README_dev.md`), otherwise a PROJ installed with
  PostgreSQL shadows the one GDAL expects. `bootstrap()` also sets `GDAL_DRIVER_PATH` to
  `<prefix>/Library/lib/gdalplugins`: without it rasterio sees 190 drivers and **no JP2**,
  even though `gdal_JP2OpenJPEG.dll` is present. Evidence that touches JP2 calls
  `appenv.require_jp2_driver()` and ends as `todo` with an explicit reason when the driver is
  missing, because a silently absent driver would mean measuring a different read path than
  the one being claimed.
- **Substrates.** Public benchmarks (`DIOR-R`, `DOTA`, `FAIR1M`, `xView3`) are read from
  `data/datasets/` by default; `importers/README.md` documents the flags that point
  elsewhere. The reported-only substrate is a real Capella SAR scene with 205 oriented boxes,
  kept outside the repository: it carries the end-to-end narrative, not reproducible
  evidence.
- **The xView3 Float16 trap.** rasterio does not map `GDT_Float16` (`KeyError: 15`), so the
  SAR VV and VH bands have to be converted to Float32 on ingest. That conversion is part of
  the contract of `v01` and `v07`.
- Importing the benchmarks into project form is described in `importers/README.md`.

## Running

Evidence that reaches into the backend (geo, rasterio) must run under the **backend
environment interpreter**, otherwise `rasterio` is missing and the script returns `todo` with
that reason. The bootstrap in `_common/appenv.py` sets `GDAL_DATA` and `PROJ_LIB` from the
environment prefix and adds `backend/` and `importers/` to `sys.path` by itself.

```powershell
$PY = "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe"
& $PY validation/v11_geo_accuracy_external/run.py
& $PY validation/v10_import_fidelity/run.py
& $PY validation/v03_run_reproducibility/run.py
& $PY validation/run_all.py     # everything, aggregating results/ into figures and a summary
```

Optional overrides: `BENCHMARK_PROJECTS_ROOT` (default `data/benchmark_projects/` in this
repository), `GEOTILE_BACKEND` (default `backend/` in this repository), `V10_SCENES_PER_DATASET`,
`V03_SCENES`, `V03_N_RUNS`, `PROVIDER_PATHS_JSON` (default `scripts/provider-import-paths.json`).

**The provider packages in `v01`** are optional and reported-only. Their paths are read only
from `PROVIDER_PATHS_JSON`, a local gitignored file, because provider data must not reach the
repository or CI. Its absence is the normal state for a reviewer: the reported-only rows
disappear, the public rows stay complete and the evidence **still passes**. No path, file name
or source name from that configuration reaches `result.json` or the CSV; a row is labelled by
the provider and an ordinal alone.

**The JP2/COG arm of `v07`** is optional and reported-only. It looks for a published 1x
derivative in the application data directory (`V07_JP2_DATA_DIR`, default
`%APPDATA%/GeoTileLabel/data`) and only **reads** it. It does not build one, because preparing
that scene takes about 19 minutes, and it does not call `SceneRasterResolver.resolve()`,
because that can write `scene.json` along the way and this is real user data. Without the
derivative the arm reports `na` with a reason and the evidence passes on the public arm alone.
The absolute values of this arm depend on the state of the filesystem cache (between roughly
60x and 700x has been observed), so the conclusion is an order of magnitude, not a number.

**Arm B of `v14`** (`V14_REAL_DATA_DIR`, same default directory) needs a scene that has **both**
a project pyramid and a published COG. Publishing a derivative does not remove
`overview.vrt.ovr`, so on a real project this is the normal state. Its scope is narrower than
arm A: it compares **assets** (transform, CRS, dimensions, zoom contract) rather than tile
server responses, because seeing the "2x preview" state through the server would mean
withdrawing the derivative, that is, modifying user data. The end-to-end case is carried by
arm A.

The status of a single piece of evidence is `pass`, `fail` or `todo` (a skeleton starts as
`todo`).

### Implemented evidence

| Evidence | Status | What it shows (summary of the result) |
| --- | --- | --- |
| `v01_compat_matrix` | **implemented** | a **13-row** matrix (provider x format x CRS x dtype x **delivery shape**). Five public rows: SAR and EO, GeoTIFF/PNG/JPEG, UTM/EPSG:4326/NO_GEO, **xView3 Float16 handled** (the original raises `KeyError: 15`, the working view is float32). Eight reported-only rows: real ICEYE, Capella, Airbus PHR-PNEO, WorldView and generic packages, scanned read-only with `resolvers.scan_source()` and never imported, including **uint16 JP2** and a **MUL+PAN** product. Ingest succeeded for 7 of 7 available rasters. Gaps are explicit: render is n/a for VRT, ingest is n/a for a raster outside the station **and when the resolver leaves the product choice to the user** (5 rows). Shapes never observed (`multipart_mosaic`, `archive`) are named in the metrics. |
| `v02_geometry_roundtrip` | **implemented** | affine round trip pixel to WGS84 to pixel (`SceneGeoModel`): p95 about 1e-9 px; OBB shape fidelity (SAR fixture and FAIR1M): minimum IoU 1.0, vertex displacement 0 px, so no skew. |
| `v03_run_reproducibility` | **implemented** | the same seed and config give an identical `input_hash` and split (random and spatial) across N runs; a different seed gives a different split; the filter snapshot is present. Real functions: `compute_grid`, `stable_tile_id`, `_assign_splits`, `dataset_input_hash`. |
| `v04_split_leakage` | **implemented** | xView3, overlapping tiles: training tiles overlapping val or test are far more numerous for random than for spatial, and spatial with a buffer reaches 0. Real functions: `_assign_splits`, `_apply_spatial_buffer`, `dataset_audit._tile_center_3857`. |
| `v05_id_provenance_chain` | **implemented** | FAIR1M and the SAR fixture: 100% of annotations reconstructed in the export, none lost, orphaned, misassigned or attached to the wrong scene. Real propagation through `annotation_propagator.propagate_annotations_with_attributes` and `compute_tile_attributes`. |
| `v06_training_example` | **implemented** | consumes a finished training run from the application (`training_runs/<run>`). DOTA, yolo26n-obb, 50 epochs: best mAP50 0.635, mAP50-95 0.477, precision 0.805, recall 0.585, 18 classes; loss and mAP curves plus the confusion matrix that feeds v09. |
| `v07_performance` | **implemented** | **Public arm:** xView3 30402x26560 float32 (807 Mpx) into 1221 tiles: ingest about 40 ms, about 0.12 s per tile, build about 150 s; **peak RSS about 90 MB**, so with windowed GeoTIFF reads memory does not scale with scene size. **JP2/COG arm (reported-only):** a real 60476x43476 uint16 scene (2.63 Gpx), a 512 px window with a fresh handle per tile: about 4.3 s per tile from the source JP2 against about 11 ms from the published COG, **two orders of magnitude**; the derivative weighs **1.56x** the source. Memory is bounded on BOTH paths (RSS delta of single megabytes). |
| `v08_offline_egress` | **implemented** | a local FAIR1M pipeline (ingest, geo, propagate, render, export): **zero egress attempts** under a socket probe, and the full run passes with egress blocked. |
| `v09_class_similarity` | **implemented** | FAIR1M: DINO embeddings (`build_object_index`, `class_similarity`) against the confusion matrix of the FAIR1M OBB run. 2502 objects across 33 classes give a **Spearman correlation of 0.63** between similarity and confusion; the top pairs are both plausible and genuinely confused (Small Car and Van, 4591 confusions; Dry and Liquid Cargo Ship; A321 and Boeing 737). |
| `v10_import_fidelity` | **implemented** | FAIR1M, DOTA and DIOR-R: maximum vertex deviation about 7e-4 px, classes and counts preserved exactly, convex IoU about 1.0. The lossless verdict rests on vertex deviation, which is robust to degenerate and non-convex source quadrilaterals. |
| `v11_geo_accuracy_external` | **implemented** | xView3: pixel to WGS84 (`SceneGeoModel.affine`) against the published `detect_lat/lon`, residual in metres in the scene CRS (UTM). p95 far below one GSD pixel. |
| `v12_metadata_stratified_eval` | **implemented** | xView3: model inference on held-out validation tiles and recall by `distance_from_shore` bin (7078 objects): shore 0.20, nearshore 0.25, offshore 0.25, a spread of 5.4 percentage points, so recall is lower near the shore. An illustration of C6 on small data (50 validation scenes). |
| `v13_split_leakage_effect` | **implemented** | consumes two OBB runs of the same project (random_tile against spatial_block). FAIR1M, yolo26n-obb, 50 epochs, seed 42: mAP50 0.330 for random against 0.268 for spatial, a **gap of +0.062** (mAP50-95 +0.059), so the random split inflates the result. Together with v04 it closes C1. |
| `v14_display_asset_invariance` | **implemented** | **Arm A (public):** a 6000x4000 JP2 fixture in two display states built with product functions (`build_direct_overviews`, then `run_scene_fullres_derivative_job`). The scene is a grid of 512 px blocks with constant codes, so a read from inside a block says directly which part of the source reached the tile. 120 shared tiles, 120 distinct code layouts, **zero window mismatches**; WGS84 for 7 pixels differs by **0.0 m**; `source_max_zoom` unchanged, `available_native_zoom` 4 to 5. The 1x asset is rejected in state 1 and served in state 2. **Arm B (reported-only):** a real 2.63 Gpx scene with the project pyramid and the published COG present at the same time: transform, CRS and dimensions **identical bit for bit** across source, preview and COG, pixel round trip 7.5e-09 px, reference level 8 unchanged with available zoom 7 to 8. |

**All 14 pieces of evidence are implemented and passing (14/14).**

> **Why `v14` does not measure object position.** The first version computed a marker
> centroid and found a 0.054 px difference between the states. The cause was not geometric:
> state 1 reads a pyramid copied from the native JP2 level (wavelet), while state 2 decimates
> the COG by box averaging. Two different pyramids over the same raster have different kernel
> phase, so at the edge of the marker state 1 returns 223 and 32 where state 2 returns a hard
> 255 and 0. **Any** position estimator computed from brightness inherits that difference and
> ends up measuring edge radiometry instead of the coordinate system. The interior of a
> constant block is invariant under any normalised kernel, so block codes give a comparison
> that needs no tolerance. Edge phase is still reported (`pyramid_phase_px_max`), but it does
> not gate the result.

**Note on the Windows runtime.** Evidence that reaches into the backend re-spawns the process
with `KMP_DUPLICATE_LIB_OK=TRUE`, `MKL_THREADING_LAYER=SEQUENTIAL` and
`OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` (see `_common/appenv.py`); without that, a numpy
matrix multiply crashes with 0xc06d007f once GDAL and torch are both loaded. `run_all.py` runs
each piece of evidence as an **isolated subprocess**, because `DATA_DIR` and `MODELS_ROOT` are
cached at import time and a shared process would mix configuration between them.
