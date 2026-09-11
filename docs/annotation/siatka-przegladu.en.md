# Review grid

**Goal.** Record which fragments of a scene have deliberately been looked at.

**When.** Working on large scenes, where without it there is no way to tell what has
already been done.

## What it is for

A large scene does not fit on the screen and cannot be held in memory. The grid divides
it into cells and lets you record the fragments you went through - **including the ones
where you found nothing**.

!!! info "Reviewed and empty is not the same as not looked at"

    This is the most important distinction on this page. A cell without annotations can
    mean "I checked, there is nothing here" or "I have not looked here yet" - and that is
    entirely different information when a dataset is built. Empty fragments confirmed by
    a person are valuable training material; unreviewed ones are a risk.

!!! warning "The grid does not cut the scene into images"

    Creating a grid stores **the geometry, the links to annotations and the review
    state** - it does not create image files. Training images are produced only when a
    dataset is built. See [Tile catalog](../datasets/katalog-kafelkow.md).

## Creating the grid

1. Open the scene and go to the **Review grid** tab.
2. Unfold **Grid configuration**.
3. Set the **Grid cell size** and the **Grid overlap**.
4. Create the grid.

**Checkpoint.** The panel moves on to the **Progress and review** section, and the grid
appears on the map.

![The Review grid tab with the progress section unfolded and the grid visible on the scene](../assets/images/review-grid-panel.png)
*The review grid panel after creation.*

!!! info "Cell size = dataset tile size"

    The review grid and the dataset use **the same tile catalog**, so **the cell size and
    overlap set here are exactly the parameters of the training tiles**. The dataset
    build tab merely inherits them (and shows them for reference). The initial values are
    chosen as early as [creating the project](../projects/utworz-projekt.md).

!!! warning "Changing the size after the review has started resets the progress"

    Because the identity of a tile depends on its size, changing the size or the overlap
    **rebuilds the grid and clears the review state** (reviewed / excluded / empty
    cells). When there is something to lose, the rebuild button requires **deliberate
    confirmation** (a checkbox). If you need a different object scale without touching
    the review, use the common GSD when building the dataset.

## Marking cells

Three modes, switched with buttons:

| Mode | What it does |
| --- | --- |
| **Mark reviewed** | records that the fragment has been looked at |
| **Clear reviewed** | undoes the mark |
| **Exclude** | takes the fragment out of further use |

Click individual cells, or **drag** the cursor to mark a run of them at once.

Shortcuts work as well, without switching the mode:

| Shortcut | Action |
| --- | --- |
| ++r++ | mark the cell under the cursor |
| ++shift+r++ | mark everything visible |
| ++e++ | exclude the cell under the cursor |
| ++shift+e++ | exclude everything visible |
| ++s++ | show or hide the grid |

!!! tip "Visible means in the current frame"

    ++shift+r++ and ++shift+e++ act on the cells visible on screen. Zoom to the area you
    want to cover before using them - otherwise it is easy to mark more than you intend.

## When to exclude

Excluding says "this fragment is not to reach the dataset". The usual reasons:

- cloud or another disturbance that makes assessment impossible;
- an area without data - the nodata border of geocoded products;
- ground outside the scope of the task.

!!! info "Excluding is not the same as skipping"

    An excluded cell is a **deliberate decision**, recorded and visible in the audit. An
    unreviewed cell is the absence of a decision. The dataset audit treats them
    differently.

## Counters

| Counter | Meaning |
| --- | --- |
| **Reviewed** | cells marked as looked at |
| **Active** | the cells taken into account, that is, without the excluded ones |
| **Positive** | cells containing objects |
| **Empty** | reviewed cells with nothing in them |
| **Unchecked** | the ones still to be looked at |
| **Excluded** | taken out of use |

The progress percentage on the tab is computed against the **active** cells - excluding
a fragment does not drag the result down.

## Products with a wide nodata border

Some geocoded products have a large empty border, because the acquisition area is
rotated with respect to the coordinate system. The grid covers **the whole raster**, so
some of the cells fall over the empty area.

!!! tip "Exclude the empty border in bulk"

    Instead of going through empty cells one by one, zoom to the border area and use
    ++shift+e++. Leaving them unreviewed drags the progress down and makes it harder to
    judge whether the scene is finished.

## Related

- [Tile catalog](../datasets/katalog-kafelkow.md) - what the grid becomes
- [Dataset audit](../datasets/audyt.md) - how unreviewed areas affect the assessment
- [Keyboard shortcuts](../reference/skroty.md)
