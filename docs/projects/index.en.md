# Projects

A project holds the profile, the scene sources, the classes, the annotations, the review
settings and the history of dataset versions. **Source scenes are not copied into it** -
the project keeps only references to them.

## Tasks

<div class="grid cards" markdown>

-   :material-folder-plus: **I am setting up a project**

    ---

    What the profile fields mean and what the choices lead to.

    [Create a project](utworz-projekt.md)

-   :material-tag-multiple: **I am setting the classes**

    ---

    Import from a file, the format, and agreeing on it in the team.

    [Classes](klasy.md)

-   :material-link-variant: **The scenes went missing**

    ---

    Pointing at the files again after the data has moved.

    [Relink a source](relinkuj-zrodlo.md)

-   :material-archive: **I am securing the work**

    ---

    The ZIP backup, restoring, and moving to another computer.

    [Backup and transfer](backup.md)

</div>

## What is in a project and what is outside it

| In the project | Outside the project |
| --- | --- |
| the profile, classes, settings | the source scene files |
| the annotations | vendor metadata |
| tile catalogs and dataset versions | base model weights |
| working views and training runs | |

This division decides everything about moving work around: **a backup takes what cannot
be reproduced**, and the scenes have to be held separately.

The complete list of files is in
[Data locations](../reference/lokalizacje-danych.md).

## Lasting decisions

Most settings can be changed as the work goes on. Three things, however, are hard or
impossible to undo:

**The annotation mode** - it decides which architectures can be trained and what the
exports look like. Settle it with the team before the start.

**The modality** - it is shared by all the sources, so electro-optical and radar
material cannot be combined in one project.

**The scene variant** - after the first annotation the application locks the working
product against change. The reason is described in
[Derived products](../input-data/produkty-pochodne.md).

## The project role

A project can be run as an individual one or as a collective one, in which the work of a
team is merged and verdicts are issued. That setting is described in
[Project roles](../teamwork/role-projektu.md).
