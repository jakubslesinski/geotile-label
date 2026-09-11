# Tile catalog

The tile catalog is **the set of candidates** for training images: the geometry of the
subdivision, the links to annotations and the review state. It comes out of the review
grid and is the input to building a dataset.

!!! info "The catalog contains no images"

    Creating a catalog **does not create thousands of files**. What is stored are
    identifiers, geometry and relations. The preview of a single tile is generated on
    demand, and the training images are produced only when
    [a dataset is built](zbuduj-dataset.md) - and only for the tiles selected for that
    particular version.

## What it holds

- the identifiers and the geometry of the tiles;
- the relations to the source annotations;
- the class, the author and the source of an annotation;
- for `GEO` scenes - the geometry in the native system and in `WGS84`.

It is **raw and undivided** - there is no split into training, validation and test sets
in it. The split is created when a specific dataset version is built.

## Cell states

| State | Meaning | Does it reach the dataset |
| --- | --- | --- |
| **Unchecked** | no confirmed review | no |
| **Reviewed** | the fragment was deliberately looked at | yes - with annotations or as a negative example |
| **Excluded** | a technical exception | no |

!!! warning "Negative examples come only from reviewed cells"

    A tile without annotations reaches the dataset as a negative example **only** when
    somebody confirmed they looked at it. Unreviewed areas are not drawn from - because
    the absence of annotations could simply mean nobody went there.

Excluding is a technical exception - a black background, corrupted data, artifacts - not
an ordinary stage in the progress of the work.

## When the catalog has to be built again

**No rebuild needed:** changing the split strategy, the random seed or the preprocessing
profile. Those parameters act on a finished catalog, so further dataset versions can use
it without repeating the work.

**A new catalog version needed:** changing the annotation geometry, the tile size, the
overlap or the propagation threshold.

!!! tip "Experiment with the split, not with the catalog"

    Comparing split strategies and seeds is cheap - it is the same catalog. Changing the
    tile size is expensive, because it invalidates the existing links.

## The tile cache

The tile catalog is metadata-only: it stores the geometry and the review state, not
persistent tile images. Grid previews render on demand and need no maintenance.

The application offers no cache-clearing button in the interface. The space taken by the
files prepared for displaying scenes is shown by the
**[Scene working files](../input-data/produkty-pochodne.md)** panel on the project
dashboard - also for reference only.

## Related

- [Review grid](../annotation/siatka-przegladu.md) - where the cell states come from
- [Build a dataset](zbuduj-dataset.md) - what happens next
