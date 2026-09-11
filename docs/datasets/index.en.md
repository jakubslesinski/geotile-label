# Datasets

A dataset is a **versioned derived product** of the canonical scenes and annotations.
The same project can generate many versions with different parameters - and all of them
stay.

## The path of the data

```text
annotations on the scene → the record of truth
   ↓  review grid        → what has been reviewed
   ↓  tile catalog       → candidates, no images
   ↓  dataset version    → images, split, statistics
   ↓  publication        → material for training
```

Every step is reproducible from the one before it. Deleting a dataset version does not
touch the annotations; deleting the catalog does not touch the grid.

## Tasks

<div class="grid cards" markdown>

-   :material-grid: **I am preparing the candidates**

    ---

    What the tile catalog holds and when it has to be rebuilt.

    [Tile catalog](katalog-kafelkow.md)

-   :material-cog-play: **I am generating a dataset**

    ---

    The split, negative examples and source filters.

    [Build a dataset](zbuduj-dataset.md)

-   :material-chart-bar: **I am checking what came out**

    ---

    Class distributions, scene usage, gaps.

    [Statistics](statystyki.md)

-   :material-image-search: **I am looking at what went into training**

    ---

    Tiles with boxes and classes, straight into the editor.

    [Contents](zawartosc.md)

-   :material-clipboard-check: **I am checking readiness**

    ---

    Spatial leakage, split integrity, review coverage.

    [Audit](audyt.md)

-   :material-publish: **I am choosing a version to train on**

    ---

    Publication states and the deletion lock.

    [Publish a version](publikowanie.md)

-   :material-scatter-plot: **I am examining the structure of the set**

    ---

    Class similarity, suspicious labels, duplicates and train/val leakage - before
    training.

    [Dataset analysis](analiza.md)

</div>

## Before you generate

The points that most often decide the quality of the result:

1. **Is the review far enough along?** Negative examples come only from reviewed cells.
2. **Does the split fit the data?** Drawing tiles at random from satellite data usually
   produces spatial leakage.
3. **Are the classes agreed?** Unused classes and a divergence in names surface in the
   statistics and in the audit.
4. **Do the filters cover what you intend?** Nothing selected means everything is used.

!!! info "A dataset version is immutable"

    A generated version is not edited - a new one is created instead. That is what makes
    a training result always traceable to one specific data configuration, and two
    versions comparable.

## Related

- [Review grid](../annotation/siatka-przegladu.md) - the input to the catalog
- [Export](../export/index.md) - the dataset package formats
- [Start training](../training/uruchom-trening.md) - what happens next with a published
  version
