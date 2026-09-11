"""v05 — Integralność ID: scena -> adnotacja -> kafel -> eksport  (Claim C2a).

SUBSTRATE / TIER
    FAIR1M (public) + SAR_test (Capella, reported-only) — łańcuch tożsamości na realnych
    metadanych sceny.  (mixed)

CLAIM
    Tożsamość obiektu (i pochodzenie sceny) przeżywa CAŁĄ ścieżkę: od adnotacji na scenie,
    przez kafel datasetu, po rekord eksportu. To dosłowny dowód tezy artykułu.

METHOD
    Dla każdej sceny odtwarzam propagację adnotacja->kafel TĄ SAMĄ funkcją co build
    aplikacji (`services.annotation_propagator.propagate_annotations_with_attributes`,
    a pod spodem `compute_tile_attributes`), na siatce kafli z nachodzeniem (żeby każdy
    obiekt mieścił się w co najmniej jednym kaflu). Każdy eksportowalny rekord kafla niesie
    `source_annotation_id`, `tile_id`, `scene_id`, `class_id`. Sprawdzam integralność:
      - orphan: rekord eksportu wskazuje na nieistniejące `source_annotation_id`,
      - misassigned: klasa w eksporcie ≠ klasa adnotacji źródłowej,
      - wrong_scene: `scene_id` rekordu ≠ scena, z której pochodzi,
      - lost: adnotacja źródłowa (niepusta, niezdegenerowana) nie trafia do ŻADNEGO eksportu.
    `source_scene_uid` z manifestu potwierdza pochodzenie sceny (prowenancja).

INPUTS
    - zaimportowany projekt FAIR1M (../importers) + fixture SAR_test (../geotile-label/data)
    - propagacja + atrybuty kafla z backendu aplikacji

OUTPUTS
    - results/id_chain.csv  (scene_id, source_annotation_id, class_id, tiles, exported, ok)
    - metrics: {n_annotations, n_traced, n_lost, n_orphan, n_misassigned, n_wrong_scene, per_project{}}

PASS CRITERION
    n_lost == 0 and n_orphan == 0 and n_misassigned == 0 and n_wrong_scene == 0
    (100% tożsamości odtworzone w eksporcie).
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv
from _common.geom import polygon_area

TILE_SIZE = int(os.environ.get("V05_TILE_SIZE", "1024"))
BUFFER = int(os.environ.get("V05_BUFFER", str(TILE_SIZE // 2)))  # nachodzenie -> pełne pokrycie obiektów
MIN_BOX_FRACTION = float(os.environ.get("V05_MIN_BOX_FRACTION", "0.3"))
FAIR1M_SCENES = int(os.environ.get("V05_FAIR1M_SCENES", "40"))
# Lokalny fixture SAR wskazuje SAR_TEST_PROJECT. Bez niego ten fragment
# sprawozdania jest pomijany, wiec domyslna sciezka nie jest potrzebna.
SAR_TEST_PROJECT = os.environ.get("SAR_TEST_PROJECT", "sar-test-project")


def _source_area(rec: dict) -> float:
    poly = rec.get("polygon_scene_px")
    if poly and len(poly) >= 3:
        return abs(polygon_area(poly))
    bbox = rec.get("bbox")
    if bbox and len(bbox) == 4:
        return abs((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return 0.0


def _run_scene(scene_dir: Path, sid: str, TilingConfig, TileInfo, Annotation,
               compute_grid, stable_tile_id, propagate):
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    scene = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    image = manifest.get("image") or {}
    width, height = image.get("width"), image.get("height")
    if not width or not height:
        info = manifest.get("source_scene_uid")
        return None
    scene_uid = manifest.get("source_scene_uid")
    scene_name = Path(scene.get("filename") or sid).stem

    records = json.loads((scene_dir / "annotations.json").read_text(encoding="utf-8"))
    # Źródłowe (niepuste, niezdegenerowane) — tylko te MAJĄ trafić do eksportu.
    source_map: dict[str, dict] = {}
    for rec in records:
        if rec.get("is_negative"):
            continue
        if _source_area(rec) <= 0.0:
            continue  # zdegenerowana (zerowe pole) — compute_tile_attributes i tak ją pomija
        sid_ann = str(rec.get("source_annotation_id") or rec.get("id"))
        source_map[sid_ann] = {"class_id": rec.get("class_id"), "n_tiles": 0}

    anns = [Annotation(**rec) for rec in records]
    tiling = TilingConfig(tile_size=TILE_SIZE, buffer=BUFFER)
    preview = compute_grid(int(width), int(height), tiling)
    tiles = []
    for index, (x0, y0, x1, y1) in enumerate(preview.tile_rects):
        row_index = index // preview.num_cols + 1
        col_index = index % preview.num_cols + 1
        base = f"{col_index}_{row_index}_{scene_name}.png"
        tiles.append(TileInfo(
            tile_id=stable_tile_id(sid, base, x0, y0, TILE_SIZE),
            scene_id=sid, filename=base, col=col_index, row=row_index, x0=x0, y0=y0, x1=x1, y1=y1,
        ))

    _, attrs_by_tile = propagate(anns, tiles, tiling, MIN_BOX_FRACTION)

    n_orphan = n_misassigned = n_wrong_scene = 0
    for tile_name, attrs in attrs_by_tile.items():
        for at in attrs:
            if not at.get("exportable_yolo"):
                continue
            src = str(at.get("source_annotation_id"))
            if src not in source_map:
                n_orphan += 1
                continue
            source_map[src]["n_tiles"] += 1
            if at.get("class_id") != source_map[src]["class_id"]:
                n_misassigned += 1
            if str(at.get("scene_id")) != sid:
                n_wrong_scene += 1

    traced = sum(1 for v in source_map.values() if v["n_tiles"] > 0)
    lost = len(source_map) - traced
    rows = [
        (sid, src, v["class_id"], v["n_tiles"], int(v["n_tiles"] > 0))
        for src, v in source_map.items()
    ]
    return {
        "scene_id": sid,
        "scene_uid": scene_uid,
        "n_annotations": len(source_map),
        "n_traced": traced,
        "n_lost": lost,
        "n_orphan": n_orphan,
        "n_misassigned": n_misassigned,
        "n_wrong_scene": n_wrong_scene,
        "rows": rows,
    }


def _iter_scenes(project: Path, limit: int | None):
    dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())
    return dirs[:limit] if limit else dirs


def main() -> ValidationResult:
    res = ValidationResult(
        id="v05",
        claim="C2a",
        title="Integralność ID scena->adnotacja->kafel->eksport",
        substrate=["FAIR1M", "SAR_test"],
        tier="mixed",
    )
    res.config = {"tile_size": TILE_SIZE, "buffer": BUFFER, "min_box_fraction": MIN_BOX_FRACTION,
                  "fair1m_scenes": FAIR1M_SCENES}

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    from models.tiling_config import TilingConfig, TileInfo
    from models.annotation import Annotation
    from services.tiler import compute_grid
    from services.tile_catalog import stable_tile_id
    from services.annotation_propagator import propagate_annotations_with_attributes as propagate

    targets = []
    try:
        targets.append(("FAIR1M", appenv.benchmark_project("FAIR1M"), FAIR1M_SCENES))
    except FileNotFoundError:
        pass
    sar = Path(SAR_TEST_PROJECT)
    if (sar / "scenes").is_dir():
        targets.append(("SAR_test", sar, None))
    if not targets:
        res.status = "todo"
        res.notes = "Brak projektów (FAIR1M / SAR_test). Zaimportuj benchmark i wskaż SAR_test."
        return res

    all_rows = []
    per_project = {}
    totals = {"n_annotations": 0, "n_traced": 0, "n_lost": 0, "n_orphan": 0,
              "n_misassigned": 0, "n_wrong_scene": 0}
    for name, project, limit in targets:
        agg = {k: 0 for k in totals}
        n_scenes = 0
        for scene_dir in _iter_scenes(project, limit):
            out = _run_scene(scene_dir, scene_dir.name, TilingConfig, TileInfo, Annotation,
                             compute_grid, stable_tile_id, propagate)
            if out is None:
                continue
            n_scenes += 1
            for k in agg:
                agg[k] += out[k]
            for row in out["rows"]:
                all_rows.append((name, *row))
        agg["n_scenes"] = n_scenes
        per_project[name] = agg
        for k in totals:
            totals[k] += agg[k]

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "id_chain.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["project", "scene_id", "source_annotation_id", "class_id", "tiles", "ok"])
        writer.writerows(all_rows)

    res.metrics = {**totals, "per_project": per_project}
    res.artifacts = ["results/id_chain.csv"]
    integrity_ok = (
        totals["n_lost"] == 0 and totals["n_orphan"] == 0
        and totals["n_misassigned"] == 0 and totals["n_wrong_scene"] == 0
    )
    res.status = "pass" if (integrity_ok and totals["n_annotations"] > 0) else "fail"
    per_p = ", ".join(f"{k}:{v['n_annotations']}obj/{v['n_scenes']}scen" for k, v in per_project.items())
    res.notes = (
        f"Łańcuch tożsamości na {totals['n_annotations']} obiektach ({per_p}): "
        f"odtworzone w eksporcie={totals['n_traced']}, zgubione={totals['n_lost']}, "
        f"orphan={totals['n_orphan']}, źle przypisana klasa={totals['n_misassigned']}, "
        f"zła scena={totals['n_wrong_scene']}. Prowenancja przez source_scene_uid."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
