"""CLI: zaimportuj FAIR1M jako projekt GeoTile Label.

Uruchamiaj interpreterem środowiska backendu (rasterio/GDAL), np. runtime aplikacji:

    & "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" \
        importers/import_fair1m.py --limit 3

GDAL_DATA/PROJ_LIB wykrywane są automatycznie z prefiksu środowiska conda.
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
    """Ustaw GDAL_DATA/PROJ_LIB z Library/share środowiska conda (jeśli nie ustawione).

    Musi zadziałać PRZED importem rasterio (patrz memory backend-tests-proj-env).
    """
    env_root = Path(sys.prefix)
    gdal_data = env_root / "Library" / "share" / "gdal"
    proj_lib = env_root / "Library" / "share" / "proj"
    if gdal_data.is_dir():
        os.environ.setdefault("GDAL_DATA", str(gdal_data))
    if proj_lib.is_dir():
        os.environ.setdefault("PROJ_LIB", str(proj_lib))
        os.environ.setdefault("PROJ_DATA", str(proj_lib))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import FAIR1M -> projekt GeoTile Label")
    parser.add_argument("--fair1m-root", default=str(_DATASETS / "FAIR1M"),
                        help="Katalog z data/images i data/labelXmls")
    parser.add_argument("--out", default=_BENCHMARK_PROJECTS,
                        help="Lokalizacja (project_location), w której powstanie folder projektu")
    parser.add_argument("--backend", default=str(_REPO / "backend"),
                        help="Ścieżka do backendu aplikacji (import writerów)")
    parser.add_argument("--name", default="FAIR1M", help="Nazwa projektu")
    parser.add_argument("--limit", type=int, default=None, help="Ogranicz liczbę scen (test)")
    parser.add_argument("--author", default=None, help="labeling_author_email (opcjonalnie)")
    args = parser.parse_args()

    _autoconfigure_geo_env()
    # DATA_DIR trzyma rejestr projektów (projects_index.json) obok wyniku importu.
    os.environ.setdefault("DATA_DIR", str(Path(args.out)))
    os.environ.setdefault("SCENES_ROOT", str(Path(args.fair1m_root) / "data" / "images"))
    os.environ.setdefault("GEOTILE_APP_VERSION", "import-fair1m")

    # import po ustawieniu env
    sys.path.insert(0, str(Path(__file__).parent))
    from fair1m.build import build_project

    result = build_project(
        args.fair1m_root,
        args.out,
        name=args.name,
        limit=args.limit,
        author_email=args.author,
        backend_path=args.backend,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
