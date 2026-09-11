"""P0 — baseline serwowania kafli scen (DESIGN_DECISIONS.md, tile-serving).

Mierzy czas produkcji kafla `scene-tiles` per poziom zoomu oraz szczytowy przyrost RSS
na wywolanie. Wola dokladnie te funkcje co endpoint (`_read_geotiff_window`), wiec
mierzy realna sciezke, nie przyblizenie. Niczego nie zmienia w aplikacji.

Uruchomienie wymaga srodowiska conda (GDAL/PROJ) i DATA_DIR wskazujacego dane:
patrz README_dev.md, sekcja o benchmarku kafli.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from db.storage import load_scene_json  # noqa: E402
from routers.scenes import (  # noqa: E402
    TILE_SIZE,
    _compute_max_zoom,
    _read_geotiff_window,
)
from services.scene_raster_resolver import resolve_scene_raster  # noqa: E402

# (project_id, scene_id, etykieta) — trzy kategorie z planu plus kontrprzyklady.
SCENES = [
    ("5543ab675134", "9fca8d60eb8e", "PNEO-direct  striped/no-ovr"),
    ("889f9094ee8a", "68bf9f7e25c9", "Capella      tiled+overviews"),
    ("5543ab675134", "57e875bc88e5", "PNEO-vrt-cog tiled+overviews"),
    ("889f9094ee8a", "f0cd18b5344d", "UMBRA small  tiled+overviews"),
]

REPEATS = 5
proc = psutil.Process()


def center_tile(z: int, max_zoom: int, w: int, h: int) -> tuple[int, int, int, int, int, int]:
    ppt = TILE_SIZE * (2 ** (max_zoom - z))
    ntx = max(1, -(-w // ppt))
    nty = max(1, -(-h // ppt))
    x, y = ntx // 2, nty // 2
    x0 = max(0, x * ppt)
    y0 = max(0, y * ppt)
    x1 = min(w, (x + 1) * ppt)
    y1 = min(h, (y + 1) * ppt)
    return x0, y0, x1, y1, ppt, ntx * nty


def bench_scene(project_id: str, scene_id: str, label: str) -> None:
    path = resolve_scene_raster(project_id, scene_id)
    scene = load_scene_json(project_id, scene_id, "scene")
    si = scene.get("scene_info") or {}
    w, h = si["width"], si["height"]
    max_zoom = _compute_max_zoom(w, h)

    print(f"\n=== {label} ===")
    print(f"    {path.name}  {w}x{h}  max_zoom={max_zoom}")
    print(f"    {'zoom':>4} {'ppt':>7} {'tiles':>6} {'p50 ms':>8} {'p95 ms':>8} {'dRSS MB':>8}")

    for z in range(0, max_zoom + 1):
        x0, y0, x1, y1, ppt, ntiles = center_tile(z, max_zoom, w, h)
        out_w = max(1, min(TILE_SIZE, round((x1 - x0) / ppt * TILE_SIZE)))
        out_h = max(1, min(TILE_SIZE, round((y1 - y0) / ppt * TILE_SIZE)))

        # rozgrzewka (cache systemu plikow), potem pomiar
        _read_geotiff_window(path, si, x0, y0, x1, y1, TILE_SIZE, out_w, out_h)
        times, rss = [], []
        for _ in range(REPEATS):
            before = proc.memory_info().rss
            t = time.perf_counter()
            _read_geotiff_window(path, si, x0, y0, x1, y1, TILE_SIZE, out_w, out_h)
            times.append((time.perf_counter() - t) * 1000.0)
            rss.append((proc.memory_info().rss - before) / 1e6)

        times.sort()
        p50 = statistics.median(times)
        p95 = times[min(len(times) - 1, round(0.95 * (len(times) - 1)))]
        print(f"    {z:>4} {ppt:>7} {ntiles:>6} {p50:>8.1f} {p95:>8.1f} {max(rss):>8.1f}")


def main() -> None:
    for project_id, scene_id, label in SCENES:
        try:
            bench_scene(project_id, scene_id, label)
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== {label} ===\n    BLAD: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
