# Dataset audit

**Goal.** Check whether a dataset version is fit for training, before you start training.

**When.** After generating a version, before [publication](publikowanie.md).

## Readiness

The audit result comes down to one of three states:

| State | Meaning | What to do |
| --- | --- | --- |
| **ready** | no blocking problems | training can start |
| **ready_with_warnings** | there are warnings | judge them deliberately before training |
| **not_ready** | there are errors | fix them before use |

!!! warning "“ready_with_warnings” is not “almost ready”"

    A warning marks a situation the application cannot settle for you - unreviewed areas
    used in the dataset, for example. It may be acceptable, or it may invalidate the
    training result. Only somebody who knows the material knows the difference.

## What is checked

**Split integrity** - whether the training, validation and test sets are disjoint and
have sensible proportions.

**Spatial leakage** - whether the same ground has landed in different sets, whether there
are similar tiles between them, and whether training tiles are **adjacent** to validation
and test ones.

**Acquisition coverage** - whether every acquisition configuration present in the
validation or test set also occurs in the training set.

**Review coverage** - how many cells remain unchecked, and whether the dataset draws on
areas nobody looked at or that were excluded technically.

**Completeness** - classes defined but unused; classes present only in the source; scenes
without annotations; missing metadata and sidecar files.

## Spatial leakage

The most important check for satellite data, and the most common cause of a result that
looks excellent and does not hold up in practice.

!!! danger "A result after leakage is useless, not merely inflated"

    When a model has seen the same ground in training and in testing, the metrics speak
    of memorization, not of the ability to generalize. Nothing can be concluded from them
    about how the model will behave on a new scene - and that is usually the only thing
    that actually matters.

Changing the split strategy to scene-based or block-based and generating the version
again is usually enough. See [Build a dataset](zbuduj-dataset.md).

A separate warning concerns **adjacency**: training tiles lying right next to validation
or test ones. Even when the sets are disjoint, touching tiles show almost the same ground
just across the boundary - a milder form of leakage. Block splits keep neighbors within
one set and reduce that effect.

## Acquisition coverage

A check that matters especially for **SAR** data. The warning appears when some
acquisition configuration reaches the validation or test set and is absent from the
training set.

An acquisition configuration is determined by the observation conditions: for SAR the
polarization, the look direction and the incidence angle; for EO the processing mode and
the cloud cover. A model evaluated on a geometry it did not see in training gets a result
that says more about the **difference in the sensor** than about its ability to recognize
the object.

!!! info "Missing acquisition metadata disables this check"

    When the scenes carry no acquisition metadata, the check is skipped rather than
    reported as an error.

## Unreviewed areas

The audit separately reports the situation in which a dataset draws on cells no person
has looked at.

A tile from such an area may contain an unlabeled object. The model then gets a
contradictory signal: the object is in the image but not in the labels - so it learns
that **it should not be detected**.

There are two ways out: finish the review, or narrow the dataset with filters.

## The report

Warnings and recommendations are shown in the interface language. The report can be saved
as **JSON** or **CSV** - useful for archiving alongside the dataset version and for
talking to the team about what to fix.

!!! tip "Audit before publication, not after training"

    Training costs time and card memory. Every problem the audit detects is cheaper to
    fix before it than after.

## Related

- [Dataset statistics](statystyki.md) - distributions and scene usage
- [Dataset contents](zawartosc.md) - to look at the reported cases on the tiles
- [Publish a version](publikowanie.md) - the next step after a passed audit
