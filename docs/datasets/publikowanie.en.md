# Publishing a dataset version

**Goal.** Single out the dataset version worth training on and make it available in the
Training tab.

**When.** Once a version has passed the [audit](audyt.md) and you consider it ready.

## What it is for

The dataset workshop produces many versions - with different splits, filters and
parameters. Most of them are attempts. Publication separates **working material from what
is actually trained on**.

The Training tab shows published versions only. That way nobody trains by accident on a
version with an experimental split.

## Three states

| State | Meaning |
| --- | --- |
| **draft** | a freshly generated version; the default state |
| **published** | a version intended for training |
| **deprecated** | a version moved aside from new training runs |

The transitions are reversible - a version can be published, deprecated and taken back to
draft.

## Steps

1. Open the list of dataset versions.
2. Select the version that passed the audit.
3. Choose **Publish** and give it a publication label.

**Checkpoint.** The version appears on the dataset list in the Training tab.

!!! tip "A publication label should describe the contents, not the order"

    "v3" says nothing a month later. "Ports, scene split, 15% negatives" lets you pick the
    right version without opening its configuration.

## Deprecating

**Deprecate** moves a version aside from new training runs, but **does not delete it** and
does not invalidate the models built on it. Their lineage stays readable.

Use it when a version turned out to be faulty or has been superseded by a better one, and
you want to prevent it from being used by accident.

## Deleting a version is restricted

!!! warning "A version used for training cannot be deleted"

    If a training run or a registered model depends on a version, deletion is blocked.
    This protects the lineage: without the dataset there would be no way to reconstruct
    what a model was built on, and such a model stops being accountable.

    When a version is no longer needed but is blocked - **deprecate it** instead of
    deleting.

## Related

- [Dataset audit](audyt.md) - check before publishing
- [Start training](../training/uruchom-trening.md) - what happens to a published version
- [Model registry](../training/rejestr-modeli.md) - the link between a model and a
  dataset
