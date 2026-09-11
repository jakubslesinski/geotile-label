"""Pipeline pansharpeningu po zmianie na pansharpened VRT (DESIGN_DECISIONS.md, scene-import P1.4b).

Kandydat zostal wybrany pomiarem na rzeczywistej dostawie WV2 MUL+PAN i przyjety, bo nie ma
regresji na zadnej mierzonej osi: piksele pelnej rozdzielczosci sa IDENTYCZNE z wynikiem starego
pipeline'u, a czas, RAM, scratch i odczyt spadaja. Testy pilnuja tych wlasnosci kontraktu, ktore
da sie sprawdzic bez udzialu sieciowego:

- wynik ma geometrie panchromatyczna i trzy pasma (`test_output_has_*`),
- na dysku NIE powstaje pelny raster posredni (`test_no_full_resolution_intermediate_*`),
- anulowanie nie zostawia partiala ani produktu (`test_cancelled_*`),
- preflight blokuje start przy braku miejsca (`test_preflight_*`),
- `processing_manifest.json` zapisuje uzyty pipeline (`test_processing_manifest_*`).

Uruchomienie: pytest backend/tests/test_pansharpen_pipeline.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest
import rasterio
from affine import Affine

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from services.scene_packages import working_view  # noqa: E402

PAN_PIXEL = 0.5
MUL_PIXEL = 2.0
PAN_SIZE = 256
MUL_SIZE = PAN_SIZE // int(MUL_PIXEL / PAN_PIXEL)
ORIGIN_X = 500_000.0
ORIGIN_Y = 5_800_000.0
RGB_BANDS = [3, 2, 1]


def _write(path: pathlib.Path, size: int, pixel: float, bands: int, seed: int) -> pathlib.Path:
    generator = np.random.default_rng(seed)
    transform = Affine(pixel, 0.0, ORIGIN_X, 0.0, -pixel, ORIGIN_Y)
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=bands,
        dtype="uint16", crs="EPSG:32634", transform=transform,
    ) as dst:
        for band in range(1, bands + 1):
            dst.write(generator.integers(100, 3000, size=(size, size), dtype="uint16"), band)
    return path


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    """Minimalna dostawa MUL+PAN zamontowana jako projekt ze zrodlem."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    _write(source_root / "MUL.tif", MUL_SIZE, MUL_PIXEL, 4, seed=1)
    _write(source_root / "PAN.tif", PAN_SIZE, PAN_PIXEL, 1, seed=2)

    project, scene, source = "p14b-project", "scene-mulpan", "src-1"
    storage.project_dir(project).mkdir(parents=True, exist_ok=True)
    (storage.project_dir(project) / "scene_sources.json").write_text(
        json.dumps({
            "schema_name": "geotile_scene_sources",
            "schema_version": 3,
            "sources": [{
                "source_id": source,
                "provider": "worldview",
                "root_path": str(source_root),
                "enabled": True,
            }],
        }),
        encoding="utf-8",
    )
    storage.save_scene_json(project, scene, "scene_manifest", {
        "source_package": {
            "source_id": source,
            "assets": [
                {"asset_id": "mul", "relative_path": "MUL.tif", "role": "raster_candidate"},
                {"asset_id": "pan", "relative_path": "PAN.tif", "role": "raster_candidate"},
            ],
            "selection": {
                "asset_ids": ["mul", "pan"],
                "multispectral_asset_ids": ["mul"],
                "panchromatic_asset_ids": ["pan"],
                "product_type": "MUL+PAN",
                "raster_kind": "derived",
                "rgb_bands": RGB_BANDS,
            },
        },
        "working_view": {},
    })
    return {"project": project, "scene": scene, "source_root": source_root}


def _variant_dir(project: str, scene: str) -> pathlib.Path:
    derived = storage.project_dir(project) / "derived_scenes" / scene
    return next(item for item in derived.iterdir() if item.is_dir())


def _variant_files(project: str, scene: str) -> list[str]:
    derived = storage.project_dir(project) / "derived_scenes" / scene
    return sorted(
        path.relative_to(derived).as_posix() for path in derived.rglob("*") if path.is_file()
    )


# --- wynik ----------------------------------------------------------------------------


def test_output_has_panchromatic_geometry_and_three_bands(delivery):
    result = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    with rasterio.open(result["path"]) as src:
        assert (src.width, src.height) == (PAN_SIZE, PAN_SIZE)
        assert src.count == 3
        assert src.dtypes[0] == "uint16"
        assert abs(abs(src.res[0]) - PAN_PIXEL) < 1e-12
        assert src.bounds.left == ORIGIN_X and src.bounds.top == ORIGIN_Y


def test_output_is_repeatable_from_the_same_inputs(delivery):
    first = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    with rasterio.open(first["path"]) as src:
        before = src.read()
    second = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    with rasterio.open(second["path"]) as src:
        after = src.read()
    assert first["variant_id"] == second["variant_id"]
    assert np.array_equal(before, after)


def test_no_full_resolution_intermediate_raster_is_left_behind(delivery):
    """Sedno zmiany: posredni raster w rozdzielczosci PAN w ogole nie powstaje.

    Stary pipeline zapisywal pelny, nieskompresowany TIFF (dla WV2 MUL+PAN 1,4 GiB), budowal
    jego piramidy i dopiero potem zapisywal COG. Teraz posrednikiem jest VRT — przepis, nie
    piksele — i jest kasowany po zakonczeniu.
    """
    working_view.prepare_pansharpened_cog(delivery["project"], delivery["scene"], RGB_BANDS)
    files = _variant_files(delivery["project"], delivery["scene"])
    rasters = [name for name in files if name.endswith(".tif")]
    assert rasters == [f"{_variant_dir(delivery['project'], delivery['scene']).name}/rgb_pansharpened.cog.tif"]
    assert not any(name.endswith(".partial.tif") for name in files)
    assert not any(name.endswith(working_view.PANSHARPENED_VRT_NAME) for name in files)


# --- anulowanie -----------------------------------------------------------------------


def test_cancelled_preparation_publishes_nothing(delivery):
    with pytest.raises(InterruptedError):
        working_view.prepare_pansharpened_cog(
            delivery["project"], delivery["scene"], RGB_BANDS, cancel_check=lambda: True
        )
    files = _variant_files(delivery["project"], delivery["scene"])
    assert not any(name.endswith(".tif") for name in files), files


def test_cancelled_preparation_leaves_no_partial(delivery):
    with pytest.raises(InterruptedError):
        working_view.prepare_pansharpened_cog(
            delivery["project"], delivery["scene"], RGB_BANDS, cancel_check=lambda: True
        )
    files = _variant_files(delivery["project"], delivery["scene"])
    assert not any(".partial" in name for name in files), files


def test_cancellation_during_the_translate_is_reported_as_interruption(delivery, monkeypatch):
    """Anulowanie w trakcie zapisu COG ma wracac jako `InterruptedError`, nie jako blad GDAL.

    Wolajacy odroznia po tym typie „przerwane przez uzytkownika" od „przygotowanie sie nie
    udalo"; pomylenie ich zamienialoby anulowanie w awarie sceny.
    """
    derived = storage.project_dir(delivery["project"]) / "derived_scenes" / delivery["scene"]
    seen_translate = {"value": False}

    def cancel_once_the_translate_started() -> bool:
        # Plik `.partial` istnieje WYLACZNIE w trakcie zapisu COG, wiec jest jednoznacznym
        # sygnalem, ze anulowanie trafia w translate, a nie we wczesniejsze bramki.
        started = any(".partial" in path.name for path in derived.rglob("*") if path.is_file())
        seen_translate["value"] = seen_translate["value"] or started
        return started

    with pytest.raises(InterruptedError):
        working_view.prepare_pansharpened_cog(
            delivery["project"],
            delivery["scene"],
            RGB_BANDS,
            cancel_check=cancel_once_the_translate_started,
        )
    assert seen_translate["value"], "anulowanie nie doszlo do fazy zapisu COG"
    files = _variant_files(delivery["project"], delivery["scene"])
    assert not any(".partial" in name for name in files)
    assert not any(name.endswith(".cog.tif") for name in files)


# --- preflight ------------------------------------------------------------------------


def test_preflight_refuses_to_start_without_free_space(delivery, monkeypatch):
    monkeypatch.setattr(
        working_view.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"total": 0, "used": 0, "free": 1024})(),
    )
    with pytest.raises(working_view.InsufficientDerivativeSpace):
        working_view.prepare_pansharpened_cog(delivery["project"], delivery["scene"], RGB_BANDS)
    assert not any(
        name.endswith(".tif") for name in _variant_files(delivery["project"], delivery["scene"])
    )


def test_preflight_budget_is_one_output_not_two(delivery, monkeypatch):
    """Po zmianie na VRT na dysku powstaje JEDEN pelny obraz, wiec budzet spada o polowe."""
    recorded: list[int] = []
    original = working_view.ensure_derivative_space

    def record(path, required_bytes, kind):
        recorded.append(int(required_bytes))
        return original(path, required_bytes, kind)

    monkeypatch.setattr(working_view, "ensure_derivative_space", record)
    working_view.prepare_pansharpened_cog(delivery["project"], delivery["scene"], RGB_BANDS)
    assert recorded, "preflight nie zostal wywolany"
    assert recorded[0] == PAN_SIZE * PAN_SIZE * len(RGB_BANDS) * 2


# --- provenance -----------------------------------------------------------------------


def test_processing_manifest_records_the_pipeline_and_its_parameters(delivery):
    result = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    manifest = json.loads(pathlib.Path(result["processing_manifest"]).read_text(encoding="utf-8"))
    assert manifest["pipeline"] == working_view.PANSHARPEN_PIPELINE
    assert manifest["method"] == "weighted_brovey"
    assert manifest["rgb_bands"] == RGB_BANDS
    assert manifest["pansharpen_threads"] == 1
    assert manifest["gdal_cache_bytes"] == working_view.COG_CACHE_BYTES
    assert sorted(item["relative_path"] for item in manifest["inputs"]) == ["MUL.tif", "PAN.tif"]


def test_processing_manifest_fingerprint_is_reproducible_from_the_same_inputs(delivery):
    """Bramka P1.5: to samo wejscie i te same parametry daja ten sam odcisk.

    Odcisk liczony jest z TOZSAMOSCI wejsc i parametrow algorytmu, nie z wyniku — dzieki temu
    da sie stwierdzic, czy wariant pochodzi z tych samych danych, bez odczytywania rastra.
    """
    first = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    first_manifest = json.loads(pathlib.Path(first["processing_manifest"]).read_text(encoding="utf-8"))
    second = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    second_manifest = json.loads(pathlib.Path(second["processing_manifest"]).read_text(encoding="utf-8"))

    assert first_manifest["inputs_fingerprint"] == second_manifest["inputs_fingerprint"]
    assert first_manifest["output_semantics"]["transform"] == second_manifest["output_semantics"]["transform"]
    assert first_manifest["output_semantics"]["crs"] == second_manifest["output_semantics"]["crs"]


def test_processing_manifest_records_input_identity_not_just_names(delivery):
    result = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    manifest = json.loads(pathlib.Path(result["processing_manifest"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == working_view.PROCESSING_MANIFEST_VERSION
    roles = {item["role"] for item in manifest["inputs"]}
    assert roles == {"multispectral", "panchromatic"}
    for item in manifest["inputs"]:
        assert set(item) >= {"asset_id", "relative_path", "size", "mtime_ns", "sha256"}
    assert manifest["environment"]["gdal_version"]
    assert manifest["algorithm"]["version"] == working_view.PANSHARPEN_ALGORITHM_VERSION


def test_processing_manifest_states_what_the_output_values_are(delivery):
    """Produkt pochodny nie jest wielkoscia fizyczna i manifest musi to mowic wprost."""
    result = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    manifest = json.loads(pathlib.Path(result["processing_manifest"]).read_text(encoding="utf-8"))
    semantics = manifest["output_semantics"]
    assert semantics["quantity"] == "pansharpened_visual"
    assert semantics["calibration_state"] == "uncalibrated"
    assert semantics["band_count"] == 3
    assert semantics["source_band_indexes"] == RGB_BANDS


def test_changed_input_changes_the_fingerprint(delivery):
    """Podmiana pliku o tej samej nazwie musi byc widoczna w odcisku."""
    first = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    before = json.loads(pathlib.Path(first["processing_manifest"]).read_text(encoding="utf-8"))

    manifest = storage.load_scene_json(delivery["project"], delivery["scene"], "scene_manifest", default={})
    for asset in manifest["source_package"]["assets"]:
        asset["size"] = int(asset.get("size") or 0) + 1
    storage.save_scene_json(delivery["project"], delivery["scene"], "scene_manifest", manifest)

    second = working_view.prepare_pansharpened_cog(
        delivery["project"], delivery["scene"], RGB_BANDS
    )
    after = json.loads(pathlib.Path(second["processing_manifest"]).read_text(encoding="utf-8"))
    assert before["inputs_fingerprint"] != after["inputs_fingerprint"]


def test_pansharpening_never_writes_to_the_source(delivery):
    source_root = delivery["source_root"]
    before = {
        path.name: path.stat().st_size for path in source_root.rglob("*") if path.is_file()
    }
    working_view.prepare_pansharpened_cog(delivery["project"], delivery["scene"], RGB_BANDS)
    after = {
        path.name: path.stat().st_size for path in source_root.rglob("*") if path.is_file()
    }
    assert after == before
