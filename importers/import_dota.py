"""CLI: zaimportuj DOTA (v2.0 OBB) jako projekt GeoTile Label.

Uruchamiaj interpreterem środowiska backendu (PIL/rasterio):

    & "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" \
        importers/import_dota.py --limit 3

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
    parser = argparse.ArgumentParser(description="Import DOTA -> projekt GeoTile Label")
    parser.add_argument("--dota-root", default=str(_DATASETS / "DOTA"),
                        help="Katalog z <split>/images i <split>/annotations/<version>")
    parser.add_argument("--out", default=_BENCHMARK_PROJECTS,
                        help="Lokalizacja (project_location), w której powstanie folder projektu")
    parser.add_argument("--backend", default=str(_REPO / "backend"))
    parser.add_argument("--name", default="DOTA")
    parser.add_argument("--splits", default="train,val", help="Splity DOTA (przecinki)")
    parser.add_argument("--version", default="version2.0", help="Wersja adnotacji OBB")
    parser.add_argument("--limit", type=int, default=None, help="Ogranicz liczbę scen na split (test)")
    parser.add_argument("--author", default=None)
    args = parser.parse_args()

    _autoconfigure_geo_env()
    os.environ.setdefault("DATA_DIR", str(Path(args.out)))
    os.environ.setdefault("SCENES_ROOT", str(Path(args.dota_root)))
    os.environ.setdefault("GEOTILE_APP_VERSION", "import-dota")

    sys.path.insert(0, str(Path(__file__).parent))
    from dota.build import build_project

    result = build_project(
        args.dota_root,
        args.out,
        name=args.name,
        splits=[s.strip() for s in args.splits.split(",") if s.strip()],
        version=args.version,
        limit=args.limit,
        author_email=args.author,
        backend_path=args.backend,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
