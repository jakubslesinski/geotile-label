# Export

The training format is produced **automatically when a dataset is generated**, according
to the [geometry declared in the project](../projects/utworz-projekt.md). Export serves to
convert to secondary formats and to assemble a portable package - it is no longer a
separate step that training depends on.

!!! info "The primary format is automatic"

    The folder of a generated version is trainable straight away, in the task matching the
    project: a project with axis-aligned boxes gets YOLO labels (`labels/`), and a project
    with oriented boxes additionally gets YOLO OBB labels (`labels_obb/`) and
    `data_obb.yaml`. There is no longer any need to "export YOLO by hand" for training to
    start.

## The action bar on a dataset version

Instead of a separate tab, the actions sit next to the **selected dataset version** (the
Dataset module):

- **Convert to COCO / Pascal VOC** - secondary formats for interoperability with other
  tools, written next to the generated dataset, on demand;
- **Export ZIP package** - choosing the formats starts a durable `dataset_export` job; the
  finished artifact is downloaded from the
  [Background jobs](../reference/zadania-w-tle.md) panel;
- **Open folder** - the folder of the selected dataset version.

## Which format

| Format | When |
| --- | --- |
| **YOLO** | detection training with axis-aligned boxes (automatic for a `bbox` project) |
| **YOLO OBB** | training on oriented boxes (automatic for a `rotated_bbox` project) |
| **COCO** | tools that expect a single JSON file (conversion on demand) |
| **Pascal VOC** | older pipelines, one XML per image (conversion on demand) |
| **GeoParquet** | checking in GIS tools (inside the ZIP package) |

## The contents of the ZIP package

The training formats, manifests, statistics, the audit report, SHA-256 checksums, and
metadata in CSV, Parquet and GeoParquet.

## The export job and cache

Export runs in the background, shows progress and can be cancelled. The package becomes
available for download only after the write has completed atomically. Repeating the export
of the same dataset version, the same set of formats and the same exporter version reuses
the ready artifact from the cache. The order in which the formats were selected does not
create a different copy.

Changing the dataset, the set of formats or the exporter version creates a new artifact.
The cache is reproducible and may be removed when artifacts are cleaned up.

!!! warning "Source scenes do not go into the package"

    The ZIP contains **tiles**, not the complete imagery. Whoever receives the dataset does
    not get the source material - if it is meant to be otherwise, it has to be handed over
    separately.

## Checksums and provenance

Every package carries SHA-256 checksums and a manifest describing the provenance: which
dataset version it came from, what the configuration and the split were. That makes it
possible to tie a package back to a project even years later.

## Checking before handing over

Before sending a package off for training it is worth checking the
[audit](../datasets/audyt.md) and the [statistics](../datasets/statystyki.md), and looking
at the GEO annotations in [QGIS](geoparquet-qgis.md).

## A dataset package versus an annotation package

Two different things. A dataset package is **the end product** - the input to training and
archival material. Exchanging work within a team is what an annotation package is for. See
[Exchange package types](../reference/rodzaje-paczek.md).
