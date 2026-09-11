# Aerial NITF scenes (sensor geometry)

A project type for **oblique aerial imagery in the NITF format** (panchromatic, mono,
`UInt16`). Annotations are made in the **native sensor geometry** - on the raw pixels of
the image, not on a map layer. At the same time the **spatial position of the tiles and
annotations is preserved** (approximately, through a TPS transformation from the
georeference points of the image).

## Why sensor geometry

An oblique aerial photograph is best interpreted in its original geometry, and a model
trained on such tiles later works on images of the same kind. That is why you label
directly on the sensor scene, without rectifying to a map. The map view of these scenes
can be inspected separately in the QGIS plugin (NTF Reader) if needed.

| | Local scenes (EO/SAR) | Aerial NITF |
| --- | --- | --- |
| Labeling geometry | scene pixels | **sensor pixels** |
| Georeferencing | affine (regular) | **TPS from 4 points** (approximate) |
| Reference map layer | optional (GEO) | **none** (preview in QGIS) |
| Bands / type | any | **mono `UInt16`** |
| Label position in the database | yes | **yes** (through TPS) |

## Creating the project

1. In the new project form choose **Project type → NITF (sensor geometry)**.
2. Point to a **folder with `.ntf` files** - or a folder with **package subdirectories**,
   where each subdirectory holds `.ntf` files (the name of the subdirectory is remembered
   with the scene).
3. Choose the **annotation mode**: an axis-aligned box or an oriented (rotated) one.
4. Optionally point to a class file.

When the project is created the application opens every NITF container, selects the image
segment, reads the georeference points and the metadata, and builds a **working sensor
raster** (a tiled `GeoTIFF` with a preview pyramid). The raw `UInt16` values are preserved
for export.

!!! info "The scope of the first version"

    **Single-segment, single-band (panchromatic) `UInt16`** scenes are supported.
    Multi-segment and multi-band/RGB containers, and RPC/DEM georeferencing, are not
    supported in this version.

## Scene metadata that is preserved

During import, a significant subset of metadata is extracted from the NITF container (the
headers and the TRE records) and stored with the scene. Some of the fields are
**searchable** - they reach the scene list and the dataset sidecar, so the set can be
filtered and grouped by them.

| Group | Example fields (scene `A001.F0012`) | What for |
| --- | --- | --- |
| **Identity / provenance** | image id `A001.F0012` (the scene name), mission `EXAMPLE-01`, scene no. `2`, target area `AREA07`, **production** date `2025-03-11` (different from the acquisition, `2025-02-04`) | telling apart and grouping in the ML database |
| **Platform / sensor** | sensor `SENSOR-X`, platform altitude `7600 m`, focal length `180.0 mm`, calibration date `2015-08-12` | filtering by sensor / acquisition geometry |
| **Resolution (approximate)** | `gsd_m ≈ 0.55` (across `0.30` × along `1.05`) | an indicative ground pixel size |
| **Band radiometry** | significant bits `10`, mask, `NoData`, scale/offset, unit | correct interpretation of the values |
| **Classification** | the NITF classification/distribution markings | security (see below) |
| **Sensor model (retention)** | the full `ACFTB` plus the raw `SENSRA` records (position/angles/altitude) | a reserve for a more accurate geometry in future |

!!! info "The GSD is approximate and anisotropic"

    On an oblique scene the ground resolution differs across and along the strip and
    varies within the frame. The recorded `gsd_m` is a single indicative value (marked as
    approximate), not a declaration of accuracy.

!!! note "The sensor model is stored “for the future”"

    The camera and sensor parameters (focal length, flight line, angles and altitude from
    `SENSRA`) are preserved but **are not used in this version**. `SENSRA` is kept in raw
    form, because GDAL does not decode that record - which means it can be interpreted
    later, once a more accurate transformation model appears, without importing again.

## The sensor view

A scene opens in the **pixel view** (with no map and no reference layer), in the default
sensor orientation. Panning and zooming work, and the **display panel** offers the same
set of adjustments as for EO satellite scenes: a histogram with two percentile handles,
the presets (`p2–98`, `1–99`, `μ±2σ`, `μ±3σ`, full range), a log/linear scale and - under
**Advanced** - brightness, contrast and gamma. The default is linear `p2–98`.

!!! tip "A very narrow brightness range is the norm"

    Panchromatic 10-bit scenes often occupy a small slice of the range. Without a
    percentile stretch the image would be almost black - which is why the default
    `pan_uint16_percentile` profile applies `p2–p98`. The display adjustments change
    neither the raw data nor the stored annotations.

Rotating and zooming out the view are purely interface state - they **do not change the
coordinates of the stored annotations**, which are always in sensor pixels.

## Tiling, annotations and AI

Tiling, axis-aligned and oriented boxes, the review grid, the statistics and the AI tools
(SAM, Find similar / DINO, YOLO prediction) work as they do for local scenes - they
operate on the pixels of the sensor scene.

For every tile and every annotation a **spatial footprint** is computed, by densifying the
edges and applying the TPS transformation. That way the position of the objects is stored
in the ML database, even though the work itself happens in sensor geometry.

!!! warning "AI tools on panchromatic data"

    The SAM and DINO models were trained mainly on EO-RGB material. On panchromatic
    `UInt16` scenes (rendered to 8-bit) they may perform less well - treat their output as
    proposals to be verified, not as certainty.

## Export and provenance

Exporting ML sets (YOLO, COCO, VOC) works the same as for other projects. The dataset
sidecars additionally carry the geometric provenance of every scene: the transformation
model (`gcp_tps`), the `approximate` marking, and the information that **this is not
orthorectification**.

A **spatial export** of the annotations to **GeoJSON / GeoPackage** in `EPSG:4326` is also
available: every object has a densified TPS polygon and explicit `transform_model`,
`approximate=true` and `orthorectified=false` fields.

!!! danger "TPS georeferencing is an approximation"

    A transformation from four corner points does not remove terrain displacement,
    parallax or the deformation of tall objects. Do not present the map extent as the
    exact geometry of an object. For an accurate view in a coordinate system, use the QGIS
    plugin.

!!! info "Classification fields"

    The NITF classification and distribution markings are preserved in the scene metadata
    but **deliberately do not reach the spatial export**. Follow the security rules in
    force in your organization when sharing data.
