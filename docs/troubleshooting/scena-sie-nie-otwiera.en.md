# Scene does not open

## Check the status first

The status of the scene in the catalog says what is missing - and each one has a
different solution.

| Status | What to do |
| --- | --- |
| **source missing** | connect the medium or [relink the source](../projects/relinkuj-zrodlo.md) |
| **decision required** | select the right product from the package |
| **preparation required** | create a working view |
| **source changed** | establish why the file changed - see below |
| **unsupported** | report it to the team; this cannot be fixed inside the project |
| **invalid** | check that the vendor package is complete |
| **migration required** | select the product again - see [below](#the-scene-requires-migration) |

The full description is in [Scene statuses](../reference/statusy-scen.md).

## The scene says “ready” and still does not open

**Check the access rights and the availability of the drive.** Scenes often live on a
network share or an external drive.

**Give large files time.** Displaying a large GeoTIFF for the first time requires reading
it and preparing the preview. Later openings are faster.

**Check the overview badge.** `queued` or `building` means the scene is already available
but low zoom may be slow. For `error`, open the
[Background jobs](../reference/zadania-w-tle.md) panel, check the message and retry. You
can also prepare a source sidecar following
[QGIS overviews and sidecars](../input-data/piramidy-qgis.md).

**After adding an `.ovr`, refresh the catalog or reopen the scene.** The application
detects the change and invalidates the old thumbnail, histogram and tile cache. The
sidecar does not have to be imported separately.

### A JP2 preview works, but 1× is unavailable

If a large generic JP2 renders from its `.ovr` preview but zooming in further is blocked,
check the **Automatic full-resolution COG** setting on the project dashboard, in the
**Scene sources** card:

1. **OFF** means the behavior is intentional - the application will not start the
   expensive COG preparation automatically and stays on the preview;
2. with **ON**, open the scene and wait for the first complete viewport; only then is the
   COG job queued;
3. open **Background jobs** in the left sidebar, immediately above Documentation, and
   inspect the `queued`, `building` or `validating` stage;
4. after `completed`, the layer switches to the validated COG on its own and exposes 1×.

After a failure the application does not start another attempt merely because the scene
was opened again. Check the stored message and use **Retry** explicitly. The `.ovr`
preview remains available after a failure or a cancellation.

**Check the logs.** `backend.stderr.log` in `%APPDATA%\GeoTileLabel\logs` usually says
outright where the read stopped.

## The scene is not listed at all

**The delivery may exist only inside an archive.** If a folder holds just a ZIP, the
delivery appears in the preview as an archive entry, not as a scene. The application does
not extract archives - extract the delivery next to the archive and rescan.

**Extraction may have been incomplete.** When a directory next to the archive holds only
some of the files, the preview reports **incomplete extraction** together with the list of
what is missing. Restore the missing files and rescan the source.

**Check that the right vendor was declared.** With the wrong vendor the application looks
for a different product pattern; the delivery is either not recognized, or it lands on the
list as **decision required** with every raster.

When it is not clear what happened, copy the
[import report](../input-data/importuj-paczki.md#import-report). It carries a reason for
every scene, including the ones that were skipped.

## The scene requires migration

Package recognition changed and the old scene has no unambiguous successor - for example a
folder previously recognized as one scene now resolves into dozens of products.

The annotations and the scene identifier stay untouched, and the previous manifest is
saved as a copy. On the scene sources use **Check migration**, review the dry run and
choose a successor. If the source is unavailable, restore it and run the check again. The
scene stays blocked until it is resolved; the plan identifies the working view and the
overview that need rebuilding.

## Source changed

This status means that **the pixels of the product differ** from those recorded at import.

!!! warning "Do not work around this status in a hurry"

    The application blocks the automatic use of the annotations because the boxes are stored
    in pixels - after the raster has been replaced they could point at a different place on
    the ground, **with no visible symptom**.

    Establish what happened first: whether the file was replaced deliberately, or whether
    this is a different version of the product of the same acquisition. In the second case
    the right answer is a separate scene, not unblocking the existing one.

## The image renders, but looks wrong

**Too dark or too bright** - that is a matter of display, not of the data. Adjust the
histogram stretch, the brightness and the contrast. Those settings do not change the
pixels.

**Distorted colors on WorldView** - probably an unconfirmed RGB band order. See
[Default vendor products](../reference/produkty-dostawcow.md).

**A hole in the middle of the image** - a tile listed in the delivery manifest is missing.
The import preview lists the missing parts; restore them in the source folder and rescan
the source. A ragged image edge, by contrast, is normal and does not indicate a gap.

**A wide black border around the image** - normal for geocoded products, when the
acquisition area is rotated with respect to the coordinate system. That area is worth
[excluding in the review grid](../annotation/siatka-przegladu.md).
