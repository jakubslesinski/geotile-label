# Troubleshooting

The starting point for every problem is **Settings → Diagnostics**: the state of the
service, component versions, logs and the diagnostics package export.

## Find your problem

<div class="grid cards" markdown>

-   :material-application-off: **Application does not start**

    ---

    An empty window or a message about the service.

    [Solve it](aplikacja-nie-startuje.md)

-   :material-image-off: **Scene does not open**

    ---

    Scene statuses and reading problems.

    [Solve it](scena-sie-nie-otwiera.md)

-   :material-robot-off: **Prediction is unavailable**

    ---

    A greyed-out panel, missing libraries.

    [Solve it](predykcja-niedostepna.md)

-   :material-package-variant-closed: **Package import fails**

    ---

    Rejected scenes, missing classes, a package conflict.

    [Solve it](import-paczek.md)

</div>

## The tools available

| Tool | What it is for |
| --- | --- |
| **Restart backend** | the first step for backend problems |
| **Open logs folder** | inspecting `app.log` and the service logs |
| **Export diagnostics ZIP** | material for a report |
| **Clear runtime/cache** | rebuilding the environment without losing projects |

!!! info "Clearing the runtime does not delete projects"

    Only the unpacked backend environment and the tile cache are removed. Projects,
    annotations and datasets live separately - see
    [Data locations](../reference/lokalizacje-danych.md).

## Logs

```text
%APPDATA%\GeoTileLabel\logs
```

For startup problems, begin with `backend.stderr.log`.

## Reporting a problem

Attach the ZIP from **Export diagnostics ZIP**. It contains schema versions, scene
identity state, a sanitized report of the last import and cache statistics -
**without source scenes, annotations or full geometries**.

!!! danger "Check the logs before sending them outside your organization"

    The diagnostics ZIP is sanitized, but the raw log files can contain project names,
    user paths and information about where the data is kept.

The address to send a report to, and what to include in the description, are given in
[Contact](../reference/kontakt.md).
