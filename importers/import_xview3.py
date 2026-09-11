"""CLI: zaimportuj xView3 (SAR) jako projekt GeoTile Label.

Uruchamiaj interpreterem środowiska backendu (rasterio/GDAL + osgeo):

    & "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" \
        importers/import_xview3.py --splits validation --limit 1

Uwaga: pełny import hashuje duże pliki VV_dB (prowenansja) — wolne dla całego zbioru.
GDAL_DATA/PROJ_LIB wykrywane automatycznie z prefiksu conda.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Domyslne sciezki liczone wzgledem repozytorium — importery sa czescia publicznego
# wydania i nie moga niesc ukladu katalogow jednej stacji. Kazda z nich jest
# nadpisywalna argumentem, a katalog projektow takze zmienna BENCHMARK_PROJECTS_ROOT
# (te sama, ktorej uzywa suite walidacyjny).
_REPO = Path(__file__).resolve().parents[1]
_DATASETS = _REPO / "data" / "datasets"
_BENCHMARK_PROJECTS = os.environ.get(
    "BENCHMARK_PROJECTS_ROOT", str(_REPO / "data" / "benchmark_projects")
)


def _autoconfigure_geo_env() -> None:
    env_root = Path(sys.prefix)
    gdal_data = env_root / "Library" / "share" / "gdal"
    proj_lib = env_root / "Library" / "share" / "proj"
    if gdal_data.is_dir():
        os.environ.setdefault("GDAL_DATA", str(gdal_data))
    if proj_lib.is_dir():
        os.environ.setdefault("PROJ_LIB", str(proj_lib))
        os.environ.setdefault("PROJ_DATA", str(proj_lib))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import xView3 -> projekt GeoTile Label")
    parser.add_argument("--xview3-root", default=str(_DATASETS / "xView3"),
                        help="Katalog z scenes/<split>/<scene_id> i labels/<split>.csv")
    parser.add_argument("--out", default=_BENCHMARK_PROJECTS,
                        help="Lokalizacja (project_location), w której powstanie folder projektu")
    parser.add_argument("--backend", default=str(_REPO / "backend"))
    parser.add_argument("--name", default="xView3")
    parser.add_argument("--splits", default="train,validation", help="Splity (przecinki)")
    parser.add_argument("--limit", type=int, default=None, help="Ogranicz liczbę scen na split (test)")
    parser.add_argument("--author", default=None)
    args = parser.parse_args()

    _autoconfigure_geo_env()
    os.environ.setdefault("DATA_DIR", str(Path(args.out)))
    os.environ.setdefault("SCENES_ROOT", str(Path(args.xview3_root) / "scenes"))
    os.environ.setdefault("GEOTILE_APP_VERSION", "import-xview3")

    sys.path.insert(0, str(Path(__file__).parent))
    from xview3.build import build_project

    result = build_project(
        args.xview3_root,
        args.out,
        name=args.name,
        splits=[s.strip() for s in args.splits.split(",") if s.strip()],
        limit=args.limit,
        author_email=args.author,
        backend_path=args.backend,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
