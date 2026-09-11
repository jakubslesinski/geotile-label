# Preflight

The check performed **before** training starts. It examines the things that would
otherwise surface a quarter of an hour later, or - worse - only in the results.

## What is checked

**The device.** Whether the GPU runtime is active and the card is visible.

**Card memory.** Whether the chosen model size and batch stand a chance of fitting.

**Disk space.** Whether there is enough for preparing the data and the checkpoints.

**Data consistency.** Whether the dataset has a correct structure, whether the split into
sets is complete, and whether the classes agree between the configuration and the labels.

**OBB labels.** In projects with oriented boxes - whether the labels have the right
format.

**Data throughput.** A bounded read-and-decode test on samples judges whether training
will be waiting mainly on the disk, the CPU or the GPU. The test covers at most 24 images
and 128 MiB of data - it does not create a full cache and does not modify the published
dataset.

## Resource recommendation

Preflight classifies the profile as **I/O**, **CPU decode**, **GPU** or **balanced**, and
proposes:

- an effective `batch`;
- a number of **DataLoader workers**;
- an image cache: disabled, on disk or in RAM.

The recommendation is stored with the manifest but **does not change the configuration
without your decision**. Use **Apply recommendation**, check the fields and run preflight
again.

!!! warning "A RAM cache has to leave a safe margin"

    Preflight accounts for the size of the dataset, the free memory and the worker
    processes. On Windows the `spawn` model can duplicate data between processes, which is
    why an explicit `cache=ram` is blocked if at least 35% of RAM or 8 GiB would not remain
    after the estimate. This is a start-up check, not merely a suggestion.

## Why the OBB label check is separate

!!! danger "The wrong label format reports no error - it gives a wrong model"

    The training framework looks for labels next to the images by a fixed naming rule. If
    it were to find rectangular labels where oriented ones are expected, **training would
    proceed normally** and finish without a warning - only the model would have learned the
    wrong geometries.

    That is why the application prepares the data for OBB models in a separate directory
    and checks the format before the start. This is a guard against silent failure, not a
    loud one.

## Estimated time

Preflight gives an indicative **time band**, not a forecast.

!!! info "An order of magnitude, not a promise"

    The real time depends on the card, the size of the images, the number of tiles and
    whatever else is running on the computer. The band serves the decision "do I start now
    or overnight", not planning to the minute.

## When preflight does not pass

**No GPU runtime** - install the
[training pack](../getting-started/pakiet-gpu.md) and start the application again.

**Not enough card memory** - reduce the image size, set a smaller batch or choose a
smaller model variant. Batch `-1` selects the value automatically.

**An unsafe RAM cache** - choose `disk` or disable the cache, reduce the number of
workers, or use a smaller dataset. Do not work around the block with an extra YAML key.

**Inconsistent classes** - check whether the dataset was generated after the last change
to the project classes.

**An incomplete split** - the dataset version does not have all the sets. Go back to
[building the dataset](../datasets/zbuduj-dataset.md).

!!! tip "Preflight does not replace the audit"

    Preflight checks whether training **can be started**. Whether the dataset is
    **sound** - free of spatial leakage and unreviewed areas - is judged by the
    [audit](../datasets/audyt.md). A passed preflight does not mean the result will be
    trustworthy.
