# Input data

GeoTile Label works on vendor product packages. This chapter describes how to prepare the
data and what happens to it after import.

## A scene is a product, not a file

The most important notion in this chapter. **A scene is the logical image product of one
acquisition**, not a single file found in a folder. It may consist of several parts and
carry its own vendor metadata.

That is why the application recognizes **packages** rather than loose rasters - and why
packages should not be unpacked into a shared folder and files should not be renamed.

## Where to start

<div class="grid cards" markdown>

-   :material-folder-search: **I have vendor packages**

    ---

    Connect the folder, scan it and add the scenes to the project.

    [Import scene packages](importuj-paczki.md)

-   :material-layers-triple: **The scene needs preparing**

    ---

    When a working view is created and why it is locked afterwards.

    [Derived products](produkty-pochodne.md)

-   :material-image-filter-center-focus-strong: **I have very large rasters**

    ---

    Prepare external `.ovr` overviews in QGIS in a batch and use them without a
    separate import.

    [QGIS overviews and sidecars](piramidy-qgis.md)

-   :material-airplane: **I have aerial NITF scenes**

    ---

    Label in sensor geometry, with the position preserved through TPS.

    [Aerial NITF scenes](nitf-lotnicze.md)

</div>

## Supported formats and vendors

| | Scope |
| --- | --- |
| Rasters | TIFF, GeoTIFF, JPEG 2000 (`.jp2`) |
| Aerial NITF | `.ntf` / `.nitf`, panchromatic mono `UInt16`, sensor geometry |
| Metadata | JSON, XML, IMD, TIL, RPB, TFW |
| SAR vendors | ICEYE, Capella, UMBRA |
| EO vendors | Pleiades Neo, WorldView, BlackSky |
| Other | the Generic source for simple image directories |

Which product is selected from a package is described by the
[table of default products](../reference/produkty-dostawcow.md).

## GEO and NO GEO scenes

Georeferenced scenes make it possible to compute dimensions in meters, show a reference
map layer and export geographic geometry. Scenes without georeferencing carry annotations
in pixels only and **do not reach geographic layers** on export - the note about what was
skipped is in the summary.

## External drives

Scenes may live on an external drive. Once it is disconnected the scenes get the **source
missing** status, but they **do not disappear from the project** and do not lose their
annotations. After reconnecting it - or after moving the data somewhere else - use
[Relink a source](../projects/relinkuj-zrodlo.md).

!!! warning "Do not tidy a package up after creating the project"

    Changing the files inside an imported package is detected as **source changed**, and
    the application then blocks the automatic use of the existing annotations. This is a
    safeguard: the boxes are stored in pixels, so replacing the raster could shift them with
    respect to the ground. See [Scene statuses](../reference/statusy-scen.md).
