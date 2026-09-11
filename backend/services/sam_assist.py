"""SAM click-to-box assist for EO and SAR scenes.

Click a point on a scene → SAM produces a mask → convert to an axis-aligned or
oriented box → store as an ``AssistanceProposal`` in the active SAM session.

The heavy image encoder runs on a fixed-size window defined in **working-grid
pixels** (not the Leaflet display zoom), so the same click always yields the
same input regardless of how far the user is zoomed in.

Offline testing uses ``GEOTILE_SAM_MOCK=1`` (deterministic synthetic mask); the
real backend wraps ``ultralytics.SAM`` and is validated on real data.
"""

from __future__ import annotations

import math
import os
from importlib.util import find_spec
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

WINDOW_PX = 1024  # fixed working-grid window side around the click
WINDOW_SNAP = 256  # snap the window origin so clustered clicks share an encode
ENCODE_CACHE_SIZE = 3  # primed windows kept in memory (encode-once-click-many)
SAM_PREPROCESSING_HASH = "sam-rgb-bgr-window-v2"
SAM_SAR_PREPROCESSING_HASH = "sam-sar-global-display-window-v1"


class SamAssistError(RuntimeError):
    """Raised for recoverable SAM-assist problems (bad input, no mask, ...)."""


def mock_enabled() -> bool:
    return os.environ.get("GEOTILE_SAM_MOCK", "0") == "1"


# Checkpoint filenames understood by the Ultralytics runtime bundled with the
# desktop app. Ordering also defines the automatic default (fastest first).
SAM1_MODEL_NAMES = ("mobile_sam.pt", "sam_b.pt", "sam_l.pt", "sam_h.pt")
SAM2_MODEL_NAMES = (
    "sam2_t.pt", "sam2.1_t.pt",
    "sam2_s.pt", "sam2.1_s.pt",
    "sam2_b.pt", "sam2.1_b.pt",
    "sam2_l.pt", "sam2.1_l.pt",
)
SAM3_MODEL_NAMES = ("sam3.pt",)
BUNDLED_SAM_NAMES = SAM1_MODEL_NAMES[:1] + SAM2_MODEL_NAMES + SAM3_MODEL_NAMES + SAM1_MODEL_NAMES[1:]


def sam_model_family(checkpoint: str | Path) -> str | None:
    """Return the supported interactive-SAM family inferred by checkpoint name."""
    name = Path(checkpoint).name.lower()
    if name.startswith("fastsam"):
        return "fastsam"
    if any(name.endswith(candidate) for candidate in SAM3_MODEL_NAMES):
        return "sam3"
    if any(name.endswith(candidate) for candidate in SAM2_MODEL_NAMES):
        return "sam2"
    if any(name.endswith(candidate) for candidate in SAM1_MODEL_NAMES):
        return "sam1"
    return None


def sam3_runtime_available() -> bool:
    """Czy runtime uniesie SAM3 offline: potrzebny pakiet CLIP (fork ultralytics) + ftfy/regex.

    Bez niego ultralytics przy budowie SAM3 wola `check_requirements("git+.../CLIP.git")`,
    czyli probuje doinstalowac CLIP z GitHuba przez siec — offline to zawis i mylacy
    "Network error". `find_spec` sprawdza dostepnosc BEZ importu (i jego skutkow ubocznych).
    """
    import importlib.util

    return all(importlib.util.find_spec(module) is not None for module in ("clip", "ftfy", "regex"))


SAM3_MISSING_DEPS_REASON = (
    "SAM3 requires the CLIP text encoder (not bundled in this offline runtime)"
)


def sam_model_info(checkpoint: str | Path) -> dict[str, Any]:
    """Describe a SAM file without loading several gigabytes of model state."""
    path = Path(checkpoint)
    family = sam_model_family(path)
    name = path.name
    lower_name = name.lower()
    reason = None
    supported = family is not None
    if family is None:
        if lower_name.startswith("fastsam"):
            reason = "FastSAM support is not enabled yet"
        elif path.suffix.lower() == ".pth":
            reason = "Original Meta SAM .pth checkpoints are not supported by this runtime"
        else:
            reason = "Unsupported SAM checkpoint name or architecture"
    elif family == "sam3" and not sam3_runtime_available():
        # Bramkuj SAM3 zamiast pozwolic mu odpalic sieciowy pip przy uzyciu offline.
        supported = False
        reason = SAM3_MISSING_DEPS_REASON
    stat = path.stat() if path.is_file() else None
    return {
        "name": name,
        "path": str(path),
        "size": stat.st_size if stat else 0,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat() if stat else "",
        "family": family,
        "supported": supported,
        "reason": reason,
    }


def scan_sam_models(models_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """List SAM candidates from the configured folder.

    The default application directory is created automatically. A custom
    directory is read-only and can live on an external or network drive.
    """
    sam_dir = bundled_sam_dir(models_dir)
    if models_dir is None:
        sam_dir.mkdir(parents=True, exist_ok=True)
    if not sam_dir.is_dir():
        return []
    priority = {name: index for index, name in enumerate(BUNDLED_SAM_NAMES)}
    candidates = [
        path for path in sam_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pt", ".pth"}
    ]
    candidates.sort(key=lambda path: (priority.get(path.name.lower(), len(priority)), path.name.lower()))
    return [sam_model_info(path) for path in candidates]


def bundled_sam_dir(models_dir: str | Path | None = None) -> Path:
    if models_dir:
        return Path(models_dir).expanduser().resolve(strict=False)
    from services.predictor import MODELS_ROOT

    return Path(MODELS_ROOT) / "sam"


def bundled_sam_checkpoint(models_dir: str | Path | None = None) -> str | None:
    """A SAM checkpoint shipped with / placed in the runtime, if present."""
    sam_dir = bundled_sam_dir(models_dir)
    for name in BUNDLED_SAM_NAMES:
        matches = sorted(sam_dir.rglob(name)) if sam_dir.is_dir() else []
        if matches:
            return str(matches[0])
    if sam_dir.is_dir():
        for candidate in sorted(sam_dir.rglob("*.pt")):
            if sam_model_family(candidate):
                return str(candidate)
    return None


def resolve_sam_checkpoint(
    configured: str | None,
    models_dir: str | Path | None = None,
) -> str | None:
    """Pick the SAM checkpoint: explicit config wins, else a bundled one."""
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise SamAssistError(f"SAM checkpoint not found: {configured}")
        info = sam_model_info(path)
        if not info["supported"]:
            raise SamAssistError(str(info["reason"]))
        return str(path)
    return bundled_sam_checkpoint(models_dir)


def sam_predictor_class_for_checkpoint(checkpoint: str | Path):
    """Choose the matching Ultralytics predictor instead of forcing SAM1."""
    family = sam_model_family(checkpoint)
    if family is None:
        raise SamAssistError(f"Unsupported SAM checkpoint: {Path(checkpoint).name}")
    try:
        from ultralytics.models.sam import Predictor as SAMPredictor
        from ultralytics.models.sam.predict import SAM2Predictor
    except (ImportError, ModuleNotFoundError) as exc:
        raise SamAssistError("SAM runtime is not installed in this application build") from exc
    if family == "sam3":
        if not sam3_runtime_available():
            # Zatrzymaj TU, zanim ultralytics sprobuje pip-install CLIP z sieci (zawis offline).
            raise SamAssistError(SAM3_MISSING_DEPS_REASON)
        try:
            from ultralytics.models.sam.predict import SAM3Predictor
        except (ImportError, ModuleNotFoundError) as exc:
            raise SamAssistError(
                "This application runtime does not support SAM3; update the Ultralytics runtime"
            ) from exc
        return SAM3Predictor
    return SAM2Predictor if family == "sam2" else SAMPredictor


def sam_capabilities() -> dict[str, Any]:
    mock = mock_enabled()
    checkpoint = None if mock else bundled_sam_checkpoint()
    runtime_available = find_spec("torch") is not None and find_spec("ultralytics") is not None
    reason = None
    if not mock and not runtime_available:
        reason = "runtime_missing"
    elif not mock and checkpoint is None:
        reason = "checkpoint_missing"
    return {
        "mock": mock,
        "bundled_checkpoint": checkpoint,
        "runtime_available": runtime_available,
        "available": mock or (runtime_available and checkpoint is not None),
        "reason": reason,
    }


# --- Window geometry (working-grid pixels, zoom-independent) -----------------

@dataclass(frozen=True)
class Window:
    x0: int
    y0: int
    width: int
    height: int


def compute_window(click_x: float, click_y: float, scene_w: int, scene_h: int) -> Window:
    """Fixed WINDOW_PX window around the click, origin snapped and clamped.

    The origin is snapped to a ``WINDOW_SNAP`` grid so that clicks in the same
    neighbourhood (e.g. cars in one corner of a parking lot) fall in the *same*
    window and reuse the cached image encoding. The 1024-px window keeps ample
    context, so SAM quality is unaffected by the object not being dead-centre.
    """
    side_w = min(WINDOW_PX, scene_w)
    side_h = min(WINDOW_PX, scene_h)
    raw_x = int(round(click_x)) - side_w // 2
    raw_y = int(round(click_y)) - side_h // 2
    x0 = (raw_x // WINDOW_SNAP) * WINDOW_SNAP
    y0 = (raw_y // WINDOW_SNAP) * WINDOW_SNAP
    x0 = max(0, min(x0, scene_w - side_w))
    y0 = max(0, min(y0, scene_h - side_h))
    return Window(x0, y0, side_w, side_h)


@lru_cache(maxsize=16)
def _raster_display_range(
    path_value: str,
    file_size: int,
    modified_ns: int,
) -> tuple[float | None, float | None, str | None]:
    """Cache one stable display range used by every SAR assist window."""
    del file_size, modified_ns
    import rasterio

    from services.scene_loader import _estimate_geotiff_display_range

    with rasterio.open(path_value) as src:
        return _estimate_geotiff_display_range(src)


def _sar_display_range(scene_path: Path) -> tuple[float | None, float | None, str | None]:
    try:
        stat = scene_path.stat()
        return _raster_display_range(str(scene_path), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError):
        return None, None, None


def read_rgb_window(
    scene_path: Path,
    window: Window,
    *,
    modality: str | None = None,
) -> np.ndarray:
    """Return an ``(h, w, 3)`` uint8 RGB array for an assist operation.

    SAR rasters use only their first band and one scene-wide display range.
    Therefore SAM and template matching receive consistent contrast in every
    processing block rather than independently stretched neighbouring windows.
    """
    is_sar = str(modality or "").upper() == "SAR"

    suffix = scene_path.suffix.lower()
    canvas = np.zeros((window.height, window.width, 3), dtype=np.uint8)
    if suffix in (".tif", ".tiff", ".vrt", ".jp2", ".img"):
        import rasterio
        from rasterio.windows import Window as RioWindow

        from utils.image import ensure_rgb_uint8

        with rasterio.open(scene_path) as src:
            w = min(window.width, src.width - window.x0)
            h = min(window.height, src.height - window.y0)
            if w <= 0 or h <= 0:
                return canvas
            indexes = [1] if is_sar else list(range(1, min(src.count, 3) + 1))
            data = src.read(indexes=indexes, window=RioWindow(window.x0, window.y0, w, h), masked=True)
            if hasattr(data, "filled"):
                data = data.filled(0)
            values = np.transpose(data, (1, 2, 0))
            if is_sar:
                display_min, display_max, display_mode = _sar_display_range(scene_path)
                arr = ensure_rgb_uint8(values, display_min, display_max, display_mode)
            else:
                arr = ensure_rgb_uint8(values)
        canvas[:h, :w] = arr[:h, :w]
        return canvas

    from PIL import Image

    with Image.open(scene_path) as img:
        crop = img.convert("RGB").crop((
            window.x0, window.y0,
            min(window.x0 + window.width, img.width),
            min(window.y0 + window.height, img.height),
        ))
        arr = np.asarray(crop, dtype=np.uint8)
    canvas[: arr.shape[0], : arr.shape[1]] = arr
    return canvas


# --- Mask → geometry ---------------------------------------------------------

def mask_to_bbox(mask: np.ndarray) -> list[int] | None:
    """Tight axis-aligned bbox [x0, y0, x1, y1] in mask/window coords."""
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def front_vector_from_polygon(polygon: list[list[float]] | None) -> list[float] | None:
    """Wektor przodu jako KRAWEDZ poligonu (p0 -> p1), znormalizowany, w pikselach.

    Kierunek przodu musi byc krawedzia ramki, a nie osobnym wektorem: na scenach
    niekonforemnych wektor pikselowy o tym samym kacie renderuje sie pod innym katem niz
    bok prostokata i strzalka wychodzi przekoszona wzgledem ramki. Konsument rysujacy
    strzalke i tak liczy ja z poligonu — tu zostaje wartosc pochodna, dla eksportow
    i zgodnosci wstecz.
    """
    if not polygon or len(polygon) < 2:
        return None
    dx = float(polygon[1][0]) - float(polygon[0][0])
    dy = float(polygon[1][1]) - float(polygon[0][1])
    length = math.hypot(dx, dy)
    if length <= 0:
        return None
    return [round(dx / length, 8), round(dy / length, 8)]


def default_front_vector(rotated: dict[str, float]) -> list[float]:
    """Unit front vector along the OBB long axis (direction is a guess).

    SAM's mask fixes the box *axis* but not which end is the front, so this
    picks one end; the user confirms or flips it before accept.

    Sciezka zapasowa dla scen BEZ poligonu wiernego mapie — tam piksel jest mapa, wiec
    wektor pikselowy i bok ramki maja ten sam kat. Gdy poligon jest, uzywa sie
    `front_vector_from_polygon`.
    """
    angle = float(rotated["angle_deg"])
    if float(rotated["width"]) < float(rotated["height"]):
        angle += 90.0
    rad = math.radians(angle)
    return [math.cos(rad), math.sin(rad)]


def mask_to_rotated_bbox(mask: np.ndarray) -> dict[str, float] | None:
    """Oriented rectangle (min-area) in mask/window coords.

    Returns cx/cy/width/height/angle_deg. The rectangle's *axis* is defined but
    its *front* is not — the caller must obtain the front direction from a user
    gesture before writing a trustworthy ``front_vector_scene_px``.
    """
    import cv2

    ys, xs = np.where(mask)
    if xs.size < 3:
        return None
    points = np.column_stack([xs, ys]).astype(np.float32)
    (cx, cy), (w, h), angle = cv2.minAreaRect(points)
    if w <= 0 or h <= 0:
        return None
    return {
        "cx": float(cx),
        "cy": float(cy),
        "width": float(w),
        "height": float(h),
        "angle_deg": float(angle),
    }


def fit_oriented_box(
    mask: np.ndarray,
    offset_x: float,
    offset_y: float,
    project_id: str | None = None,
    scene_id: str | None = None,
) -> tuple[dict[str, float] | None, list[list[float]] | None]:
    """Prostokat zorientowany dopasowany W PRZESTRZENI WYSWIETLANIA, gdy scena ma geo.

    DLACZEGO NIE W PIKSELACH. Widok mapy jest konforemny wzgledem elipsoidy, piksele
    sceny w EPSG:4326 nie sa. Prostokat o najmniejszym polu dopasowany w pikselach jest
    na mapie ROWNOLEGLOBOKIEM — na szerokosci 60 stopni ramka obrocona o 35 stopni traci
    tam okolo 35 stopni prostopadloscia. Uzytkownik widzi to jako „skoszona ramke z AI",
    bo ramki rysowane recznie sa prostokatami wlasnie na mapie.

    DLACZEGO PRZEZ OTOCZKE, A NIE PRZEZ KOREKTE GOTOWEGO PROSTOKATA. Przeliczenie juz
    dopasowanego prostokata pikselowego i „wyprostowanie" go na mapie zachowuje srodek,
    ale nie tresc: zmierzone na tej samej scenie ramka rosla wtedy o polowe wysokosci
    i przestawala przylegac do obiektu. Dopasowanie liczymy wiec od nowa na otoczce
    wypuklej maski przeniesionej do przestrzeni wyswietlania — wynik jest prostokatem
    na mapie I nadal najciasniejszym wokol obiektu. `minAreaRect` zalezy wylacznie od
    otoczki, wiec przenoszenie samej otoczki jest dokladne, nie przyblizone.

    Zwraca `(rotated_bbox w pikselach sceny, polygon_scene_px)`. Bez modelu geo poligon
    jest `None`, a prostokat pikselowy zostaje dokladnie taki jak dotad.
    """
    import cv2

    fallback = mask_to_rotated_bbox(mask)
    if fallback is not None:
        fallback = dict(fallback)
        fallback["cx"] += offset_x
        fallback["cy"] += offset_y
    if not project_id or not scene_id:
        return fallback, None

    from db.storage import load_scene_json
    from services.sensor_geometry import (
        SceneGeoModel,
        display_to_pixel,
        pixel_to_display,
        rotated_bbox_from_polygon_px,
    )

    try:
        model = SceneGeoModel.from_manifest(
            load_scene_json(project_id, scene_id, "scene_manifest", default={})
        )
    except Exception:
        model = None
    if model is None:
        return fallback, None

    ys, xs = np.where(mask)
    if xs.size < 3:
        return fallback, None
    try:
        hull = cv2.convexHull(np.column_stack([xs, ys]).astype(np.float32)).reshape(-1, 2)
        scene_hull = [[float(x) + offset_x, float(y) + offset_y] for x, y in hull]
        display = np.asarray(pixel_to_display(model, scene_hull), dtype=np.float64)
        # Srodek odejmujemy przed dopasowaniem: wspolrzedne Merkatora sa rzedu milionow
        # metrow, a `minAreaRect` liczy na float32 — bez tego gubimy precyzje na metrach.
        origin = display.mean(axis=0)
        rect = cv2.minAreaRect((display - origin).astype(np.float32))
        if rect[1][0] <= 0 or rect[1][1] <= 0:
            return fallback, None
        corners = [[float(x) + origin[0], float(y) + origin[1]] for x, y in cv2.boxPoints(rect)]
        # Pierwsza krawedz (p0->p1) jest PRZODEM ramki. Ustawiamy ja na dluzszym boku,
        # mierzonym w przestrzeni wyswietlania, bo domyslny przod ma biec wzdluz dluzszej
        # osi. Dzieki temu kierunek przodu jest zawsze KRAWEDZIA poligonu, a nie osobnym
        # wektorem, ktory moze sie z nim rozjechac na scenach niekonforemnych.
        first = math.dist(corners[0], corners[1])
        second = math.dist(corners[1], corners[2])
        if second > first:
            corners = corners[1:] + corners[:1]
        polygon = [
            [round(float(x), 3), round(float(y), 3)]
            for x, y in display_to_pixel(model, corners)
        ]
    except Exception:
        return fallback, None

    rotated = rotated_bbox_from_polygon_px(polygon)
    if rotated is None:
        return fallback, None
    return rotated, polygon


# --- SAM backends ------------------------------------------------------------

# Two-phase interface: `encode` runs the heavy image encoder once per window;
# `decode` is the light per-click prompt step. This is what makes
# encode-once-click-many possible — the encoded state is cached per window.

# Instrumentation for tests: counts how many times the encoder actually ran.
ENCODE_CALLS = {"count": 0}


class SamBackend:
    device_precision = "unknown"

    def encode(self, rgb: np.ndarray) -> Any:
        raise NotImplementedError

    def decode_masks(
        self,
        state: Any,
        positive_points: list[list[float]],
        negative_points: list[list[float]],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Warianty maski + oceny jakości: ``(masks (N,H,W) bool, scores (N,) float)``.

        Scory to jakość per maska z modelu (np. przewidywane IoU); ``NaN``, gdy rodzina ich
        nie udostępnia — wtedy selekcja (SB2) opiera się na samej heurystyce rozmiaru/zwartości.
        """
        raise NotImplementedError

    def decode(
        self,
        state: Any,
        positive_points: list[list[float]],
        negative_points: list[list[float]],
    ) -> np.ndarray:
        """Jedna maska boolowska — zgodność wsteczna. Wybór SB2 robi ``sam_click_proposal``
        (ma kontekst klasy/GSD); ten skrót bierze największą maskę jak dawniej."""
        masks, _ = self.decode_masks(state, positive_points, negative_points)
        if masks.shape[0] == 0:
            return np.zeros((0, 0), dtype=bool)
        areas = masks.reshape(masks.shape[0], -1).sum(axis=1)
        return masks[int(np.argmax(areas))].astype(bool)

    def text_masks(self, rgb: np.ndarray, texts: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Segmentacja warunkowana tekstem (ST): ``(masks (N,H,W) bool, scores (N,) float)``.

        Domyślnie niedostępna — wymaga SAM3 z tekstowym enkoderem (CLIP). Rodziny bez tego
        trybu zgłaszają czytelny powód, zamiast cichej awarii."""
        raise SamAssistError("Text-prompted segmentation requires a SAM3 checkpoint with CLIP")


class MockSamBackend(SamBackend):
    """Deterministic synthetic mask: a filled rotated rectangle at the click.

    Lets the whole click → encode → decode → OBB → proposal pipeline be tested
    offline without model weights.
    """

    device_precision = "mock"

    def encode(self, rgb):
        ENCODE_CALLS["count"] += 1
        return {"shape": rgb.shape[:2]}

    def decode_masks(self, state, positive_points, negative_points):
        import cv2

        h, w = state["shape"]
        mask = np.zeros((h, w), dtype=np.uint8)
        if not positive_points:
            return np.zeros((0, h, w), dtype=bool), np.zeros((0,), dtype=float)
        cx, cy = positive_points[0]
        rect = ((float(cx), float(cy)), (120.0, 60.0), 30.0)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.fillPoly(mask, [box], 1)
        return mask.astype(bool)[None, ...], np.array([1.0], dtype=float)

    def text_masks(self, rgb, texts):
        # Deterministyczne, syntetyczne instancje w oknie — pozwala testowac endpoint /sam/text
        # i przeglad propozycji offline, bez wag SAM3.
        import cv2

        h, w = rgb.shape[:2]
        centers = [(w * 0.30, h * 0.35, 0.86), (w * 0.62, h * 0.55, 0.71), (w * 0.45, h * 0.72, 0.58)]
        masks, scores = [], []
        for cx, cy, score in centers:
            mask = np.zeros((h, w), dtype=np.uint8)
            rect = ((float(cx), float(cy)), (90.0, 50.0), 20.0)
            cv2.fillPoly(mask, [cv2.boxPoints(rect).astype(np.int32)], 1)
            masks.append(mask.astype(bool))
            scores.append(score)
        return np.stack(masks), np.array(scores, dtype=float)


@dataclass(frozen=True)
class UltralyticsEncodedState:
    features: Any
    source_bgr: np.ndarray


class UltralyticsSamBackend(SamBackend):
    """Real backend wrapping ``ultralytics`` SAM predictor (validated on data).

    ``encode`` primes the predictor with ``set_image`` (heavy, once per window);
    ``decode`` runs prompt points (light, per click).
    """

    device_precision = "fp32"

    def __init__(self, checkpoint: str, device: str):
        try:
            predictor_class = sam_predictor_class_for_checkpoint(checkpoint)
            # SAM3 idzie w trybie eager — torch.compile jest wylaczony globalnie przez
            # TORCHDYNAMO_DISABLE (main.py), bo inductor wymaga kompilatora C++ nieobecnego
            # w runtime. Belt-and-suspenders, gdyby backend byl uruchomiony inaczej:
            os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
            self._predictor = predictor_class(overrides={
                "task": "segment", "mode": "predict", "model": checkpoint,
                "device": device, "half": False, "verbose": False, "save": False,
            })
        except Exception as exc:
            raise SamAssistError(f"Could not initialize the SAM checkpoint: {exc}") from exc
        self._checkpoint = checkpoint
        self._device = device
        self._text_predictor: Any = None  # osobny predyktor semantyczny SAM3 (lazy)

    def encode(self, rgb):
        ENCODE_CALLS["count"] += 1
        # Ultralytics accepts OpenCV-style BGR ndarrays. Keep both the encoded
        # features and their source image: one predictor instance serves many
        # cache entries, so keeping only a "primed" flag would make an A -> B ->
        # A cache hit decode B's image.
        source_bgr = np.ascontiguousarray(rgb[..., ::-1])
        self._predictor.set_image(source_bgr)
        return UltralyticsEncodedState(
            features=self._predictor.features,
            source_bgr=source_bgr,
        )

    def decode_masks(self, state, positive_points, negative_points):
        if not isinstance(state, UltralyticsEncodedState):
            raise SamAssistError("Invalid cached SAM encoder state")
        points = [list(map(float, p)) for p in positive_points] + [
            list(map(float, p)) for p in negative_points
        ]
        labels = [1] * len(positive_points) + [0] * len(negative_points)
        self._predictor.features = state.features
        # multimask_output=True → obiekt / część / podczęść + pred_scores; degradujemy do
        # wywołania bez tego argumentu, gdy dana wersja ultralytics go nie przyjmuje (SB1).
        try:
            results = self._predictor(
                source=state.source_bgr, points=points, labels=labels, multimask_output=True
            )
        except TypeError:
            results = self._predictor(source=state.source_bgr, points=points, labels=labels)
        result = results[0]
        if result.masks is None or len(result.masks.data) == 0:
            return np.zeros((0, 0, 0), dtype=bool), np.zeros((0,), dtype=float)
        masks = result.masks.data.detach().cpu().numpy().astype(bool)
        return masks, _extract_mask_scores(result, masks.shape[0])

    def text_masks(self, rgb, texts):
        # SAM3 tekstowy to OSOBNY predyktor semantyczny (grounding), nie interaktywny klik-SAM.
        # Prompt tekstowy idzie przez `set_prompts({"text": [...]})` -> `forward_grounding`.
        # Prog pewnosci trzymamy nisko (conf=0.01) i nakladamy wlasciwy w sam_text_proposals.
        if sam_model_family(self._checkpoint) != "sam3":
            raise SamAssistError("Text-prompted segmentation requires a SAM3 checkpoint with CLIP")
        predictor = self._text_predictor
        if predictor is None:
            try:
                from ultralytics.models.sam.predict import SAM3SemanticPredictor
            except Exception as exc:  # noqa: BLE001
                raise SamAssistError(
                    "SAM3 text mode is unavailable in this runtime (semantic predictor missing)"
                ) from exc
            os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
            try:
                predictor = SAM3SemanticPredictor(overrides={
                    "task": "segment", "mode": "predict", "model": self._checkpoint,
                    "device": self._device, "half": False, "verbose": False, "save": False,
                    "conf": 0.01,
                })
            except Exception as exc:
                raise SamAssistError(f"Could not initialize the SAM3 text predictor: {exc}") from exc
            self._text_predictor = predictor
        source_bgr = np.ascontiguousarray(rgb[..., ::-1])
        predictor.set_prompts({"text": list(texts)})
        try:
            results = predictor(source=source_bgr)
        except Exception as exc:  # noqa: BLE001
            raise SamAssistError(f"SAM3 text inference failed: {exc}") from exc
        result = results[0]
        if result.masks is None or len(result.masks.data) == 0:
            return np.zeros((0, 0, 0), dtype=bool), np.zeros((0,), dtype=float)
        masks = result.masks.data.detach().cpu().numpy().astype(bool)
        return masks, _extract_mask_scores(result, masks.shape[0])


@dataclass(frozen=True)
class FastSamEncodedState:
    results: Any


class FastSamBackend(SamBackend):
    """FastSAM (YOLOv8-seg) — segmentuje cala scene raz, potem klik wybiera maske.

    Inny model niz SAM (szybszy, mniej precyzyjny), ale mapuje sie na ten sam kontrakt:
    ``encode`` uruchamia segmentacje okna (ciezkie, raz), ``decode`` wybiera maske pod
    klikiem promptem punktowym (lekkie). Nie wymaga CLIP ani dodatkowych pobran.
    """

    device_precision = "fp32"

    def __init__(self, checkpoint: str, device: str):
        try:
            from ultralytics.models.fastsam import FastSAMPredictor
        except (ImportError, ModuleNotFoundError) as exc:
            raise SamAssistError("FastSAM runtime is not installed in this application build") from exc
        try:
            self._predictor = FastSAMPredictor(overrides={
                "task": "segment", "mode": "predict", "model": checkpoint,
                "device": device, "verbose": False, "save": False, "retina_masks": True,
            })
        except Exception as exc:
            raise SamAssistError(f"Could not initialize the FastSAM checkpoint: {exc}") from exc

    def encode(self, rgb):
        ENCODE_CALLS["count"] += 1
        source_bgr = np.ascontiguousarray(rgb[..., ::-1])
        results = self._predictor(source=source_bgr)  # segmentacja calosci okna, raz
        return FastSamEncodedState(results=results)

    def decode_masks(self, state, positive_points, negative_points):
        if not isinstance(state, FastSamEncodedState):
            raise SamAssistError("Invalid cached FastSAM encoder state")
        points = [list(map(float, p)) for p in positive_points] + [
            list(map(float, p)) for p in negative_points
        ]
        if not points:
            return np.zeros((0, 0, 0), dtype=bool), np.zeros((0,), dtype=float)
        labels = [1] * len(positive_points) + [0] * len(negative_points)
        prompted = self._predictor.prompt(state.results, points=points, labels=labels)
        result = prompted[0] if isinstance(prompted, (list, tuple)) else prompted
        if result.masks is None or len(result.masks.data) == 0:
            return np.zeros((0, 0, 0), dtype=bool), np.zeros((0,), dtype=float)
        masks = result.masks.data.detach().cpu().numpy().astype(bool)
        return masks, _extract_mask_scores(result, masks.shape[0])


_BACKEND_CACHE: dict[str, SamBackend] = {}
# Canonical-key → (backend, encoded state). One primed window per key.
_ENCODE_CACHE: "OrderedDict[str, tuple[SamBackend, Any]]" = OrderedDict()


def get_backend(checkpoint: str | None, device: str) -> SamBackend:
    if mock_enabled():
        return _BACKEND_CACHE.setdefault("mock", MockSamBackend())
    if not checkpoint:
        raise SamAssistError("No SAM checkpoint configured")
    key = f"{checkpoint}|{device}"
    backend = _BACKEND_CACHE.get(key)
    if backend is None:
        if sam_model_family(checkpoint) == "fastsam":
            backend = FastSamBackend(checkpoint, device)
        else:
            backend = UltralyticsSamBackend(checkpoint, device)
        _BACKEND_CACHE[key] = backend
    return backend


def encode_key(
    *,
    scene_identity: str,
    working_grid_uid: str | None,
    window: Window,
    model_sha: str | None,
    device: str,
    device_precision: str,
    preprocessing_hash: str = SAM_PREPROCESSING_HASH,
) -> str:
    """Canonical encoder-cache key (per the AI plan).

    Includes device: fp16/GPU and fp32/CPU produce different features, so an
    encode must never be reused across devices.
    """
    return "|".join(str(part) for part in [
        scene_identity, working_grid_uid,
        window.x0, window.y0, window.width, window.height,
        preprocessing_hash,
        model_sha or "none", device, device_precision,
    ])


def get_encoded_state(backend: SamBackend, key: str, rgb: np.ndarray) -> Any:
    """Return the cached encoded state for ``key`` or encode + cache it."""
    cached = _ENCODE_CACHE.get(key)
    if cached is not None and cached[0] is backend:
        _ENCODE_CACHE.move_to_end(key)
        return cached[1]
    state = backend.encode(rgb)
    _ENCODE_CACHE[key] = (backend, state)
    _ENCODE_CACHE.move_to_end(key)
    while len(_ENCODE_CACHE) > ENCODE_CACHE_SIZE:
        _ENCODE_CACHE.popitem(last=False)
    return state


def clear_encode_cache() -> None:
    _ENCODE_CACHE.clear()


# --- Orchestration -----------------------------------------------------------

def _extract_mask_scores(result: Any, n: int) -> np.ndarray:
    """Oceny jakości masek z wyniku ultralytics; ``NaN``, gdy niedostępne (rodzina bez scorów)."""
    try:
        boxes = getattr(result, "boxes", None)
        conf = getattr(boxes, "conf", None) if boxes is not None else None
        if conf is not None:
            arr = np.asarray(conf.detach().cpu().numpy(), dtype=float).reshape(-1)
            if arr.shape[0] == n:
                return arr
    except Exception:
        pass
    return np.full(n, np.nan, dtype=float)


def _expected_area_px(project_id: str, scene_id: str, class_id: int | None) -> float | None:
    """Prior rozmiaru = mediana pola boxów **tej klasy** na scenie (px². Skala okna = skala
    sceny — okno to natywne wycięcie). ``None``, gdy brak adnotacji klasy → człon rozmiaru
    w SB2 jest neutralny."""
    if class_id is None:
        return None
    from db.storage import load_scene_json

    annotations = load_scene_json(project_id, scene_id, "annotations", default=[]) or []
    areas: list[float] = []
    for ann in annotations:
        if ann.get("class_id") != class_id or ann.get("is_negative") or not ann.get("bbox"):
            continue
        x0, y0, x1, y1 = ann["bbox"]
        w, h = abs(float(x1) - float(x0)), abs(float(y1) - float(y0))
        if w > 0 and h > 0:
            areas.append(w * h)
    return float(np.median(areas)) if areas else None


def _bbox_area_px(bbox: list[float] | None) -> float | None:
    """Pole boxa [x0,y0,x1,y1] w px², albo None gdy nieprawidłowy (prior rozmiaru SB3)."""
    if not bbox or len(bbox) < 4:
        return None
    w, h = abs(float(bbox[2]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[1]))
    return w * h if (w > 0 and h > 0) else None


def _mask_geometry(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    """(pole, pole bboxa, centroid_x, centroid_y) w px; None dla pustej maski."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    bbox_area = float((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1))
    return float(xs.size), bbox_area, float(xs.mean()), float(ys.mean())


def select_object_mask(
    masks: np.ndarray,
    scores: np.ndarray | None,
    click_xy: tuple[float, float],
    expected_area_px: float | None,
    *,
    weights: tuple[float, float, float] = (0.55, 0.25, 0.20),
    oversize_reject: float = 4.0,
) -> int:
    """SB2 — wybór **pojedynczego obiektu** spośród wariantów maski (nie „grupy").

    Ranking ``w1·score + w2·dopasowanie_rozmiaru + w3·zwartość``, z premią za zawieranie
    kliknięcia. **Twardo odrzuca** maski wielokrotnie większe od oczekiwanego footprintu
    (sklejona grupa). Nigdy nie wraca do ``argmax(area)``: gdy wszystko odrzucone, bierze
    najmniejszą maskę zawierającą klik (a w ostateczności najmniejszą w ogóle).
    """
    n = int(masks.shape[0])
    if n == 0:
        return -1
    if n == 1:
        return 0
    cxq, cyq = click_xy
    height, width = int(masks.shape[1]), int(masks.shape[2])
    diag = float(np.hypot(height, width)) or 1.0

    sc = np.asarray(scores, dtype=float).reshape(-1) if scores is not None else np.full(n, np.nan)
    if sc.shape[0] != n or not np.any(np.isfinite(sc)):
        score_term = np.full(n, 0.5)  # brak scorów → neutralnie, decyduje rozmiar/zwartość
    else:
        finite = sc[np.isfinite(sc)]
        lo, hi = float(finite.min()), float(finite.max())
        span = (hi - lo) or 1.0
        score_term = np.where(np.isfinite(sc), (sc - lo) / span, 0.5)

    best_idx, best_val = -1, -np.inf
    fallback_idx, fallback_area = -1, np.inf  # najmniejsza maska zawierająca klik
    smallest_idx, smallest_area = -1, np.inf
    for i in range(n):
        geom = _mask_geometry(masks[i])
        if geom is None:
            continue
        area, bbox_area, cx, cy = geom
        if area < smallest_area:
            smallest_area, smallest_idx = area, i
        contains = (
            0 <= cyq < height and 0 <= cxq < width and bool(masks[i][int(round(cyq)), int(round(cxq))])
        )
        if contains and area < fallback_area:
            fallback_area, fallback_idx = area, i
        # twarde odrzucenie: maska rozlana wielokrotnie ponad oczekiwany rozmiar = grupa
        if expected_area_px and area > oversize_reject * expected_area_px:
            continue
        size_fit = 1.0
        if expected_area_px:
            size_fit = 1.0 / (1.0 + abs(float(np.log(area / max(expected_area_px, 1.0)))))
        fill = area / max(bbox_area, 1.0)  # rozlana grupa ma niski fill
        centroid_dist = float(np.hypot(cx - cxq, cy - cyq)) / diag
        compactness = fill * (1.0 - min(centroid_dist, 1.0))
        contain_bonus = 1.0 if contains else 0.4
        value = (
            weights[0] * score_term[i] + weights[1] * size_fit + weights[2] * compactness
        ) * contain_bonus
        if value > best_val:
            best_val, best_idx = value, i

    if best_idx >= 0:
        return best_idx
    if fallback_idx >= 0:
        return fallback_idx
    return smallest_idx if smallest_idx >= 0 else 0


def sam_click_proposal(
    project_id: str,
    scene_id: str,
    *,
    click_x: float,
    click_y: float,
    positive_points: list[list[float]] | None,
    negative_points: list[list[float]] | None,
    geometry_type: str,
    class_id: int | None,
    checkpoint: str | None,
    device: str,
    modality: str | None = None,
    size_prior_bbox: list[float] | None = None,
) -> dict[str, Any]:
    """Run one SAM click and return an AssistanceProposal payload (not yet saved)."""
    from db.storage import load_scene_json
    from models.assistance import AssistanceProposal
    from services.assistance_sessions import model_sha256
    from services.scene_loader import get_scene_info
    from services.scene_raster_resolver import resolve_scene_raster

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise SamAssistError("Scene not found")

    scene_path = resolve_scene_raster(project_id, scene_id)
    info = get_scene_info(scene_path)
    scene_w, scene_h = int(info.width), int(info.height)

    window = compute_window(click_x, click_y, scene_w, scene_h)

    # Click + prompt points in window coordinates.
    positives = [[click_x - window.x0, click_y - window.y0]]
    for point in positive_points or []:
        positives.append([point[0] - window.x0, point[1] - window.y0])
    negatives = [[p[0] - window.x0, p[1] - window.y0] for p in negative_points or []]

    model_sha = model_sha256(checkpoint) if (checkpoint and not mock_enabled()) else ("mock" if mock_enabled() else None)
    backend = get_backend(checkpoint, device)
    scene_identity = scene.get("source_scene_uid") or f"project:{project_id}:scene:{scene_id}"
    preprocessing_hash = (
        SAM_SAR_PREPROCESSING_HASH
        if str(modality or "").upper() == "SAR"
        else SAM_PREPROCESSING_HASH
    )
    key = encode_key(
        scene_identity=scene_identity,
        working_grid_uid=scene.get("working_grid_uid"),
        window=window,
        model_sha=model_sha,
        device=device,
        device_precision=backend.device_precision,
        preprocessing_hash=preprocessing_hash,
    )
    cached = _ENCODE_CACHE.get(key)
    if cached is not None and cached[0] is backend:
        state = get_encoded_state(backend, key, np.empty((0, 0, 3), dtype=np.uint8))
    else:
        rgb = read_rgb_window(scene_path, window, modality=modality)  # read only on encode miss
        state = get_encoded_state(backend, key, rgb)
    try:
        masks, scores = backend.decode_masks(state, positives, negatives)
    except SamAssistError:
        raise
    except Exception as exc:
        raise SamAssistError(f"SAM inference failed: {exc}") from exc
    if masks.shape[0] == 0 or not masks.any():
        raise SamAssistError("SAM returned an empty mask for this point")

    # SB2: wybór pojedynczego obiektu (nie „grupy") — score × rozmiar × zwartość, z priorem
    # rozmiaru z median boxów tej klasy. Zastępuje dawny argmax(area) systematycznie biasujący
    # w stronę sklejonej grupy na gęstych scenach satelitarnych.
    # SB3: jawnie wskazany rozmiar odniesienia ma pierwszeństwo przed mediana klasy.
    expected_area = _bbox_area_px(size_prior_bbox) or _expected_area_px(project_id, scene_id, class_id)
    selected = select_object_mask(masks, scores, (positives[0][0], positives[0][1]), expected_area)
    mask = masks[selected].astype(bool)
    if not mask.any():
        raise SamAssistError("SAM returned an empty mask for this point")
    score = float(scores[selected]) if 0 <= selected < len(scores) else float("nan")

    bbox_window = mask_to_bbox(mask)
    if bbox_window is None:
        raise SamAssistError("Could not derive a box from the mask")
    scene_bbox = [
        float(bbox_window[0] + window.x0),
        float(bbox_window[1] + window.y0),
        float(bbox_window[2] + window.x0),
        float(bbox_window[3] + window.y0),
    ]

    rotated = None
    polygon_scene_px = None
    front_vector = None
    if geometry_type == "rotated_bbox":
        rotated, polygon_scene_px = fit_oriented_box(
            mask, window.x0, window.y0, project_id, scene_id
        )
        if rotated is not None:
            # Default front along the long axis; the user confirms/flips it.
            front_vector = (
                front_vector_from_polygon(polygon_scene_px)
                or default_front_vector(rotated)
            )

    proposal = AssistanceProposal(
        session_id="",
        source_tool="sam_click",
        geometry_type=geometry_type if (geometry_type == "rotated_bbox" and rotated) else "bbox",
        bbox=scene_bbox,
        rotated_bbox=rotated,
        polygon_scene_px=polygon_scene_px,
        front_vector_scene_px=front_vector,
        class_id=class_id,
        confidence=(round(score, 4) if np.isfinite(score) else None),
        model_name=(Path(checkpoint).name if checkpoint and not mock_enabled() else ("mock_sam" if mock_enabled() else None)),
        model_sha256=(model_sha if not mock_enabled() else None),
        working_grid_uid=scene.get("working_grid_uid"),
        source_window=[window.x0, window.y0, window.width, window.height],
        preprocessing_hash=preprocessing_hash,
    )
    return proposal.model_dump()


# Cap na okno tekstowe (px, natywna rozdzielczość): SAM3 tekstowy liczy w pełnej
# rozdzielczości okna, więc przy zbyt szerokim widoku prosimy o przybliżenie.
MAX_TEXT_WINDOW_PX = 2048


def sam_text_proposals(
    project_id: str,
    scene_id: str,
    *,
    class_id: int,
    prompt: str,
    search_bbox: list[float],
    geometry_type: str,
    checkpoint: str | None,
    device: str,
    confidence_threshold: float = 0.25,
    modality: str | None = None,
    max_proposals: int = 300,
) -> list[dict[str, Any]]:
    """SAM3 tekstowy (ST): segmentacja instancji klasy ``class_name`` w oknie widoku.

    Zwraca listę payloadów ``AssistanceProposal`` (``source_tool='sam_text'``). Filtr pewności
    + ta sama bramka rozmiaru co SB2 (odrzuca sklejone grupy). Bramkowanie rodziny/CLIP jest
    w routerze; tu zakłada się dostępny backend (albo mock)."""
    from db.storage import load_scene_json
    from models.assistance import AssistanceProposal
    from services.assistance_sessions import model_sha256
    from services.scene_loader import get_scene_info
    from services.scene_raster_resolver import resolve_scene_raster

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise SamAssistError("Scene not found")
    if not prompt:
        raise SamAssistError("Text mode needs a text prompt")

    scene_path = resolve_scene_raster(project_id, scene_id)
    info = get_scene_info(scene_path)
    scene_w, scene_h = int(info.width), int(info.height)

    if not search_bbox or len(search_bbox) != 4:
        raise SamAssistError("search_bbox [x0,y0,x1,y1] is required for text mode")
    x0 = max(0, int(np.floor(search_bbox[0])))
    y0 = max(0, int(np.floor(search_bbox[1])))
    x1 = min(scene_w, int(np.ceil(search_bbox[2])))
    y1 = min(scene_h, int(np.ceil(search_bbox[3])))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise SamAssistError("The view window is empty")
    if (x1 - x0) > MAX_TEXT_WINDOW_PX or (y1 - y0) > MAX_TEXT_WINDOW_PX:
        raise SamAssistError(
            f"Zoom in — text segmentation runs at full resolution and the view exceeds "
            f"{MAX_TEXT_WINDOW_PX}px per side."
        )
    window = Window(x0, y0, x1 - x0, y1 - y0)

    preprocessing_hash = (
        SAM_SAR_PREPROCESSING_HASH if str(modality or "").upper() == "SAR" else SAM_PREPROCESSING_HASH
    )
    model_sha = model_sha256(checkpoint) if (checkpoint and not mock_enabled()) else ("mock" if mock_enabled() else None)
    model_name = Path(checkpoint).name if (checkpoint and not mock_enabled()) else ("mock_sam" if mock_enabled() else None)
    backend = get_backend(checkpoint, device)
    rgb = read_rgb_window(scene_path, window, modality=modality)
    try:
        masks, scores = backend.text_masks(rgb, [prompt])
    except SamAssistError:
        raise
    except Exception as exc:
        raise SamAssistError(f"SAM3 text inference failed: {exc}") from exc
    if masks.shape[0] == 0:
        return []

    expected_area = _expected_area_px(project_id, scene_id, class_id)
    payloads: list[dict[str, Any]] = []
    for i in range(masks.shape[0]):
        mask = masks[i].astype(bool)
        if not mask.any():
            continue
        score = float(scores[i]) if i < len(scores) else float("nan")
        if np.isfinite(score) and score < confidence_threshold:  # filtr pewności
            continue
        if expected_area and float(mask.sum()) > 4.0 * expected_area:  # bramka rozmiaru (SB2)
            continue
        bbox_window = mask_to_bbox(mask)
        if bbox_window is None:
            continue
        scene_bbox = [
            float(bbox_window[0] + window.x0),
            float(bbox_window[1] + window.y0),
            float(bbox_window[2] + window.x0),
            float(bbox_window[3] + window.y0),
        ]
        rotated = None
        polygon_scene_px = None
        front_vector = None
        if geometry_type == "rotated_bbox":
            rotated, polygon_scene_px = fit_oriented_box(
                mask, window.x0, window.y0, project_id, scene_id
            )
            if rotated is not None:
                front_vector = (
                front_vector_from_polygon(polygon_scene_px)
                or default_front_vector(rotated)
            )
        proposal = AssistanceProposal(
            session_id="",
            source_tool="sam_text",
            geometry_type=geometry_type if (geometry_type == "rotated_bbox" and rotated) else "bbox",
            bbox=scene_bbox,
            rotated_bbox=rotated,
            polygon_scene_px=polygon_scene_px,
            front_vector_scene_px=front_vector,
            class_id=class_id,
            confidence=(round(score, 4) if np.isfinite(score) else None),
            model_name=model_name,
            model_sha256=(model_sha if not mock_enabled() else None),
            working_grid_uid=scene.get("working_grid_uid"),
            source_window=[window.x0, window.y0, window.width, window.height],
            preprocessing_hash=preprocessing_hash,
        )
        payloads.append(proposal.model_dump())
        if len(payloads) >= max_proposals:
            break
    return payloads
