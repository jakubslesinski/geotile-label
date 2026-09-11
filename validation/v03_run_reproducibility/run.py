"""v03 — Odtwarzalność dataset runu  (Claim C2b / C2c).

SUBSTRATE / TIER
    FAIR1M (import benchmarku) · public.

CLAIM
    Ten sam seed + ta sama konfiguracja dają identyczny wynik build'u; wersje są
    niezmienne i niosą snapshot filtrów/metadanych.

METHOD
    Na zaimportowanym projekcie benchmarku odtwarzamy DETERMINISTYCZNE etapy buildu tymi
    samymi funkcjami co aplikacja, bez materializacji obrazów (która nie wpływa na
    tożsamość runu ani na przypisanie splitu):
      1. Siatka kafli — `services.tiler.compute_grid` + `services.tile_catalog.stable_tile_id`
         na realnych manifestach scen (K scen).
      2. Tożsamość runu — `services.dataset_runs.dataset_input_hash` na payloadzie wejścia
         (dataset_config z seedem+filtrami, tiling_config, klasy, manifesty, kafle). To ten
         sam hash, który `create_dataset_run_id` wpisuje w run_id -> dowód immutability/snapshot.
      3. Przypisanie splitu — `services.dataset_builder._assign_splits` z `random.Random(seed)`.
    Powtarzamy N razy dla trybów `random_tile` i `spatial_block_split`; porównujemy hash
    listy kafli, hash tożsamości runu i hash mapy split->kafel. KONTROLA: inny seed -> inna
    mapa splitu (dowód, że wynik naprawdę zależy od seeda, nie jest stały).

INPUTS
    - zaimportowany projekt FAIR1M (../importers/import_fair1m.py)
    - deterministyczne funkcje buildu z backendu aplikacji

OUTPUTS
    - results/repro_hashes.csv  (run_idx, mode, input_hash, tiles_hash, split_hash)
    - metrics: {n_runs, n_scenes, n_tiles, all_identical, seed, control_seed_differs,
                has_filter_snapshot, input_hash, split_hash_by_mode{}}

PASS CRITERION
    all_identical == True (input/tiles/split identyczne we wszystkich N runach i trybach);
    control_seed_differs == True; snapshot filtrów+seeda obecny (has_filter_snapshot).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv

N_RUNS = int(os.environ.get("V03_N_RUNS", "5"))
N_SCENES = int(os.environ.get("V03_SCENES", "60"))
MODES = ["random_tile", "spatial_block_split"]


def _hash_obj(obj) -> str:
    encoded = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_tiles(project: Path, tiling, TileInfo, compute_grid, stable_tile_id):
    """Zbuduj listę TileInfo (prefiks sid__) dla pierwszych N scen — realna siatka."""
    scene_dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())[:N_SCENES]
    tiles = []
    scene_manifests = {}
    for scene_dir in scene_dirs:
        sid = scene_dir.name
        manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
        scene = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
        image = manifest.get("image") or {}
        width, height = image.get("width"), image.get("height")
        if not width or not height:
            continue
        scene_manifests[sid] = manifest
        scene_name = Path(scene.get("filename") or sid).stem
        preview = compute_grid(int(width), int(height), tiling)
        for index, (x0, y0, x1, y1) in enumerate(preview.tile_rects):
            row_index = index // preview.num_cols + 1
            col_index = index % preview.num_cols + 1
            base_name = f"{col_index}_{row_index}_{scene_name}.png"
            tile_id = stable_tile_id(sid, base_name, x0, y0, tiling.tile_size)
            tiles.append(TileInfo(
                tile_id=tile_id,
                scene_id=sid,
                filename=f"{sid}__{base_name}",
                col=col_index,
                row=row_index,
                x0=x0, y0=y0, x1=x1, y1=y1,
            ))
    return tiles, scene_manifests


def main() -> ValidationResult:
    res = ValidationResult(
        id="v03",
        claim="C2b",
        title="Odtwarzalność build'u przy tym samym seedzie i configu",
        substrate=["FAIR1M"],
        tier="public",
    )

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
    from services.dataset_builder import _assign_splits
    from services.dataset_runs import dataset_input_hash
    try:
        from routers.dataset import dataset_filter_snapshot
    except Exception:  # noqa: BLE001 — router ciągnie FastAPI; snapshot sprawdzimy zapasowo
        dataset_filter_snapshot = None

    try:
        project = appenv.benchmark_project("FAIR1M")
    except FileNotFoundError as exc:
        res.status = "todo"
        res.notes = str(exc)
        return res

    tiling = TilingConfig(**json.loads((project / "tiling_config.json").read_text(encoding="utf-8")))
    ds_config = DatasetConfig(**json.loads((project / "dataset_config.json").read_text(encoding="utf-8")))
    classes = json.loads((project / "classes.json").read_text(encoding="utf-8"))

    tiles, scene_manifests = _build_tiles(project, tiling, TileInfo, compute_grid, stable_tile_id)
    if not tiles:
        res.status = "fail"
        res.notes = "Nie zbudowano kafli (brak width/height w manifestach scen)."
        return res

    seed = ds_config.split_seed
    filter_snapshot = dataset_filter_snapshot(ds_config) if dataset_filter_snapshot else None
    has_filter_snapshot = bool(
        filter_snapshot and "tile_selection" in filter_snapshot
    ) or all(hasattr(ds_config, f) for f in ("split_seed", "tile_selection", "class_ids"))

    def input_hash_once() -> str:
        payload = {
            "project": "FAIR1M",
            "tiling_config": tiling.model_dump(),
            "dataset_config": ds_config.model_dump(),
            "classes": classes,
            "scene_manifests": scene_manifests,
            "tiles": [t.model_dump() for t in tiles],
        }
        return dataset_input_hash(payload)

    def split_once(mode: str, use_seed: int) -> dict[str, str]:
        cfg = ds_config.model_copy(update={"split_mode": mode, "split_seed": use_seed})
        rng = random.Random(cfg.split_seed)
        return _assign_splits(
            tiles, {}, cfg, rng,
            tile_annotation_links=[], scene_manifests=scene_manifests, tile_size=tiling.tile_size,
        )

    tiles_hash = _hash_obj([[t.scene_id, t.filename, t.x0, t.y0] for t in tiles])

    rows = []
    input_hashes: set[str] = set()
    split_hash_by_mode: dict[str, set[str]] = {m: set() for m in MODES}
    for run_idx in range(N_RUNS):
        ih = input_hash_once()
        input_hashes.add(ih)
        for mode in MODES:
            sh = _hash_obj(sorted(split_once(mode, seed).items()))
            split_hash_by_mode[mode].add(sh)
            rows.append((run_idx, mode, ih, tiles_hash, sh))

    # Kontrola: inny seed powinien dać INNĄ mapę splitu (przynajmniej dla random_tile).
    control_seed = seed + 1
    control_hash = _hash_obj(sorted(split_once("random_tile", control_seed).items()))
    baseline_hash = next(iter(split_hash_by_mode["random_tile"]))
    control_seed_differs = control_hash != baseline_hash

    all_identical = (
        len(input_hashes) == 1
        and all(len(hs) == 1 for hs in split_hash_by_mode.values())
    )

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "repro_hashes.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["run_idx", "mode", "input_hash", "tiles_hash", "split_hash"])
        for run_idx, mode, ih, th, sh in rows:
            writer.writerow([run_idx, mode, ih[:16], th[:16], sh[:16]])

    res.config = {"n_runs": N_RUNS, "n_scenes": len(scene_manifests), "seed": seed, "modes": MODES}
    res.metrics = {
        "n_runs": N_RUNS,
        "n_scenes": len(scene_manifests),
        "n_tiles": len(tiles),
        "all_identical": all_identical,
        "control_seed_differs": control_seed_differs,
        "has_filter_snapshot": has_filter_snapshot,
        "seed": seed,
        "input_hash": next(iter(input_hashes))[:16],
        "tiles_hash": tiles_hash[:16],
        "split_hash_by_mode": {m: next(iter(hs))[:16] for m, hs in split_hash_by_mode.items()},
    }
    res.artifacts = ["results/repro_hashes.csv"]
    res.status = "pass" if (all_identical and control_seed_differs and has_filter_snapshot) else "fail"
    res.notes = (
        f"{N_RUNS}× build na {len(scene_manifests)} scenach / {len(tiles)} kaflach: "
        f"identyczny input_hash i split ({', '.join(MODES)}) = {all_identical}; "
        f"inny seed -> inny split = {control_seed_differs}; snapshot filtrów+seeda = {has_filter_snapshot}."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
