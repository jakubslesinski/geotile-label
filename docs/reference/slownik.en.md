# Glossary

Terms used in the application and in this documentation. Abbreviations and
alternative names are given in parentheses where you are likely to meet them in
metadata, ML formats or vendor material.

## Source data and scenes

Scene
:   The logical image product of one acquisition, **not a single file**. It may
    consist of several parts and carry its own vendor metadata.

Vendor package
:   A folder holding the products of one scene exactly as the vendor delivered it.
    The application reads it read-only and never modifies it.

Working product
:   The file selected from a package for labeling - for example GRD for ICEYE or
    GEC for Capella. The full list is in
    [Default vendor products](produkty-dostawcow.md).

Working view
:   A form of the scene prepared for labeling when the product cannot be labeled
    directly - for example parts joined into a VRT, or pansharpening. It never
    changes the source pixels.

Variant
:   The identifier of one specific product choice and set of preparation parameters.
    Once labeling starts, the variant of a scene is locked.

Source
:   A folder of packages from one vendor connected to the project. A project can
    have several sources as long as they share its modality.

Relinking
:   Pointing the project at a source folder again after the data has moved, without
    losing scenes or annotations. See
    [Relink a source](../projects/relinkuj-zrodlo.md).

Modality
:   `EO` - electro-optical imagery, `SAR` - radar imagery. Set in the project
    profile and shared by all of its sources.

GEO / NO GEO
:   A scene with or without georeferencing. `NO GEO` scenes carry annotations in
    pixels only and do not reach geographic layers on export.

GSD (ground sample distance)
:   The ground size of one pixel, usually in meters.

CRS (coordinate reference system)
:   The coordinate system of the scene, for example `EPSG:4326` (degrees) or a UTM
    zone (meters).

Nodata
:   The value that marks the absence of data. Geocoded products often have a wide
    nodata border when the footprint of the acquisition is rotated with respect to
    the coordinate system.

VRT
:   A virtual raster that joins several files without copying data.

COG (cloud optimized GeoTIFF)
:   A GeoTIFF laid out so that reading fragments of it is fast.

## Annotations

Canonical annotation
:   An annotation stored on the full scene - **the record of truth**. Labels in
    tiles and in export files are derived from it.

Axis-aligned box (bounding box, bbox)
:   A rectangle whose sides are parallel to the image axes.

Oriented box (oriented bounding box, OBB)
:   A rectangle rotated to the shape of the object, with the heading preserved.

AI proposal
:   The output of prediction, SAM or exemplar matching, which is **not an annotation
    yet**. It becomes one only once it is accepted.

Annotation owner
:   The person an annotation belongs to in a collective project. Ownership changes
    when work is handed over - unlike authorship, which stays fixed.

Computed attributes
:   Dimensions, area, aspect ratio and azimuth derived automatically from the
    geometry and the georeferencing of the scene. They are computed in meters
    regardless of the coordinate system.

## Grid and datasets

Review grid
:   A regular subdivision of a scene used to keep track of what has already been
    checked. It does not itself create training images. See
    [Review grid](../annotation/siatka-przegladu.md).

Reviewed cell
:   A fragment of a scene that has deliberately been looked at - including when no
    object was found in it.

Excluded cell
:   A fragment skipped on purpose, for example cloud, missing data or an area
    outside the scope of the task.

Tile catalog
:   The stored grid geometry together with annotation links and review state. It
    **contains no images** - previews are produced on demand.

Tile
:   A crop of a scene of a given size, forming a single training image.

Overlap
:   The amount by which neighboring tiles overlap. It reduces the risk of cutting
    an object at a tile boundary.

Dataset run
:   An immutable version of a dataset: one specific configuration, preprocessing,
    split and set of generated artifacts. It can be reproduced and compared with
    another.

Dataset publication
:   Marking a version as production, which makes it available for training.

Split (train / val / test)
:   The division into training, validation and test sets.

Spatial leakage
:   The situation in which the same ground area lands in both the training and the
    test set, which inflates the result.

Negative example (negative)
:   A tile without objects, deliberately included in the dataset so that the model
    also learns what not to mark.

## Teamwork

Project role
:   The setting that decides whether a project is used for labeling or for merging
    and reviewing the work of a team.

Package scope
:   The "scene × owner" pair within which an import replaces annotations wholesale.
    This is what lets corrections and deletions propagate as well.

Supersedes chain
:   The declaration of which earlier package a new one replaces. Without it, a
    second package for the same scene is rejected.

Review round
:   The number of the correction cycle. A verdict from an earlier round becomes
    stale once corrected work is sent back.

Verdict
:   The assessment of a scene issued by a manager, together with a comment. It
    carries no geometry.

## Training

Base weights
:   The starting point of training - a model trained beforehand on a large general
    dataset.

Architecture
:   The family and size of the network, for example YOLO11m-OBB. See
    [Base architectures](architektury.md).

Training from scratch
:   Learning from random initialization, without base weights. It requires a much
    larger dataset and a much longer run.

Training run
:   A single execution of training together with its configuration, metrics and
    model checkpoint.

Epoch
:   One pass over the whole training set.

Preflight
:   The check performed before training starts: device, card memory, disk space and
    data consistency. See [Preflight](../training/preflight.md).

Validation set
:   The data used during training to compare configurations and select a model.

Test set
:   Data set aside for a **single** final evaluation. Selecting a model repeatedly on
    its test result turns it into a second validation set and inflates the
    assessment.

mAP50, mAP50-95
:   Detection quality measures. `mAP50` is the more lenient one; `mAP50-95` averages
    over several matching thresholds and is more demanding.

Model registry
:   The list of models registered in the project together with their promotion
    history.

Model promotion
:   Designating the model the project uses for prediction.

Lineage
:   The link between a model and the dataset, tiles and annotation authorship it was
    built from.

## Export formats

YOLO
:   Text labels with normalized axis-aligned boxes.

YOLO OBB
:   The variant for oriented boxes: four vertices instead of a rectangle.

COCO
:   A single JSON file with images, categories and annotations.

Pascal VOC
:   One XML file per image.

GeoParquet
:   A tabular format with geometry, used to inspect annotations in GIS tools. See
    [GeoParquet in QGIS](../export/geoparquet-qgis.md).
