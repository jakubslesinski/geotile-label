# Default vendor products

GeoTile Label recognizes a vendor package and selects a **working product** from it -
the raster used for labeling. This page states what gets selected and when the
application asks you to decide.

## Product selection

| Vendor | Working product | When it is missing |
| --- | --- | --- |
| ICEYE | GRD TIFF | decision required |
| Capella | GEO TIFF, GEC as second choice | decision required |
| UMBRA | GEC TIFF | decision required |
| BlackSky | ortho RGB TIFF | decision required |
| Pleiades Neo | PMS-FS RGB ORTHO | MS-FS RGB + PAN to prepare |
| WorldView | ready pansharpened RGB | MUL + PAN to prepare |
| Generic | a single raster file | - |

Modality follows the vendor: ICEYE, Capella and UMBRA are `SAR`, the rest are `EO`.

!!! info "You choose the vendor when adding a source"

    Recognition never guesses the vendor from folder contents - it uses your choice.
    Selecting the wrong vendor makes the application look for a different product
    pattern and usually ends with **decision required** listing every raster.

## ICEYE: legacy and current deliveries

Both package layouts are recognized:

| Layout | Contents | Notes |
| --- | --- | --- |
| legacy | `*.tif` next to `*.xml` | vendor metadata in XML |
| current | COG `*_GRD.tif` + `*.geojson` | GeoJSON sidecar with product parameters |

Labeling uses **GRD** (or `ORT` when the delivery contains it). Products inside
`.h5`, `.hdf5` and `.nc` containers - `SLC` and `CSI` - are **not scene candidates**:
they are complex data, not imagery. The `VID` preview is not a working product either.

One delivery may contain several polarizations of the same acquisition. Each becomes
a **separate scene** and receives only its own metadata - an `HH` sidecar does not
describe an `HV` scene.

## Capella: GEO and GEC

Capella ships geocoded products in two variants. When an acquisition contains more
than one, **GEO** is selected and `GEC` is the second choice. An acquisition holding
only GEC still yields GEC - the preference narrows the choice, it never blocks it.

A single Capella folder can hold **two acquisitions**. They are separated by satellite
identifier and timestamp, so you get two scenes instead of one scene with mixed
metadata.

`*_preview.tif` files are vendor previews and never appear as products.

## Pleiades: PHR and PNEO

Both missions ship as DIMAP and are handled by the same resolver. The sensor comes from
the metadata, not from the declared source: `PNEO3`/`PNEO4` or `PHR1A`/`PHR1B`.

| Mission | Working product | RGB band order |
| --- | --- | --- |
| PNEO | `PMS-FS`, `_RGB` variant | from metadata, usually 1, 2, 3 |
| PHR | `PMS` | from metadata, usually 3, 2, 1 |

Band order is **read from the DIMAP**, never assumed - both missions declare it in the
field meant for exactly that, and they differ: PHR lists bands as `B0…B3` where natural
color is the third, second and first, while PNEO lists them directly as `R`, `G`, `B`.

A PNEO `PMS-FS` delivery contains **two files of the same product**: `_RGB` in natural
color and `_NED` (near infrared, red edge, deep blue) in false color. `_RGB` is
selected by default; `_NED` is offered as a product alternative.

When the package holds no ready pansharpened product, a derived one is built from MS-FS
and PAN.

!!! info "Declaring the source does not change the sensor"

    Declaring a PHR source as Pleiades Neo will not record the scene as PNEO. The
    manifest notes the mismatch between the declaration and the metadata - still, declare
    the right mission, because the mismatch stays in the scene history.

### Pixel values in Pleiades products

The deliveries in this corpus are **display-ready** products: 8-bit, already stretched by the
vendor. They are neither radiance nor reflectance, so the manifest records them as
`display_ready`, with no physical unit and no calibration state.

This matters when building a dataset: such a scene **cannot be compared directly** with a
calibrated one, and the application refuses to mix them in one dataset without your explicit
consent.

!!! warning "Zero means no data, not black"

    Pleiades products declare `NODATA = 0` in their metadata even though the raster file
    itself does not carry it. The application takes the value from the metadata, because in
    real deliveries this "black border" of a rotated product covers **between a third and a
    half of the image**. Without it, those zeros would enter the brightness range
    calculation and darken tiles at the edge of the scene.

    `SATURATED = 255` is read the same way - it marks over-exposed pixels.

## WorldView: PAN, MUL and MUL+PAN

A WorldView delivery may contain the panchromatic component, the multispectral one, or
both. All three cases are recognized:

| Delivery contents | Working product | Status |
| --- | --- | --- |
| ready pansharpened (`PSH`) | that file | ready |
| MUL + PAN | derived RGB product | preparation required |
| MUL only | multispectral mosaic | ready |
| PAN only | panchromatic mosaic | ready |

A **panchromatic-only delivery is a complete scene**, not a missing component. It gets
no RGB mapping: a single-band image is displayed in grayscale.

### Part list and order from the TIL manifest

Tiled products ship with a `.TIL` file - the vendor manifest listing parts and their
order. The application takes the list **from there**, not from sorting filenames, and
uses it to judge completeness:

- all parts present → scene is **ready**;
- a declared part missing → **decision required**, a `partial_delivery` warning and the
  list of missing parts. Partial coverage can be imported deliberately, but that has to
  be your decision.

### WorldView band order

The application reads it from the `IMD` metadata:

| Found in metadata | RGB bands used |
| --- | --- |
| `BAND_C`, `BAND_B`, `BAND_G`, `BAND_R` (8-band product) | 5, 3, 2 |
| `BAND_B`, `BAND_G`, `BAND_R` (4-band product) | 3, 2, 1 |
| none of the above | asks for confirmation |

MUL and PAN metadata is **not merged into one scene description**. A MUL+PAN product is
described by the multispectral component, because that is what carries the spectral
characteristics; PAN metadata stays available separately.

## Multi-part products

Products split into tiles (`R1C1`, `R1C2`, …) are combined into a virtual raster
**without copying data**. You do not need to merge them or move them into one folder.

Before the mosaic is built, parts are checked: coordinate system, band count and type,
resolution, and alignment to a common pixel grid. A mismatch in any of these blocks the
mosaic and is reported in the scene status. Overlapping parts and coverage holes do
**not** block - they are a property of the delivery and become warnings.

!!! info "A ragged edge is not a hole"

    Tiles in the last column and row often differ in size, so the bounding rectangle is
    not fully covered. That is normal and is not reported. A warning appears only when a
    tile is missing **inside** the image or when more than 1% of the area is uncovered.

!!! tip "TIFF takes precedence over JP2"

    When Pleiades Neo delivers the same product in both formats, the application
    chooses the TIFF. There is no need to convert the JP2 yourself - the installer
    carries the `JP2OpenJPEG` driver, so both formats open without extra work.

## ZIP archives next to a delivery

An archive is not a scene and is never selected as a working product, but it is
**visible** in the import preview together with its state:

| State | Meaning |
| --- | --- |
| **archive duplicate** | every file from the archive is already extracted next to it |
| **incomplete extraction** | only part of the files were extracted; the missing list is given |
| **archive only** | the delivery exists solely inside the ZIP |

The application **does not extract archives**. When a delivery is archive-only or
incompletely extracted, extract it yourself next to the archive - ideally into a
directory named after it - and run the scan again.

## What not to do before importing

!!! warning "Do not process packages outside the application"

    If a package contains one of the products above, do not convert it, rename files, or
    move them into a shared folder. Keeping the original structure preserves full data
    provenance, and preparing a working view inside the application does not change
    pixels. Manual conversion breaks that chain and usually ends with **decision
    required**.

Source folder layout is described in
[Import scene packages](../input-data/importuj-paczki.md), and status meanings in
[Scene statuses](statusy-scen.md).
