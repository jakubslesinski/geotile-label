# Prediction is unavailable

The prediction panel is greyed out or reports that prediction is not available.

## Check the diagnostics

Open **Settings → Diagnostics** and check whether:

- the `YOLO` feature has the enabled status;
- the `Torch` and `Ultralytics` library versions are shown.

If they are not there, the backend environment did not load the prediction libraries.

## The fix

1. Choose **Clear runtime/cache**.
2. Start the application again.
3. Check the diagnostics once more.

If the problem comes back, export the diagnostics ZIP and pass it to the team.

## Prediction works, but not on the graphics card

That is normal. **Prediction runs on the CPU** in every installation and does not need
the GPU pack.

!!! info "The GPU pack is only for training"

    Installing the [GPU training pack](../getting-started/pakiet-gpu.md) will not speed
    prediction up - it serves only the training of your own models.

## A model path points somewhere that no longer exists

Model paths (the YOLO `.pt`, the **SAM** folder and checkpoint, the **DINO** folder and
weights) are stored **separately in every project**. After moving a project to another
computer, or changing where the models are kept, those paths may point at places that
are gone.

Symptoms: saving the prediction settings does not go through (a message along the lines
of "SAM models directory not found"), the model does not load, and sometimes the error
concerns a different model than the one you are changing.

The fix, in the prediction panel:

- **YOLO** - choose **Select model** and point to the current `.pt` file.
- **SAM / DINO** - if the folder is out of date, use **Clear path**, then **Change
  folder** and point to the new directory with the weights.

!!! info "A stale path for one model no longer blocks the others"

    An update validates only the path you are actually changing - a stale entry for
    another model will not prevent a correct path from being saved. You can also remove
    a stale entry outright with the **Clear path** button.

## Prediction works, but detects nothing

This is not a problem with the availability of the tool. Check:

- the **confidence threshold** - set too high, it rejects everything;
- the **modality of the model** - a model from electro-optical imagery will not work on
  SAR, and the other way round;
- the **class names** - matching is done by name, so a model class with no counterpart
  in the project will not be assigned.

Details in [YOLO prediction](../ai-assistance/predykcja.md).
