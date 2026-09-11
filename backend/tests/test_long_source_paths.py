"""Zrodla o sciezkach dluzszych niz MAX_PATH (260 znakow) musza byc widoczne.

Windows bez prefiksu rozszerzonego tnie sciezki na 260 znakach w sposob, ktory NIE
zglasza bledu: `os.scandir` zwraca jeszcze nazwy, ale `is_file()`/`exists()`/`stat()`
na pelnej sciezce zawodza z WinError 3. Pliki wypadaja z inwentarza cicho.

Realny przypadek: dostawa PNEO, w ktorej cztery rastry mialy 282 znaki — resolver
proponowal do wyboru wylacznie logotypy, bo tylko one miescily sie ponizej limitu.
Bliznacza dostawa o krotszej nazwie folderu dzialala poprawnie.

Run: python backend/tests/test_long_source_paths.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-longpath-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import save_json  # noqa: E402
from services.scene_sources import (  # noqa: E402
    extended_path,
    plain_path,
    resolve_source_asset,
    save_scene_sources,
)

IS_WINDOWS = os.name == "nt"
DEEP_SEGMENT = "Kronsztad_Minesweeper Tactical Group_Small Anti-Submarine Ship Group"
RASTER_NAME = "IMG_PNEO3_202204300932140_PMS-FS_ORT_d3f7d090-5edc-4a48-caef-d96b5b2b9efd_RGB_R1C1.TIF"


def _deep_source(project_id: str) -> tuple[pathlib.Path, str]:
    """Zrodlo, ktorego raster ma pelna sciezke powyzej 260 znakow."""
    root = TEST_ROOT / "src" / DEEP_SEGMENT
    relative = pathlib.Path(DEEP_SEGMENT) / "IMG_01_PNEO3_PMS-FS" / RASTER_NAME
    target = root / relative
    # Utworzenie tez wymaga prefiksu — bez niego `mkdir` konczy sie WinError 3.
    extended_path(target.parent).mkdir(parents=True, exist_ok=True)
    extended_path(target).write_bytes(b"II*\x00 nie-raster, wystarczy plik")

    save_json(project_id, "project", {"id": project_id, "name": project_id})
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src",
        "provider": "pleiades_neo",
        "root_path": str(root),
        "enabled": True,
    }]})
    return target, relative.as_posix()


def test_extended_prefix_round_trips():
    plain = r"\\fileserver\share\deliveries\EO" if IS_WINDOWS else "/mnt/dane"
    assert plain_path(extended_path(plain)) == plain
    local = r"C:\dane\PORTY" if IS_WINDOWS else "/mnt/dane/PORTY"
    assert plain_path(extended_path(local)) == local
    # Prefiks nakladany dwa razy to nadal jedna sciezka.
    assert extended_path(extended_path(local)) == extended_path(local)


def test_plain_path_leaves_ordinary_paths_alone():
    value = r"C:\dane\PORTY" if IS_WINDOWS else "/mnt/dane/PORTY"
    assert plain_path(value) == value


def test_asset_above_max_path_is_resolved():
    target, relative = _deep_source("longpath-project")
    if IS_WINDOWS:
        assert len(str(target)) > 260, f"fixture za krotki: {len(str(target))}"
        # Dowod, ze bez prefiksu problem naprawde istnieje.
        assert not os.path.exists(str(target))

    resolved = resolve_source_asset("longpath-project", "src", relative)
    assert resolved is not None, "zasob powyzej MAX_PATH nie zostal odnaleziony"
    assert resolved.is_file()
    assert resolved.name == RASTER_NAME


def test_escape_from_source_root_is_still_rejected():
    """Prefiks nie moze rozluznic kontroli ucieczki z korzenia zrodla."""
    _deep_source("longpath-escape")
    assert resolve_source_asset("longpath-escape", "src", "../../etc/passwd") is None
    assert resolve_source_asset("longpath-escape", "src", "..") is None
    absolute = r"C:\Windows\win.ini" if IS_WINDOWS else "/etc/passwd"
    assert resolve_source_asset("longpath-escape", "src", absolute) is None


def _run() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[OK  ] {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - raport testowy
            failed += 1
            print(f"[BLAD] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} przeszlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
