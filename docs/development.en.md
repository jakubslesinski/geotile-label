# Development

This page is for anyone who wants to build GeoTile Label from source, run its checks, or
change its code. It is self-contained for those tasks: everything needed to compile, test
and produce an installer is here.

[`README_dev.md`](https://github.com/jakubslesinski/geotile-label/blob/main/README_dev.md)
in the repository root is the exhaustive reference - it goes down to resolver internals and
per-vendor product rules. It is written in Polish.

## What is in the repository

| Directory | Contents |
| --- | --- |
| `frontend/` | React 19, TypeScript, Vite, Chakra UI, Leaflet, ApexCharts |
| `frontend/src-tauri/` | the Tauri v2 shell: backend lifecycle, diagnostics, the NSIS installer |
| `backend/` | FastAPI, rasterio/GDAL, pyarrow, tiling, dataset runs, exports, optional YOLO |
| `scripts/` | runtime preparation, release build, smoke tests, documentation tooling |
| `docs/` | this documentation; it is also bundled into the application as offline help |
| `validation/` | the evidence suite behind the published claims |
| `importers/` | parsers for the benchmark datasets used by the evidence suite |

Tauri starts the backend on a random `127.0.0.1` port, generates a session token and hands
the frontend `{ baseUrl, token, capabilities }`. The frontend sends the token in the
`X-GeoTile-Token` header; tile URLs carry it in the query string.

The interactive [architecture explorer](architecture/index.md) shows the same structure as
a pipeline and a data model, with links from each element into the code.

## The invariants

```text
full scene + metadata + georeferencing + source annotations = canonical data
tiles + YOLO/COCO/VOC + ZIP                                 = versioned derived products
```

- the full scene and its annotations are the record of truth;
- every scene, annotation, tile and dataset version has a stable identifier;
- `dataset_runs/<run_id>/` is the durable result of a generation run;
- `dataset/` remains a compatibility cache of the last successful run;
- exports carry provenance in manifests, CSV, Parquet and GeoParquet;
- context data is versioned separately from canonical annotations.

The engineering decisions that code comments cite by stage id are collected in
[`DESIGN_DECISIONS.md`](https://github.com/jakubslesinski/geotile-label/blob/main/DESIGN_DECISIONS.md),
including the ones that record what the software deliberately does *not* do.

## Requirements

- Windows 10 or 11, x64;
- Node.js 18+;
- Python 3.11+;
- Rust stable with Cargo;
- Microsoft C++ Build Tools, required by Tauri;
- Miniconda or Anaconda with the `conda` command - needed **only** to build the packed
  runtime and the installer.

Frontend changes need neither a Tauri build nor conda-pack. Run the full desktop build
before an integration test or a release, not on every iteration.

!!! tip "Restrictive PowerShell policies"

    Use `npm.cmd` instead of `npm`, and start scripts with `-ExecutionPolicy Bypass`.

## Running it in development

### Backend

```powershell
cd <repository>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

!!! warning "pip alone will not give you a working geospatial stack"

    `pip install -e .` **does not install GDAL with the JP2 driver** on Windows - the pip
    wheels do not carry the complete set of native libraries. The reproducible environment
    with pinned versions is `environment.yml` in the repository root:

    ```powershell
    conda env create -f environment.yml -p .\.venv-backend
    conda run -p .\.venv-backend python -m pip install -e ".[dev]"
    conda run -p .\.venv-backend python -m pytest backend/tests
    ```

`pyproject.toml` declares three extras: `yolo` (torch and ultralytics), `sam3` (the text
encoder) and `dev` (pytest). **Without the `dev` extra, 37 of the 76 backend test files
will not run** - the runtime packed with the application does not contain pytest.

The backend imports `torch` and `ultralytics` lazily, so it starts correctly without them
and `/api/capabilities` reports what is actually available.

### Frontend in a browser

Start the backend on `127.0.0.1:8000`, then:

```powershell
cd <repository>\frontend
npm.cmd install
npm.cmd run dev
```

To point the dev proxy elsewhere, set `VITE_BACKEND_DEV_URL` before `npm.cmd run dev`.

Browser mode falls back to substitutes for the native dialogs. The system file picker,
opening a folder and *Save ZIP as…* have to be verified in Tauri.

### Tauri dev

```powershell
cd <repository>\frontend
npm.cmd run tauri:dev
```

The backend interpreter is resolved in this order:

1. `GEOTILE_BACKEND_PYTHON`;
2. `backend\.venv\Scripts\python.exe`;
3. `python` from `PATH`.

`tauri:dev` does not build an installer and does not re-run conda-pack. React and Vite
changes refresh live; backend changes need the Tauri process restarted.

## Build the packed runtime first

!!! danger "This is the trap on a fresh clone"

    **Before the first `cargo` or `tauri` command, build the backend runtime.**
    `tauri.conf.json` declares `resources/backend-env.tar.gz.*` as a bundled resource, and
    the parts of that archive - the packed Python environment, about 800 MB - are
    deliberately kept out of the repository by `.gitignore`. A resource glob that matches
    nothing aborts the Tauri build script with **exit code 101 before anything is
    compiled**, so the message does not look like a missing file:

    ```text
    error: failed to run custom build command for `geotile-label-desktop`
      glob pattern resources/backend-env.tar.gz.* path not found or didn't match any files.
    ```

    ```powershell
    powershell -ExecutionPolicy Bypass -File .\scripts\build-backend-env.ps1
    ```

    Only after that do `cargo check`, `cargo build` and `npm run desktop:build` have
    anything to work with. `build-release-desktop.ps1` performs this step itself, so the
    warning applies to manual cargo and tauri invocations.

That script resolves the newest compatible dependency versions rather than installing a
pinned set, so it records what it actually installed in `runtime-lock.txt` - conda
packages with their full URLs, pip packages with their versions. **That file, not
`environment.yml`, is what tells you exactly what a released installer contains.**

## Checks

| Check | Command | What it covers |
| --- | --- | --- |
| Frontend | `npm.cmd run build` in `frontend/` | `tsc -b` plus the production Vite build |
| Backend | `python -m pytest backend/tests` | needs the `dev` extra |
| Backend, quick | `python -m compileall .` in `backend/` | syntax only |
| Rust | `cargo check` in `frontend\src-tauri\` | needs the packed runtime, see above |
| Documentation | `scripts\build-docs.ps1` | must build with no dead-link warnings |
| Documentation, full | `scripts\validate-docs.ps1` | strict mode, offline assets, search, version, diagram drift |
| Installer | `scripts\build-release-desktop.ps1` | the only proof the tree is complete |

Run the checks closest to what you changed first. The full NSIS build is the final
validation, not part of every iteration.

For a live documentation preview use `scripts\serve-docs.ps1`; adding `-LiveEdit` lets you
edit the Markdown in the browser. That mode writes straight into `docs/` and can rename and
delete files, so the server is deliberately bound to `127.0.0.1`. The explicit `nav:` in
`mkdocs.yml` is not updated automatically when a page is added, renamed or removed.

## The architecture diagrams are generated

`scripts\validate-docs.ps1` first runs the **diagram drift gate**
(`scripts/check-architecture-model.py`) and stops before building anything if it fails.

The single source is `docs/architecture/architecture-model.json`. Both
`docs/architecture/explorer.html` and the `.d2` files under `docs/architecture/src/` are
**generated** - editing them by hand is lost on the next generation. Change the model (or
`scripts/templates/explorer.template.html`), then:

```powershell
python .\scripts\generate-architecture-explorer.py
python .\scripts\generate-architecture-d2.py
```

The gate can be run on its own; it needs only the standard library:

```powershell
python .\scripts\check-architecture-model.py
```

It also compares the model against the figure exports used in the article, when the
article repository sits next to this one. When it does not, the figures are **skipped**
rather than reported as an error; `GEOTILE_PAPER_ROOT` points at that repository.

## Building the installer

A quick development build:

```powershell
cd <repository>\frontend
npm.cmd run desktop:build
```

The installer lands in `frontend\src-tauri\target\release\bundle\nsis\`. Only the NSIS
`.exe` is built; MSI is not part of the workflow.

A release build:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-release-desktop.ps1 -Version 1.4.0
```

The script synchronizes the version across npm, Cargo and Tauri; prepares the backend and
the base runtime (CPU-only torch); runs the smoke tests **against the packed runtime**;
builds NSIS; and produces `release/GeoTileLabel-<version>` with the installer,
`release_manifest.json` and `SHA256SUMS.txt`.

- `-ReuseBackendRuntime` skips rebuilding the packed environment. Do not use it after
  changing `backend/pyproject.toml` or `scripts/build-backend-env.ps1`.
- `-SkipSmokeTests` is for diagnosing the build process locally, never for a release.

Building the runtime needs access to `repo.anaconda.com`, conda-forge, PyPI and
`download.pytorch.org`. `CondaHTTPError: HTTP 000 CONNECTION FAILED` is a network, DNS,
VPN/proxy or domain-blocking problem, not an application error; rerun the same command once
connectivity is back.

### Editions

`-Lite` sets `VITE_GEOTILE_EDITION=lite` for the duration of the build. Vite injects it
into `import.meta.env`, `frontend/src/config/edition.ts` reads it, and three project tabs -
**Dataset analysis, Training and Results** - are hidden. It is a user-interface change
only: the backend and the endpoints are identical in both editions.

## Environment variables

Tauri sets at least:

```text
DATA_DIR=%APPDATA%\GeoTileLabel\data
MODELS_ROOT=%APPDATA%\GeoTileLabel\data\models
GEOTILE_DESKTOP=1
GEOTILE_BUILD_VARIANT=yolo
GEOTILE_ENABLE_YOLO=1
GEOTILE_AUTH_TOKEN=<session token>
```

Development variables, used by the smoke tests - never set them in a build distributed to
users:

```text
GEOTILE_TRAIN_MOCK=1          # the worker simulates training instead of calling ultralytics
GEOTILE_SAM_MOCK=1            # SAM returns a deterministic mask
GEOTILE_SAR_EXEMPLAR_MOCK=1   # exemplar matching without a model
```

`MODELS_ROOT` is not a development variable - Tauri sets it. Override it only in tests, so
that test weights do not mix with real ones.

The Python environment with `torch`, `ultralytics`, `psutil` and `cv2` used by the smoke
tests is `.desktop-build/backend-env/python.exe`, not the system Python.

## Release readiness

- `npm.cmd run build` passes with no TypeScript errors;
- `cargo check` passes;
- the NSIS build completes;
- the backend starts without a system Python;
- EO/SAR, GEO/NO GEO and 8/16-bit GeoTIFF scenes all work;
- axis-aligned and rotated boxes, tiling and review all work;
- generation, statistics, audit and export of a selected run all pass;
- the ZIP carries manifests, checksums, Parquet/GeoParquet and the reports;
- backup, import and diagnostics have been checked;
- the application reports `yolo=true` and prediction works on a test model.

## Where to look next

- [Architecture](architecture/index.md) - the interactive pipeline and data model
- `README_dev.md` - the full developer reference, in Polish
- `DESIGN_DECISIONS.md` - the decisions cited from code comments
- `validation/` - the evidence suite and how to rerun it
