"""CLI: zaimportuj DIOR-R (OBB) jako projekt GeoTile Label.

    & "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" \
        importers/import_diorr.py --limit 3
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
    parser = argparse.ArgumentParser(description="Import DIOR-R -> projekt GeoTile Label")
    parser.add_argument("--diorr-root", default=str(_DATASETS / "DIOR-R"),
                        help="Katalog z Annotations/'Oriented Bounding Boxes', ImageSets/Main, JPEGImages-*")
    parser.add_argument("--classes-file", default=str(_DATASETS / "DIOR-R" / "classes.txt"),
                        help="Lista 20 klas (kolejność); jeśli brak — zebrane z XML")
    parser.add_argument("--out", default=_BENCHMARK_PROJECTS)
    parser.add_argument("--backend", default=str(_REPO / "backend"))
    parser.add_argument("--name", default="DIOR-R")
    parser.add_argument("--splits", default="train,val", help="Splity (train,val,test)")
    parser.add_argument("--limit", type=int, default=None, help="Ogranicz liczbę scen na split (test)")
    parser.add_argument("--author", default=None)
    args = parser.parse_args()

    _autoconfigure_geo_env()
    os.environ.setdefault("DATA_DIR", str(Path(args.out)))
    os.environ.setdefault("SCENES_ROOT", str(Path(args.diorr_root)))
    os.environ.setdefault("GEOTILE_APP_VERSION", "import-diorr")

    sys.path.insert(0, str(Path(__file__).parent))
    from diorr.build import build_project

    result = build_project(
        args.diorr_root,
        args.out,
        name=args.name,
        splits=[s.strip() for s in args.splits.split(",") if s.strip()],
        classes_file=args.classes_file,
        limit=args.limit,
        author_email=args.author,
        backend_path=args.backend,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
