"""GeoTile Label — FastAPI backend."""

import importlib.util
from importlib import metadata
import os
from pathlib import Path

YOLO_ENABLED = os.environ.get("GEOTILE_ENABLE_YOLO", "1") == "1"
BUILD_VARIANT = os.environ.get("GEOTILE_BUILD_VARIANT", "yolo" if YOLO_ENABLED else "base")

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# SAM3 (i wszystko, co uzywa torch.compile) probuje kompilowac przez inductor, ktory
# wymaga kompilatora C++ (cl/MSVC) nieobecnego w runtime desktopowym → InvalidCxxCompiler.
# Wylaczamy dynamo globalnie: wszystko idzie w trybie eager (aplikacja nie polega na
# torch.compile), a SAM3 dziala poprawnie. Musi byc USTAWIONE PRZED importem torch/_dynamo.
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

# Sceny to zaufane, lokalne pliki użytkownika, a benchmarki (DOTA/FAIR1M) miewają obrazy
# rzędu 28k×28k px. Wyłączamy anty-DoS limit PIL globalnie, żeby nie rzucał
# DecompressionBombError; miniatury i tak czytamy zdecymowanym odczytem GDAL.
try:
    from PIL import Image as _PILImage

    _PILImage.MAX_IMAGE_PIXELS = None
except Exception:  # pragma: no cover — brak PIL nie może zablokować startu
    pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from routers import projects, scenes, annotations, classes, tiling, dataset, export, browse, annotation_workflow, diagnostics, scene_import, analysis, jobs

try:
    from routers import predictions
except Exception as _e:
    predictions = None  # type: ignore[assignment]
    import logging
    logging.getLogger(__name__).warning("Predictions router not available: %s", _e)

try:
    from routers import assist
except Exception as _e:
    assist = None  # type: ignore[assignment]
    import logging
    logging.getLogger(__name__).warning("Assist router not available: %s", _e)

try:
    from routers import training
except Exception as _e:
    training = None  # type: ignore[assignment]
    import logging
    logging.getLogger(__name__).warning("Training router not available: %s", _e)

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent.parent / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
AUTH_TOKEN = os.environ.get("GEOTILE_AUTH_TOKEN", "")

app = FastAPI(title="GeoTile Label API", version="0.2.0", redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def local_auth_middleware(request: Request, call_next):
    if not AUTH_TOKEN:
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or path == "/api/health":
        return await call_next(request)

    if path.startswith("/api"):
        supplied = request.headers.get("X-GeoTile-Token") or request.query_params.get("token")
        if supplied != AUTH_TOKEN:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    return await call_next(request)


app.include_router(browse.router, prefix="/api/browse", tags=["browse"])
app.include_router(diagnostics.router, prefix="/api/diagnostics", tags=["diagnostics"])
app.include_router(scene_import.router, prefix="/api", tags=["scene-import"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(jobs.router, prefix="/api/projects/{project_id}/jobs", tags=["jobs"])
app.include_router(scenes.router, prefix="/api/projects/{project_id}/scenes", tags=["scenes"])
app.include_router(annotations.router, prefix="/api/projects/{project_id}/scenes/{scene_id}/annotations", tags=["annotations"])
app.include_router(classes.router, prefix="/api/projects/{project_id}/classes", tags=["classes"])
app.include_router(tiling.router, prefix="/api/projects/{project_id}/tiling", tags=["tiling"])
app.include_router(dataset.router, prefix="/api/projects/{project_id}/dataset", tags=["dataset"])
app.include_router(export.router, prefix="/api/projects/{project_id}/export", tags=["export"])
app.include_router(analysis.router, prefix="/api/projects/{project_id}/analysis", tags=["analysis"])
app.include_router(
    annotation_workflow.router,
    prefix="/api/projects/{project_id}",
    tags=["annotation-workflow"],
)
if predictions is not None:
    app.include_router(predictions.router, prefix="/api/projects/{project_id}/predictions", tags=["predictions"])
if assist is not None:
    app.include_router(assist.router, prefix="/api/projects/{project_id}/scenes/{scene_id}/assist", tags=["assist"])
if training is not None:
    app.include_router(training.router, prefix="/api/projects/{project_id}/training", tags=["training"])
# Serve project data files (thumbnails, tiles, etc.)
app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")


@app.on_event("startup")
def cleanup_scene_import_runtime():
    from services.archive_io import cleanup_partial_archives
    from services.dataset_runs import cleanup_stale_dataset_run_partials
    from services.scene_packages.scan_cache import invalidate_legacy_scan_caches
    from services.scene_packages.working_view import cleanup_partial_scene_products

    cleanup_partial_archives()
    cleanup_stale_dataset_run_partials()
    cleanup_partial_scene_products()
    invalidate_legacy_scan_caches()
    from services.jobs.scheduler import get_scheduler

    get_scheduler().start()


@app.on_event("shutdown")
def stop_job_scheduler():
    from services.jobs.scheduler import get_scheduler

    get_scheduler().stop()


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/capabilities")
async def capabilities():
    from services.scene_packages.contracts import graph_v2_enabled

    true_values = {"1", "true", "yes", "on"}

    def feature_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        return default if raw is None else raw.strip().lower() in true_values

    release_feature_flags = {
        "GEOTILE_SCENE_PACKAGE_GRAPH_V2": graph_v2_enabled(),
        "GEOTILE_JSON_INDEX_V2": feature_flag("GEOTILE_JSON_INDEX_V2", False),
        "GEOTILE_JSON_INDEX_V2_DELTA": feature_flag(
            "GEOTILE_JSON_INDEX_V2_DELTA", True
        ),
        "GEOTILE_TRAINING_DATASET_CACHE": feature_flag(
            "GEOTILE_TRAINING_DATASET_CACHE", False
        ),
        "GEOTILE_CATALOG_SNAPSHOT": feature_flag("GEOTILE_CATALOG_SNAPSHOT", True),
        "GEOTILE_SCENE_IMPORT_COMMON_JOBS": feature_flag(
            "GEOTILE_SCENE_IMPORT_COMMON_JOBS", True
        ),
    }
    rasterio_available = importlib.util.find_spec("rasterio") is not None
    ultralytics_available = importlib.util.find_spec("ultralytics") is not None
    torch_available = importlib.util.find_spec("torch") is not None
    yolo_available = YOLO_ENABLED and ultralytics_available and torch_available
    torch_version = None
    ultralytics_version = None
    cuda_available = False
    device = "cpu"
    if yolo_available:
        try:
            torch_version = metadata.version("torch")
            import torch
            from services.predictor import resolve_device

            cuda_available = bool(torch.cuda.is_available())
            device = resolve_device()
        except Exception:
            cuda_available = False
            device = "cpu"
    if ultralytics_available:
        try:
            ultralytics_version = metadata.version("ultralytics")
        except Exception:
            ultralytics_version = None
    jp2_available = False
    if rasterio_available:
        try:
            import rasterio

            with rasterio.Env() as env:
                jp2_available = "JP2OpenJPEG" in env.drivers()
        except Exception:
            jp2_available = False
    return {
        "desktop": os.environ.get("GEOTILE_DESKTOP") == "1",
        "build_variant": BUILD_VARIANT,
        "scene_package_graph_v2": graph_v2_enabled(),
        "feature_flags": release_feature_flags,
        "yolo": yolo_available,
        "rasterio": rasterio_available,
        "jp2": jp2_available,
        "torch": torch_version,
        "ultralytics": ultralytics_version,
        "cuda_available": cuda_available,
        "device": device,
        "sam": _sam_capabilities(),
        "sar_exemplar": _sar_exemplar_capabilities(),
        "training": _training_capabilities(yolo_available, cuda_available),
    }


def _training_capabilities(yolo_available: bool, cuda_available: bool) -> dict:
    """Whether training is usable, and why not when it is not.

    The base installation ships CPU torch, so the usual reason is a missing GPU
    runtime rather than a missing feature — the message names the remedy instead of
    leaving the UI silently greyed out.
    """
    if not yolo_available:
        return {
            "available": False,
            "reason": "The ML stack (torch/ultralytics) is unavailable in this runtime.",
        }
    try:
        from services.training_models import list_base_models

        catalog = list_base_models()
        if not any(item["available"] for item in catalog["models"]):
            return {
                "available": False,
                "reason": (
                    f"No base weights in {catalog['directory']}. "
                    "Prepare them with scripts/fetch-base-models.ps1."
                ),
            }
    except Exception as exc:
        return {"available": False, "reason": f"Base model registry unavailable: {exc}"}

    if not cuda_available:
        return {
            "available": True,
            "cpu_fallback": True,
            "device": "cpu",
            "gpu_name": None,
            "vram_free_gb": None,
            "vram_total_gb": None,
            "reason": (
                "No GPU detected. CPU training is available but may take hours to days. "
                "Install the GPU training pack for CUDA acceleration."
            ),
        }
    gpu_name = None
    vram_free_gb = None
    vram_total_gb = None
    try:
        import torch

        gpu_name = torch.cuda.get_device_name(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        vram_free_gb = round(free_bytes / (1024 ** 3), 2)
        vram_total_gb = round(total_bytes / (1024 ** 3), 2)
    except Exception:
        pass
    return {
        "available": True,
        "cpu_fallback": False,
        "device": "cuda",
        "gpu_name": gpu_name,
        "vram_free_gb": vram_free_gb,
        "vram_total_gb": vram_total_gb,
        "reason": None,
    }


def _sam_capabilities() -> dict:
    try:
        from services.sam_assist import sam_capabilities

        return sam_capabilities()
    except Exception:
        return {"mock": False, "bundled_checkpoint": None, "available": False}


def _sar_exemplar_capabilities() -> dict:
    try:
        from services.sar_exemplar_assist import sar_exemplar_capabilities

        return sar_exemplar_capabilities()
    except Exception:
        return {"mock": False, "bundled_backbone": None, "available": False}
