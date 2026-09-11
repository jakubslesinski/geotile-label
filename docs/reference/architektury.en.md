# Base architectures

The list of architectures available in the
[Training tab](../training/uruchom-trening.md). Training always starts from base
weights - the application never downloads them itself, so they have to be prepared in
advance together with the
[GPU training pack](../getting-started/pakiet-gpu.md).

## Available families

Every family comes in three sizes: **n** (the smallest), **s** and **m**. The
"parameters" column gives the size of the model in millions of parameters - the larger
it is, the slower the training and the greater the demand on card memory.

| Architecture | Task | Parameters (n / s / m) |
| --- | --- | --- |
| YOLO11 | detection | 2.6 / 9.4 / 20.1 M |
| YOLO11-OBB | oriented boxes | 2.7 / 9.7 / 20.9 M |
| YOLOv8 | detection | 3.2 / 11.2 / 25.9 M |
| YOLOv8-OBB | oriented boxes | 3.2 / 11.5 / 26.5 M |
| YOLOv10 | detection | 2.8 / 8.1 / 16.6 M |
| YOLO12 | detection | 2.6 / 9.3 / 20.2 M |
| YOLO12-OBB | oriented boxes | 2.7 / 9.6 / 21.0 M |
| YOLO26 | detection | 2.6 / 10.0 / 21.9 M |
| YOLO26-OBB | oriented boxes | 2.7 / 10.6 / 23.6 M |

## The project geometry decides the choice

The list is filtered by the geometry type set in the project profile:

| Project geometry | Available architectures |
| --- | --- |
| Axis-aligned boxes | the detection variants |
| Oriented boxes | the OBB variants |

Architectures that do not fit the project stay visible but **greyed out with the reason
stated**. They are not hidden - a model missing from the list always has a readable
explanation.

!!! info "Why there is no YOLOv10-OBB"

    Not every family has a variant for oriented boxes. YOLOv10 was not published in such
    a version, which is why in a project with oriented boxes that family does not appear
    at all.

## Training from scratch

**YOLO12-OBB** is the exception: the architecture exists, but no pretrained weights were
published for it. The application offers it marked **from scratch** - the network starts
learning from random initialization.

!!! warning "Training from scratch gives markedly weaker results"

    Pretrained weights carry knowledge from a large general dataset, which lets a model
    learn new classes from relatively few examples. Without them a considerably larger
    dataset and a considerably longer run are needed to reach comparable quality. Choose
    this option deliberately, and not because it is the newest architecture available.

The "from scratch" entries need no weight files at all and are available even when the
weights directory is empty.

## License

All the base weights come from Ultralytics and are covered by the **AGPL-3.0** license.

!!! danger "Commercial use requires a separate license"

    A model trained from these weights inherits the license obligations. Commercial use
    requires an Ultralytics Enterprise license. This applies to the from-scratch entries
    as well - no weights are inherited there, but the training code is still covered by
    AGPL-3.0.

    The license is shown in the application under the architecture selection. Consult the
    person responsible for legal compliance before deploying a model in a product.

## Which variant to choose

Size **n** is suited to a quick check of whether the dataset learns at all. Sizes **s**
and **m** usually give better quality at the cost of time and card memory.

Do not assume in advance that a newer family is better for your data. The
[Compare results](../training/wyniki.md) tab exists precisely to check that on one
dataset, instead of relying on general benchmarks.
