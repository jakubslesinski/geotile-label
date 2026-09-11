# Analyst quick start

**Goal.** From an empty application to your first saved annotation.

**Time.** About 15 minutes.

**What you need.** The application installed, a folder with the scene packages of one
vendor, and a `classes.json` file if your team has settled on one.

## 1. Create a project

In the **Projects** view the existing projects are at the top and the form for a new one
is below.

1. Enter the project name.
2. Add a source: choose the provider and the folder with its packages.
3. Optionally point to a parent folder for the project. Without it the project goes to
   the default location.
4. Set the project profile:
    - **modality** - `EO` or `SAR`;
    - **georeferencing** - `GEO` or `NO GEO`;
    - **annotation mode** - Axis-aligned bbox or Rotated bbox;
    - the **labeling author email**;
    - the default preprocessing profile and the split strategy.
5. Create the project.

!!! info "You do not choose the provider a second time"

    The provider and the sensor follow from the scene sources and from the metadata
    detected during the scan. They are not part of the project profile.

!!! warning "The annotation mode cannot be changed later without consequences"

    The choice between an axis-aligned and an oriented box decides which architectures
    can be trained and what the exports look like. Settle it with your team before the
    work starts.

![Project creation form showing the profile and the annotation mode selection](../assets/images/new-project-form.png)
*The new project form.*

## 2. Import the classes

1. Open the project.
2. In the **Classes** section choose **Import from file**.
3. Point to `classes.json`.

Classes that have shortcut numbers assigned can then be selected with the ++1++–++9++
keys.

## 3. Import the scenes

1. On the source you added, use **Scan and verify**.
2. Review the table of recognized scenes.
3. Scenes with the **decision required** status need a product to be selected.
4. Confirm the import.

**Checkpoint.** The scene catalog shows scenes with the **ready** status. The meaning of
the remaining statuses is described in [Scene statuses](../reference/statusy-scen.md).

## 4. Draw the first annotation

1. Open a scene from the catalog.
2. Choose a class - with the ++1++–++9++ keys or through the class search (++k++).
3. Turn on drawing mode with the ++d++ key.
4. Draw a box around the object.

**Checkpoint.** The annotation appears in the list next to the map, and on `GEO` scenes
the computed dimensions in meters are shown.

![Labeling view with a drawn box and the annotation list](../assets/images/first-annotation-on-a-scene.png)
*The first annotation on a scene.*

!!! tip "Annotations save themselves"

    There is no separate save button. The work goes into the scene file in the project
    folder as you go.

## 5. Mark the area you reviewed

The review grid lets you record which fragments of a scene have already been looked at
- including the ones where you found nothing.

1. Go to the **Review grid** tab.
2. Create the grid in the **Grid configuration** section.
3. Turn on **Mark reviewed** and click or drag across the cells.

**Checkpoint.** The progress bar and the counter of reviewed cells go up.

!!! info "Reviewed empty areas are information too"

    A model learns not only what it is supposed to detect, but also where the objects
    are not. Telling "reviewed and empty" apart from "not looked at yet" is what matters
    here.

## 6. Hand over the work

Once a scene is finished, use **Export annotation package** and pass the package to the
manager.

!!! warning "An annotation package, not a backup"

    A backup restores a whole project as a new one. Handing over work is what the
    annotation package is for: it attaches the labels to a collective project. See
    [Exchange package types](../reference/rodzaje-paczek.md).

## What next

- The full data cycle: [From scenes to a dataset](od-sceny-do-datasetu.md)
- Every shortcut: [Keyboard shortcuts](../reference/skroty.md)
- The correction cycle: [Analyst workflow](../teamwork/workflow-analityka.md)
