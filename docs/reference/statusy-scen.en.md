# Scene statuses

A scene status answers whether the scene can be annotated and what action is needed.

| Status | Meaning | Action |
| --- | --- | --- |
| **ready** | the working product is readable | start annotation |
| **preparation required** | a working view must be created | start scene preparation |
| **decision required** | several equivalent products were found | select the intended product |
| **unsupported** | the installed runtime lacks a driver | report it to the application team |
| **source missing** | the folder or package is unavailable | reconnect the drive or relink the source |
| **source changed** | source pixels no longer match import identity | investigate before using annotations |
| **invalid** | the package is incomplete or inconsistent | verify the vendor package |
| **migration required** | package recognition changed and this scene has no unambiguous successor | select the product again - see [below](#migration-required) |

## Entries that are not scenes

The import preview also lists archives found next to deliveries. They are never
imported, but their state is sometimes the only trace of a delivery:

| Archive state | Meaning |
| --- | --- |
| **archive duplicate** | the ZIP contents are already extracted next to it |
| **incomplete extraction** | only part of the files were extracted; the missing list is given |
| **archive only** | the delivery exists solely inside the ZIP |
| **archive unreadable** | the file could not be opened, or the format is unsupported |

The application does not extract archives. For the last two states, extract the
delivery yourself next to the archive and run the scan again.

## Overview status

Scene readiness and overview readiness are separate. A **ready** scene may still have
an overview job in progress and remain usable.

| Overview status | Meaning |
| --- | --- |
| **pending** | an overview is required |
| **queued** | the durable job is waiting for a worker |
| **building** | GDAL is generating levels |
| **ready** | a complete overview is available |
| **native** | the raster provides levels sufficient for responsive display; a large JP2 may still require a project overview |
| **error** | preparation failed; the scene may still open more slowly |

The badge tooltip reports overview type and factors. An external `.ovr` added after
import is detected on refresh or before the next scene read.
A large JP2 may migrate from the former **native** status to **pending** on its first
refresh. This is a controlled high-zoom performance migration and does not change the
source raster.

### Full-resolution status for a large JP2

Preview readiness and full-resolution readiness are separate. `preview_status=ready`
means that the standalone `.ovr` can be used down to 2×; it does not promise 1× yet.

| Status | Meaning |
| --- | --- |
| **missing / stale** | there is no current COG; the `.ovr` preview remains active; this is expected when automatic COG preparation is off |
| **queued / building / validating** | the COG is waiting, being built, or being validated |
| **ready** | a validated COG is active and 1× is available |
| **error** | preparation failed and will not restart without an explicit retry |

After publication the layer switches to the COG without changing annotation
coordinates. After failure, inspect the error, retry explicitly, or continue with the
preview. Cancelling the job does not remove `.ovr`.

Automatic starts are controlled by **Automatic full-resolution COG** in the **Scene
sources** card. Turning it off does not change the status of an existing active COG or
cancel a running job. See [Derived products](../input-data/produkty-pochodne.md#large-generic-jp2-2-preview-and-full-resolution-1)
for the full workflow.

## Decision required

The application does not guess when products are equivalent - it asks you to choose. The
typical cases:

- the package contains several SAR polarizations;
- both a finished product and material that needs preparing are available;
- for a WorldView `MUL+PAN` the RGB band indexes have to be confirmed as well.

!!! warning "Choose the product, not the quick-look"

    The list of alternatives may include the vendor's quick-look files - with `preview`,
    `browse` or `quicklook` in the name. They look like the scene and carry the same
    georeferencing, but they are a processed 8-bit image, not measurement data. For SAR that
    matters for everything that depends on brightness. If you are unsure, choose the product
    named in the [table of default products](produkty-dostawcow.md).

## Migration required

This status appears after package recognition changes, when an old scene has no
unambiguous successor - typically a folder previously recognized as **one** scene that
now resolves into dozens of separate products.

The application **does not choose for you** and blocks opening the scene. Select
**Check migration** next to the scene sources, review the dry-run, and choose a
successor in every ambiguous row. Annotations and the scene identifier stay untouched.
The previous manifest is backed up before migration, and working views and overviews
are not deleted - the migration plan lists them as needing a rebuild.

## Source changed

This is a safety status. Annotations use scene pixel coordinates, so a different
raster can silently move them to another ground location. Verify whether the file was
intentionally replaced; create a separate scene for another product variant.

## Source missing versus deleting a scene

Refreshing the catalog **does not remove** the scenes whose source is temporarily not
visible. A scene stays with the **source missing** status, and the annotations remain
untouched. That is what makes disconnecting an external drive harmless to the work.

!!! info "The product is locked once work has started"

    Once a scene has annotations, the application does not allow its working product to be
    changed. The same principle as with the **source changed** status: the boxes are bound to
    one specific pixel grid. If you need a different product of the same acquisition, create a
    separate scene instead of replacing the existing one.
