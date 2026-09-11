# Delete a project

**Goal.** Remove a project from the application permanently.

!!! danger "The operation cannot be undone"

    Make a [backup](backup.md) before deleting. It is light and takes a moment, and it is
    the only way back.

## What will be deleted

- the configuration and the project profile;
- **the annotations**;
- the tile catalogs;
- the generated datasets and training runs.

## What will be left untouched

**The source scene files.** They live outside the project and the application does not
delete them. Deleting a project does not remove the vendor imagery.

## Steps

1. In the projects view click the bin icon next to the project.
2. Read the confirmation window - it lists what will be deleted.
3. Confirm.

## Before you delete

Ask yourself three questions:

1. Has the work from this project been **handed over or exported**? If not, the
   annotations go with the project.
2. Is there a **model trained** on this project? Deleting removes the training runs, and
   with them the lineage of the model - there will be no way to tell what data it was
   built on.
3. Is somebody else **using the same folder**? The project may live on a shared drive.

!!! tip "An alternative to deleting"

    If it is only about tidying the list up, consider moving the project folder somewhere
    else instead of deleting it. The project disappears from the list and the data stays
    - it can be opened later with **Import project folder**.
