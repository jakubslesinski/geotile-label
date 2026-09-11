"""Discovery archiwow bez rozpakowywania (DESIGN_DECISIONS.md, scene-import P1.3a).

Etap zostal przyciety po pomiarze korpusu: 33 archiwa w czterech zrodlach, z czego 32 to
duplikaty juz rozpakowanych dostaw, a jedno — jedyna paczka PNEO w calym korpusie — jest
rozpakowane w 2 z 16 plikow i przez to nie istnieje dla aplikacji. Ekstrakcja z archiwum
(P1.3b) jest odlozona, wiec zaden test w tym pliku nie rozpakowuje ani bajtu.

Bramka P1.3a i jej testy:

- ZIP powiazany z rozpakowanym katalogiem → `test_archive_next_to_extracted_delivery_*`,
- niepelna ekstrakcja widoczna z lista brakow → `test_incomplete_extraction_*`,
- archiwum nigdy jako raster ani scena → `test_archive_is_never_*`,
- wpisy niebezpieczne i indeks obciety raportowane → `test_unsafe_entries_*`, `test_truncated_*`,
- brak zapisu do zrodla → `test_discovery_does_not_write_to_the_source`,
- istniejace pakiety bez zmian → `test_archive_does_not_change_the_delivery_package`.

Uruchomienie: pytest backend/tests/test_archive_discovery.py
"""

from __future__ import annotations

import pathlib
import sys
import zipfile

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))

from fixtures.scene_packages import (  # noqa: E402
    build_airbus_archive_incomplete,
    build_airbus_archive_only,
    build_archive_with_unsafe_entries,
    build_worldview_archive_duplicate,
)
from db import storage  # noqa: E402
from services.scene_packages import archives  # noqa: E402
from services.scene_packages.contracts import FLAG_GRAPH_V2, ROLE_ARCHIVE  # noqa: E402
from services.scene_packages.resolvers import scan_source  # noqa: E402
from services.scene_packages.scan_cache import scan_source_cached  # noqa: E402


@pytest.fixture
def graph_v2(monkeypatch):
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")


def _scan(tmp_path: pathlib.Path, builder, name: str):
    workspace = tmp_path / name
    workspace.mkdir(parents=True)
    package = builder(workspace)
    packages, _diagnostics = scan_source(package.root, package.provider)
    return package, packages


def _archives(packages: list[dict]) -> list[dict]:
    return [item for item in packages if item.get("package_kind") == "archive"]


def _deliveries(packages: list[dict]) -> list[dict]:
    return [item for item in packages if item.get("package_kind") != "archive"]


# --- klasyfikacja nazw wpisow ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../escape.tif",
        "a/../../escape.tif",
        "/absolute.tif",
        "\\windows\\style.tif",
        "dir\\sub\\file.tif",
        "C:/drive.tif",
        "",
    ],
)
def test_unsafe_entry_names_are_rejected(name):
    assert archives.is_unsafe_entry(name) is True


@pytest.mark.parametrize(
    "name",
    ["IMG/legit.tif", "a/b/c/deep.TIF", "plik z spacja.xml", "0140_01/_PAN/R1C1.TIF"],
)
def test_ordinary_entry_names_are_accepted(name):
    assert archives.is_unsafe_entry(name) is False


def test_dotdot_is_only_rejected_as_a_whole_path_segment():
    # `..cache` nie wychodzi w gore drzewa; odrzucanie go byloby falszywym alarmem.
    assert archives.is_unsafe_entry("dir/..cache/file.tif") is False


# --- indeks archiwum ------------------------------------------------------------------


def test_index_reads_only_the_central_directory(tmp_path):
    target = tmp_path / "delivery.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("IMG/R1C1.TIF", b"r" * 100)
        archive.writestr("IMG/meta.xml", "<x/>")
    index = archives.read_archive_index(target)
    assert index.is_readable
    assert index.entry_count == 2
    assert index.uncompressed_bytes == 104
    assert index.delivery_root == "IMG"


def test_index_excludes_unsafe_entries_but_reports_them(tmp_path):
    target = tmp_path / "delivery.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../escape.tif", b"x")
        archive.writestr("IMG/R1C1.TIF", b"r")
    index = archives.read_archive_index(target)
    assert [entry.name for entry in index.entries] == ["IMG/R1C1.TIF"]
    assert index.unsafe_names == ("../escape.tif",)


def test_index_of_a_broken_archive_is_an_error_not_a_crash(tmp_path):
    target = tmp_path / "broken.zip"
    target.write_bytes(b"not a zip at all")
    index = archives.read_archive_index(target)
    assert index.is_readable is False
    assert index.entry_count == 0


def test_index_of_an_unsupported_format_says_so(tmp_path):
    target = tmp_path / "delivery.7z"
    target.write_bytes(b"7z stub")
    assert archives.read_archive_index(target).error == "unsupported_archive_format"


def test_truncated_index_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "MAX_INDEXED_ENTRIES", 2)
    target = tmp_path / "many.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for index_number in range(5):
            archive.writestr(f"IMG/R1C{index_number}.TIF", b"r")
    index = archives.read_archive_index(target)
    assert index.truncated is True
    assert index.entry_count == 2


def test_delivery_root_is_none_when_the_archive_has_several_top_level_names(tmp_path):
    target = tmp_path / "flat.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("A/one.tif", b"a")
        archive.writestr("B/two.tif", b"b")
    assert archives.read_archive_index(target).delivery_root is None


# --- powiazanie z rozpakowana kopia ---------------------------------------------------


def test_archive_next_to_extracted_delivery_is_a_duplicate(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_worldview_archive_duplicate, "wv-duplicate")
    archive = _archives(packages)[0]
    selection = archive["selection"]
    assert selection["status"] == archives.ARCHIVE_STATUS_DUPLICATE
    assert selection["archive"]["extracted_present"] == fixture.notes["archive_entries"]
    assert selection["archive"]["extracted_total"] == fixture.notes["archive_entries"]
    assert selection["archive"]["missing_count"] == 0


def test_archive_next_to_extracted_delivery_names_the_extracted_copy(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_worldview_archive_duplicate, "wv-duplicate-2")
    # Bramka wymaga POWIAZANIA, nie tylko wykrycia: raport ma wskazywac konkretny katalog.
    assert _archives(packages)[0]["selection"]["archive"]["extracted_root_relative"] == "."


def test_incomplete_extraction_is_reported_with_the_missing_files(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_airbus_archive_incomplete, "airbus-incomplete")
    selection = _archives(packages)[0]["selection"]
    assert selection["status"] == archives.ARCHIVE_STATUS_INCOMPLETE
    assert selection["archive"]["extracted_present"] == fixture.notes["extracted_present"]
    assert fixture.notes["missing_raster"] in selection["archive"]["missing_paths"]


def test_incomplete_extraction_points_at_the_complete_copy(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_airbus_archive_incomplete, "airbus-incomplete-2")
    archive = _archives(packages)[0]
    # Drugi warunek bramki: niepelny katalog ma wskazac kompletna kopie w archiwum.
    assert archive["package_root_relative"] == fixture.notes["archive_relative"]
    assert archive["selection"]["archive"]["entry_count"] == fixture.notes["archive_entries"]


def test_delivery_available_only_in_an_archive_stays_visible(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_airbus_archive_only, "airbus-only")
    # Bez P1.3a ta dostawa nie produkuje ZADNEGO pakietu: katalog nie zawiera rastra.
    assert _deliveries(packages) == []
    archive = _archives(packages)[0]
    assert archive["selection"]["status"] == archives.ARCHIVE_STATUS_ONLY
    assert archive["package_root_relative"] == fixture.notes["archive_relative"]


def test_archive_only_does_not_pretend_the_files_are_missing_from_disk(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_airbus_archive_only, "airbus-only-2")
    payload = _archives(packages)[0]["selection"]["archive"]
    # Brak rozpakowanej kopii to nie to samo co niepelna ekstrakcja; podanie tu bazy i listy
    # „brakow" sugerowaloby uszkodzona ekstrakcje tam, gdzie jej po prostu nigdy nie bylo.
    assert payload["extracted_root_relative"] is None
    assert payload["missing_paths"] == []


def test_unsafe_entries_are_reported_on_the_package(tmp_path, graph_v2):
    fixture, packages = _scan(tmp_path, build_archive_with_unsafe_entries, "unsafe")
    selection = _archives(packages)[0]["selection"]
    assert selection["archive"]["unsafe_entry_count"] == fixture.notes["unsafe_entries"]
    assert selection["archive"]["entry_count"] == fixture.notes["safe_entries"]
    codes = {warning["code"] for warning in selection["diagnostics"]["warnings"]}
    assert "archive_unsafe_entries" in codes


# --- archiwum nie jest scena ----------------------------------------------------------


def test_archive_is_never_a_raster_candidate(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_worldview_archive_duplicate, "wv-role")
    assets = _archives(packages)[0]["assets"]
    assert [asset["role"] for asset in assets] == [ROLE_ARCHIVE]
    assert [asset["asset_role"] for asset in assets] == [ROLE_ARCHIVE]


def test_archive_selection_selects_nothing(tmp_path, graph_v2):
    _fixture, packages = _scan(tmp_path, build_worldview_archive_duplicate, "wv-empty")
    selection = _archives(packages)[0]["selection"]
    # Pusta selekcja jest tym, co powstrzymuje katalogowanie przed proba zrobienia z ZIP-a sceny.
    assert selection["asset_ids"] == []
    assert selection["identity_asset_ids"] == []
    assert selection["raster_kind"] == "archive"


def test_archive_status_is_recognised_as_non_importable():
    assert archives.ARCHIVE_STATUS_ONLY in archives.ARCHIVE_STATUSES
    assert archives.ARCHIVE_STATUS_DUPLICATE in archives.ARCHIVE_STATUSES
    assert archives.ARCHIVE_STATUS_INCOMPLETE in archives.ARCHIVE_STATUSES
    assert archives.ARCHIVE_STATUS_UNREADABLE in archives.ARCHIVE_STATUSES
    assert "ready" not in archives.ARCHIVE_STATUSES


def test_discovery_does_not_write_to_the_source(tmp_path, graph_v2):
    workspace = tmp_path / "readonly"
    workspace.mkdir()
    fixture = build_worldview_archive_duplicate(workspace)
    before = {
        path.relative_to(fixture.root).as_posix(): path.stat().st_size
        for path in fixture.root.rglob("*")
        if path.is_file()
    }
    scan_source(fixture.root, fixture.provider)
    after = {
        path.relative_to(fixture.root).as_posix(): path.stat().st_size
        for path in fixture.root.rglob("*")
        if path.is_file()
    }
    assert after == before


# --- addytywnosc wzgledem istniejacych pakietow ---------------------------------------


def test_archive_does_not_change_the_delivery_package(tmp_path, graph_v2):
    """Obecnosc ZIP-a nie moze przesunac ani jednego pola pakietu z rastrami.

    Gdyby archiwum trafialo do inwentarza dostawy, zmienialoby `assets`, odcisk tozsamosci i
    snapshot juz zaimportowanych scen — czyli sam etap widocznosci archiwow wymuszalby migracje.
    """
    with_archive = tmp_path / "with"
    with_archive.mkdir()
    fixture = build_worldview_archive_duplicate(with_archive)
    packages_with, _ = scan_source(fixture.root, fixture.provider)

    (fixture.root / str(fixture.notes["archive_relative"])).unlink()
    packages_without, _ = scan_source(fixture.root, fixture.provider)

    assert len(_archives(packages_with)) == 1
    assert _archives(packages_without) == []
    assert _deliveries(packages_with) == _deliveries(packages_without)


def test_archive_package_ids_are_stable_across_scans(tmp_path, graph_v2):
    fixture, first = _scan(tmp_path, build_worldview_archive_duplicate, "stable")
    second, _ = scan_source(fixture.root, fixture.provider)
    assert [item["package_id"] for item in _archives(first)] == [
        item["package_id"] for item in _archives(second)
    ]


def test_warm_cache_does_not_serve_a_stale_archive_classification(tmp_path, monkeypatch, graph_v2):
    """Usuniecie rozpakowanej kopii ma przeklasyfikowac archiwum, mimo cieplego cache.

    Odcisk kandydata-archiwum to sam plik ZIP, a ten sie nie zmienia. Gdyby cache per kandydat
    obowiazywal takze dla archiwow, duplikat zostalby duplikatem po skasowaniu kopii z dysku —
    czyli aplikacja twierdzilaby, ze dostawa jest rozpakowana, gdy juz jej nie ma.
    """
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")
    workspace = tmp_path / "cache-source"
    workspace.mkdir()
    fixture = build_worldview_archive_duplicate(workspace)

    warm = scan_source_cached(fixture.root, fixture.provider)
    assert _archives(warm["packages"])[0]["selection"]["status"] == archives.ARCHIVE_STATUS_DUPLICATE

    for path in sorted((fixture.root / "014670314010_01_003").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (fixture.root / "014670314010_01_003").rmdir()

    after = scan_source_cached(fixture.root, fixture.provider)
    assert _archives(after["packages"])[0]["selection"]["status"] == archives.ARCHIVE_STATUS_ONLY


# --- odwracalnosc ---------------------------------------------------------------------


def test_archives_are_invisible_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _fixture, packages = _scan(tmp_path, build_worldview_archive_duplicate, "flag-off")
    assert _archives(packages) == []


def test_archive_only_delivery_disappears_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    _fixture, packages = _scan(tmp_path, build_airbus_archive_only, "flag-off-only")
    # Dokladnie ta usterka, ktora zamyka P1.3a: bez flagi dostawa nie istnieje dla aplikacji.
    assert packages == []
