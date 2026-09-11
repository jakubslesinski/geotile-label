"""Panel „Pliki robocze scen" — klasyfikacja, sumy i gwarancja read-only.

Bramka B0 planu: kazdy plik fixture nalezy do DOKLADNIE jednej kategorii, suma kategorii
rowna sie `total`, a plik spoza `derived_scenes` nie jest liczony. Bez tego ten sam
`.tif` moglby zostac policzony i jako COG, i jako materializowany widok, a gigabajty
przerwanej budowy zniknelyby w „inne".

Rozmiary plikow fixture sa rozne i pierwsze, zeby zla przynaleznosc kategorii nie mogla
sie zamaskowac przypadkowo rowna suma.
"""

from __future__ import annotations

import itertools
import os
import pathlib
import sys
import tempfile

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="geotile_working_storage_"))

from db.storage import create_project_root, project_paths, save_json  # noqa: E402
from services.scene_working_storage import (  # noqa: E402
    CATEGORY_DISPLAY_OVERVIEW,
    CATEGORY_FULLRES_COG,
    CATEGORY_INCOMPLETE,
    CATEGORY_MATERIALIZED_VIEW,
    CATEGORY_METADATA,
    CATEGORY_ORDER,
    CATEGORY_OTHER,
    CATEGORY_VIRTUAL_VIEW,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    classify_working_file,
    summarize_scene_working_storage,
)


# --- fixture projektu -----------------------------------------------------------------

#: Sciezka wzgledem `derived_scenes` -> (rozmiar, oczekiwana kategoria).
#: Nazwy pochodza z `working_view.py` i `fullres_cog_builder.py`, nie sa wymyslone.
FIXTURE: dict[str, tuple[int, str]] = {
    # scena A: generic JP2 z opublikowanym derywatem 1x
    "sceneA/variant-1/overview.vrt": (101, CATEGORY_VIRTUAL_VIEW),
    "sceneA/variant-1/overview.vrt.ovr": (10_007, CATEGORY_DISPLAY_OVERVIEW),
    "sceneA/variant-1/overview.profile.json": (211, CATEGORY_METADATA),
    "sceneA/variant-1/fullres/fullres.tif": (100_003, CATEGORY_FULLRES_COG),
    "sceneA/variant-1/fullres/fullres.state.json": (307, CATEGORY_METADATA),
    # scena B: pansharpening + slady po przerwanej budowie
    "sceneB/variant-1/rgb_pansharpened.cog.tif": (50_021, CATEGORY_MATERIALIZED_VIEW),
    "sceneB/variant-1/rgb_pansharpened.vrt": (103, CATEGORY_VIRTUAL_VIEW),
    "sceneB/variant-1/multispectral.vrt": (107, CATEGORY_VIRTUAL_VIEW),
    "sceneB/variant-1/panchromatic.vrt": (109, CATEGORY_VIRTUAL_VIEW),
    "sceneB/variant-1/processing_manifest.json": (401, CATEGORY_METADATA),
    "sceneB/variant-1/fullres/fullres.candidate.tif": (20_011, CATEGORY_INCOMPLETE),
    "sceneB/variant-1/fullres/fullres.raw": (30_013, CATEGORY_INCOMPLETE),
    "sceneB/variant-1/fullres/fullres.vrt": (113, CATEGORY_INCOMPLETE),
    "sceneB/variant-1/.overview.vrt.partial.vrt.ovr": (5_003, CATEGORY_INCOMPLETE),
    "sceneB/variant-1/.strip_00000000_00001024.raw": (40_009, CATEGORY_INCOMPLETE),
    "sceneB/variant-1/build.log": (127, CATEGORY_METADATA),
    "sceneB/variant-1/mystery.dat": (131, CATEGORY_OTHER),
}

#: Pliki, ktore MUSZA pozostac poza suma: zrodla, adnotacje, katalogi kafli.
OUTSIDE = {
    "scenes/sceneA/scene.json": 1_000_003,
    "scenes/sceneA/annotations.json": 2_000_003,
    "tile_catalogs/cat1/preview_cache/tile.png": 3_000_003,
    "source/big_source.jp2": 4_000_003,
    "source/big_source.jp2.ovr": 5_000_003,
}


def _write(path: pathlib.Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


_COUNTER = itertools.count()


def _new_project() -> str:
    """Osobny projekt na test: kilka testow dopisuje pliki do `derived_scenes`,
    a wspoldzielony katalog przenosilby te zmiany na sasiadow."""

    project_id = f"working_storage_{next(_COUNTER)}"
    create_project_root(project_id, project_id)
    save_json(project_id, "project", {"id": project_id, "name": project_id})
    return project_id


@pytest.fixture()
def project() -> str:
    project_id = _new_project()
    root = project_paths(project_id).root
    for relative, (size, _category) in FIXTURE.items():
        _write(root / "derived_scenes" / relative, size)
    for relative, size in OUTSIDE.items():
        _write(root / relative, size)
    return project_id


def _expected(category: str) -> tuple[int, int]:
    items = [item for item in FIXTURE.values() if item[1] == category]
    return sum(size for size, _ in items), len(items)


# --- klasyfikacja ---------------------------------------------------------------------


@pytest.mark.parametrize("relative,expected", [(k, v[1]) for k, v in FIXTURE.items()])
def test_every_fixture_file_lands_in_exactly_one_expected_category(relative, expected):
    assert classify_working_file(tuple(relative.split("/"))) == expected


def test_partial_beats_the_overview_rule():
    """Kolejnosc regul ma znaczenie: `.partial.vrt.ovr` to smiec po przerwanym
    zapisie, a nie dzialajaca piramida."""

    assert classify_working_file((".overview.vrt.partial.vrt.ovr",)) == CATEGORY_INCOMPLETE
    assert classify_working_file(("overview.vrt.ovr",)) == CATEGORY_DISPLAY_OVERVIEW


def test_fullres_cog_is_recognised_by_location_not_by_name():
    assert classify_working_file(("s", "v", "fullres", "fullres.tif")) == CATEGORY_FULLRES_COG
    # Ten sam plik poza katalogiem `fullres/` jest zwyklym materializowanym widokiem.
    assert classify_working_file(("s", "v", "fullres.tif")) == CATEGORY_MATERIALIZED_VIEW


def test_pansharpened_cog_is_not_counted_as_fullres():
    """Scenariusz D: nazwa zawiera `cog`, ale to nie jest derywat 1x."""

    assert (
        classify_working_file(("s", "v", "rgb_pansharpened.cog.tif"))
        == CATEGORY_MATERIALIZED_VIEW
    )


def test_unknown_extension_falls_through_to_other_so_the_sum_stays_closed():
    assert classify_working_file(("s", "v", "mystery.dat")) == CATEGORY_OTHER
    assert classify_working_file(("s", "v", "no_extension")) == CATEGORY_OTHER


def test_classification_is_case_insensitive_on_the_extension():
    assert classify_working_file(("s", "v", "overview.VRT.OVR")) == CATEGORY_DISPLAY_OVERVIEW
    assert classify_working_file(("s", "v", "scene.TIF")) == CATEGORY_MATERIALIZED_VIEW


# --- sumy -----------------------------------------------------------------------------


def test_contract_envelope(project):
    summary = summarize_scene_working_storage(project)
    assert summary["schema_name"] == SCHEMA_NAME
    assert summary["schema_version"] == SCHEMA_VERSION
    assert summary["project_id"] == project
    assert summary["directory_exists"] is True
    assert summary["working_files_dir"].endswith("derived_scenes")
    assert summary["generated_at"].endswith("Z")
    assert summary["scan_duration_ms"] >= 0
    assert summary["scan_errors"] == 0
    assert set(summary["categories"]) == set(CATEGORY_ORDER)


def test_category_sums_match_the_fixture_byte_for_byte(project):
    categories = summarize_scene_working_storage(project)["categories"]
    for name in CATEGORY_ORDER:
        expected_bytes, expected_files = _expected(name)
        assert categories[name]["bytes"] == expected_bytes, name
        assert categories[name]["file_count"] == expected_files, name


def test_categories_sum_to_total(project):
    """Bramka B0: kazdy plik policzony dokladnie raz, ani mniej, ani wiecej."""

    summary = summarize_scene_working_storage(project)
    assert sum(item["bytes"] for item in summary["categories"].values()) == summary["total"]["bytes"]
    assert (
        sum(item["file_count"] for item in summary["categories"].values())
        == summary["total"]["file_count"]
    )
    assert summary["total"]["bytes"] == sum(size for size, _ in FIXTURE.values())
    assert summary["total"]["file_count"] == len(FIXTURE)
    assert summary["total"]["scene_count"] == 2


def test_files_outside_derived_scenes_are_never_counted(project):
    """Scenariusz F: zrodlo i sidecar QGIS sa o rzedy wielkosci wieksze niz derywaty,
    wiec ich policzenie byloby widoczne od razu."""

    summary = summarize_scene_working_storage(project)
    assert summary["total"]["bytes"] < min(OUTSIDE.values())


def test_scene_count_per_category_counts_scenes_not_files(project):
    categories = summarize_scene_working_storage(project)["categories"]
    # Piramida jest tylko u sceny A, a pliki przejsciowe tylko u sceny B.
    assert categories[CATEGORY_DISPLAY_OVERVIEW]["scene_count"] == 1
    assert categories[CATEGORY_INCOMPLETE]["scene_count"] == 1
    # Metadane ma kazda ze scen, mimo ze plikow jest wiecej niz scen.
    assert categories[CATEGORY_METADATA]["scene_count"] == 2
    assert categories[CATEGORY_METADATA]["file_count"] == 4


def test_file_directly_under_derived_scenes_counts_in_total_but_has_no_scene(project):
    root = project_paths(project).root
    _write(root / "derived_scenes" / "stray.json", 17)
    summary = summarize_scene_working_storage(project)
    assert summary["total"]["bytes"] == sum(size for size, _ in FIXTURE.values()) + 17
    assert summary["total"]["scene_count"] == 2
    assert summary["categories"][CATEGORY_METADATA]["scene_count"] == 2


# --- najwieksze sceny -----------------------------------------------------------------


def test_largest_scenes_are_ordered_by_size(project):
    largest = summarize_scene_working_storage(project)["largest_scenes"]
    assert [item["scene_id"] for item in largest] == ["sceneB", "sceneA"]
    assert largest[0]["bytes"] > largest[1]["bytes"]
    assert largest[0]["file_count"] == 12


def test_largest_scene_categories_only_list_what_the_scene_actually_has(project):
    largest = summarize_scene_working_storage(project)["largest_scenes"]
    scene_a = next(item for item in largest if item["scene_id"] == "sceneA")
    assert set(scene_a["categories"]) == {
        CATEGORY_FULLRES_COG,
        CATEGORY_DISPLAY_OVERVIEW,
        CATEGORY_VIRTUAL_VIEW,
        CATEGORY_METADATA,
    }
    assert sum(scene_a["categories"].values()) == scene_a["bytes"]


def test_largest_limit_truncates_without_touching_the_totals(project):
    summary = summarize_scene_working_storage(project, largest_limit=1)
    assert len(summary["largest_scenes"]) == 1
    assert summary["total"]["scene_count"] == 2


def test_largest_limit_zero_returns_an_empty_list(project):
    assert summarize_scene_working_storage(project, largest_limit=0)["largest_scenes"] == []


def test_largest_limit_is_clamped_to_the_documented_ceiling(project):
    # Serwis broni sie sam, niezaleznie od walidacji FastAPI na endpoincie.
    assert summarize_scene_working_storage(project, largest_limit=10_000)["largest_scenes"]


def test_display_name_comes_from_the_scenes_index_when_present(project):
    import json

    root = project_paths(project).root
    (root / "scenes_index.json").write_text(
        json.dumps({"scenes": [{"scene_id": "sceneB", "display_name": "ARSENYEV_0304.jp2"}]}),
        encoding="utf-8",
    )
    largest = summarize_scene_working_storage(project)["largest_scenes"]
    assert largest[0]["display_name"] == "ARSENYEV_0304.jp2"
    # Scena bez wpisu nie psuje odpowiedzi — nazwa jest ozdoba, nie trescia.
    assert largest[1]["display_name"] is None


def test_a_broken_scenes_index_does_not_break_the_panel(project):
    root = project_paths(project).root
    (root / "scenes_index.json").write_text("{ not json", encoding="utf-8")
    summary = summarize_scene_working_storage(project)
    assert summary["total"]["file_count"] == len(FIXTURE)
    assert all(item["display_name"] is None for item in summary["largest_scenes"])


# --- brak katalogu, bledy, read-only --------------------------------------------------


def test_missing_directory_is_a_valid_zero_answer_not_an_error():
    project_id = _new_project()
    summary = summarize_scene_working_storage(project_id)
    assert summary["directory_exists"] is False
    assert summary["total"] == {"bytes": 0, "file_count": 0, "scene_count": 0}
    assert summary["largest_scenes"] == []
    assert all(item["bytes"] == 0 for item in summary["categories"].values())


def test_reading_a_project_without_derived_scenes_does_not_create_it():
    """Panel jest odczytem. Zalozenie katalogu tylko po to, zeby go zmierzyc, kasowaloby
    roznice miedzy „nic nie zbudowano" a „zbudowano i skasowano"."""

    project_id = _new_project()
    summarize_scene_working_storage(project_id)
    assert not (project_paths(project_id).root / "derived_scenes").exists()


def test_unreadable_entry_is_counted_but_does_not_abort_the_scan(project, monkeypatch):
    import services.scene_working_storage as module

    real_scandir = os.scandir
    target = str(project_paths(project).root / "derived_scenes" / "sceneA")

    def failing_scandir(path):
        if str(path) == target:
            raise PermissionError(target)
        return real_scandir(path)

    monkeypatch.setattr(module.os, "scandir", failing_scandir)
    summary = summarize_scene_working_storage(project)
    assert summary["scan_errors"] == 1
    # Scena B nadal policzona w calosci — jeden bledny katalog nie kasuje odpowiedzi.
    scene_b_bytes = sum(
        size for path, (size, _) in FIXTURE.items() if path.startswith("sceneB/")
    )
    assert summary["total"]["bytes"] == scene_b_bytes


def test_failing_stat_is_counted_as_a_scan_error(project, monkeypatch):
    import services.scene_working_storage as module

    real_walk = module._walk

    def walk_with_one_bad_file(root):
        for parts, info in real_walk(root):
            yield (parts, None) if parts[-1] == "overview.vrt" else (parts, info)

    monkeypatch.setattr(module, "_walk", walk_with_one_bad_file)
    summary = summarize_scene_working_storage(project)
    assert summary["scan_errors"] == 1
    assert summary["total"]["file_count"] == len(FIXTURE) - 1


def test_scan_does_not_follow_directory_symlinks(project, tmp_path):
    """Dowiazanie do zrodel policzyloby cudzy katalog jako derywat, a petla zawiesilaby
    skan. Bez uprawnien do symlinkow na Windowsie test sie pomija."""

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge.tif").write_bytes(b"y" * 999_983)
    link = project_paths(project).root / "derived_scenes" / "sceneA" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("brak uprawnien do tworzenia dowiazan symbolicznych")

    summary = summarize_scene_working_storage(project)
    assert summary["total"]["bytes"] == sum(size for size, _ in FIXTURE.values())


def _tree_fingerprint(root: pathlib.Path) -> list[tuple[str, int, int]]:
    return sorted(
        (
            str(path.relative_to(root)),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    )


def test_two_reads_leave_the_tree_bit_identical(project):
    """Bramka P0: po dwoch odczytach nazwa, rozmiar i mtime kazdego pliku sa te same."""

    working_dir = project_paths(project).root / "derived_scenes"
    before = _tree_fingerprint(working_dir)
    summarize_scene_working_storage(project)
    summarize_scene_working_storage(project)
    assert _tree_fingerprint(working_dir) == before


def test_scan_never_reads_file_contents(project, monkeypatch):
    """Czas skanu ma zalezec od liczby plikow, nie od ich rozmiaru. Otwarcie
    ktoregokolwiek pliku w `derived_scenes` jest bledem, nie optymalizacja."""

    working_dir = str(project_paths(project).root / "derived_scenes")
    real_open = open
    opened: list[str] = []

    def tracking_open(file, *args, **kwargs):
        if str(file).startswith(working_dir):
            opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    summarize_scene_working_storage(project)
    assert opened == []


# --- endpoint -------------------------------------------------------------------------


def _endpoint():
    """Import routera dopiero tutaj: reszta pliku testuje sam serwis i nie ma powodu
    ciagnac za soba calego `routers.projects`."""

    from routers.projects import get_scene_working_storage

    return get_scene_working_storage


def test_endpoint_returns_the_service_contract(project):
    payload = _endpoint()(project, largest_limit=10)
    assert payload["schema_name"] == SCHEMA_NAME
    assert payload["total"]["file_count"] == len(FIXTURE)


def test_endpoint_404s_for_an_unknown_project():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _endpoint()("nie_ma_takiego_projektu", largest_limit=10)
    assert excinfo.value.status_code == 404


def test_endpoint_is_a_plain_def_so_fastapi_runs_the_scan_off_the_event_loop():
    """Skan katalogu to praca blokujaca. Jako `async def` zatrzymalby petle zdarzen
    razem z kaflami i zadaniami w tle — dokladnie to, przed czym broni sie plan."""

    import inspect

    assert not inspect.iscoroutinefunction(_endpoint())


def test_endpoint_declares_the_documented_largest_limit_bounds():
    """Granice 0..25 sa czescia kontraktu: bez gornej frontend moglby poprosic o
    tysiace wierszy i zamienic lekki panel w wielka odpowiedz."""

    import inspect

    query = inspect.signature(_endpoint()).parameters["largest_limit"].default
    assert query.default == 10
    bounds = {type(item).__name__: item for item in query.metadata}
    assert bounds["Ge"].ge == 0
    assert bounds["Le"].le == 25
