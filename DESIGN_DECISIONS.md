# Design decisions

Comments in this repository cite decisions by a short stage id, for example
`(DESIGN_DECISIONS.md, scene-import P0.5)`. This file is what those citations point at.

The staged engineering plans in which these decisions were originally argued out are
internal working documents and are not part of this repository. What a reader of the code
needs is here instead: the decision, and the reason it was taken. Several entries record a
deliberate *exclusion* - something the software intentionally does not do. Those are the
ones worth reading before calling a missing behavior a gap: code alone never shows that an
absence was a choice.

Stage ids are kept as they appear in the code so that a citation and this file can be
matched by grep.

---

## Scene import from provider packages - `scene-import`

Cited by the resolver, the package model, the import report and their tests.

### Standing decisions

- **Sources stay read-only.** The application never modifies, moves or deletes a delivery.
  Virtual rasters, COGs, pansharpened products, pyramids and caches are written into the
  project directory or a controlled application cache. Unpacking an archive into the source
  tree is not something the application does on its own.
- **JSON stays the source of truth.** No embedded SQL database. Scan indexes, delivery
  snapshots, validation reports, product-detection caches and asset-relation indexes are
  allowed, but each is versioned and rebuildable: it carries a schema version, a resolver
  version and a fingerprint of its input, and is written atomically.
- **A package is a graph, not a folder.** The domain model is
  `Source → Delivery → Acquisition → Product → assets`, plus derived working variants. One
  folder may hold several acquisitions and products; one acquisition may hold several
  polarisations, bands or raster parts. A parent directory therefore never automatically
  means "one scene".
- **Declared and detected provider are separate fields.** The manifest keeps what the user
  declared, what detection found, the detected sensor, a confidence and the evidence behind
  it. A mismatch is surfaced, never silently overridden by the user's choice.
- **Source asset, labeling view and analytic view are distinct.** The original provider
  raster, the raster used for display and labeling, the variant used for datasets and
  inference, and a display-only overview are four different things. Pansharpening or SAR
  calibration produce a working variant with an explicit processing manifest - never
  something presented as the provider's own product.
- **Correctness before automation.** When the acquisition, the product, the set of raster
  parts or a sidecar cannot be determined unambiguously, the result is `decision_required`
  or `invalid`. It is never `ready` with a guess behind it.

### Stage register

| Stage | What it covers |
| --- | --- |
| `B0` | Repeatable baseline and a contract corpus of synthetic package fixtures, so resolver behavior is pinned by tests rather than by one machine's data. |
| `B0b` | Read-only audit harness that inspects real deliveries without importing them. |
| `P0.1` | Versioned delivery–acquisition–product–asset graph contract. |
| `P0.2` | Asset role classification and strict binding of metadata to a product. |
| `P0.3` | ICEYE resolver: name grammar and sidecar binding. |
| `P0.4` | Capella resolver: name grammar and acquisition grouping. |
| `P0.5` | Airbus DIMAP (Pléiades / Pléiades Neo) resolver: name grammar, band order, acquisition grouping. |
| `P0.6` | WorldView resolver: name grammar, components and delivery tile manifests. |
| `P0.7` | Pixel spacing and GSD derived from the full geotransform, not from a single axis. |
| `P0.8` | Transactional cataloguing, recorded user decisions and relink. |
| `P1.1` | Identity refresh and cache invalidation scoped to the selected scene. |
| `P1.2` | Asynchronous source scan with a JSON cache. |
| `P1.3` / `P1.3a` | Explicit archive handling: archives are indexed without being unpacked, and partial extractions are reported rather than treated as complete. |
| `P1.4` / `P1.4b` | PAN/MUL mosaic geometry checks and the pansharpening pipeline as a working variant. |
| `P1.5` | Pixel-value semantics (radiometry) and provenance of working variants. |
| `P1.6` | Import telemetry and a durable import report. |
| `P2.1` | Hierarchical import preview. |
| `P2.2` | Migration of older project manifests onto the v2 graph contract. |
| `P2.3` | Documentation and the installer gate for the feature. |

## Full-resolution JP2 display - `jp2-fullres`

Cited by the display contract and the full-resolution derivative builder, and by the JP2
benchmarks.

- **1× (full resolution) is a functional requirement**, not an optimization. A scene that
  can only be shown decimated is not considered usable for labeling.
- **`R0.1` - the coordinate frame and the servable zoom level are two different things.**
  Windows are always computed against the reference zoom; the highest level that can
  currently be served is tracked separately. Collapsing them into one counter shifts read
  windows and, with them, annotations.
- **`R0.2` - classify the active decode path, not the source format.** What matters is how
  the pixels are actually being read right now, not the file extension.
- **`R1.0` - only scenes that need it get a full-resolution derivative.** Qualification is
  restricted to problematic `direct` generic JP2 scenes. Virtual mosaics have their own
  preparation path, and GeoTIFF, NITF, SAR and small JP2 scenes with usable native levels
  are excluded: for them the derivative would cost roughly twenty minutes and several
  gigabytes and buy nothing.
- **`R1.2` - the build is a background job of its own type**, not a variant of scene
  preparation, so it can be started, retried and recovered independently.
- **The Grok/COG experiment is closed.** Stages `E0`–`E5` compared the OpenJPEG and Grok
  JP2 drivers against a COG-based path on frozen workloads; stage `E4` covered COG
  construction and validation (including the multiband layout gap), `E5` the runtime gate.
  The decision (`D1`) was to build COGs and to reject Grok as a direct runtime driver, so
  the lab environment that hosted the comparison is not part of this repository. The
  measurement scripts that produce reusable numbers stayed in `backend/benchmarks/`.

## Tile serving - `tile-serving`

Cited by the scene tile router, the raster resolver and the working-view builder.

| Stage | Decision |
| --- | --- |
| `P0` | Baseline measurement harness for scene tile serving, so later stages are compared against numbers rather than impressions. |
| `P1` | Build display pyramids for `direct` products that ship without overviews, and confine the tile-serving redirect to serving - identity and provenance are not affected by it. |
| `P2` | Move tile reads off the event loop into a worker pool. |
| `P3` | Cache the per-scene render context instead of rebuilding it per tile. |
| `P4` | Evict the on-disk tile cache under a size budget. |

## Tonal stretch and adaptive display - `display-stretch`

Cited by the scene tile router, the image utilities, the scene histogram, the view-statistics
service, the display panel and the map canvas.

### Standing decisions

- **One quantization.** The stretch window is folded into the conversion to 8 bits, and
  brightness, contrast and gamma are applied in floating point in the same pass. Stretching an
  image already quantized through the wide SAR base window left about 35 grey levels per tile,
  with only every fifth level in use.
- **One set of thresholds per image on screen.** All tiles of a view share one window. A
  stretch computed per tile was measured and rejected: 19–41 grey-level steps at tile borders
  against ~7 inside a tile, the same backscatter value rendered anywhere between 12 and 253
  depending on the tile, and nodata pulling edge tiles towards white.
- **Display never changes data.** The stretch affects neither annotations, nor dataset
  generation (which keeps its own per-tile normalization), nor the input of AI tools.
- **Adaptivity is explicit.** The statistics extent is shown in the panel and on the map; the
  default is the whole scene.
- **A tile is a function of its URL.** View thresholds travel in the tile URL as numbers; the
  backend keeps no per-view state.

### Stage register

| Stage | Decision |
| --- | --- |
| `A` | Scene percentiles resolve to a window in the display domain (after the scene's display transform, e.g. `log1p` for SAR), rendered by `render_display_window` in a single quantization. Rendered pixels changed, so the geo tile cache moved to `v9` and tile URLs carry the render version `_rv` (`_r` belongs to tile retries). |
| `B` | The scene histogram carries dense tail percentiles (`p0.1` … `p99.9`, histogram v3). Without them a 99.8% threshold was interpolated towards the scene maximum, a single outlier. |
| `C` | SAR scenes open on the map with a 1–99.8% stretch and neutral brightness, contrast and gamma. These are map-only defaults: the preprocessing profile that drives dataset generation is unchanged. The 2–98% EO convention clipped the brightest 2% of a SAR scene, which is where the targets are. |
| `D` | Statistics extent "Scene / View", the counterpart of QGIS *Statistics extent* (whole raster / current canvas / updated canvas). `view-stats` samples the visible window from the display pyramid without nodata; the frontend resolves one window for all tiles with a 300 ms debounce, 2% hysteresis and a minimum width of 25% of the scene window, and can freeze it. Tiles with an explicit window are not written to the disk cache. |
| `F0` | `backend/benchmarks/display_stretch_audit.py` measures these gates on a real scene without writing to the project. |

Known limitation: zoomed out to the whole scene, view thresholds differ from scene thresholds by
a few percent of the window width, because the statistics are read from a coarser, averaged
pyramid level - the same pixels the map shows at that zoom.

## Application performance audit - `performance-audit`

Cited by the desktop shell, the annotation layer, the dataset builder and the project view.

| Stage | Decision |
| --- | --- |
| `E3` | The backend process is bound to a Windows Job Object so it cannot outlive the application, and orphans from earlier runs are swept at startup. |
| `E4` | Annotation and tile layers are updated through their existing handles instead of being cleared and rebuilt; rebuilding the geometry on every change was the cost. |
| `E5` | Leaflet renders vector layers on canvas rather than SVG. |
| `E7` | Tile writes in the dataset build run through a worker pool. |
| `E8a` | The project view's critical path is loaded separately from its aggregates, so the view does not wait for the slowest query. |
| `E8b` | The scenes index is used only when it is not older than every scene file it summarizes. |
| `E8c` | The annotation summary is cached. |

Two measurement rules from that audit still apply to any new work in this area: results are
reported as measured numbers rather than estimates, and a result measured on one machine is
not assumed to transfer to another.

## Earlier performance roadmap - `performance-roadmap`

- `B0` - the benchmark suite in `backend/benchmarks/` exists because of this stage: it
  measures without writing into a project.
- `P0.3` - a round of router-level work that deliberately left the annotations router
  untouched; the comment there records that it was skipped, not overlooked.
- The attribute engine recomputes derived annotation attributes behind a gate rather than on
  every edit; the trigger was labeling-view responsiveness.

## Dataset intelligence - `dataset-intelligence`

| Stage | Decision |
| --- | --- |
| `DI0` | Measure first whether a class-separability signal is trustworthy before building product features on it. |
| `DI1` | The QA layer over a built dataset. |
| `DI-F` | Browsing the content of a published dataset, including the deep link from a tile back to its scene. |

Image embeddings and the CLIP text encoder are shared with the SAM3 assist path rather than
maintained twice.

## Dataset package layout - `dataset-package`

The exported ZIP carries derived tiles in the selected formats plus canonical geospatial
provenance: run manifest, scene manifests, split manifest, statistics, audit and context
reports, GeoParquet annotations in WGS84 and in native coordinates, checksums and an export
log. **Source scenes are never copied into the package.** The package is meant to be
reproducible from the project, not to be a copy of the imagery.

## Team review workflow - `team-review`

Staged `T0`–`T6`. Two decisions matter when reading the code:

- **Importing a review never changes an annotation.** The review loop carries verdicts and
  comments; it does not rewrite geometry or classes on import.
- **Correction and deletion must propagate.** The difference between a one-way handoff and a
  working review loop is exactly that both propagate; the smoke tests assert it explicitly.

The review model was deliberately kept narrow - several richer per-annotation review
features were considered and excluded - so a missing review feature here is more likely a
recorded scope decision than an oversight.

## NITF airborne scenes - `nitf`

Version 1 scope is deliberately narrow: single-segment panchromatic UInt16 scenes labeled in
native sensor geometry. This is why the packed backend runtime must include the GDAL NITF
driver, and why the geo model has to support a non-affine (TPS) path alongside the affine
one.

## Scene working files - `working-files`

The scene working-storage figure counts the working rasters and derivatives that belong to
the scene. Tile caches are not counted: they are reproducible and evictable, so including
them would report a number the user cannot act on.

## Training workbench and the CUDA pack - `cuda-pack`

The GPU training dependencies are distributed as a separate pack rather than being bundled
into the installer, because the NSIS/WiX installer toolchain does not handle a payload of
around three gigabytes.

## Architecture diagrams - `architecture-diagrams`

`docs/architecture/architecture-model.json` is the single source for the architecture
figures. The `.d2` sources and the interactive `explorer.html` are generated from it by
`scripts/generate-architecture-d2.py` and `scripts/generate-architecture-explorer.py` - edit
the model, never the generated files. `scripts/check-architecture-model.py` fails when the
generated artifacts drift from the model. It also compares the model against the SVG figures
exported for the article; that check is skipped automatically when the article repository is
not present next to this one.

## Contextual help texts - `ui-popovers`

Longer explanations in the interface go through `InfoPopover`, keyed like any other
translated string, with Polish and English entries kept together. Information icons are not
added next to obvious buttons or standard table columns - an icon on everything trains
people to ignore all of them.

## Canonical geospatial dataset - `geospatial-dataset`

The full scenes, their metadata and their annotations are the canonical data. Tiles and the
YOLO, COCO and Pascal VOC exports are derived products that can be regenerated with
different settings. Nothing in the pipeline may treat a derived artifact as the record of
truth.

## Scene import resolver, stages R0–R5 - `scene-import-resolver`

The first generation of the import resolver, superseded by the provider-package work above
(`scene-import`). `README_dev.md` describes the state as implemented today; the stage
history itself is not reproduced here.
