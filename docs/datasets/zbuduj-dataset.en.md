# Build a dataset

**Goal.** Generate a versioned set of training images with a split into sets.

**When.** Once labeling is far enough along that a model is worth checking.

**Requirements.** A [tile catalog](katalog-kafelkow.md) that has been created.

## Steps

1. Open the **Dataset** module.
2. Choose the preprocessing profile.
3. Check the **tile grid** (size and overlap) - it is **inherited from the
   [Review grid](../annotation/siatka-przegladu.md)** and shown for reference only.
4. Choose the split strategy and the set proportions.
5. Choose the **Dataset content** (and in *Reviewed with limit* mode set the **Negative
   Ratio**).
6. Optionally set the **Common GSD** (resampling to one ground resolution).
7. Optionally narrow the **Dataset source filters**.
8. Optionally narrow the **Metadata filters** (`GSD` range, SAR incidence angle,
   acquisition date).
9. Optionally **merge similar classes** (Class merges).
10. Choose **Generate Dataset**.

The build starts a durable `dataset_build` job. You can move to another view; progress,
cancellation and retry are available under
[Background jobs](../reference/zadania-w-tle.md).

!!! info "The version appears only once it is finished"

    Images and labels are written into a temporary directory. The version pointer is
    published atomically only after all the stages and the audit have completed. An
    interrupted `.partial` directory is not a trainable dataset and can safely be cleaned
    up, or used by a controlled resume.

![The Dataset module with the split settings and source filters](../assets/images/dataset-version-configuration.png)
*The configuration before generating a dataset version.*

## The tile grid

The tile size and overlap are shown at the top of the configuration **for reference
only** - they come from the [Review grid](../annotation/siatka-przegladu.md). That is
deliberate: a dataset is tiled exactly the way you review it (the same tile catalog), so
the rule **what you review is what you train on** applies.

!!! info "Why the grid is not changed here"

    Changing the tile size would rebuild the tile catalog and **reset the review state**
    (reviewed / excluded / empty cells), because the identity of a tile depends on its
    size. That is why the grid parameters are changed in the **Review grid**, and the
    build merely inherits them. If you need a different object scale without touching the
    review, use the [Common GSD](#common-gsd-optional) (re-tiling from the source).

## Checkpoint

A **dataset version** appears on the list, with its own configuration and statistics.
The previous versions stay untouched.

## What reaches the model

Whatever the source format (`GeoTIFF`, `COG`, `JP2`, `NITF`, vendor packages), every
tile of a dataset is written as an **8-bit, 3-channel image**. The **preprocessing
profile** decides how the source is brought to that form - above all how the values (for
example `UInt16`, panchromatic) are stretched to 8 bits.

That determines what the model **sees** and what it does not:

- the **bit depth** of the source is not carried over - what counts is the stretch from
  the profile, not the raw range of values;
- **three channels** reach the tile; for multi-band scenes, which bands are rendered as
  `RGB` is chosen in the labeling window (see
  [Derived products](../input-data/produkty-pochodne.md));
- the **ground resolution** (`GSD`) is not equalized between scenes by default - the
  optional [Common GSD](#common-gsd-optional) field below is what does that.

!!! info "Display adjustments are not preprocessing"

    The brightness/contrast sliders and the histogram stretch in the labeling window
    change **the preview only**. It is the preprocessing profile - not the display
    settings - that determines the pixels written into the dataset tiles.

## Split strategies

| Strategy | When |
| --- | --- |
| Random tile | homogeneous data, a quick check |
| Scene split | different acquisitions or areas |
| Image block split | `NO GEO` data |
| Spatial block split | `GEO` data |
| Class balanced spatial | rare classes in `GEO` data |

!!! warning "Random tiles usually inflate the result"

    Neighboring tiles overlap and show the same ground. With a random draw some of them
    land in the training set and some in the test set - so the model sees the test during
    learning and the result comes out better than it really is. That is **spatial
    leakage**.

    For satellite data use a scene or block split. Leave the random draw for a quick check
    of whether the pipeline works at all.

Once a version is built, the [audit](audyt.md) checks whether the split introduced
spatial leakage or tile adjacency between the sets, and whether every acquisition
configuration from validation and test also occurs in the training set.

## Dataset content

The **Dataset content** field decides which tiles reach the version. It is a single
choice - three levels of inclusiveness arranged as a ladder:

| Mode | Tiles with annotations | Reviewed empty | Unreviewed | Excluded |
| --- | --- | --- | --- | --- |
| **Reviewed with limit** (the default) | ✅ | a sample per the slider | ❌ | ❌ |
| **All reviewed (no excluded)** | ✅ | all | ❌ | ❌ |
| **All tiles (with unreviewed and excluded)** | ✅ | all | ✅ | ✅ |

In **Reviewed with limit** mode the number of empty tiles is governed by the **Negative
Ratio** slider - a percentage *relative to the number of tiles with annotations*, not a
percentage of all the tiles (100% ≈ one empty tile per positive one). The slider works
in that mode only.

Negative examples teach the model what **not** to mark. Without them a model reports
false detections on the background more often.

!!! warning "“All tiles” mode pulls in unreviewed and excluded ones"

    Unreviewed and excluded tiles then reach the dataset as background. If there are
    unlabeled objects in the areas nobody looked at, you are teaching the model **false
    negatives**. That mode serves full coverage or prediction, **not** training.

!!! info "Empty tiles are not drawn from unreviewed areas"

    In the reviewed modes the relationship is simple: the more fragments confirmed as
    empty, the larger the supply of negative material. If you expected more empty tiles,
    check the progress of the [review grid](../annotation/siatka-przegladu.md).

## Source filters

The **Dataset source filters** section narrows a version to selected scenes, classes,
authors or annotation sources. Nothing selected means everything is used.

Every version stores **its own snapshot of the filters** and the list of the tiles
actually used. That way further versions can use the same catalog with an entirely
different configuration and remain comparable.

!!! tip "The author filter for checking team consistency"

    Building a version from one analyst's work and comparing it with the collective one
    can reveal a divergence in class interpretation - faster than going through the
    annotations by hand.

## Metadata filters

The **Metadata filters** section narrows a version by **the properties of the scenes**,
rather than by who labeled what. Every filter is a range - an empty field means no limit
on that side:

- **GSD range (m)** - only scenes with a ground resolution within the given interval;
- **SAR incidence angle (°)** - only scenes with an incidence angle in the interval (a
  radar field);
- **Acquisition date (from / to)** - only scenes recorded within that time window.

Typical uses: a **temporal hold-out** (train on older scenes, evaluate on newer ones),
narrowing to one radar geometry regime, or screening out imagery with an extreme `GSD`.

!!! warning "A scene without that metadata is excluded"

    When a filter is active and a scene does not carry that information, it is **skipped**
    (it cannot be verified). The incidence angle is radar metadata, so its filter will
    reject optical scenes. The "X of Y scenes" counter above the section shows live how
    many scenes pass the set of filters; the "Available: …" hints appear once the tile
    catalog has been rebuilt with this version of the application.

Like the source filters, these go into the **version snapshot** - the selection of
scenes is recorded in the manifest and reproducible.

## Class merges

When the [dataset analysis](analiza.md) shows that two classes are visually
indistinguishable, the **Class merges** section lets you **combine them into one** for
the purposes of a given dataset version - without touching the source annotations.

Within a merge group you choose the **primary class** (its name and identifier are kept)
and tick the classes **merged into** it. During generation the annotations of the merged
classes take the primary class, so the model learns them as one. A class can belong to
only one group.

!!! info "Non-destructive and reversible"

    A merge concerns **only the version being generated** - the source annotations stay
    untouched. Changing the merges is a configuration change, so it produces a **new
    version** of the dataset; that makes it easy to build a merged variant and compare it
    with the unmerged one (analysis, training, Results).

!!! note "A merged class stays on the list, but empty"

    For consistency with the training format, the model class list stays complete. A
    merged class no longer has instances of its own (its counts go to the primary one), so
    it appears with a zero in the version statistics - that is expected, and confirms the
    merge worked.

## Common GSD (optional)

Scenes of different spatial resolution (`GSD`, meters per pixel) give objects at
different pixel scales - and a model learns on tiles of a fixed size. The **Common GSD**
field lets you **resample every tile to one ground resolution**, so that objects have a
consistent scale regardless of the scene. Empty = native resolution (the default).

How it works:

- the output tile still has the size set in pixels; what changes is the **ground area**
  read from the scene (`window = size · scene GSD / target GSD`), and it is resampled;
- in this mode the dataset is **tiled anew from the source annotations**, so the
  **per-tile review flags do not apply** (scene-level exclusions and filters do);
- scenes **with no known `GSD`** are skipped, and the build reports that in the warnings.

!!! tip "When to use it"

    Turn it on when you are mixing scenes of markedly different `GSD` in one dataset. For
    a homogeneous resolution leave it empty - native tiling is then more accurate and
    faster.

!!! info "Common GSD is not the same as the GSD filter"

    **Common GSD** *rescales the pixels* of every scene to one resolution. The **GSD
    range** in the [Metadata filters](#metadata-filters) *selects which scenes* get in at
    all - it does not change any pixels. They can be used together: narrow the population
    with the range first, then normalize the scale with the common GSD.

## A version is immutable

Every use of **Generate Dataset** creates a new, immutable version. The history can be
browsed and exported independently, and versions compared with one another.

## Deleting a dataset version

In the run history (the **History** button above the tabs) every row has a **Delete**
option. Deletion is **permanent** - it removes the version directory from disk and from
the list; there is no recycle bin.

A **soft lineage gate** applies: if training runs were built from a version or models
were registered from it, or the version is published, the dialog shows the list of
dependent artifacts and requires deliberate confirmation. After forcing it through, the
dependent artifacts remain but are **orphaned** - their lineage can no longer be traced
back to the data. Deleting the newest version moves the "latest" pointer to the next one
and refreshes the cache.

!!! tip "What this is for"

    Mainly for reclaiming space after failed or experimental versions, and after heavy
    benchmark sets. A version intended for training is usually not deleted - it is what
    keeps the models trained from it reproducible.

## Next step

Check the [statistics](statystyki.md) and the [audit](audyt.md), and
[publish](publikowanie.md) the version intended for training.
