"""DI0 — pomiar wiarygodnosci sygnalu separowalnosci klas (DESIGN_DECISIONS.md, dataset-intelligence).

Dla kazdej pary klas w projekcie liczy, czy embeddingi backbone'u YOLO rozrozniaja je
LEPIEJ NIZ LOSOWO, z 95% przedzialem ufnosci (bootstrap). To odpowiada na pytanie bramki
DI0: przy realnej liczbie probek per klasa sygnal jest odrozniny od szumu, czy nie.

Metryka pary: trafnosc najblizszego prototypu z leave-one-out (0.5 = poziom losowy).
Werdykt: CI_low > 0.5 -> sygnal; CI obejmuje 0.5 -> nierozroznialne przy tej probce.

Nie trenuje modelu. Uzywa wag bazowych (MODELS_ROOT/base) i torcha CPU. Uruchomienie:
srodowisko conda, DATA_DIR wskazujacy projekty, MODELS_ROOT z wagami — patrz README_dev.md.

    python scripts/di0-class-separability.py <project_id> [--min N] [--model yolo11n.pt] [--chip 64]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from db.storage import list_scene_ids, load_json, load_scene_json  # noqa: E402
from routers.scenes import _read_geotiff_window  # noqa: E402
from services.scene_raster_resolver import resolve_scene_raster  # noqa: E402


def cut_chip(scene_path: Path, scene_info: dict, bbox, chip: int) -> np.ndarray | None:
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    w, h = scene_info.get("width", 0), scene_info.get("height", 0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    try:
        return _read_geotiff_window(scene_path, scene_info, x0, y0, x1, y1, chip, chip, chip)
    except Exception:
        return None


def collect_chips(project_id: str, min_samples: int, chip: int):
    classes = {c["id"]: c["name"] for c in load_json(project_id, "classes", default=[])}
    by_class: dict[int, list[np.ndarray]] = defaultdict(list)
    modality = (load_json(project_id, "project", default={}).get("profile") or {}).get("modality", "?")

    for scene_id in list_scene_ids(project_id):
        anns = load_scene_json(project_id, scene_id, "annotations", default=[])
        if not anns:
            continue
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        si = scene.get("scene_info") or {}
        if not si.get("width"):
            continue
        try:
            scene_path = resolve_scene_raster(project_id, scene_id)
        except Exception:
            continue
        for ann in anns:
            if ann.get("is_negative") or not ann.get("bbox"):
                continue
            arr = cut_chip(scene_path, si, ann["bbox"], chip)
            if arr is not None:
                by_class[ann["class_id"]].append(arr)

    kept = {cid: chips for cid, chips in by_class.items() if len(chips) >= min_samples}
    return classes, kept, modality


def embed(model, chips: list[np.ndarray]) -> np.ndarray:
    vecs = []
    for i in range(0, len(chips), 32):
        for e in model.embed(chips[i : i + 32], verbose=False):
            vecs.append(e.cpu().numpy())
    v = np.asarray(vecs, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)  # L2 -> cosine


def loo_accuracy(emb_a: np.ndarray, emb_b: np.ndarray, idx: np.ndarray) -> float:
    """Trafnosc najblizszego prototypu (leave-one-out) na resamplowanych indeksach."""
    na = int((idx < len(emb_a)).sum())
    all_emb = np.vstack([emb_a, emb_b])
    sel = all_emb[idx]
    labels = (idx >= len(emb_a)).astype(int)  # 0=A, 1=B
    correct = 0
    for k in range(len(sel)):
        mask0 = (labels == 0) & (np.arange(len(sel)) != k)
        mask1 = (labels == 1) & (np.arange(len(sel)) != k)
        if not mask0.any() or not mask1.any():
            continue
        p0 = sel[mask0].mean(axis=0)
        p1 = sel[mask1].mean(axis=0)
        d0 = 1 - sel[k] @ p0 / (np.linalg.norm(p0) + 1e-9)
        d1 = 1 - sel[k] @ p1 / (np.linalg.norm(p1) + 1e-9)
        pred = 0 if d0 < d1 else 1
        correct += int(pred == labels[k])
    return correct / max(1, len(sel)), na


def separability(emb_a: np.ndarray, emb_b: np.ndarray, n_boot: int = 300):
    base = np.arange(len(emb_a) + len(emb_b))
    acc, _ = loo_accuracy(emb_a, emb_b, base)
    boots = []
    rng = np.random.default_rng(42)
    n = len(base)
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        b, _ = loo_accuracy(emb_a, emb_b, idx)
        boots.append(b)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return acc, float(lo), float(hi)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_id")
    ap.add_argument("--min", type=int, default=8, help="min. adnotacji na klase")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--chip", type=int, default=64)
    args = ap.parse_args()

    classes, kept, modality = collect_chips(args.project_id, args.min, args.chip)
    print(f"Projekt {args.project_id}  modalnosc={modality}")
    print(f"Klas z >= {args.min} adnotacjami: {len(kept)}")
    for cid, chips in kept.items():
        print(f"   {len(chips):4} szt  {classes.get(cid, cid)}")
    if len(kept) < 2:
        print("\nZa malo klas z probkami do testu par (potrzeba >= 2). Uruchom na wiekszej taksonomii.")
        return

    from ultralytics import YOLO
    from services.training_models import base_models_dir

    weights = base_models_dir() / args.model
    if not weights.is_file():
        print(f"\nBrak wag backbone'u: {weights}. Przygotuj je (fetch-base-models.ps1).")
        return
    model = YOLO(str(weights))
    emb = {cid: embed(model, chips) for cid, chips in kept.items()}

    print(f"\nSeparowalnosc par (trafnosc LOO, 0.5=losowo; werdykt: CI_low>0.5 => sygnal):")
    ids = list(kept)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            acc, lo, hi = separability(emb[a], emb[b])
            verdict = "SYGNAL" if lo > 0.5 else "nierozroznialne (CI obejmuje 0.5)"
            print(
                f"   {classes.get(a,a)[:22]:22} vs {classes.get(b,b)[:22]:22}"
                f"  acc={acc:.2f}  95%CI=[{lo:.2f},{hi:.2f}]  n={len(emb[a])}+{len(emb[b])}  -> {verdict}"
            )


if __name__ == "__main__":
    main()
