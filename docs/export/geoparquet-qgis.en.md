# Checking GeoParquet in QGIS

**Goal.** Look at the annotations in a GIS tool - against other layers and with the full
attribute table.

**When.** During quality control of a team's work, when going scene by scene is
impractical.

## Steps

1. Export the source GeoParquet annotations from the manager panel.
2. In QGIS choose **Layer → Add Layer → Add Vector Layer**.
3. Point to `annotations_wgs84.geoparquet`.
4. Check the geometry, class, scene and author in the attribute table.

For data in the native system use `annotations_native.geoparquet` - QGIS reads the
reference system stored in the file.

## Which file to choose

| File | When |
| --- | --- |
| `annotations_wgs84.geoparquet` | a compilation of many scenes, working against maps |
| `annotations_native.geoparquet` | measurements in the metric system of the scene |

!!! warning "NO GEO annotations do not reach the WGS84 layer"

    Scenes without georeferencing carry annotations in pixels only, so they cannot be
    placed on a map. The note about what was skipped is in the export summary - worth
    reading before concluding that objects are missing.

## What this view gives you

Several things show up better here than in the application:

- **the spatial distribution of the work** - which areas are covered and which are empty;
- **consistency between analysts** - filtering by author reveals differences in class
  interpretation;
- **duplicates where scenes meet** - the same object labeled in two overlapping scenes;
- **outliers** - unusual sizes stand out when the table is sorted.

!!! tip "Filter by author before talking to the team"

    Putting one analyst's annotations next to the others shows a divergence in class
    interpretation faster than going through the scenes one by one.

## This is checking, not editing

Changes made in QGIS **do not come back** to the project. GeoParquet is an export
product - corrections are made in the application, on the scenes.
