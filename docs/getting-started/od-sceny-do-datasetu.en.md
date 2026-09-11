# From scenes to a dataset

A guide to the whole path of the data - from a vendor package to a model. It does not
replace the instructions for the individual steps; it shows **the order, the
intermediate products and the places worth stopping at**.

Every stage produces something and assumes something about the stage before it. Skipping
a check moves the problem further down, where fixing it costs more.

## The road map

```text
vendor package
   │  import and product recognition
   ▼
scene in the project         ← the record of truth
   │  labeling
   ▼
canonical annotations        ← the record of truth
   │  review grid
   ▼
confirmed coverage
   │  tile catalog
   ▼
tile candidates              (metadata, no images)
   │  dataset build
   ▼
dataset version              (images, split, statistics)
   │  audit and publication
   ▼
published version
   │  training
   ▼
model + lineage
```

Everything below the "canonical annotations" line can be **reproduced**. Everything
above it cannot.

## 1. From a package to a scene

**What you do.** You connect a vendor folder and import the recognized scenes.

**What is produced.** A scene catalog with statuses; for some products, a working view.

**Before you move on.** The scenes have the **ready** status. Scenes that required a
decision were resolved deliberately - with SAR products, take care not to pick the
quick-look file instead of the measurement data.

→ [Import scene packages](../input-data/importuj-paczki.md)

!!! warning "This is where decisions that are hard to undo are made"

    After the first annotation, the variant of a scene is locked. The choice of product
    and of the way it is prepared is at its cheapest to reconsider now. See
    [Derived products](../input-data/produkty-pochodne.md).

## 2. From a scene to annotations

**What you do.** You draw boxes on full scenes.

**What is produced.** Canonical annotations - **the one thing that cannot be
reproduced**.

**Before you move on.** The classes are agreed with the team, the interpretation is
consistent, and the orientation of the objects is set carefully if the project uses
oriented boxes.

→ [Annotation](../annotation/index.md)

## 3. From annotations to confirmed coverage

**What you do.** You mark the fragments you reviewed on the review grid.

**What is produced.** The information about where somebody actually looked.

**Before you move on.** The empty areas are reviewed, not merely empty. The nodata border
is excluded.

→ [Review grid](../annotation/siatka-przegladu.md)

!!! info "This stage gets skipped, and it takes its revenge later"

    Without confirmed coverage there is nowhere to take negative examples from, and the
    audit will report the use of areas nobody looked at. Both problems come back once
    the dataset is already built.

## 4. From coverage to candidates

**What you do.** You build the tile catalog.

**What is produced.** The geometry of the subdivision and its links to the annotations -
**without images**.

**Before you move on.** The tile size and the overlap are thought through: changing them
requires a new version of the catalog, unlike the split or the preprocessing.

→ [Tile catalog](../datasets/katalog-kafelkow.md)

## 5. From candidates to a dataset version

**What you do.** You generate a dataset with the chosen split, share of negatives and
filters.

**What is produced.** An immutable version with images, a split and statistics.

**Before you move on.** The split strategy matches the data - for satellite material,
by scene or by spatial block, not a random draw of tiles.

→ [Build a dataset](../datasets/zbuduj-dataset.md)

## 6. Checking and publication

**What you do.** You read the statistics, run the audit and publish the version.

**What is produced.** A version marked as material for training.

**Before you move on.** The audit is `ready`, or the warnings have been judged
**deliberately** - especially the ones about spatial leakage and unreviewed areas.

→ [Audit](../datasets/audyt.md) · [Publish a version](../datasets/publikowanie.md)

!!! danger "This is the last moment before a costly mistake"

    Spatial leakage does not show up as a failure - it shows up as a **good result**.
    The model looks effective and then fails on a new scene. Discovering this after
    training means repeating all the work from stage 5.

## 7. From a dataset to a model

**What you do.** You run preflight, train, compare the runs and register the model.

**What is produced.** A model together with a lineage reaching back to the authorship of
the annotations.

**Before you call it finished.** The comparison is made on validation metrics, and the
test evaluation happens once, at the end.

→ [Start training](../training/uruchom-trening.md) ·
[Compare results](../training/wyniki.md) ·
[Model registry](../training/rejestr-modeli.md)

## Where the team comes in

In team work, stages 1–3 are done by the analysts in their own projects, and stages 4–7
by the manager in the collective project. Annotation packages carry the result of stages
2 and 3.

→ [Teamwork](../teamwork/index.md)

## What comes back to the start

A model is rarely the end. The usual loops:

- **a weak result for a class** → more examples of that class → stage 2;
- **false detections on the background** → more reviewed empty areas → stage 3;
- **spatial leakage in the audit** → a different split strategy → stage 5;
- **a model ready to help** → prediction as labeling support → stage 2.

The last loop is the most valuable and at the same time the most risky: the proposals of
a model speed the work up, but accepted without checking they **cement its own mistakes**
into the next dataset.

→ [AI tools](../ai-assistance/index.md)
