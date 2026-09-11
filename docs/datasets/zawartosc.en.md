# Dataset contents

**Goal.** Look inside a **finished, published** dataset - the real tiles with the boxes
and classes drawn on them - before you trust the metrics and launch a training run.

**Where.** The **Content** tab in the Dataset module, next to Statistics and Audit. It
concerns the selected dataset version (the run), not the state of the project before the
build.

![The Content tab with the tile grid and colored object boxes](../assets/images/dataset-content-tiles.png)
*The tiles as they went into training - with the object boxes.*

## What you see

A grid of the tiles of the selected split. On every tile there are **colored object
boxes**, with no captions on the image - captions would obscure the content, and with
many classes they would break the evenness of the tiles in the gallery. The class is told
by the color of the box, and the full **color legend** (only the classes on that tile) is
shown by the **enlargement** after a click.

Filters narrow the view to one **split** (training / validation / test) and one **class**
- picking a class is also the quickest way to read off its color. On large datasets the
tiles load further with the **Load more** button - the view does not fetch thousands of
images at once.

!!! info "This is a frozen artifact, not a live preview"

    You see exactly what went into training in this version: that particular split, the
    rendered tiles and the exact boxes. The version is immutable - nothing here is edited.

## From a tile to the scene

Clicking a tile opens the enlargement with the boxes, the **class legend** and an **Open
scene here** button. It takes you to the editor positioned on that fragment of the scene.

!!! warning "A correction creates a new dataset version"

    The deep link opens the **live scene**, not the frozen tile. Corrections are made on
    the annotations of the scene, and they reach the model only through **generating the
    dataset again**. The current version stays untouched - which is what keeps a training
    result traceable to it.

## When to look here

- **Before training** - to see what actually goes into the model, instead of trusting the
  numbers alone.
- **After the audit** - to look at the specific cases the [check](audyt.md) reported.
- **To catch mistakes** - a badly rendered tile, a box that is not on the object, a
  confused class. From here one click takes you back to the editor.

## Related

- [Dataset statistics](statystyki.md) - distributions and scene usage
- [Audit](audyt.md) - the readiness of a version for training
- [Start training](../training/uruchom-trening.md) - the next step
