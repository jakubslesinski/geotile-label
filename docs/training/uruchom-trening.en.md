# Start training

**Goal.** Train a model on a published dataset version.

**Requirements.** The [GPU training pack](../getting-started/pakiet-gpu.md), a
[published dataset version](../datasets/publikowanie.md) and prepared base weights.

## Steps

1. Open the **Training** tab.
2. Choose the **dataset** from the list of published versions.
3. Choose the **Base model**.
4. Set the parameters or leave the defaults.
5. Choose **Run preflight**.
6. Once the check passes - **Start training**.

![The Training tab with the dataset, base model and preflight result](../assets/images/training-run-configuration.png)
*The configuration before starting a training run.*

## Basic parameters

| Parameter | Meaning |
| --- | --- |
| **Epochs** | how many times the model goes through the whole training set |
| **Image size** | the size the tiles are scaled to |
| **Batch** | how many images at a time; `-1` selects it automatically from card memory |
| **DataLoader workers** | how many processes prepare the next images; too many can increase RAM use and make a slow disk worse |
| **Image cache** | `auto`, disabled, `disk` or `ram`; a RAM cache needs preflight's consent |
| **Seed** | reproducibility of the run |
| **Patience** | after how many epochs without improvement training stops |

!!! tip "Start with the defaults and one small model"

    The first run is meant to answer the question "does this dataset learn at all", not to
    give the best possible result. Size **n** and the default parameters are enough to
    check that in a reasonable time.

After the preflight, the recommendation card shows the bottleneck it identified, the
proposed `batch/workers/cache`, a memory estimate and the reasoning. **Apply
recommendation** only copies the values into the form - the configuration can still be
changed and checked again.

## Configuration through YAML

Besides the form there is a YAML editor for the advanced parameters - the optimizer, the
learning rate or the augmentations, for example.

!!! info "Some keys are managed by the application"

    Keys such as the data path, the output directory or the model selection are set
    automatically and **will be rejected** if you enter them by hand. That is how the
    application keeps the chosen dataset, the model and the stored run consistent with one
    another.

The CLI command shown is **explanatory** - it shows what the same training run would look
like started from the command line. It serves to verify the configuration, not to be
copied outside the application.

## The run

Progress is visible as it goes: the epoch number and the metrics. Training can be
**cancelled** - the run is then marked as cancelled and keeps the results obtained so far.

The run is also visible under [Background jobs](../reference/zadania-w-tle.md). The
manifest records the requested and the effective values, so that it is later clear which
batch, worker count and cache were actually used.

!!! warning "Closing the application interrupts training"

    Training runs as a child process of the application. Closing the window stops it. The
    run is then marked as **externally interrupted** - the application detects that the
    process is gone, instead of leaving it "running" forever.

## Run statuses

| Status | Meaning |
| --- | --- |
| **queued** | the run is waiting to start |
| **running** | training is under way |
| **completed** | finished successfully |
| **cancelled** | stopped deliberately |
| **interrupted** | the process disappeared, for example when the application was closed |
| **failed** | training ended with an error |

## Repeating the same configuration

The same set of parameters can be run several times. That is not a flaw in the interface
- repetitions let you judge the **spread of the results**, which on small datasets can be
larger than the differences between configurations.

The [Compare results](wyniki.md) tab groups such runs together.

## Next step

[Preflight](preflight.md) explains the checks before the start, and
[Compare results](wyniki.md) how to read the outcomes.
