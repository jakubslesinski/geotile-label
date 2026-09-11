# Model training

The training workbench lets you train a detection model on a dataset built in the
application, compare several configurations on the same data, and designate the model the
project uses for prediction.

Training requires an NVIDIA card and the **GPU training pack**, which is installed
separately - the base installation runs on the CPU and covers labeling, the AI tools and
prediction.

## Scope

- installing the GPU training pack and verifying that the runtime is active;
- selecting **all** the files of the pack at once - together with the base weights
  archive, without which the architectures stay unavailable;
- preparing the base weights and the list of available architectures (YOLOv8, YOLOv10,
  YOLO11, YOLO12, YOLO26 in the n/s/m sizes);
- why some architectures are greyed out - the geometry of the project decides whether a
  detection model or an OBB model is needed;
- choosing the dataset, the architecture and the hyperparameters;
- configuration through the form and through the YAML editor;
- the adaptive preflight: GPU, VRAM, disk, data consistency, a throughput test and a
  `batch/workers/cache` recommendation;
- the training run, progress and interruption;
- comparing runs within one dataset;
- the final evaluation on the test set;
- the model registry, promoting the project model and data lineage;
- training performance telemetry stored with the run.

!!! note "Training from scratch gives weaker results"

    Most architectures start from weights pretrained on COCO. YOLO12-OBB is the exception
    - no weights were published for it, so the network learns from random initialization.
    At the typical size of an annotation dataset the results will be markedly worse than
    with a pretrained model. The application marks such entries **from scratch**.

!!! warning "Closing the application interrupts training"

    Training runs as a child process of the application. Closing the window interrupts it,
    and the run is marked as interrupted.

!!! note "The test set is used once"

    The evaluation on the test set is performed once per run and stored as the final
    result. Selecting a model repeatedly on the test set turns it into a second validation
    set and inflates the assessment - which is why comparisons and iterations go by the
    validation metrics.

## In this chapter

- [Start training](uruchom-trening.md)
- [Preflight](preflight.md)
- [Compare results](wyniki.md)
- [Model registry](rejestr-modeli.md)
