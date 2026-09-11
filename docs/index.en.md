# GeoTile Label

GeoTile Label is a desktop application for preparing machine-learning datasets from
satellite and airborne imagery. It preserves georeferencing, source metadata and the
provenance of every annotation.

## Choose a path

<div class="grid cards" markdown>

-   :material-account-edit: **I am an analyst**

    ---

    Start with installation, create a project and annotate scenes.

    [Getting started](getting-started/index.md)

-   :material-account-tie: **I am a dataset manager**

    ---

    Learn how analyst packages are imported, how work is reviewed and how datasets
    are built.

    [Teamwork](teamwork/index.md)

-   :material-image-multiple: **I am preparing the data**

    ---

    Check the supported formats and the rules for importing vendor packages.

    [Input data](input-data/index.md)

-   :material-lifebuoy: **I am looking for a fix**

    ---

    Go to troubleshooting, logs and the most common problems.

    [Troubleshooting](troubleshooting/index.md)

</div>

## The core principle

```text
full scenes + metadata + source annotations = the record of truth
tiles + splits + ML formats = derived products
```

Original input data stays read-only. GeoTile Label writes projects, annotations and
regenerable derived products to the project location.
