"""Few-shot exemplar propagation with DINO embeddings — training-free, EO + SAR.

„Oznacz jeden (lub kilka), znajdź resztę" oparte na cechach **DINO** zamiast korelacji
szablonu (NCC). Z oznaczonych boxów budujemy **prototyp** (uśredniony, znormalizowany
embedding), przesuwamy okna tego samego rozmiaru po regionie szukania, rankujemy po cosine,
NMS i wykluczamy same egzemplarze. Działa na EO **i** SAR (DINO obsługuje 1 kanał przez
powielenie; DINOv3-SAT domyka domenę SAR — zweryfikowane).

Zakres MVP: **ramki** (geometria przenoszona z egzemplarza przez router, jak w find-similar
szablonowym). Doprecyzowanie masek SAM to osobna, późniejsza faza. Ograniczenia (jawne):
pojedyncza skala, okna osiowo-równoległe; jakość zależy od cech DINO.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from services.exemplar_assist import ExemplarError, _iou, _nms, compute_region
from services.sam_assist import Window, read_rgb_window

DEFAULT_THRESHOLD = 0.40  # centrowany cosine (po whiteningu): tło ≈ 0, dopasowania wyraźnie > 0
DEFAULT_MAX_RESULTS = 200
MAX_CANDIDATES = 6000  # budżet sliding-window; stride wyliczany, by go nie przekroczyć
SELF_IOU = 0.5
EMBED_EPS = 1e-6
# Dense-feature pooling: jeden przebieg DINO na region → gęsty grid patch-tokenów, okna poolowane
# integral-image (zamiast tysięcy przebiegów per chip). TARGET_PATCHES = ile patchy ma obejmować
# obiekt (rozdzielczość na obiekt), MAX_UPSCALE ogranicza rozmiar/koszt, MAX_DENSE_TILE = kafel ViT.
TARGET_PATCHES = 6
MAX_UPSCALE = 4.0
MAX_DENSE_TILE = 1024


def _default_exemplar_checkpoint(models_dir: str | None = None) -> str | None:
    """Domyślny checkpoint dla egzemplarza: **DINOv3-SAT** (satelitarny), gdy dostępny —
    domenowo mocniejszy na EO/SAR niż najlżejszy DINOv2-S (który zostaje domyślny dla analizy
    DI2, bo tam na CPU liczy się przepustowość na dziesiątkach tysięcy obiektów)."""
    from services.embedding_backbone import list_dino_checkpoints

    # Celowo ViT-L SAT (~300M, 1024-dim) — praktyczny do interaktywnego sliding-window.
    # NIE 7B (vit7b16): miliardy parametrów, niepraktyczne nawet na GPU dla tego zastosowania.
    for path in list_dino_checkpoints(models_dir):
        name = path.name.lower()
        if "dinov3" in name and "sat" in name and "vitl16" in name:
            return path.name
    return None  # brak ViT-L SAT → fallback do domyślnej preferencji (najlżejszy DINOv2-S)


class DinoFeatureBackend:
    """Opakowanie embeddera DINO do interfejsu „patche RGB → wektory jednostkowe (N,D)".

    `preferred` = wybrany checkpoint (nazwa lub ścieżka z konfiguracji); gdy brak — auto ViT-L
    SAT. `models_dir` = katalog wag z konfiguracji projektu (albo domyślny MODELS_ROOT/dino).
    """

    def __init__(
        self, preferred: str | None = None, device: str = "cpu", models_dir: str | None = None
    ):
        from services.embedding_backbone import get_dino_embedder

        if preferred is None:
            preferred = _default_exemplar_checkpoint(models_dir)
        self._embedder = get_dino_embedder(preferred=preferred, device=device, models_dir=models_dir)
        self.name = f"dino:{self._embedder.checkpoint_name}"
        self.patch = int(self._embedder._patch)

    def embed(self, patches: list[np.ndarray]) -> np.ndarray:
        # embed_chips zwraca już (N, D) znormalizowane L2 (cosine).
        return np.asarray(self._embedder.embed_chips(patches), dtype=np.float32)

    def dense_grid(self, image: np.ndarray, upscale: float = 1.0, max_tile_px: int = 1024):
        """Gęsta mapa patch-tokenów regionu (jeden przebieg) → (grid [Gh,Gw,D], native_py, native_px)."""
        return self._embedder.dense_grid(image, upscale=upscale, max_tile_px=max_tile_px)


def _derive_stride(region: Window, tw: int, th: int) -> int:
    """Krok siatki: ograniczony rozmiarem obiektu od dołu, budżetem kandydatów od góry."""
    base = max(4, min(tw, th) // 3)
    longest = max(region.width, region.height)
    budget = int(np.ceil(longest / max(1.0, np.sqrt(MAX_CANDIDATES))))
    return max(base, budget)


def _center(b: list[float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _near_any_exemplar(box: list[float], exemplars: list[list[float]], tw: int, th: int) -> bool:
    """True, gdy ``box`` pokrywa się z którymś egzemplarzem (IoU albo bliskość centrów) —
    inaczej przy zgrubnym stride egzemplarz bywa re-proponowany jako własne „dopasowanie"."""
    tol = 0.6 * min(tw, th)
    bx, by = _center(box)
    for ex in exemplars:
        if _iou(box, ex) >= SELF_IOU:
            return True
        ex_cx, ex_cy = _center(ex)
        if abs(bx - ex_cx) <= tol and abs(by - ex_cy) <= tol:
            return True
    return False


def _region_from_search(
    search_bbox: list[float] | None, exemplar: list[float], scene_w: int, scene_h: int
) -> Window:
    """Region szukania: jawny `search_bbox` (viewport/scena), inaczej okolica egzemplarza (local)."""
    if search_bbox and len(search_bbox) == 4:
        x0 = max(0, int(np.floor(search_bbox[0])))
        y0 = max(0, int(np.floor(search_bbox[1])))
        x1 = min(scene_w, int(np.ceil(search_bbox[2])))
        y1 = min(scene_h, int(np.ceil(search_bbox[3])))
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            return Window(x0, y0, x1 - x0, y1 - y0)
    return compute_region(exemplar, scene_w, scene_h)


def _read_exemplar_chip(scene_path, exemplar: list[float], scene_w: int, scene_h: int, modality):
    """Wytnij chip RGB egzemplarza z jego własnej lokalizacji (może leżeć poza regionem)."""
    x0 = max(0, min(int(np.floor(exemplar[0])), scene_w - 1))
    y0 = max(0, min(int(np.floor(exemplar[1])), scene_h - 1))
    x1 = max(x0 + 1, min(int(np.ceil(exemplar[2])), scene_w))
    y1 = max(y0 + 1, min(int(np.ceil(exemplar[3])), scene_h))
    chip = read_rgb_window(scene_path, Window(x0, y0, x1 - x0, y1 - y0), modality=modality)
    return chip


def _candidates_chips(backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality):
    """Ścieżka klasyczna: chip per okno + prototyp z chipów egzemplarzy (fallback dla dense)."""
    ex_chips = [_read_exemplar_chip(scene_path, e, scene_w, scene_h, modality) for e in exemplars]
    prototype = backbone.embed(ex_chips).mean(axis=0)
    norm = float(np.linalg.norm(prototype))
    if norm < EMBED_EPS:
        raise ExemplarError("DINO produced an empty exemplar prototype")
    prototype = (prototype / (norm + EMBED_EPS)).astype(np.float32)

    stride = _derive_stride(region, tw, th)
    positions: list[tuple[int, int]] = []
    patches: list[np.ndarray] = []
    max_y, max_x = rgb.shape[0] - th, rgb.shape[1] - tw
    for ry in range(0, max_y + 1, stride):
        for rx in range(0, max_x + 1, stride):
            positions.append((rx, ry))
            patches.append(rgb[ry:ry + th, rx:rx + tw])
    if not patches:
        return [], np.zeros((0, prototype.shape[0]), dtype=np.float32), prototype
    return positions, backbone.embed(patches), prototype


def _integral(grid: np.ndarray, D: int) -> np.ndarray:
    """Integral image (summed-area table) po osiach przestrzennych → [Gh+1, Gw+1, D]."""
    sat = np.zeros((grid.shape[0] + 1, grid.shape[1] + 1, D), dtype=np.float64)
    sat[1:, 1:, :] = np.cumsum(np.cumsum(grid.astype(np.float64), axis=0), axis=1)
    return sat


def _candidates_dense(backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality):
    """Dense-feature pooling: JEDEN przebieg DINO na region → grid patch-tokenów; okna poolowane
    integral-image. Prototyp = uśrednione, znormalizowane cechy patchowe egzemplarzy (spójne z
    poolingiem okien). Zwraca ``(positions [(rx,ry)], cand [N,D] L2, prototype [D] L2)``."""
    patch = backbone.patch
    upscale = float(np.clip(TARGET_PATCHES * patch / max(1, min(tw, th)), 1.0, MAX_UPSCALE))

    grid, py, px = backbone.dense_grid(rgb, upscale=upscale, max_tile_px=MAX_DENSE_TILE)
    Gh, Gw, D = grid.shape
    if Gh < 1 or Gw < 1 or D == 0:
        raise ExemplarError("Empty dense grid")
    sat = _integral(grid, D)

    def _pool(s, gh, gw, spy, spx, bx0, by0, bx1, by1):
        """Średni patch-token po prostokącie (px lokalne) z integral image ``s``."""
        r0 = min(max(int(by0 / spy), 0), gh - 1)
        c0 = min(max(int(bx0 / spx), 0), gw - 1)
        r1 = min(max(int(np.ceil(by1 / spy)), r0 + 1), gh)
        c1 = min(max(int(np.ceil(bx1 / spx)), c0 + 1), gw)
        return (s[r1, c1] - s[r0, c1] - s[r1, c0] + s[r0, c0]) / ((r1 - r0) * (c1 - c0))

    # Prototyp MUSI pochodzić z tej samej mapy cech co kandydaci (kontekst/rozdzielczość) —
    # inaczej cechy nieporównywalne (izolowany chip egzemplarza dawał ~zerowe dopasowania).
    # Egzemplarz w regionie → pool z gridu regionu; poza regionem → z okna z kontekstem.
    rx0, ry0 = region.x0, region.y0
    ex_vecs: list[np.ndarray] = []
    for e in exemplars:
        if rx0 <= e[0] and e[2] <= rx0 + region.width and ry0 <= e[1] and e[3] <= ry0 + region.height:
            vec = _pool(sat, Gh, Gw, py, px, e[0] - rx0, e[1] - ry0, e[2] - rx0, e[3] - ry0)
        else:
            pad_x = max(1, int(round((e[2] - e[0]) * 0.5)))
            pad_y = max(1, int(round((e[3] - e[1]) * 0.5)))
            wx0 = max(0, int(e[0] - pad_x))
            wy0 = max(0, int(e[1] - pad_y))
            wx1 = min(scene_w, int(e[2] + pad_x))
            wy1 = min(scene_h, int(e[3] + pad_y))
            wchip = read_rgb_window(scene_path, Window(wx0, wy0, wx1 - wx0, wy1 - wy0), modality=modality)
            eg, epy, epx = backbone.dense_grid(wchip, upscale=upscale, max_tile_px=MAX_DENSE_TILE)
            if eg.size == 0:
                continue
            egh, egw = eg.shape[0], eg.shape[1]
            esat = _integral(eg, eg.shape[-1])
            vec = _pool(esat, egh, egw, epy, epx, e[0] - wx0, e[1] - wy0, e[2] - wx0, e[3] - wy0)
        nv = float(np.linalg.norm(vec))
        if nv > EMBED_EPS:
            ex_vecs.append(vec / nv)
    if not ex_vecs:
        raise ExemplarError("No exemplar dense features")
    prototype = np.mean(ex_vecs, axis=0)
    norm = float(np.linalg.norm(prototype))
    if norm < EMBED_EPS:
        raise ExemplarError("Empty exemplar prototype (dense)")
    prototype = (prototype / (norm + EMBED_EPS)).astype(np.float32)

    # Pozycje okien (jak w chipach), ale pooling przez integral image — bez przebiegów per okno.
    stride = _derive_stride(region, tw, th)
    max_y, max_x = rgb.shape[0] - th, rgb.shape[1] - tw
    ys, xs = [], []
    for ry in range(0, max_y + 1, stride):
        for rx in range(0, max_x + 1, stride):
            ys.append(ry)
            xs.append(rx)
    if not ys:
        return [], np.zeros((0, D), dtype=np.float32), prototype
    ys_a = np.asarray(ys, dtype=np.float64)
    xs_a = np.asarray(xs, dtype=np.float64)
    r0 = np.clip((ys_a / py).astype(int), 0, Gh - 1)
    c0 = np.clip((xs_a / px).astype(int), 0, Gw - 1)
    r1 = np.clip(np.ceil((ys_a + th) / py).astype(int), r0 + 1, Gh)
    c1 = np.clip(np.ceil((xs_a + tw) / px).astype(int), c0 + 1, Gw)
    total = sat[r1, c1] - sat[r0, c1] - sat[r1, c0] + sat[r0, c0]  # [N, D]
    count = ((r1 - r0) * (c1 - c0)).astype(np.float64)[:, None]
    cand = total / np.maximum(count, 1.0)
    cand = cand / (np.linalg.norm(cand, axis=1, keepdims=True) + EMBED_EPS)
    positions = list(zip((xs_a.astype(int)).tolist(), (ys_a.astype(int)).tolist()))
    return positions, cand.astype(np.float32), prototype


def _embed_candidates(backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality):
    """Kandydaci + prototyp: dense-pooling (jeden przebieg); fallback do chipów per okno, gdy dense
    zawiedzie (np. wariant bez patch-tokenów albo błąd)."""
    try:
        return _candidates_dense(backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality)
    except Exception:
        # Każdy problem dense (wariant bez patch-tokenów, błąd kształtu, OOM) → klasyczne chipy.
        return _candidates_chips(backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality)


def find_similar_dino(
    project_id: str,
    scene_id: str,
    *,
    exemplar_bboxes: list[list[float]],
    class_id: int | None,
    backbone: DinoFeatureBackend,
    threshold: float = DEFAULT_THRESHOLD,
    max_results: int = DEFAULT_MAX_RESULTS,
    nms_iou: float = 0.2,
    search_bbox: list[float] | None = None,
    modality: str | None = None,
) -> list[dict[str, Any]]:
    """Payloady AssistanceProposal dla obiektów podobnych do egzemplarza(y) wg cech DINO."""
    from db.storage import load_scene_json
    from models.assistance import AssistanceProposal
    from services.scene_loader import get_scene_info
    from services.scene_raster_resolver import resolve_scene_raster

    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise ExemplarError("Scene not found")
    exemplars = [[float(v) for v in bb] for bb in exemplar_bboxes if bb and len(bb) == 4]
    if not exemplars:
        raise ExemplarError("At least one exemplar box is required")

    scene_path = resolve_scene_raster(project_id, scene_id)
    info = get_scene_info(scene_path)
    scene_w, scene_h = int(info.width), int(info.height)

    # Rozmiar okna z mediany egzemplarzy (few-shot, różne rozmiary tolerowane).
    tw = int(round(float(np.median([e[2] - e[0] for e in exemplars]))))
    th = int(round(float(np.median([e[3] - e[1] for e in exemplars]))))
    if tw < 4 or th < 4:
        raise ExemplarError("Exemplar is too small")

    region = _region_from_search(search_bbox, exemplars[0], scene_w, scene_h)
    if tw >= region.width or th >= region.height:
        raise ExemplarError("Exemplar is larger than the search region")

    rgb = read_rgb_window(scene_path, region, modality=modality)
    # Kandydaci + prototyp: dense-feature pooling (jeden przebieg DINO na region), z fallbackiem
    # do klasycznych chipów per okno. positions to lewe-górne rogi okien w px regionu.
    positions, cand, prototype = _embed_candidates(
        backbone, scene_path, rgb, tw, th, exemplars, region, scene_w, scene_h, modality,
    )
    if cand.shape[0] == 0:
        return []

    # Mean-centering (whitening). Cechy DINO są anizotropowe — surowy cosine DOWOLNYCH
    # dwóch okien jest wysoki (~0,5–0,8), więc próg przepuszcza tło (siatka fałszywych trafień).
    # Odejmujemy średnią okien regionu (rozkład „tła") od prototypu i kandydatów → score = „o ile
    # bardziej podobne do wzorca niż typowe okno tutaj". Tło wypada ~0, realne dopasowania wystają.
    mu = cand.mean(axis=0)
    proto_c = prototype - mu
    proto_c = proto_c / (np.linalg.norm(proto_c) + EMBED_EPS)
    cand_c = cand - mu
    cand_c = cand_c / (np.linalg.norm(cand_c, axis=1, keepdims=True) + EMBED_EPS)
    sims = cand_c @ proto_c  # centrowany cosine (tło ≈ 0)
    hits = np.where(sims >= threshold)[0]
    if hits.size == 0:
        return []
    order = hits[np.argsort(-sims[hits])][: max(max_results * 4, 1000)]
    boxes = [[float(region.x0 + positions[i][0]), float(region.y0 + positions[i][1]),
              float(region.x0 + positions[i][0] + tw), float(region.y0 + positions[i][1] + th)]
             for i in order]
    box_scores = [float(sims[i]) for i in order]

    proposals: list[dict[str, Any]] = []
    for idx in _nms(boxes, box_scores, nms_iou):
        box = boxes[idx]
        if _near_any_exemplar(box, exemplars, tw, th):
            continue
        proposals.append(AssistanceProposal(
            session_id="",
            source_tool="exemplar_dino",
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
