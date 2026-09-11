"""v04 — random_tile vs spatial_block_split: MECHANIZM przecieku  (Claim C1).

SUBSTRATE / TIER
    xView3 — DUŻE sceny SAR (GEO/UTM), gdzie blok geograficzny *wewnątrz* jednej sceny
    ma sens i kafle mogą się nachodzić. · public. (Skutek — luka mAP — jest w v13.)

CLAIM
    Podział scenowy/blokowy zapobiega przeciekowi przestrzennemu (nachodzące/sąsiadujące
    kafle w różnych zbiorach), a aplikacja to audytuje. Losowy podział — nie.

METHOD
    Na kilku dużych scenach xView3 buduję siatkę kafli z NACHODZENIEM (buffer>0, stride<tile),
    liczę pozycje/rozpiętości kafli w EPSG:3857 TĄ SAMĄ funkcją co audyt aplikacji
    (`dataset_audit._tile_center_3857`). Przypisuję splity REALNĄ logiką aplikacji
    (`dataset_builder._assign_splits`) w trzech wariantach:
      1. random_tile,
      2. spatial_block_split (bez bufora),
      3. spatial_block_split + `spatial_buffer_tiles` (blockCV buforowany —
         `dataset_builder._apply_spatial_buffer`, ta sama kontrola co w buildzie).
    Dla każdego liczę kafle treningowe, które NACHODZĄ / SĄSIADUJĄ z kaflem val/test
    (metryka „adjacency" identyczna jak w kontroli audytu `cross_split_spatial_adjacency`).

INPUTS
    - zaimportowany projekt xView3 (../importers/import_xview3.py)
    - realne funkcje splitu + pozycjonowania kafli z backendu

OUTPUTS
    - results/leakage_counts.csv  (mode, n_train, n_holdout, train_overlap, train_adjacent)
    - metrics: {random{}, spatial{}, spatial_buffered{}, overlap_reduction_pct}

PASS CRITERION
    random_tile: train_overlap > 0 (problem istnieje);
    spatial_block_split < random (mechanizm ogranicza);
    spatial_block_split+buffer: train_overlap == 0 i train_adjacent == 0 (kontrola zeruje przeciek).
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv

N_SCENES = int(os.environ.get("V04_SCENES", "3"))
TILE_SIZE = int(os.environ.get("V04_TILE_SIZE", "1024"))
BUFFER = int(os.environ.get("V04_BUFFER", str(TILE_SIZE // 2)))  # nachodzenie kafli
BLOCK_SIZE_TILES = int(os.environ.get("V04_BLOCK", "5"))
# Bufor buildu mierzy rozpiętość jako BOK kafla (_tile_web_mercator_position), a audytowa
# adjacency jako PRZEKĄTNĄ (_tile_center_3857 ≈ bok·√2). Promień bufora = (N+0.5)·bok, próg
# adjacency = 1.5·przekątna ≈ 2.12·bok, więc dopiero N=2 (promień 2.5·bok) zeruje adjacency.
SPATIAL_BUFFER_TILES = int(os.environ.get("V04_SPATIAL_BUFFER", "2"))
SEED = 42


def _count_leakage(positions: dict, splits: dict, side: float, diag: float) -> dict:
    """Kafle treningowe nachodzące (d<side) / sąsiadujące (d<1.5*diag) z val/test.

    Kubełkowanie po siatce o boku progu adjacency -> O(N): sprawdzamy tylko komórkę
    kafla i 8 sąsiednich. Zgodne z `dataset_audit.add_spatial_adjacency_checks`.
    """
    adj_thr = 1.5 * diag
    cell = max(adj_thr, 1.0)
    holdout_buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    n_holdout = 0
    for name, pos in positions.items():
        if splits.get(name) in ("val", "test"):
            holdout_buckets[(int(pos[0] // cell), int(pos[1] // cell))].append(pos)
            n_holdout += 1

    train_overlap = train_adjacent = n_train = 0
    for name, pos in positions.items():
        if splits.get(name) != "train":
            continue
        n_train += 1
        cx, cy = int(pos[0] // cell), int(pos[1] // cell)
        best = math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for hx, hy in holdout_buckets.get((cx + dx, cy + dy), ()):
                    d = math.hypot(pos[0] - hx, pos[1] - hy)
                    if d < best:
                        best = d
        if best < side:
            train_overlap += 1
        if best < adj_thr:
            train_adjacent += 1
    return {
        "n_train": n_train,
        "n_holdout": n_holdout,
        "train_overlap": train_overlap,
        "train_adjacent": train_adjacent,
    }


def main() -> ValidationResult:
    res = ValidationResult(
        id="v04",
        claim="C1",
        title="Przeciek przestrzenny (mechanizm): random_tile vs spatial_block_split",
        substrate=["xView3"],
        tier="public",
    )
    res.config = {
        "n_scenes": N_SCENES, "tile_size": TILE_SIZE, "buffer": BUFFER,
        "block_size_tiles": BLOCK_SIZE_TILES, "spatial_buffer_tiles": SPATIAL_BUFFER_TILES, "seed": SEED,
    }

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    from models.dataset_config import DatasetConfig
    from models.tiling_config import TilingConfig, TileInfo
    from services.tiler import compute_grid
    from services.tile_catalog import stable_tile_id
    from services.dataset_builder import _assign_splits, _apply_spatial_buffer
    from services.dataset_audit import _tile_center_3857

    try:
        project = appenv.benchmark_project("xView3")
    except FileNotFoundError as exc:
        res.status = "todo"
        res.notes = str(exc)
        return res

    tiling = TilingConfig(tile_size=TILE_SIZE, buffer=BUFFER)
    scene_dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())[:N_SCENES]
    tiles: list[TileInfo] = []
    scene_manifests: dict[str, dict] = {}
    for scene_dir in scene_dirs:
        sid = scene_dir.name
        manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
        scene = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
        image = manifest.get("image") or {}
        width, height = image.get("width"), image.get("height")
        if not (manifest.get("geospatial") or {}).get("has_geo") or not width or not height:
            continue
        scene_manifests[sid] = manifest
        scene_name = Path(scene.get("filename") or sid).stem
        preview = compute_grid(int(width), int(height), tiling)
        for index, (x0, y0, x1, y1) in enumerate(preview.tile_rects):
            row_index = index // preview.num_cols + 1
            col_index = index % preview.num_cols + 1
            base = f"{col_index}_{row_index}_{scene_name}.png"
            tiles.append(TileInfo(
                tile_id=stable_tile_id(sid, base, x0, y0, TILE_SIZE),
                scene_id=sid, filename=f"{sid}__{base}",
                col=col_index, row=row_index, x0=x0, y0=y0, x1=x1, y1=y1,
            ))

    if not tiles or not scene_manifests:
        res.status = "fail"
        res.notes = "Brak kafli z georeferencją (sceny xView3 bez geospatial/rozmiaru)."
        return res

    # Pozycje kafli w 3857 tą samą funkcją co audyt aplikacji.
    geo_models: dict = {}
    positions: dict[str, tuple[float, float]] = {}
    spans: list[float] = []
    for tile in tiles:
        result = _tile_center_3857(tile.model_dump(), scene_manifests, TILE_SIZE, geo_models)
        if result is None:
            continue
        positions[tile.filename] = result[0]
        if result[1] > 0:
            spans.append(result[1])
    if not positions or not spans:
        res.status = "fail"
        res.notes = "Nie udało się wyznaczyć pozycji kafli w EPSG:3857."
        return res
    diag = float(median(spans))          # rozpiętość = przekątna kafla (jak w audycie)
    side = diag / math.sqrt(2.0)         # bok kafla -> próg fizycznego nachodzenia

    def assign(mode: str) -> dict:
        cfg = DatasetConfig(
            split_mode=mode, split_seed=SEED, block_size_tiles=BLOCK_SIZE_TILES,
            spatial_buffer_tiles=0,
        )
        rng = random.Random(cfg.split_seed)
        return _assign_splits(
            tiles, {}, cfg, rng,
            tile_annotation_links=[], scene_manifests=scene_manifests, tile_size=TILE_SIZE,
        )

    random_splits = assign("random_tile")
    spatial_splits = assign("spatial_block_split")

    # Wariant 3: ten sam split blokowy + bufor ochronny (dokładnie kontrola z buildu).
    cfg_buf = DatasetConfig(
        split_mode="spatial_block_split", split_seed=SEED,
        block_size_tiles=BLOCK_SIZE_TILES, spatial_buffer_tiles=SPATIAL_BUFFER_TILES,
    )
    buffered_tiles, buffered_splits = _apply_spatial_buffer(
        tiles, dict(spatial_splits), cfg_buf, scene_manifests, TILE_SIZE
    )
    buffered_positions = {t.filename: positions[t.filename] for t in buffered_tiles if t.filename in positions}

    modes = {
        "random_tile": _count_leakage(positions, random_splits, side, diag),
        "spatial_block_split": _count_leakage(positions, spatial_splits, side, diag),
        "spatial_block_split+buffer": _count_leakage(buffered_positions, buffered_splits, side, diag),
    }

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "leakage_counts.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["mode", "n_train", "n_holdout", "train_overlap", "train_adjacent"])
        for mode, m in modes.items():
            writer.writerow([mode, m["n_train"], m["n_holdout"], m["train_overlap"], m["train_adjacent"]])

    rnd = modes["random_tile"]
    spa = modes["spatial_block_split"]
    buf = modes["spatial_block_split+buffer"]
    overlap_reduction_pct = (
        round(100.0 * (rnd["train_overlap"] - buf["train_overlap"]) / rnd["train_overlap"], 2)
        if rnd["train_overlap"] else None
    )

    res.metrics = {
        "n_tiles": len(tiles),
        "n_scenes": len(scene_manifests),
        "tile_side_m": round(side, 1),
        "random": rnd,
        "spatial": spa,
        "spatial_buffered": buf,
        "overlap_reduction_pct": overlap_reduction_pct,
    }
    res.artifacts = ["results/leakage_counts.csv"]
    res.status = "pass" if (
        rnd["train_overlap"] > 0
        and spa["train_overlap"] < rnd["train_overlap"]
        and buf["train_overlap"] == 0
        and buf["train_adjacent"] == 0
    ) else "fail"
    res.notes = (
        f"{len(tiles)} kafli / {len(scene_manifests)} scen xView3 (nachodzenie buffer={BUFFER}px). "
        f"Kafle treningowe nachodzące na val/test — random={rnd['train_overlap']}, "
        f"spatial={spa['train_overlap']}, spatial+buffer={buf['train_overlap']}; "
        f"sąsiadujące — random={rnd['train_adjacent']}, spatial+buffer={buf['train_adjacent']}. "
        f"Redukcja nachodzenia (random->spatial+buffer) = {overlap_reduction_pct}%."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
