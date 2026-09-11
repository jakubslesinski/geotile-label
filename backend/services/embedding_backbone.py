"""Backbone embeddingów (DINO) dla modułu Dataset Intelligence — DI2.

Interfejs **„chip → wektor"** za którym chowa się źródło embeddingów, żeby analiza
(separowalność klas, podobieństwo, near-duplikaty, „20 najbardziej podobnych", find-similar)
dała się przełączać między **DINO** (preferowany, zamrożony backbone) a YOLO `embed()`
bez zmian w reszcie modułu. Zero dotrenowania — używamy pretrenowanych wag.

Wczytanie: architektura z `torch.hub` (kod), wagi z lokalnego pliku `.pth` w
``MODELS_ROOT/dino``. Offline: jeśli obok wag leży sklonowane repo (``dinov2_repo`` /
``dinov3_repo``), ładujemy przez ``source="local"`` bez sieci; inaczej fallback na GitHub
(pobranie kodu raz, cache w TORCH_HOME). To ta sama decyzja „loader = zależność" co przy
CLIP dla SAM3 — patrz DESIGN_DECISIONS.md, dataset-intelligence.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from services.predictor import MODELS_ROOT

DINO_DIR = MODELS_ROOT / "dino"
# Kod repo bundlowany z aplikacją: leży obok backendu (`backend/vendor/dino`), a więc jedzie
# w `resources/backend` instalatora (robocopy w prepare-tauri-backend.ps1). Dzięki temu DINO
# działa offline out-of-the-box; użytkownik dostarcza tylko wagi `.pth` do MODELS_ROOT/dino.
_BUNDLED_DINO_DIR = Path(__file__).resolve().parent.parent / "vendor" / "dino"
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_DEFAULT_IMG_SIZE = 112  # px; przycinany do wielokrotności patcha


class EmbeddingBackboneError(RuntimeError):
    """Backbone embeddingów niedostępny / błąd wczytania (bramkowane, bez cichej awarii)."""


def _arch_for(checkpoint_name: str) -> tuple[str, str, int] | None:
    """(repo torch.hub, entry, patch) dla nazwy checkpointu Meta, albo None."""
    name = checkpoint_name.lower()
    if "dinov3" in name and "vitl16" in name:
        return ("facebookresearch/dinov3", "dinov3_vitl16", 16)
    if "dinov3" in name and "vit7b16" in name:
        return ("facebookresearch/dinov3", "dinov3_vit7b16", 16)
    if "vits14" in name:
        return ("facebookresearch/dinov2", "dinov2_vits14_reg", 14)
    if "vitb14" in name:
        return ("facebookresearch/dinov2", "dinov2_vitb14_reg", 14)
    if "vitl14" in name:
        return ("facebookresearch/dinov2", "dinov2_vitl14_reg", 14)
    return None


# Kolejność preferencji na CPU: najpierw najlżejsze/najlepiej pasujące domenowo.
_PREFERENCE = ("vits14", "vitb14", "dinov3_vitl16", "vitl14", "vit7b16")


def _dino_repo_dir(family: str) -> Path | None:
    """Katalog kodu repo DINO, w kolejności: (1) override użytkownika
    `MODELS_ROOT/dino/<family>_repo`, (2) kod bundlowany z aplikacją
    `backend/vendor/dino/<family>_repo`, (3) cache torch.hub (`facebookresearch_<family>_main`)."""
    for base in (DINO_DIR, _BUNDLED_DINO_DIR):
        candidate = base / f"{family}_repo"
        if candidate.is_dir():
            return candidate
    import torch

    cache = Path(torch.hub.get_dir()) / f"facebookresearch_{family}_main"
    return cache if cache.is_dir() else None


def _build_dinov3_backbone(entry: str):
    """Zbuduj sam backbone DINOv3 (bez hubconf, ktory ciagnie segmentory/torchmetrics)."""
    import importlib
    import sys as _sys

    repo_dir = _dino_repo_dir("dinov3")
    if repo_dir is None:
        raise EmbeddingBackboneError(
            "Kod repo DINOv3 nie jest dostępny lokalnie (MODELS_ROOT/dino/dinov3_repo). "
            "Uruchom scripts/fetch-dino-repos.ps1 (DINOv3 nie ładuje się przez torch.hub — "
            "jego hubconf wymaga torchmetrics)."
        )
    if str(repo_dir) not in _sys.path:
        _sys.path.insert(0, str(repo_dir))
    builder = getattr(importlib.import_module("dinov3.hub.backbones"), entry)
    return builder(pretrained=False)


def _models_dir(models_dir: str | Path | None) -> Path:
    """Katalog wag DINO: skonfigurowany przez użytkownika albo domyślny MODELS_ROOT/dino."""
    return Path(models_dir) if models_dir else DINO_DIR


def list_dino_checkpoints(models_dir: str | Path | None = None) -> list[Path]:
    root = _models_dir(models_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.pth") if _arch_for(p.name) is not None)


def resolve_dino_checkpoint(
    preferred: str | None = None, models_dir: str | Path | None = None
) -> Path | None:
    """Wybierz checkpoint: jawna ścieżka > jawna nazwa > najlżejszy/najlepiej pasujący wg preferencji."""
    if preferred:
        as_path = Path(preferred)
        if as_path.is_file() and _arch_for(as_path.name) is not None:
            return as_path
    available = list_dino_checkpoints(models_dir)
    if not available:
        return None
    if preferred:
        for path in available:
            if path.name == preferred or path.stem == preferred:
                return path
    for token in _PREFERENCE:
        for path in available:
            if token in path.name.lower():
                return path
    return available[0]


def _variant_label(name: str) -> str:
    """Krótki, czytelny opis wariantu do UI (rodzina, rozmiar, domena)."""
    lower = name.lower()
    family = "DINOv3" if "dinov3" in lower else "DINOv2"
    size = next((s.upper() for s in ("vit7b16", "vitl16", "vitl14", "vitb14", "vits14") if s in lower), "?")
    sat = " · SAT" if "sat" in lower else ""
    return f"{family} {size}{sat}"


def scan_dino_models(models_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Rozpoznane wagi DINO w katalogu — do wyboru w GUI (jak `scan_sam_models`)."""
    out: list[dict[str, Any]] = []
    for path in list_dino_checkpoints(models_dir):
        arch = _arch_for(path.name)
        out.append({
            "name": path.name,
            "path": str(path),
            "family": _family_of(path.name),
            "arch": arch[1] if arch else None,
            "label": _variant_label(path.name),
            "supported": True,
        })
    return out


def _family_of(checkpoint_name: str) -> str | None:
    arch = _arch_for(checkpoint_name)
    return None if arch is None else ("dinov3" if "dinov3" in arch[1] else "dinov2")


def _allow_download() -> bool:
    """Sieciowe pobranie kodu repo (torch.hub) — domyślnie WYŁĄCZONE (inwariant offline)."""
    import os

    return os.environ.get("GEOTILE_DINO_ALLOW_DOWNLOAD", "0").strip().lower() in ("1", "true", "yes")


def dino_runtime_available(
    preferred: str | None = None, models_dir: str | Path | None = None
) -> bool:
    """Czy DINO ruszy **offline**: torch + rozpoznany checkpoint + **kod repo lokalnie**
    (bundlowane `MODELS_ROOT/dino/<family>_repo` albo cache torch.hub). Bez kodu repo backbone
    nie zbuduje się bez sieci — więc bramka jest tu, nie dopiero przy próbie użycia."""
    import importlib.util

    checkpoint = resolve_dino_checkpoint(preferred, models_dir)
    if checkpoint is None or importlib.util.find_spec("torch") is None:
        return False
    family = _family_of(checkpoint.name)
    if family is None:
        return False
    return _dino_repo_dir(family) is not None or _allow_download()


class DinoEmbedder:
    """Zamrożony backbone DINO: `embed_chips(chips) -> (N, D)` znormalizowane L2 (cosine)."""

    def __init__(self, checkpoint: str | Path, device: str = "cpu", image_size: int = _DEFAULT_IMG_SIZE):
        import torch

        path = Path(checkpoint)
        arch = _arch_for(path.name)
        if arch is None:
            raise EmbeddingBackboneError(f"Unrecognized DINO checkpoint: {path.name}")
        repo, entry, patch = arch
        family = "dinov3" if "dinov3" in entry else "dinov2"
        try:
            if family == "dinov3":
                # hubconf DINOv3 ciagnie segmentory (torchmetrics itd.) i pada — importujemy
                # SAM backbone bezposrednio z repo, z pominieciem hubconf.
                model = _build_dinov3_backbone(entry)
            else:
                repo_dir = _dino_repo_dir("dinov2")
                if repo_dir is not None:  # offline: bundlowane repo albo cache torch.hub
                    model = torch.hub.load(str(repo_dir), entry, source="local", pretrained=False, trust_repo=True)
                elif _allow_download():
                    model = torch.hub.load(repo, entry, source="github", pretrained=False, trust_repo=True)
                else:
                    raise EmbeddingBackboneError(
                        "Kod repo DINOv2 nie jest dostępny lokalnie (MODELS_ROOT/dino/dinov2_repo). "
                        "Uruchom scripts/fetch-dino-repos.ps1 albo ustaw GEOTILE_DINO_ALLOW_DOWNLOAD=1."
                    )
            state = torch.load(str(path), map_location=device)
            model.load_state_dict(state, strict=False)
            model.eval().to(device)
        except EmbeddingBackboneError:
            raise
        except Exception as exc:
            raise EmbeddingBackboneError(f"Could not load DINO backbone ({path.name}): {exc}") from exc

        self._torch = torch
        self._model = model
        self._device = device
        self._patch = patch
        self._size = max(patch, image_size - (image_size % patch))  # wielokrotność patcha
        self.embed_dim = int(getattr(model, "embed_dim", 0)) or None
        self.checkpoint_name = path.name

    def _preprocess(self, chips: list[np.ndarray]):
        import cv2

        tensors = []
        for chip in chips:
            arr = np.asarray(chip)
            if arr.ndim == 2:
                arr = np.stack([arr] * 3, axis=-1)  # SAR / 1-kanał → 3 kanały
            elif arr.shape[-1] == 1:
                arr = np.repeat(arr, 3, axis=-1)
            elif arr.shape[-1] > 3:
                arr = arr[..., :3]
            if arr.dtype != np.float32:
                arr = arr.astype(np.float32)
            if arr.max() > 1.5:  # zakładamy 0..255 gdy skala duża
                arr = arr / 255.0
            arr = cv2.resize(arr, (self._size, self._size), interpolation=cv2.INTER_AREA)
            arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
            tensors.append(arr.transpose(2, 0, 1))
        return self._torch.from_numpy(np.ascontiguousarray(np.stack(tensors), dtype=np.float32))

    def embed_chips(self, chips: list[np.ndarray], batch_size: int | None = None) -> np.ndarray:
        if not chips:
            return np.zeros((0, self.embed_dim or 0), dtype=np.float32)
        if batch_size is None:
            # GPU dusi sie duzym batchem (A100 80 GB uniesie setki chipow ViT-L 112 px);
            # na CPU maly batch, by nie skakac pamiecia. Batch 16 marnowal GPU.
            batch_size = 256 if str(self._device).startswith("cuda") else 32
        vectors: list[np.ndarray] = []
        for start in range(0, len(chips), batch_size):
            batch = self._preprocess(chips[start : start + batch_size]).to(self._device)
            with self._torch.no_grad():
                out = self._model(batch)  # token CLS (head=Identity dla wag pretrain)
            vectors.append(out.detach().cpu().numpy().astype(np.float32))
        vecs = np.concatenate(vectors, axis=0)
        return vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)  # L2 → cosine

    def _dense_forward(self, tensor):
        """[1,3,Hp*patch,Wp*patch] → grid patch-tokenów [D, gh, gw] (numpy). Odporny DINOv2/v3."""
        try:
            out = self._model.get_intermediate_layers(
                tensor, n=1, reshape=True, return_class_token=False, norm=True
            )
        except TypeError:
            out = self._model.get_intermediate_layers(tensor, n=1, reshape=True)
        feat = out[0]
        if isinstance(feat, (tuple, list)):
            feat = feat[0]
        return feat[0].detach().cpu().numpy()  # [D, gh, gw]

    def dense_grid(
        self, image_rgb: np.ndarray, upscale: float = 1.0, max_tile_px: int = 1024
    ) -> tuple[np.ndarray, float, float]:
        """Gęsta mapa cech patchowych regionu — **jeden** przebieg (kafelkowany).

        Zwraca ``(grid [Gh, Gw, D], native_py, native_px)``, gdzie ``native_p*`` = liczba pikseli
        SCENY na komórkę patcha. Zamiast tysięcy przebiegów per chip: liczymy cechy raz, a okna
        poolujemy z gridu (integral image). ``upscale`` podnosi rozdzielczość drobnych obiektów.
        """
        import cv2

        torch = self._torch
        patch = self._patch
        H, W = int(image_rgb.shape[0]), int(image_rgb.shape[1])
        if H < patch or W < patch:
            raise EmbeddingBackboneError("Region smaller than one patch for dense features")
        Ht = max(patch, int(round(H * upscale / patch)) * patch)
        Wt = max(patch, int(round(W * upscale / patch)) * patch)

        arr = np.asarray(image_rgb)
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)  # SAR / 1-kanał → 3 kanały
        elif arr.shape[-1] == 1:
            arr = np.repeat(arr, 3, axis=-1)
        elif arr.shape[-1] > 3:
            arr = arr[..., :3]
        arr = arr.astype(np.float32)
        if arr.max() > 1.5:
            arr = arr / 255.0
        arr = cv2.resize(arr, (Wt, Ht), interpolation=cv2.INTER_AREA)
        arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD

        Gh, Gw = Ht // patch, Wt // patch
        grid = np.zeros((Gh, Gw, self.embed_dim or 0), dtype=np.float32)
        tile = max(patch, (max_tile_px // patch) * patch)  # kafel wyrównany do patcha
        for ty in range(0, Ht, tile):
            for tx in range(0, Wt, tile):
                th = min(tile, Ht - ty)
                tw = min(tile, Wt - tx)
                th -= th % patch
                tw -= tw % patch
                if th < patch or tw < patch:
                    continue
                sub = arr[ty:ty + th, tx:tx + tw].transpose(2, 0, 1)[None]
                tensor = torch.from_numpy(np.ascontiguousarray(sub, dtype=np.float32)).to(self._device)
                with torch.inference_mode():
                    feat = self._dense_forward(tensor)  # [D, gh, gw]
                gy, gx = ty // patch, tx // patch
                grid[gy:gy + feat.shape[1], gx:gx + feat.shape[2]] = feat.transpose(1, 2, 0)
        return grid, H / Gh, W / Gw


_EMBEDDER_CACHE: dict[str, DinoEmbedder] = {}
_EMBEDDER_LOCK = threading.Lock()


def get_dino_embedder(
    preferred: str | None = None, device: str = "cpu", models_dir: str | Path | None = None
) -> DinoEmbedder:
    """Współdzielony embedder DINO (jeden na checkpoint|device); wczytanie jest kosztowne."""
    checkpoint = resolve_dino_checkpoint(preferred, models_dir)
    if checkpoint is None:
        raise EmbeddingBackboneError(
            "No DINO checkpoint found in MODELS_ROOT/dino (expected e.g. dinov2_vits14_reg4_pretrain.pth)"
        )
    key = f"{checkpoint.resolve()}|{device}"
    with _EMBEDDER_LOCK:
        cached = _EMBEDDER_CACHE.get(key)
        if cached is not None:
            return cached
    embedder = DinoEmbedder(checkpoint, device=device)  # ładowanie poza lockiem
    with _EMBEDDER_LOCK:
        _EMBEDDER_CACHE[key] = embedder
    return embedder
