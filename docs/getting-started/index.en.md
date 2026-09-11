# Getting started

This chapter takes you from installation to your first saved annotation, and shows
where to start depending on your role.

## Choose a path

<div class="grid cards" markdown>

-   :material-download: **Installation**

    ---

    Requirements, the installer and the first launch.

    [Install the application](instalacja.md)

-   :material-account-edit: **I annotate scenes**

    ---

    From creating a project to the first annotation, in about fifteen minutes.

    [Analyst quick start](szybki-start-analityka.md)

-   :material-account-tie: **I merge the work of a team**

    ---

    A collective project, package import and dataset building.

    [Manager quick start](../teamwork/szybki-start-managera.md)

-   :material-graph: **I want to see the whole picture**

    ---

    The full path of the data, from source scene to dataset version.

    [From scenes to a dataset](od-sceny-do-datasetu.md)

</div>

## What works right after installation

The base installation covers **labeling, the AI tools and prediction**. Everything runs
on the CPU - a graphics card is not needed.

Only **model training** requires the separate [GPU training pack](pakiet-gpu.md).
Without it the **Training** tab is visible, but the architectures stay unavailable
with the reason stated.

!!! info "Source data stays where it is"

    The application reads scenes read-only and does not copy them into itself. Into the
    project folder it writes the configuration, the annotations and the derived
    products. Details in [Data locations](../reference/lokalizacje-danych.md).

## Before you start

Prepare:

- the **scenes** in the form the vendor delivered them - do not unpack them into a
  shared folder and do not rename the files;
- the **class file** `classes.json`, if your team has already settled on one;
- a **folder for projects** - it can be on a working drive or a shared one.

!!! warning "Do not convert packages before importing"

    The application recognizes vendor products and prepares the working view itself
    when one is needed. Manual conversion usually makes recognition harder and breaks
    the provenance chain of the data. See
    [Default vendor products](../reference/produkty-dostawcow.md).

## Interface language and help

The language is switched in **Settings**. This documentation is opened by the
**Documentation** button and by the ++f1++ key.
