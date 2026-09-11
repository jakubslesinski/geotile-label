# Relink a source

**Goal.** Point at the new location of the source files without losing scenes or
annotations.

**When.** Once scenes have the **source missing** status - after a drive letter changed,
after the data was moved, or after opening the project on another workstation.

## Before you start

Check that the medium is connected and visible in the system. Relinking points at a new
path, but it will not restore files that are not there.

## Steps

1. Open the project and go to the list of sources.
2. On the source with the unavailable status choose **Relink**.
3. Point to the folder containing the same packages as before.
4. Review the **compatibility check** - see below.
5. Confirm.

Verifying scene identity and preparing missing overviews happens as a background job. A
scene may briefly show a pending status; do not start a second relink of the same source
in the meantime.

## The compatibility check before confirming

Pointing at a folder does not change anything yet. The application first compares the
place you indicated with what it recorded during the import, and shows the result. Only
confirmation repoints the source.

When the folder you pointed at **does not correspond** to the registered delivery, the
relink is rejected together with a mismatch report - rather than repointing the project at
different data and leaving you with annotations describing something else.

The repoint itself is **transactional**: either every scene of the source goes through, or
none does. A relink interrupted halfway does not leave the project in a mixed state.

## Checkpoint

The scenes return to the **ready** status, and the annotations stay where they were.

If you use external overviews, move `scene.tif.ovr` together with `scene.tif`. A missing
sidecar does not change the identity of the pixels and does not remove annotations - the
application can rebuild the overview. Replacing the `.ovr` alone invalidates only the
display cache.

!!! info "Why annotations survive a move"

    The application recognizes packages by a stable source identifier and by the checksums
    of the products, not by the path. As long as you point at the same files, their new
    location does not matter.

## When relinking does not help

**Some scenes still say "source missing".** The folder you pointed at does not contain all
the packages. Check whether the whole source was moved, and not only part of it.

**The scenes have the "source changed" status.** The files were found, but their contents
differ from what was recorded at import.

!!! warning "Source changed is not the same as moved"

    That status means **the pixels of the product are different** from what they were when
    the labeling was done. The application then blocks the automatic use of the annotations,
    because the boxes are stored in pixels - they could point at a different place on the
    ground.

    Before going further, establish what happened: whether the file was replaced
    deliberately, or whether this is a different version of the product of the same
    acquisition. Details in [Scene statuses](../reference/statusy-scen.md).

## Refreshing does not delete scenes

Rescanning a source **does not remove** the scenes that are temporarily not visible. They
stay with the **source missing** status together with their annotations. Disconnecting an
external drive does not destroy the work.
