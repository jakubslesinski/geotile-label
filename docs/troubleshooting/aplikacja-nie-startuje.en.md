# Application does not start

The window opens but stays empty, or a message appears saying the service is not
running.

!!! info "The first launch after installation takes longer"

    The backend environment is being unpacked. Before deciding something is wrong, give
    the application time to finish - later starts are fast.

## Steps

Three actions are available on the start screen. Work through them in order:

1. **Restart backend** - usually enough.
2. **Open logs folder** - see what happened.
3. **Export diagnostics ZIP** - when the problem repeats.

## What is in the logs

| File | Contents |
| --- | --- |
| `app.log` | application events |
| `backend.stdout.log` | service output |
| `backend.stderr.log` | service errors - **start here** |
| runtime unpacking logs | first-launch problems |

The logs are in `%APPDATA%\GeoTileLabel\logs`.

## When a restart does not help

Use **Settings → Diagnostics → Clear runtime/cache**, then start the application again.

!!! info "This operation does not delete projects"

    Only the unpacked runtime and the tile cache are removed. They are rebuilt on the
    next start. Projects, annotations and datasets live elsewhere - see
    [Data locations](../reference/lokalizacje-danych.md).

## Reporting the problem

Attach the ZIP from **Export diagnostics ZIP** to your report. It contains schema
versions, scene identity state, a sanitized report of the last import and cache
statistics.

!!! info "The diagnostics ZIP does not contain your data"

    There are no source scenes, annotations or full geometries in it - it can be handed
    over without revealing the material you are working on.
