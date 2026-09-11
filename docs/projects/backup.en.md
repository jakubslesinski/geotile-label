# Project backup and transfer

**Goal.** Protect the work, or move a project to another computer.

**When.** Before deleting a project, before a larger configuration change, and when
handing work over to another workstation.

## Create a backup

1. Open the project.
2. Choose **Project backup**.
3. The application starts a `project_backup` job. You can close the panel and carry on
   working.
4. Once it finishes, open [Background jobs](../reference/zadania-w-tle.md), choose
   **Download** and pick where to save the ZIP.

The backup is created as a stream and published only after the archive closes correctly. A
cancelled or failed job does not expose a partial ZIP as a finished backup.

**What it contains.** The configuration, the profile, the classes, the scene list, the
annotations and the tile metadata.

**What it does not contain.** The source scene files, or the generated datasets.

!!! info "A backup is light by design"

    Scenes can weigh tens of gigabytes and are usually available on a shared drive. A
    backup carries what cannot be reproduced - your work - and not the data you have
    anyway.

## Restore from a backup

1. In the projects view choose **Import backup ZIP**.
2. Point to the ZIP file.
3. Point to the folder with the original scenes.

The third step is necessary precisely because a backup does not carry the scenes. Without
the sources being pointed at, the project is restored with its annotations, but the scenes
will have the **source missing** status.

!!! warning "A backup creates a new project, it does not merge work"

    Importing a backup restores **the whole project as a separate one**. Attaching an
    analyst's work to a collective project is what an annotation package is for - a
    different operation, and the two are not interchangeable. See
    [Exchange package types](../reference/rodzaje-paczek.md).

## Open an existing project folder

The **Import project folder** option opens a project created earlier - one copied from
another workstation, for example, or coming from an older version of the application.

After the import, point at the scene folder again. Any missing scenes are listed in the
report.

## Migration from older versions

The first time a project from version `0.1.3` or `0.1.4` is opened, the application
migrates the metadata. Before the change it writes a light JSON snapshot into
`.migration_backups`.

The migration **does not copy scenes or datasets** and **does not change the identifiers**
of scenes or annotations - which is why packages exported earlier still fit the project.

## Moving to another computer

Two routes, depending on whether you have access to the same project folder.

**Through a backup ZIP** - when moving between workstations: create a backup, move the
file, import it and point at the scenes.

**By copying the project folder** - when the project lives on a portable or shared drive:
copy the whole folder and use **Import project folder**.

!!! warning "Either way you need the same scenes"

    The project folder does not contain the imagery. On the new workstation connect the
    same medium or point at a copy of the sources, and then
    [relink the sources](relinkuj-zrodlo.md). The annotations survive - they are stored in
    the project, not next to the scenes.

## Related

- [Data locations](../reference/lokalizacje-danych.md) - exactly what sits in the project
  folder
- [Delete a project](usun-projekt.md) - an irreversible operation, make a backup first
