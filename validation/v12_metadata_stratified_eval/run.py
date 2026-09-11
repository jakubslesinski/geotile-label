"""v12 — Ocena stratyfikowana po metadanych akwizycji  (Claim C6, nowy filar).

SUBSTRATE / TIER
    xView3 — bogate metadane per-detekcja: `distance_from_shore_km` · public.

CLAIM
    Ponieważ metadane akwizycji przeżywają cały łańcuch, aplikacja pozwala liczyć metryki
    W ROZBICIU na te metadane — czego zbiory „obrazek+etykieta" nie umożliwiają. To osobny
    filar tezy (metadata-stratified evaluation), wcześniej bez żadnego testu.

METHOD
    Bierzemy UKOŃCZONY przebieg detekcji xView3 z aplikacji (wagi `weights/best.pt`) i jego
    dataset run. Ewaluujemy TYLKO na kaflach HELD-OUT (split val z `split_manifest`), żeby
    uniknąć przecieku train->eval nawet w obrębie jednego projektu. Dla każdego obiektu GT
    (z `tile_annotation_links`) sprawdzamy, czy model go wykrył (predykcja w tolerancji
    środka, class-agnostic — „czy w ogóle znaleziony"). Obiekty GT binujemy po
    `distance_from_shore_km` (z `source_annotations`) i liczymy RECALL per bin. Pokazuje, że
    metryka zmienia się z metadanymi (np. recall inny przy brzegu vs offshore) — JEDNA
    ilustracja zdolności narzędzia (nie benchmark; dane = 50 scen val xView3).

INPUTS
    - ukończony przebieg detekcji xView3 (wagi + dataset run) z danych aplikacji
    - ultralytics YOLO (inferencja) + metadane z source_annotations

OUTPUTS
    - results/stratified_metrics.csv  (metadata_bin, n_gt, n_detected, recall)
    - metrics: {run_id, conf, tol_px, bins[], recall_by_bin{}, spread_pp, n_gt_total}

PASS CRITERION
    Recall policzony dla ≥2 binów metadanych; różnica między binami raportowana (wynik
    opisowy — pokazuje zdolność, nie próg jakościowy).
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv, appruns

PROJECT = os.environ.get("V12_PROJECT", "xView3")
SPLIT = os.environ.get("V12_SPLIT", "val")           # held-out do ewaluacji
CONF = float(os.environ.get("V12_CONF", "0.10"))
TOL_PX = float(os.environ.get("V12_TOL_PX", "20"))   # ~200 m przy GSD 10 m (tolerancja xView3)
MAX_TILES = int(os.environ.get("V12_MAX_TILES", "0"))  # 0 = bez limitu
TILE_SIZE = int(os.environ.get("V12_TILE_SIZE", "640"))

# Biny distance_from_shore_km; sentinel 9999.99 = otwarte morze -> bin "offshore".
BINS = [("0-2km (brzeg)", 0.0, 2.0), ("2-10km (przybrzeże)", 2.0, 10.0), ("offshore (>10km)", 10.0, 1e12)]


def _bin_of(dist: float) -> str:
    for label, lo, hi in BINS:
        if lo <= dist < hi:
            return label
    return BINS[-1][0]


def main() -> ValidationResult:
    res = ValidationResult(
        id="v12", claim="C6",
        title="Ocena stratyfikowana po metadanych (distance_from_shore, kąt padania)",
        substrate=["xView3"], tier="public",
    )
    res.config = {"project": PROJECT, "split": SPLIT, "conf": CONF, "tol_px": TOL_PX}

    appenv.bootstrap()  # OMP/MKL + re-spawn (torch+numpy)

    # Ukończony przebieg detekcji z wagami
    runs = appruns.list_completed_runs(PROJECT)
    run = next((r for r in runs if r["task"] in ("detect", "obb")
                and (r["dir"] / "weights" / "best.pt").is_file()), None)
    if run is None:
        res.status = "todo"
        res.notes = f"Brak ukończonego przebiegu detekcji z wagami w {PROJECT}. Wytrenuj model w aplikacji."
        return res

    dataset_dir = appruns.app_data_dir() / "projects" / appruns.project_id_by_name(PROJECT) / \
        "dataset_runs" / run["dataset_run_id"]
    if not dataset_dir.is_dir():
        res.status = "todo"; res.notes = f"Brak katalogu dataset run: {dataset_dir}"; return res

    def _load(name):
        p = dataset_dir / name
        return json.load(io.open(p, encoding="utf-8")) if p.is_file() else None

    # tile_size z manifestu runu (xView3 = 1024, nie 640!) — inaczej środki GT liczone
    # w złej skali nie pasują do predykcji i recall spada do ~0.
    run_manifest = _load("dataset_run_manifest.json") or {}
    tile_size = int((run_manifest.get("tiling_config") or {}).get("tile_size") or TILE_SIZE)

    split_manifest = _load("split_manifest.json") or {}
    assignments = split_manifest.get("assignments") or {}
    held_out = {fn for fn, sp in assignments.items() if sp == SPLIT}
    if not held_out:
        res.status = "fail"; res.notes = f"Split '{SPLIT}' pusty w split_manifest."; return res

    # source_annotation_id -> distance_from_shore_km.
    # UWAGA: source_annotations.json runu oraz sceny projektu aplikacji mają OKROJONE atrybuty
    # (bez distance). Pełne metadane per-detekcja niesie projekt benchmarkowy (import xView3),
    # a source_annotation_id jest wspólne (deterministyczne) — stąd bierzemy odległości.
    dist_of: dict[str, float] = {}
    try:
        bench = appenv.benchmark_project(PROJECT)
        for sd in (bench / "scenes").iterdir():
            af = sd / "annotations.json"
            if not af.is_file():
                continue
            for a in json.load(io.open(af, encoding="utf-8")):
                sid = str(a.get("source_annotation_id") or a.get("id"))
                d = (a.get("attributes") or {}).get("distance_from_shore_km")
                if isinstance(d, (int, float)):
                    dist_of[sid] = float(d)
    except FileNotFoundError:
        pass
    if not dist_of:
        res.status = "todo"
        res.notes = (f"Brak metadanych distance_from_shore (projekt benchmarkowy {PROJECT} niedostępny). "
                     "v12 wymaga zaimportowanego xView3 z pełnymi atrybutami detekcji.")
        return res

    # GT per kafel held-out: (cx_px, cy_px, distance_bin)
    links = _load("tile_annotation_links.json") or {}
    la = links.get("annotations") if isinstance(links, dict) else links
    gt_by_tile: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for link in (la or []):
        if not link.get("exportable_yolo"):
            continue
        tile = str(link.get("dataset_tile_filename") or "")
        if tile not in held_out:
            continue
        sid = str(link.get("source_annotation_id"))
        dist = dist_of.get(sid)
        if dist is None:
            continue
        bb = link.get("bbox_yolo_norm") or []
        if len(bb) != 4:
            continue
        gt_by_tile[tile].append((bb[0] * tile_size, bb[1] * tile_size, _bin_of(dist)))

    tiles = sorted(gt_by_tile)
    if MAX_TILES:
        tiles = tiles[:MAX_TILES]
    if not tiles:
        res.status = "fail"; res.notes = "Brak kafli held-out z obiektami GT i metadanymi."; return res

    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"; res.notes = blocked; return res
    from ultralytics import YOLO

    device = os.environ.get("V12_DEVICE", "cpu")
    model = YOLO(str(run["dir"] / "weights" / "best.pt"))

    images_dir = dataset_dir / SPLIT / "images"
    per_bin_total: dict[str, int] = defaultdict(int)
    per_bin_hit: dict[str, int] = defaultdict(int)
    n_tiles_done = 0
    for tile in tiles:
        img = images_dir / tile
        if not img.is_file():
            continue
        result = model.predict(str(img), conf=CONF, device=device, verbose=False)[0]
        pred_centers = []
        if result.boxes is not None and len(result.boxes):
            xywh = result.boxes.xywh.cpu().numpy()
            pred_centers = [(float(r[0]), float(r[1])) for r in xywh]
        for gx, gy, bin_label in gt_by_tile[tile]:
            per_bin_total[bin_label] += 1
            hit = any((gx - px) ** 2 + (gy - py) ** 2 <= TOL_PX * TOL_PX for px, py in pred_centers)
            if hit:
                per_bin_hit[bin_label] += 1
        n_tiles_done += 1

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    recall_by_bin: dict[str, float] = {}
    with open(os.path.join(out_dir, "stratified_metrics.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["metadata_bin", "n_gt", "n_detected", "recall"])
        for label, _lo, _hi in BINS:
            n = per_bin_total.get(label, 0)
            hit = per_bin_hit.get(label, 0)
            rec = (hit / n) if n else None
            if n:
                recall_by_bin[label] = round(rec, 4)
            w.writerow([label, n, hit, f"{rec:.4f}" if rec is not None else ""])

    graded = [v for v in recall_by_bin.values()]
    spread_pp = round(100.0 * (max(graded) - min(graded)), 2) if len(graded) >= 2 else None
    n_gt_total = sum(per_bin_total.values())

    res.metrics = {
        "run_id": run["run_id"],
        "task": run["task"],
        "conf": CONF,
        "tol_px": TOL_PX,
        "device": device,
        "n_tiles_eval": n_tiles_done,
        "n_gt_total": n_gt_total,
        "bins": [b[0] for b in BINS],
        "recall_by_bin": recall_by_bin,
        "n_gt_by_bin": {label: per_bin_total.get(label, 0) for label, _l, _h in BINS},
        "spread_pp": spread_pp,
    }
    res.artifacts = ["results/stratified_metrics.csv"]
    res.status = "pass" if len(recall_by_bin) >= 2 else "fail"
    parts = ", ".join(f"{k}={v:.2f} (n={per_bin_total[k]})" for k, v in recall_by_bin.items())
    res.notes = (
        f"Recall stratyfikowany po distance_from_shore ({PROJECT} {SPLIT}, {n_tiles_done} kafli / "
        f"{n_gt_total} obiektów, model {run['run_id']} @conf{CONF}): {parts}. "
        f"Rozrzut recall między binami = {spread_pp} pp — metryka zmienia się z metadanymi. "
        f"Ilustracja zdolności (małe dane: 50 scen val xView3), nie benchmark."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
