# Derived products and working views

A **working view** is the scene representation used for annotation. A tiled product
may be assembled as a virtual VRT without copying pixels. Pleiades Neo `MS-FS RGB +
PAN` and WorldView `MUL + PAN` require a separate, reproducible pansharpened file.
Source pixels remain unchanged.

Working views are stored under `derived_scenes\` and may be rebuilt from the same
source and parameters.

### Scene working files

The collapsible **Scene working files** panel on the project dashboard reports how much
space these files take. It counts the `derived_scenes\` folder **only**, so its total
matches the size of the folder opened by **Open working files folder**. Source files,
annotations and tile catalogs are excluded - this is **not** the total project size.

Statistics are read only after the panel is expanded, so the scan never delays the
dashboard and never competes for disk with COG or overview builds. The result is a
snapshot: the scan time is shown below the list, and **Refresh** performs a new read.

Space is broken down into categories:

| Category | What it covers |
| --- | --- |
| Full-resolution COGs | `fullres/fullres.tif` - the 1x derivative for large JP2 |
| Project overviews | `.ovr` sidecars next to working views |
| Materialized working views | e.g. `rgb_pansharpened.cog.tif` |
| Virtual views | lightweight composition descriptions such as `overview.vrt` |
| Manifests and profiles | `processing_manifest.json`, `overview.profile.json`, logs |
| Incomplete build files | leftovers from an interrupted job: candidate, RAW, `.partial` |

!!! info "The panel never deletes anything"

    Every panel operation is a read. The application offers no button to clear these
    files: they are rebuildable, but rebuilding is often expensive - a full-resolution
    COG for a large JP2 scene costs tens of minutes and several GB. If you decide to
    remove something, do it deliberately in the file manager after opening the folder
    from the panel.

    A non-zero **Incomplete build files** entry means a job was interrupted. The next
    build of that scene cleans these files up on its own.

## Display overviews are not working views

An overview only improves rendering at small scales. It does not change the scene
pixel grid, selected product, or annotation geometry. GeoTile Label distinguishes:

- a source sidecar such as `scene.tif.ovr`;
- internal GeoTIFF overviews;
- native JP2 multiresolution levels;
- the reproducible project overview `overview.vrt.ovr`.

An external sidecar can be prepared in [QGIS](piramidy-qgis.md). Move it with the
raster when possible; losing it does not remove annotations.
Large JP2 files may receive a project overview even when native levels exist. This is
a decoding optimization; it does not create a new scene variant or change geometry.
The first project `.ovr` level is copied from the matching native JPEG 2000 level;
only the remaining levels are generated from that smaller raster. This avoids another
full-resolution decode. Base factor `2`, `4`, or `8` is selected from available memory
and overview concurrency, and can be overridden with
`GEOTILE_JP2_OVERVIEW_BASE_FACTOR`.

### Large generic JP2: 2× preview and full-resolution 1×

Problematic large generic JP2 scenes deliberately use two preparation stages:

1. import creates `overview.vrt` and the standalone GeoTIFF `overview.vrt.ovr`; it
   provides a fast preview down to 2× and remains a durable fallback;
2. after the first viewport has loaded, the application may start a durable job that
   builds a full-resolution COG. Until it completes, the source JP2 is not used for
   interactive 1× rendering.

The COG is first written as `fullres.candidate.tif`. GeoTile Label validates dimensions,
band types and semantics, georeferencing, nodata, alpha/mask behavior, internal
overviews, edges, strip boundaries, and sample pixels. Only a valid candidate is
atomically published as `fullres.tif`. A TIFF file without a matching active publication
record is never selected by the renderer.

A failed build is not retried merely because the scene is opened again. The `.ovr`
preview remains usable, while explicit retry and cancellation are available from the
annotation view. Source replacement, relinking, a variant change, or a profile change
invalidates the COG without removing the source, project, or annotations.

A single-band build may use whole-image decoding only on a host with ample free RAM;
GeoTile Label keeps at least 8 GiB for the system and enforces a 16 GiB child-process
limit. Multi-band scenes default to safer strip decoding with an approximately 4.5 GiB
limit. A memory overshoot retries only the current range at a smaller height instead of
discarding rows that were already written. These limits affect COG preparation time,
not `.ovr` preview availability.

#### Enable or disable automatic COG preparation

The project dashboard exposes **Automatic full-resolution COG** in the **Scene
sources** card. This preference is stored per project and defaults to **OFF** -
building a COG costs tens of minutes and several GB per scene, so it never starts
without your decision.

- **ON** - after the first complete `.ovr` viewport loads, GeoTile Label may
  automatically queue a 1× COG for an eligible generic JP2 scene;
- **OFF** - the scene stays on its `.ovr` preview and no new COG job starts
  automatically. The 1× level remains unavailable until a valid COG exists.

Switching the preference **OFF** does not cancel a job that is already running and
does not disable an existing validated COG. Cancel an active job from
[Background jobs](../reference/zadania-w-tle.md), opened from the left application
sidebar.

Import mode and automatic COG preparation are independent. Import mode controls
`overview.vrt` + `overview.vrt.ovr`; the COG can start only after the scene is opened.
Administrators can disable automatic starts across all projects with
`GEOTILE_AUTO_FULLRES_COG_V2=0`. The project switch cannot override this emergency
runtime restriction.

## Before preparation starts

Free space is checked before every heavy preparation. When there is not enough, the job
does not start - instead of failing mid-write with a partial file on disk. The estimated
output size is shown in the import preview for products that need preparation.

Preparation can be **cancelled while running**. After cancelling, neither a partial file
nor a product remains in the variant folder: a cancelled result is never published.

Next to the output, `processing_manifest.json` records how the scene was produced: the
identity of every input (size, modification time, signature), the algorithm version and
its parameters, the GDAL version, and the semantics of the output. Two runs over the
same files produce the same input fingerprint and the same geometry.

!!! info "A pansharpened product is not a physical quantity"

    Pansharpening mixes bands, so the radiometric calibration of the source no longer
    applies. The manifest states this explicitly: the output is described as an image for
    visual interpretation, not as radiance or sigma0.

## What pixel values mean

GeoTile Label **never rescales source radiometry**. The scene manifest records what the
values are according to vendor metadata - for example uncalibrated amplitude with a
calibration factor alongside (ICEYE), or calibrated `sigma0` (Capella).

!!! warning "A dataset will not silently mix calibrations"

    If scenes with conflicting calibration - calibrated next to uncalibrated - would end
    up in one dataset, generation is **stopped** and the scenes are listed. The same
    number would mean different things across them. Mixing remains possible, but only as
    an explicit decision that is recorded in the dataset manifest.

    Scenes whose vendor declares no radiometry are marked "unknown" and do not block
    generation: missing information is not the same as conflicting information.

Display contrast stretch **does not affect training data**. Dataset tiles are produced
from the preprocessing profile, not from scene display settings.

## RGB band selection

For a local raster with more than three bands, select the `R`, `G`, and `B` indices in
the annotation view. GeoTile Label creates a VRT band-selection view. Without an
explicit selection, bands 1–3 are used.

## Scene variant and locking

Each working view has a variant identifying its product and preparation parameters.
After the first annotation, reviewed cell, or dataset use, the application blocks a
variant change because annotations are tied to that exact pixel grid. Create a
separate scene when another product is required. Rebuilding a missing file for the
same variant is allowed.

## Reprojection is not performed automatically

Annotations are not transferred between different working grids. If a package from the
same source scene, but prepared differently, turns up in a collective project, the
import reports it instead of forcing a match.

!!! tip "Agree on how scenes are prepared across the team"

    The simplest way to avoid this problem is to settle together who prepares the scenes
    and how, before the labeling starts. A divergence surfaces only at import, that is,
    after the work has been done.

## Related

- [Default vendor products](../reference/produkty-dostawcow.md)
- [Scene statuses](../reference/statusy-scen.md)
- [Data locations](../reference/lokalizacje-danych.md)
