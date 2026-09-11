"""Exemplar propagation for SAR — "label one, find the rest" (A3).

SAR is a separate, harder track from the EO exemplar floor (A2). Optical
template correlation (``cv2.matchTemplate``) is unreliable on SAR: speckle,
non-Lambertian backscatter and layover break raw-pixel similarity. Instead we
compare **learned embeddings** from a SAR-native backbone (SARATR-X / FG-MAE
family): embed the exemplar patch and candidate windows, then rank by cosine
similarity, NMS, and self-exclusion.

This is a **GPU-oriented research spike** with a go/no-go gate (A3.2). There is
no CPU quality tier: the real backbone needs weights + GPU, which are a manual
validation step. What ships here (A3.1) is the *orchestration* — modality
gating, the backbone interface, the sliding-window search, and proposals into
the shared staging — validated offline with a deterministic mock backbone
(``GEOTILE_SAR_EXEMPLAR_MOCK=1``). Real embeddings replace the mock behind the
same interface with no pipeline changes.

Limitations (documented, not hidden): single-scale, axis-aligned candidates;
quality is entirely a function of the backbone's SAR features — the mock only
proves the plumbing, not detection quality.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from services.exemplar_assist import ExemplarError, _iou, _nms, compute_region
from services.sam_assist import Window, read_rgb_window

DEFAULT_THRESHOLD = 0.55  # cosine similarity in embedding space
DEFAULT_MAX_RESULTS = 200
SELF_IOU = 0.5
MAX_CANDIDATES = 8000  # sliding-window budget; stride is derived to respect it
EMBED_EPS = 1e-6


def mock_enabled() -> bool:
    return os.environ.get("GEOTILE_SAR_EXEMPLAR_MOCK", "0").strip().lower() in ("1", "true", "yes")


class SarFeatureBackend:
    """Embeds gray image patches into unit vectors for cosine comparison."""

    name = "sar_backbone"

    def embed(self, patches: list[np.ndarray]) -> np.ndarray:  # (N, D) unit rows
        raise NotImplementedError


class MockSarFeatureBackend(SarFeatureBackend):
    """Deterministic, content-sensitive descriptor — a test double, not a model.

    A patch is reduced to a mean-subtracted, L2-normalised low-res signature.
    Identical texture → identical vector → cosine ≈ 1; flat background → ~zero
    vector → cosine ≈ 0. Enough to exercise the search + staging offline; it is
    emphatically *not* a SAR feature extractor.
    """

    name = "sar_mock_descriptor"
    _GRID = 8

    def embed(self, patches: list[np.ndarray]) -> np.ndarray:
        import cv2

        out = np.zeros((len(patches), self._GRID * self._GRID), dtype=np.float32)
        for i, patch in enumerate(patches):
            small = cv2.resize(patch, (self._GRID, self._GRID), interpolation=cv2.INTER_AREA)
            vec = small.astype(np.float32).flatten()
            vec -= float(vec.mean())
            norm = float(np.linalg.norm(vec))
            if norm > EMBED_EPS:
                out[i] = vec / norm
        return out


class SaratrxFeatureBackend(SarFeatureBackend):
    """Real SAR-native backbone (SARATR-X / FG-MAE). GPU-oriented, manual A3.2.

    Deferred: this environment has no weights or GPU. Loading and running it is
    a documented manual validation step; the interface is fixed so swapping it
    in requires no changes to the search or staging code.

    Efficiency note for A3.2: ``find_similar_sar`` calls ``embed`` once with the
    full list of candidate windows, so a real backbone should batch them on the
    GPU — and ideally encode the region into a *dense* feature map once and pool
    per-window from it, rather than one forward pass per window (thousands of
    windows per region would otherwise be prohibitively slow).
    """

    name = "saratrx"

    def __init__(self, checkpoint: Path):
        self.checkpoint = checkpoint

    def embed(self, patches: list[np.ndarray]) -> np.ndarray:
        raise ExemplarError(
            "The SAR-native backbone (SARATR-X/FG-MAE) requires bundled weights and a GPU; "
            "run the A3.2 manual validation step to enable it."
        )


def sar_exemplar_capabilities() -> dict[str, Any]:
    """What the SAR exemplar tool can do right now (project-agnostic part).

    ``available`` here means the tool works without any per-project config — i.e.
    the mock backend is on. A real SARATR-X/FG-MAE backbone is supplied per
    project via ``prediction_config.sar_exemplar_backbone`` (see the router), so
    the frontend ORs this with the project setting.
    """
    return {"mock": mock_enabled(), "bundled_backbone": None, "available": mock_enabled()}


def resolve_backbone(config_backbone: str | None) -> SarFeatureBackend:
    """Pick the SAR feature backbone: mock (test) → configured weights → error."""
    if mock_enabled():
        return MockSarFeatureBackend()
    if config_backbone:
        path = Path(config_backbone)
        if not path.is_file():
            raise ExemplarError(f"SAR backbone weights not found: {config_backbone}")
        return SaratrxFeatureBackend(path)
    raise ExemplarError("No SAR exemplar backbone configured (GPU + SARATR-X/FG-MAE weights required)")


def _derive_stride(region: Window, tw: int, th: int) -> int:
    """Sliding-window stride, floored by object size, capped by a candidate budget."""
    base = max(4, min(tw, th) // 4)
    longest = max(region.width, region.height)
    # keep (region/stride)^2 under MAX_CANDIDATES
    budget_stride = int(np.ceil(longest / max(1.0, np.sqrt(MAX_CANDIDATES))))
    return max(base, budget_stride)


def _center(b: list[float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _near_exemplar(box: list[float], ex: list[float], tw: int, th: int) -> bool:
    """True if ``box`` essentially coincides with the exemplar.

    Uses IoU *and* centre distance: with a coarse sliding-window stride the best
    window over the exemplar can be offset enough that IoU alone misses it, which
    would re-propose the exemplar as its own "match".
    """
    if _iou(box, ex) >= SELF_IOU:
        return True
    (bx, by), (ex_cx, ex_cy) = _center(box), _center(ex)
    tol = 0.6 * min(tw, th)
    return abs(bx - ex_cx) <= tol and abs(by - ex_cy) <= tol


def find_similar_sar(
    project_id: str,
    scene_id: str,
    *,
    exemplar_bbox: list[float],
    class_id: int | None,
    backbone: SarFeatureBackend,
    threshold: float = DEFAULT_THRESHOLD,
    max_results: int = DEFAULT_MAX_RESULTS,
    nms_iou: float = 0.15,  # tighter than EO: embedding peaks are broader than NCC
) -> list[dict[str, Any]]:
    """AssistanceProposal payloads for SAR objects similar to the exemplar.

    Embeds the exemplar and a grid of same-size candidate windows with the SAR
    backbone, ranks by cosine similarity, applies NMS and self-exclusion.
    """
    import cv2

    from db.storage import load_scene_json
    from models.assistance import AssistanceProposal
    from services.scene_loader import get_scene_info
    from services.scene_raster_resolver import resolve_scene_raster

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise ExemplarError("Scene not found")

    ex = [float(v) for v in exemplar_bbox]
    tw, th = int(round(ex[2] - ex[0])), int(round(ex[3] - ex[1]))
    if tw < 4 or th < 4:
        raise ExemplarError("Exemplar is too small")

    scene_path = resolve_scene_raster(project_id, scene_id)
    info = get_scene_info(scene_path)
    scene_w, scene_h = int(info.width), int(info.height)

    region = compute_region(ex, scene_w, scene_h)
    if tw >= region.width or th >= region.height:
        raise ExemplarError("Exemplar is larger than the search region")

    rgb = read_rgb_window(scene_path, region)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    px0 = int(round(ex[0])) - region.x0
    py0 = int(round(ex[1])) - region.y0
    if px0 < 0 or py0 < 0 or px0 + tw > gray.shape[1] or py0 + th > gray.shape[0]:
        raise ExemplarError("Exemplar lies outside the searchable region")
    template = gray[py0:py0 + th, px0:px0 + tw]

    # Grid of candidate windows over the region.
    stride = _derive_stride(region, tw, th)
    positions: list[tuple[int, int]] = []
    patches: list[np.ndarray] = []
    max_y = gray.shape[0] - th
    max_x = gray.shape[1] - tw
    for ry in range(0, max_y + 1, stride):
        for rx in range(0, max_x + 1, stride):
            positions.append((rx, ry))
            patches.append(gray[ry:ry + th, rx:rx + tw])
    if not patches:
        return []

    ex_vec = backbone.embed([template])[0]
    if float(np.linalg.norm(ex_vec)) < EMBED_EPS:
        raise ExemplarError("Backbone produced an empty exemplar embedding")
    cand = backbone.embed(patches)  # (N, D) unit rows
    sims = cand @ ex_vec  # cosine (both L2-normalised)

    hits = np.where(sims >= threshold)[0]
    if hits.size == 0:
        return []
    order = hits[np.argsort(-sims[hits])][: max(max_results * 4, 1000)]
    boxes = [[float(region.x0 + positions[i][0]), float(region.y0 + positions[i][1]),
              float(region.x0 + positions[i][0] + tw), float(region.y0 + positions[i][1] + th)]
             for i in order]
    box_scores = [float(sims[i]) for i in order]

    keep = _nms(boxes, box_scores, nms_iou)

    proposals: list[dict[str, Any]] = []
    for idx in keep:
        box = boxes[idx]
        if _near_exemplar(box, ex, tw, th):
            continue
        proposals.append(AssistanceProposal(
            session_id="",
            source_tool="exemplar_sar",
            geometry_type="bbox",
            bbox=box,
            class_id=class_id,
            confidence=round(box_scores[idx], 4),
            model_name=backbone.name,
            working_grid_uid=scene.get("working_grid_uid"),
            source_window=[region.x0, region.y0, region.width, region.height],
        ).model_dump())
        if len(proposals) >= max_results:
            break
    return proposals
