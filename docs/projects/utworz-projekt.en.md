# Create a project

**Goal.** Set up a project and configure the profile that decides how the work is done.

**When.** At the beginning of a new labeling task.

Step by step is covered by the
[Analyst quick start](../getting-started/szybki-start-analityka.md). This page describes
**what the individual fields mean** and what the choices lead to.

## Name and location

The **name** identifies the project on the list.

The **parent folder** is optional. Without it the project goes to the default location
in the application data. Pointing to a folder of your own - on a working or a shared
drive - makes backups and team work easier.

!!! info "You do not have to remember the location"

    The application records the actual path of every project in its own index, so
    projects outside the default location also appear on the list. See
    [Data locations](../reference/lokalizacje-danych.md).

## Project type

| Type | When |
| --- | --- |
| **Local scenes** | you have vendor product packages |
| **Airborne NITF scenes** | you have imagery in sensor geometry |

The type decides the rest of the form.

## Project profile

### Modality

`EO` - electro-optical imagery, `SAR` - radar. Shared by all the sources of the project,
which is why optical and radar material cannot be mixed within one project.

### Georeferencing

`GEO` or `NO GEO`. Scenes without georeferencing carry annotations in pixels only and do
not reach geographic layers on export.

### Annotation mode

**Axis-aligned bbox** or **Rotated bbox**.

!!! warning "This is a decision for the whole project"

    The annotation mode translates into two things that are not visible when a project is
    being set up:

    - **which architectures can be trained** - axis-aligned boxes require detection
      models, oriented ones require OBB models, and the list is filtered automatically;
    - **the shape of the exports** - YOLO OBB stores four vertices instead of a
      rectangle.

    Oriented boxes make sense where the direction of an object matters - ships, aircraft,
    vehicles in car parks. For objects with no defined orientation, axis-aligned boxes are
    usually enough.

### Author email

It goes into the annotations as authorship information and carries on from there - all
the way to the [lineage of a model](../training/rejestr-modeli.md). In a collective
project it is what identifies whose work is being imported.

### Preprocessing profile and split strategy

The defaults for building datasets later. They can be changed on every dataset run, so
they are not an irreversible decision.

## The tile grid

When creating a project (with local sources) you set the initial **tile size** and
**overlap** - an important dataset parameter, which is why it is defined at the very
start. The default is 640 px with no overlap.

These values can be changed later in the
[Review grid](../annotation/siatka-przegladu.md); the **dataset build** tab inherits them
(and shows them for reference only). The rule is **what you review is what you train
on**, so changing the tile size after the review has started requires confirmation (it
resets the review progress).

## What the profile does not contain

!!! info "You do not choose the provider or the sensor"

    They follow from the scene sources and from the metadata detected during the scan.
    They are not in the project form - the provider is selected when a source is added.

## Next step

[Import the classes](klasy.md), then
[connect the scene packages](../input-data/importuj-paczki.md).
