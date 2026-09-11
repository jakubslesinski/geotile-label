# Annotation

Annotations are made **on the full scene** and are the canonical source of labels.
Labels in tiles are a derived product, computed when a dataset is built.

!!! info "There is no save button"

    The work is saved as you go into the scene file in the project folder.

## Tools

A tool is chosen with a key and stays active until it is changed.

| Tool | Shortcut | What for |
| --- | --- | --- |
| Pan / select | ++v++ | moving around the scene, selecting |
| Draw annotation | ++d++ | creating new boxes |
| Multi-select | ++a++ | operations on a group of annotations |
| Measure | ++m++ | distances on the scene |
| Class paint | ++p++ | quickly assigning a class to one object after another |

The full list is in [Keyboard shortcuts](../reference/skroty.md).

## Axis-aligned and oriented boxes

The geometry type follows from the project profile and does not change during the work.

An **axis-aligned box** has sides parallel to the image axes. It is simpler and faster
to draw.

An **oriented box** is rotated to the shape of the object and preserves the **heading**.
It makes sense where the orientation carries information - ships, aircraft, vehicles.

!!! tip "The heading is not cosmetic"

    With oriented boxes the orientation is stored as an azimuth relative to true north
    and goes into the computed attributes and into the exports. Setting the heading
    carelessly corrupts the data, even though it looks right on the map.

## Working on a scene

- moving, resizing and rotating existing boxes;
- copying a box by dragging its center with ++c++ held down;
- side dimensions shown while drawing on `GEO` scenes;
- selecting an annotation from the map and from the list, zooming in with a double
  click;
- changing the class of the selected object straight from the list (the **Drawing**
  tab) - the icon next to the bin unfolds a class list under the name; picking from the
  list changes the class immediately;
- deleting with ++delete++ outside drawing mode.

A reference map layer can be turned on for `GEO` scenes. For raster scenes the **display
panel** allows the tonal stretch to be adjusted directly on the **histogram** (two percentile
handles, the presets `p2–98`, `1–99`, `μ±2σ`, `μ±3σ`, full range, log/linear scale, and for
SAR also `SAR 1–99.8%`) and - in the **Advanced** section - brightness, contrast and gamma. For
multi-band scenes the histogram can be switched between **bands** (a separate curve for R, G
and B) and perceived **brightness**; the switch changes only the chart, not the image.

The **statistics extent** decides which pixels the stretch thresholds are computed from:

- **Scene** (default) - thresholds from the whole-scene histogram. Neighboring tiles and the
  same object in different parts of the scene get identical brightness.
- **View** - thresholds from the current map view, recomputed after every pan or zoom (like
  "Updated canvas" in QGIS). Useful for SAR scenes that combine dark water and bright built-up
  areas: the part you are looking at gets full contrast, while all of its tiles share the same
  thresholds, so no seams appear. The lock **freezes** the thresholds of the current view, and
  a badge in the map corner reminds you that the stretch comes from the view. The choice is
  remembered per project.

SAR scenes open with the `1–99.8%` preset and neutral brightness, contrast and gamma - the upper
threshold keeps bright targets (vehicles, buildings, ships) from burning out to white. Display
settings **do not change the data** - they affect only what you see; the dataset and AI tools
use their own processing.

## Coordinates (`GEO` scenes only)

On georeferenced scenes the labeling window shows coordinates and lets you work with
them. These functions are available for `GEO` scenes only - on scenes without
georeferencing they do not appear.

- **Cursor readout** - a small box in the bottom left corner of the map continuously
  shows the cursor position in **MGRS** and in decimal degrees (`lat, lon`).
- **Right-click → copy coordinates** - the right mouse button on the scene opens a menu
  with three formats for the clipboard:
    - **Copy MGRS** - the MGRS reference alone (for example `34U EC 00833 86587`);
    - **Copy lat, lon** - decimal degrees;
    - **Copy coordinates + ID** - MGRS + `lat, lon` + the scene identifier on one line.
- **Go to MGRS or lat, lon** - in the same box you type **MGRS or `lat, lon`** (the
  format is detected automatically) and jump to the point; the place you land on is
  highlighted briefly.

!!! tip "Why an analyst wants this"

    When you are unsure about an object in one particular place, copy **coordinates +
    ID** and paste it to the manager - they get the MGRS, the degrees and the scene
    identifier at once, so they land on exactly the same place with "go to".

!!! info "A guard against a wrong jump"

    Coordinates outside the area of the scene are usually a mistake, so "go to" warns
    first and does not jump far outside the scene. If you really do want to go there,
    confirm with **Show anyway**. An invalid format is reported immediately.

## Automatically computed attributes

For every annotation on a georeferenced scene, the dimensions, area, aspect ratio and
azimuth are computed. They are computed in meters regardless of the coordinate system
of the scene, so values from different vendors are comparable.

## Tracking progress

On large scenes it is impossible to remember what has already been looked at. That is
what the [review grid](siatka-przegladu.md) is for - it lets you record the fragments
you checked, including the ones with no objects in them.

## Quality rules

- outline the object **tightly**, without a margin of slack;
- keep to one interpretation of a class throughout the project;
- mark objects partly visible at the edge of a scene the way the team agreed - and do it
  consistently;
- when you are not sure about a class, it is better to leave the object unlabeled and
  raise the doubt than to guess.

!!! warning "Inconsistency does more harm than gaps"

    A model learns from what it is given. The same object labeled once and skipped the
    next time is a contradictory signal to it - worse than consistently skipping a whole
    category.

## AI tools

Prediction, SAM and exemplar matching produce **proposals**, not annotations. A proposal
becomes an annotation only once it is accepted. See
[AI tools](../ai-assistance/index.md).
