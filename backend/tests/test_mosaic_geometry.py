"""Kontrola geometrii mozaiki wirtualnej (DESIGN_DECISIONS.md, scene-import P1.4).

Zakres P1.4 wymienia cztery rzeczy, ktorych stara kontrola nie robila: semantyczne porownanie
CRS zamiast porownania napisow, tolerancja numeryczna rozdzielczosci, wykrywanie duplikatow
i nakladek oraz wykrywanie luk w pokryciu. Kazda ma tu wlasny test na prawdziwych, malych
GeoTIFF-ach — geometria jest tym, co testujemy, wiec pliki musza byc prawdziwe.

Podzial na `errors` i `warnings` jest osobnym kontraktem: niezgodna siatka uniemozliwia
mozaike, natomiast dziurawe albo zachodzace pokrycie jest STANEM dostawy i ma byc widoczne,
a nie blokowac import (ta sama zasada co `partial_delivery` w P0.6).

Uruchomienie: pytest backend/tests/test_mosaic_geometry.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from services.scene_packages import mosaics, working_view  # noqa: E402
from services.scene_packages.working_view import _validate_mosaic_parts  # noqa: E402

PIXEL = 0.5
ORIGIN_X = 500_000.0
ORIGIN_Y = 5_800_000.0


def _part(
    directory: pathlib.Path,
    name: str,
    *,
    col: int = 0,
    row: int = 0,
    width: int = 64,
    height: int = 64,
    pixel: float = PIXEL,
    crs: object = "EPSG:32634",
    dtype: str = "uint16",
    count: int = 1,
    offset: tuple[float, float] = (0.0, 0.0),
    col_px: int | None = None,
    row_px: int | None = None,
) -> pathlib.Path:
    """Zapisz czesc mozaiki umieszczona w komorce (col, row) wspolnej siatki.

    Przy kafelkach o roznych rozmiarach polozenie nie wynika z wlasnej szerokosci czesci,
    dlatego `col_px`/`row_px` pozwalaja podac przesuniecie w pikselach wprost.
    """
    path = directory / name
    left_px = col * width if col_px is None else col_px
    top_px = row * height if row_px is None else row_px
    transform = Affine(
        pixel, 0.0, ORIGIN_X + left_px * pixel + offset[0],
        0.0, -pixel, ORIGIN_Y - top_px * pixel + offset[1],
    )
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype=dtype, crs=crs, transform=transform,
    ) as dst:
        for band in range(1, count + 1):
            dst.write(np.full((height, width), band, dtype=dtype), band)
    return path


def _native_jp2(
    directory: pathlib.Path,
    name: str,
    *,
    count: int = 1,
    width: int = 512,
    height: int = 384,
) -> pathlib.Path:
    """Create a small lossless, multiresolution JP2 using the packaged GDAL driver."""

    gdal = pytest.importorskip("osgeo.gdal")
    driver = gdal.GetDriverByName("JP2OpenJPEG")
    if driver is None:
        pytest.skip("JP2OpenJPEG driver is unavailable")
    path = directory / name
    memory = gdal.GetDriverByName("MEM").Create(
        "",
        width,
        height,
        count,
        gdal.GDT_UInt16,
    )
    memory.SetGeoTransform((ORIGIN_X, PIXEL, 0.0, ORIGIN_Y, 0.0, -PIXEL))
    memory.SetProjection(CRS.from_epsg(32634).to_wkt())
    for band_index in range(1, count + 1):
        band = memory.GetRasterBand(band_index)
        band.Fill(65535 if band_index == 2 else band_index * 1000)
        band.SetColorInterpretation(
            gdal.GCI_AlphaBand if band_index == 2 else gdal.GCI_GrayIndex
        )
    output = driver.CreateCopy(
        str(path),
        memory,
        options=[
            "REVERSIBLE=YES",
            "QUALITY=100",
            "RESOLUTIONS=5",
            "BLOCKXSIZE=128",
            "BLOCKYSIZE=128",
        ],
    )
    if output is None:
        pytest.skip("JP2OpenJPEG could not create the test raster")
    output = None
    memory = None
    return path


def _force_native_jp2_overview_policy(monkeypatch, source: pathlib.Path) -> dict:
    """Force the production large-JP2 branch for a compact synthetic fixture."""

    with rasterio.open(source) as dataset:
        factors = list(dataset.overviews(1))
        state = {
            "type": "native_multiresolution",
            "driver": dataset.driver,
            "width": dataset.width,
            "height": dataset.height,
            "usable": bool(factors),
            "factors": factors,
            "factors_by_band": [list(dataset.overviews(i)) for i in range(1, dataset.count + 1)],
            "read_error": None,
        }
    assert state["usable"], "Synthetic JP2 has no native resolution levels"
    monkeypatch.setattr(working_view, "inspect_source_overviews", lambda _path: state)
    monkeypatch.setattr(
        working_view,
        "source_overviews_are_display_ready",
        lambda _state: False,
    )
    return state


# --- siatka zgodna --------------------------------------------------------------------


def test_two_adjacent_parts_form_a_clean_mosaic(tmp_path):
    parts = [_part(tmp_path, "R1C1.tif"), _part(tmp_path, "R1C2.tif", col=1)]
    report = mosaics.inspect_parts(parts)
    assert report.errors == ()
    assert report.warnings == ()
    assert report.coverage["gap_pixels"] == 0
    assert report.coverage["overlap_pixels"] == 0
    assert report.coverage["covered_pixels"] == 2 * 64 * 64


def test_part_offsets_are_expressed_on_the_reference_grid(tmp_path):
    parts = [_part(tmp_path, "R1C1.tif"), _part(tmp_path, "R2C1.tif", row=1)]
    report = mosaics.inspect_parts(parts)
    assert [(part.col_off, part.row_off) for part in report.parts] == [(0, 0), (0, 64)]


# --- CRS porownywany semantycznie -----------------------------------------------------


def test_equivalent_crs_written_differently_is_not_a_mismatch(tmp_path):
    """Ten sam uklad podany jako kod EPSG i jako WKT ma zostac uznany za zgodny.

    UWAGA co do zasiegu: sprawdzono pomiarem, ze GDAL normalizuje CRS przy ZAPISIE GeoTIFF —
    obie sciezki wracaja z odczytu jako `EPSG:32634`, a dla ukladu bez kodu autorytetu jako
    identyczny WKT. Porownanie napisow dawaloby wiec tutaj ten sam wynik. Semantyczna rownosc
    `rasterio.crs.CRS` jest zabezpieczeniem na wypadek zrodel, ktore nie przechodza przez ten
    zapis (np. sidecar `.prj` albo sterownik zachowujacy oryginalny WKT), a nie naprawa
    zaobserwowanej usterki. Test pilnuje kontraktu, nie udaje regresji.
    """
    same_crs_as_wkt = CRS.from_epsg(32634).to_wkt()
    parts = [
        _part(tmp_path, "R1C1.tif", crs="EPSG:32634"),
        _part(tmp_path, "R1C2.tif", col=1, crs=same_crs_as_wkt),
    ]
    with rasterio.open(parts[0]) as src:
        assert src.crs is not None
    report = mosaics.inspect_parts(parts)
    assert report.errors == (), report.errors


def test_different_crs_is_a_blocking_error(tmp_path):
    parts = [
        _part(tmp_path, "R1C1.tif", crs="EPSG:32634"),
        _part(tmp_path, "R1C2.tif", col=1, crs="EPSG:32633"),
    ]
    report = mosaics.inspect_parts(parts)
    assert [item["code"] for item in report.errors] == ["crs_mismatch"]
    assert report.is_blocking is True


# --- tolerancja numeryczna ------------------------------------------------------------


def test_resolution_differing_in_the_last_bits_is_accepted(tmp_path):
    """Ta sama rozdzielczosc zapisana rozna droga rozni sie w ostatnich bitach mantysy.

    Dryf dobrany pomiarem tak, zeby rozroznial STARA i NOWA kontrole: stara porownywala
    `round(res, 12)`, wiec 0,500000000005 odrzucala; nowa uzywa tolerancji wzglednej 1e-9.
    """
    drifted = PIXEL * (1 + 1e-11)
    assert round(drifted, 12) != round(PIXEL, 12), "dryf musi byc widoczny dla starej kontroli"
    parts = [_part(tmp_path, "R1C1.tif"), _part(tmp_path, "R1C2.tif", col=1, pixel=drifted)]
    report = mosaics.inspect_parts(parts)
    assert report.errors == (), report.errors


def test_genuinely_different_resolution_is_rejected(tmp_path):
    parts = [_part(tmp_path, "R1C1.tif"), _part(tmp_path, "R1C2.tif", col=1, pixel=1.0)]
    report = mosaics.inspect_parts(parts)
    assert [item["code"] for item in report.errors] == ["resolution_mismatch"]


def test_part_off_the_common_grid_is_rejected(tmp_path):
    # Przesuniecie o pol piksela: rozdzielczosc i CRS sie zgadzaja, ale siatki nie da sie zlozyc.
    parts = [_part(tmp_path, "R1C1.tif"), _part(tmp_path, "R1C2.tif", col=1, offset=(PIXEL / 2, 0.0))]
    report = mosaics.inspect_parts(parts)
    assert [item["code"] for item in report.errors] == ["grid_misaligned"]


def test_band_layout_mismatch_is_rejected(tmp_path):
    parts = [_part(tmp_path, "R1C1.tif", count=1), _part(tmp_path, "R1C2.tif", col=1, count=3)]
    report = mosaics.inspect_parts(parts)
    assert [item["code"] for item in report.errors] == ["band_mismatch"]


def test_dtype_mismatch_is_rejected(tmp_path):
    parts = [_part(tmp_path, "R1C1.tif", dtype="uint16"), _part(tmp_path, "R1C2.tif", col=1, dtype="uint8")]
    report = mosaics.inspect_parts(parts)
    assert [item["code"] for item in report.errors] == ["band_mismatch"]


# --- duplikaty, nakladki, luki --------------------------------------------------------


def test_two_parts_covering_the_same_window_are_reported_as_duplicates(tmp_path):
    first = _part(tmp_path, "R1C1.tif")
    second = _part(tmp_path, "R1C1_copy.tif")
    report = mosaics.inspect_parts([first, second])
    codes = {item["code"] for item in report.warnings}
    assert "duplicate_parts" in codes
    # Duplikat nie blokuje: VRT poradzi sobie, ale uzytkownik ma wiedziec, ze czesc jest podwojna.
    assert report.is_blocking is False


def test_partially_overlapping_parts_are_reported_with_the_shared_area(tmp_path):
    parts = [
        _part(tmp_path, "R1C1.tif", width=64),
        _part(tmp_path, "R1C2.tif", offset=(32 * PIXEL, 0.0)),
    ]
    report = mosaics.inspect_parts(parts)
    overlap = next(item for item in report.warnings if item["code"] == "parts_overlap")
    assert report.coverage["overlap_pixels"] == 32 * 64
    assert "R1C1.tif" in overlap["message"] and "R1C2.tif" in overlap["message"]


def test_a_missing_corner_tile_is_reported(tmp_path):
    """Trzy z czterech kafli kwadratu 2×2 — brakuje 25% powierzchni."""
    parts = [
        _part(tmp_path, "R1C1.tif"),
        _part(tmp_path, "R1C2.tif", col=1),
        _part(tmp_path, "R2C1.tif", row=1),
    ]
    report = mosaics.inspect_parts(parts)
    gaps = next(item for item in report.warnings if item["code"] == "coverage_gaps")
    assert report.coverage["gap_pixels"] == 64 * 64
    assert report.coverage["bounding_pixels"] == 4 * 64 * 64
    # Luka narozna jest osiagalna z zewnatrz, wiec nie jest dziura wewnetrzna —
    # zglasza ja dopiero prog udzialu powierzchni.
    assert report.coverage["interior_gap_pixels"] == 0
    assert "75.0%" in gaps["message"]


def test_a_hole_enclosed_by_other_parts_is_reported_however_small(tmp_path):
    """Brak kafla w SRODKU siatki 3×3 — dziura otoczona ze wszystkich stron."""
    parts = [
        _part(tmp_path, f"R{row + 1}C{col + 1}.tif", col=col, row=row)
        for row in range(3)
        for col in range(3)
        if (row, col) != (1, 1)
    ]
    report = mosaics.inspect_parts(parts)
    gaps = next(item for item in report.warnings if item["code"] == "coverage_gaps")
    assert report.coverage["interior_gap_pixels"] == 64 * 64
    assert "enclosed" in gaps["message"]


def test_ragged_tile_edges_do_not_raise_a_false_alarm(tmp_path):
    """Poszarpany brzeg dostawy tiled jest normalny i nie moze byc ostrzezeniem.

    Uklad odwzorowuje rzeczywista dostawe WV2 PAN, gdzie kafle prawej kolumny maja rozne
    szerokosci (10559, 10568, 10574 px), przez co 0,05% prostokata obejmujacego nie jest
    pokryte. Ostrzeganie o tym przy kazdej dostawie byloby szumem.
    """
    parts = [
        _part(tmp_path, "R1C1.tif", width=64, height=64),
        _part(tmp_path, "R1C2.tif", col_px=64, width=63, height=64),
        _part(tmp_path, "R2C1.tif", row_px=64, width=64, height=64),
        _part(tmp_path, "R2C2.tif", col_px=64, row_px=64, width=64, height=64),
    ]
    report = mosaics.inspect_parts(parts)
    assert report.coverage["gap_pixels"] > 0
    assert report.coverage["interior_gap_pixels"] == 0
    assert [item["code"] for item in report.warnings] == []


def test_an_l_shaped_delivery_without_holes_reports_no_gap(tmp_path):
    """Prostokat obejmujacy nie jest pokryciem: kafle w jednym rzedzie nie maja dziury."""
    parts = [_part(tmp_path, f"R1C{index + 1}.tif", col=index) for index in range(3)]
    report = mosaics.inspect_parts(parts)
    assert report.coverage["gap_pixels"] == 0
    assert report.coverage["interior_gap_pixels"] == 0
    assert [item["code"] for item in report.warnings] == []


# --- kontrakt wolajacego --------------------------------------------------------------


def test_validate_raises_only_on_blocking_problems(tmp_path):
    overlapping = [
        _part(tmp_path, "R1C1.tif"),
        _part(tmp_path, "R1C2.tif", offset=(32 * PIXEL, 0.0)),
    ]
    # Nakladka nie jest bledem — `create_vrt` ma sie zbudowac.
    _validate_mosaic_parts(overlapping)

    mismatched = [
        _part(tmp_path, "A.tif", crs="EPSG:32634"),
        _part(tmp_path, "B.tif", col=1, crs="EPSG:32633"),
    ]
    with pytest.raises(ValueError, match="different CRS"):
        _validate_mosaic_parts(mismatched)


def test_unreadable_part_is_an_error_not_a_crash(tmp_path):
    good = _part(tmp_path, "R1C1.tif")
    broken = tmp_path / "R1C2.tif"
    broken.write_bytes(b"not a raster")
    report = mosaics.inspect_parts([good, broken])
    assert [item["code"] for item in report.errors] == ["part_unreadable"]


def test_empty_part_list_is_rejected():
    report = mosaics.inspect_parts([])
    assert [item["code"] for item in report.errors] == ["no_parts"]


def test_single_part_needs_no_comparison(tmp_path):
    report = mosaics.inspect_parts([_part(tmp_path, "only.tif")])
    assert report.errors == () and report.warnings == ()
    assert report.coverage["covered_pixels"] == 64 * 64


# --- budowa mozaiki, piramidy i preflight ---------------------------------------------


def _project(tmp_path: pathlib.Path, monkeypatch, name: str) -> str:
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")
    storage.project_dir(name).mkdir(parents=True, exist_ok=True)
    return name


def test_vrt_keeps_the_order_it_was_given(tmp_path, monkeypatch):
    """Kolejnosc czesci w VRT ma pochodzic od wolajacego (dla WV2 z manifestu TIL),
    a nie z sortowania nazw wewnatrz `create_vrt`."""
    project = _project(tmp_path, monkeypatch, "p14-order")
    parts = [_part(tmp_path, f"R1C{index + 1}.tif", col=index) for index in range(3)]
    reversed_parts = list(reversed(parts))
    vrt = working_view.create_vrt(project, "scene", "variant", reversed_parts)
    body = vrt.read_text(encoding="utf-8")
    positions = [body.index(part.name) for part in reversed_parts]
    assert positions == sorted(positions), "VRT wymienia czesci w innej kolejnosci niz podana"


def test_vrt_footprint_is_the_union_of_its_parts(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-union")
    parts = [
        _part(tmp_path, "R1C1.tif"),
        _part(tmp_path, "R1C2.tif", col=1),
        _part(tmp_path, "R2C1.tif", row=1),
        _part(tmp_path, "R2C2.tif", col=1, row=1),
    ]
    vrt = working_view.create_vrt(project, "scene", "variant", parts)
    with rasterio.open(vrt) as mosaic:
        assert (mosaic.width, mosaic.height) == (128, 128)
        assert mosaic.crs == CRS.from_epsg(32634)
        assert abs(abs(mosaic.res[0]) - PIXEL) < 1e-12
        bounds = mosaic.bounds
    assert bounds.left == ORIGIN_X
    assert bounds.top == ORIGIN_Y


def test_incompatible_parts_never_leave_a_partial_vrt(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-partial")
    parts = [
        _part(tmp_path, "A.tif", crs="EPSG:32634"),
        _part(tmp_path, "B.tif", col=1, crs="EPSG:32633"),
    ]
    with pytest.raises(ValueError):
        working_view.create_vrt(project, "scene", "variant", parts)
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    assert not target_dir.exists() or not list(target_dir.iterdir())


def test_cancelled_overview_build_leaves_no_partial(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-cancel")
    source = _part(tmp_path, "scene.tif", width=2048, height=2048)
    with pytest.raises(InterruptedError):
        working_view.build_direct_overviews(
            project, "scene", source, "variant", cancel_check=lambda: True
        )
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    assert [item.name for item in target_dir.iterdir()] == []


def test_overview_preflight_refuses_to_start_without_free_space(tmp_path, monkeypatch):
    """Brak miejsca ma sie ujawnic PRZED odczytem calego zrodla, a nie w polowie zapisu."""
    project = _project(tmp_path, monkeypatch, "p14-space")
    source = _part(tmp_path, "scene.tif", width=2048, height=2048)
    monkeypatch.setattr(
        working_view.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"total": 0, "used": 0, "free": 1024})(),
    )
    with pytest.raises(working_view.InsufficientDerivativeSpace):
        working_view.build_direct_overviews(project, "scene", source, "variant")
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    assert [item.name for item in target_dir.iterdir()] == []


def test_completed_overview_records_the_effective_cache_limit(tmp_path, monkeypatch):
    """A successful build must not fail while persisting its execution profile."""
    pytest.importorskip("osgeo.gdal")
    project = _project(tmp_path, monkeypatch, "p14-complete")
    source = _part(tmp_path, "scene.tif", width=1024, height=1024)
    cache_bytes = 32 * 1024 * 1024

    result = working_view.build_direct_overviews(
        project,
        "scene",
        source,
        "variant",
        profile={"gdal_cache_bytes": cache_bytes},
    )

    assert result is not None and result.is_file()
    assert result.with_name(result.name + ".ovr").is_file()
    profile_path = result.parent / working_view.DIRECT_OVERVIEW_PROFILE_NAME
    recorded = json.loads(profile_path.read_text(encoding="utf-8"))
    assert recorded["gdal_cache_bytes"] == cache_bytes


@pytest.mark.parametrize("band_count", [1, 2], ids=["gray", "gray-alpha"])
def test_native_jp2_copies_existing_level_into_project_overview(
    tmp_path,
    monkeypatch,
    band_count,
):
    """JP2 must use its codestream level and preserve a possible alpha band."""

    project = _project(tmp_path, monkeypatch, f"p14-jp2-{band_count}")
    source = _native_jp2(tmp_path, f"native-{band_count}.jp2", count=band_count)
    source_before = (source.stat().st_size, source.stat().st_mtime_ns)
    source_state = _force_native_jp2_overview_policy(monkeypatch, source)

    result = working_view.build_direct_overviews(
        project,
        "scene",
        source,
        "variant",
        profile={
            "compression": "ZSTD",
            "jp2_gdal_threads": 2,
            "jp2_base_factor": 2,
        },
    )

    assert result is not None and result.is_file()
    assert result.with_name(result.name + ".ovr").is_file()
    assert (source.stat().st_size, source.stat().st_mtime_ns) == source_before
    with rasterio.open(result) as dataset:
        assert dataset.count == band_count
        assert dataset.overviews(1)
        assert dataset.overviews(1)[0] == min(source_state["factors"])
        assert all(
            dataset.overviews(index) == dataset.overviews(1)
            for index in range(1, band_count + 1)
        )
        if band_count == 2:
            assert dataset.colorinterp[1].name.lower() == "alpha"

    recorded = json.loads(
        (result.parent / working_view.DIRECT_OVERVIEW_PROFILE_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert recorded["schema_version"] == working_view.DIRECT_OVERVIEW_PROFILE_VERSION
    assert recorded["strategy"] == working_view.JP2_NATIVE_OVERVIEW_STRATEGY
    assert recorded["jp2_base_factor"] == min(source_state["factors"])
    assert recorded["factors"][0] == recorded["jp2_base_factor"]


def test_native_jp2_cancel_removes_partial_proxy(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-jp2-cancel")
    source = _native_jp2(tmp_path, "cancel.jp2")
    _force_native_jp2_overview_policy(monkeypatch, source)
    calls = 0

    def cancel_after_preflight():
        nonlocal calls
        calls += 1
        return calls >= 2

    with pytest.raises(InterruptedError):
        working_view.build_direct_overviews(
            project,
            "scene",
            source,
            "variant",
            cancel_check=cancel_after_preflight,
        )
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    assert [item.name for item in target_dir.iterdir()] == []


def test_native_jp2_preflight_checks_space_before_translate(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-jp2-space")
    source = _native_jp2(tmp_path, "space.jp2")
    _force_native_jp2_overview_policy(monkeypatch, source)
    monkeypatch.setattr(
        working_view.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"total": 0, "used": 0, "free": 1024})(),
    )

    with pytest.raises(working_view.InsufficientDerivativeSpace):
        working_view.build_direct_overviews(project, "scene", source, "variant")
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    assert [item.name for item in target_dir.iterdir()] == []


def test_native_jp2_profile_v2_is_rebuilt_once(tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch, "p14-jp2-profile")
    target_dir = storage.project_dir(project) / "derived_scenes" / "scene" / "variant"
    target_dir.mkdir(parents=True)
    vrt = target_dir / working_view.DIRECT_OVERVIEW_VRT_NAME
    vrt.write_text("legacy", encoding="utf-8")
    vrt.with_name(vrt.name + ".ovr").write_bytes(b"legacy")
    profile_path = target_dir / working_view.DIRECT_OVERVIEW_PROFILE_NAME
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "source_overview_type": "native_multiresolution",
            }
        ),
        encoding="utf-8",
    )

    assert working_view.direct_overview_vrt(project, "scene", "variant") is None

    profile_path.write_text(
        json.dumps(
            {
                "schema_version": working_view.DIRECT_OVERVIEW_PROFILE_VERSION,
                "source_overview_type": "native_multiresolution",
                "strategy": working_view.JP2_NATIVE_OVERVIEW_STRATEGY,
            }
        ),
        encoding="utf-8",
    )
    assert working_view.direct_overview_vrt(project, "scene", "variant") == vrt


@pytest.mark.parametrize(
    ("available_memory", "workers", "expected_factor"),
    [
        (64 * 1024**3, 2, 2),
        (24 * 1024**3, 2, 4),
        (8 * 1024**3, 1, 8),
    ],
)
def test_native_jp2_base_factor_respects_memory_budget(
    available_memory,
    workers,
    expected_factor,
):
    factor, selection, estimated, recorded_memory = working_view._select_jp2_base_factor(
        [2, 4, 8, 16],
        uncompressed_source_bytes=6 * 1024**3,
        requested={
            "available_memory_bytes": available_memory,
            "workers": workers,
        },
    )

    assert factor == expected_factor
    assert selection == "adaptive_memory_budget"
    assert estimated > 0
    assert recorded_memory == available_memory


def test_native_jp2_base_factor_allows_explicit_override():
    factor, selection, _estimated, recorded_memory = working_view._select_jp2_base_factor(
        [2, 4, 8],
        uncompressed_source_bytes=6 * 1024**3,
        requested={"jp2_base_factor": 4},
    )

    assert factor == 4
    assert selection == "explicit"
    assert recorded_memory is None


def test_manifest_records_mosaic_geometry_and_surfaces_overlap(tmp_path, monkeypatch):
    """Raport geometrii ma dotrzec do manifestu i do diagnostyki selekcji, a nie zginac w VRT."""
    from routers import scene_import as si

    project = _project(tmp_path, monkeypatch, "p14-manifest")
    source_root = tmp_path / "source"
    source_root.mkdir()
    first = _part(source_root, "R1C1.tif")
    second = _part(source_root, "R1C2.tif", offset=(32 * PIXEL, 0.0))
    monkeypatch.setattr(
        si, "resolve_source_asset", lambda _pid, _sid, relative: source_root / relative
    )

    package = {
        "source_id": "src",
        "assets": [
            {"asset_id": "a1", "relative_path": first.name, "role": "raster_candidate"},
            {"asset_id": "a2", "relative_path": second.name, "role": "raster_candidate"},
        ],
    }
    selection = {
        "asset_ids": ["a1", "a2"],
        "status": "ready",
        "raster_kind": "virtual_mosaic",
        "diagnostics": {"warnings": [], "errors": [], "metadata_conflicts": []},
    }
    manifest: dict = {}
    si._configure_working_view(project, "scene", package, manifest, selection)

    working = manifest["working_view"]
    assert working["raster_ref"]["storage"] == "project"
    assert working["mosaic_geometry"]["parts"] == 2
    assert working["mosaic_geometry"]["overlap_pixels"] == 32 * 64
    assert [item["code"] for item in selection["diagnostics"]["warnings"]] == ["parts_overlap"]
    assert working["preparation_status"] == "ready"


def test_manifest_reports_a_blocking_geometry_error_instead_of_a_scene(tmp_path, monkeypatch):
    from routers import scene_import as si

    project = _project(tmp_path, monkeypatch, "p14-manifest-bad")
    source_root = tmp_path / "bad_source"
    source_root.mkdir()
    first = _part(source_root, "A.tif", crs="EPSG:32634")
    second = _part(source_root, "B.tif", col=1, crs="EPSG:32633")
    monkeypatch.setattr(
        si, "resolve_source_asset", lambda _pid, _sid, relative: source_root / relative
    )

    package = {
        "source_id": "src",
        "assets": [
            {"asset_id": "a1", "relative_path": first.name, "role": "raster_candidate"},
            {"asset_id": "a2", "relative_path": second.name, "role": "raster_candidate"},
        ],
    }
    selection = {"asset_ids": ["a1", "a2"], "status": "ready", "raster_kind": "virtual_mosaic"}
    manifest: dict = {}
    si._configure_working_view(project, "scene", package, manifest, selection)

    assert manifest["working_view"]["preparation_status"] == "prepare_required"
    assert manifest["working_view"]["raster_ref"] is None
    assert [item["code"] for item in selection["diagnostics"]["errors"]] == ["vrt_unavailable"]


def test_overview_estimate_scales_with_pixels_and_depth():
    single = working_view.estimate_overview_bytes(1000, 1000, 1, 1)
    deeper = working_view.estimate_overview_bytes(1000, 1000, 3, 2)
    # Roznica wobec `6 * single` to wylacznie obciecie do liczby calkowitej.
    assert abs(deeper - 6 * single) <= 6
    # Suma poziomow 1/4 + 1/16 + ... zbiega do 1/3 pelnego obrazu.
    assert single == int(1000 * 1000 / 3)
