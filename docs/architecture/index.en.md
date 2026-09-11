# Architecture

An interactive overview of the GeoTile Label architecture: **the processing pipeline** -
from the source data, through the canonical set, to export - and **the data model**
(entities and relations). Click the elements, switch views and use the search; every
element links to the documentation. The links to the code are active only when a
repository address has been configured for the documentation build.

[Open the interactive explorer :material-open-in-new:](explorer.html){ .md-button .md-button--primary }

## What it contains

- **Pipeline** - seven stages with the canonical set at the center; it covers durable
  jobs, progressive import, overviews, artifact caches and adaptive training; clicking a
  stage shows its elements, its documentation and its source files.
- **Data model** - the canonical layer versus the derived one, entities and relations with
  their cardinalities; clicking an entity shows its attributes and links (with the
  relations highlighted).
- **Cross-linking** - from a pipeline stage you can jump to a model entity and back.
- A search across both diagrams, a light/dark theme, and deep links (`#p:…`, `#m:…`).

!!! note "The same model in two forms"

    The explorer renders the same schemas that go into the publication in their static form
    (PDF/SVG). The interactive version serves to understand how it works and to navigate the
    code.

!!! info "Links to the code"

    The packaged offline documentation does not assume a public repository, which is why
    unconfigured "Source" links are inactive rather than leading to a placeholder address. In
    an internal release, `REPO` and `REF` are set in the
    `scripts/templates/explorer.template.html` template and the page is generated with
    `python scripts/generate-architecture-explorer.py` - `explorer.html` itself is a
    generated file and manual changes to it are lost on the next generation.
