# Data locations

Where the application keeps projects, models, logs and the runtime environment. The paths
come in useful for backups, for moving work to another computer, and for reporting bugs.

## The overriding rule

Source files - scenes, vendor metadata, class files - **stay where you pointed at them**.
The application opens them read-only and does not copy them into its own directory.
Moving or disconnecting the drive with the scenes does not destroy the project: the
annotations are stored separately, and the source can be pointed at again through
[Relink a source](../projects/relinkuj-zrodlo.md).

## Application data

Everything below is under `%APPDATA%\GeoTileLabel`.

| Path | Contents |
| --- | --- |
| `data\projects\` | projects stored in the default location |
| `data\projects_index.json` | the list of projects together with the actual path of each |
| `data\models\base\` | the base weights of the architectures used for training |
| `data\models\sam\` | the SAM models for the click-to-box tool |
| `logs\` | `app.log`, `backend.stdout.log`, `backend.stderr.log` |
| `runtime\` | the unpacked backend environment; with the GPU pack installed there are two side by side |

!!! tip "A project does not have to live in `data\projects`"

    When a project is created, any parent folder can be chosen - on a working or a shared
    drive, for example. `projects_index.json` then remembers the real path - which is why
    it, and not the contents of `data\projects`, is the list of projects.

The `runtime\` directory can be deleted without losing data - it is rebuilt on the next
start. **Settings → Diagnostics → Clear runtime/cache** does exactly that.

## The project directory

Wherever it is stored, a project always has the same structure.

| Path | Contents |
| --- | --- |
| `project.json` | the project profile: modality, georeferencing, geometry type, role |
| `classes.json` | the class definitions together with the shortcut numbers |
| `scene_sources.json` | the connected scene sources and their identifiers |
| `scene_import_config.json` | the import mode and the automatic full-resolution COG setting |
| `scenes_index.json` | the scene catalog together with the statuses |
| `scenes\<id>\annotations.json` | **the annotations of the scene - the canonical data** |
| `scenes\<id>\scene.json` | the scene summary for the catalog and the UI |
| `scenes\<id>\scene_manifest.json` | the full metadata: CRS, transform, lineage |
| `scenes\<id>\assistance\` | the output of the AI tools awaiting a decision |
| `derived_scenes\<id>\<variant>\` | VRT/COG working views, where a product needs them |
| `tile_catalogs\` | tile catalogs: geometry and review state, without images |
| `dataset_runs\` | dataset versions together with their manifests |
| `training_runs\` | training runs, metrics and model checkpoints |
| `jobs\<job_id>\` | durable jobs: `job.json`, `state.json`, `events.jsonl`, `performance.json`, `artifacts.json` |
| `artifacts\dataset_exports\` | the cache of finished ZIP export packages; reproducible |
| `artifacts\training_datasets\` | the shared training staging cache; reproducible and dependent on the release configuration |
| `import_reports\` | the reports of annotation package imports |
| `*_config.json` | the tiling, dataset, prediction and preprocessing profile settings |

The variant directory `derived_scenes\<id>\<variant>\` may contain `overview.vrt`,
`overview.vrt.ovr` and `overview.profile.json`. These are reproducible display products.
An external `scene.tif.ovr` sits next to the source file, that is, **outside the project
folder**.

For a large generic JP2 the variant directory may also contain `fullres.state.json` and
the active `fullres.tif`. The `fullres.candidate.tif` file, the raw decode and the helper
VRT are transient build artifacts: the renderer never selects them, and they are cleaned
up after publication or after a failure. All of these files are reproducible; they contain
no annotations.

The space taken by the whole of `derived_scenes\` is reported by the **Scene working
files** panel on the project dashboard - broken down by category, with a list of the
largest scenes and a button that opens the folder in the file manager. The panel is
read-only and counts nothing outside `derived_scenes\`, so it is not a measure of total
project size. The panel is described in
[Derived products](../input-data/produkty-pochodne.en.md#scene-working-files).

A training run may contain `training_performance.json` next to `metrics.json`,
`results.csv`, the confusion matrix and the weights. The performance report does not
replace the quality metrics.

!!! info "What is the record of truth and what is a derived product"

    Canonical are **the full scenes, their metadata and `annotations.json`**. Tile
    catalogs, dataset runs and exports are derived products - they can be deleted and
    generated again from the same annotations. That principle is described in
    [Tile catalog](../datasets/katalog-kafelkow.md).

Versioned JSON indexes and aggregates are derived as well. Deleting them may make the
first read slower, but the application rebuilds them from the canonical files. Do not edit
the indexes by hand and do not treat them as a backup of the annotations.

## What to take when moving work

**The whole project folder** and access to the same source files are enough. The
`runtime\` directory rebuilds itself, and `data\models\` is filled when the training pack
is installed.

The ready-made procedure is described in
[Project backup and transfer](../projects/backup.md).

!!! warning "Moving the project folder alone does not move the scenes"

    The scenes live outside the project. On the new computer the same medium has to be
    connected, or a copy of the sources pointed at, and then the sources relinked. The
    annotations survive, because they are stored in the project folder.
