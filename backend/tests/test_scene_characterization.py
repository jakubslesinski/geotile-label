"""P1.7 contracts for bounded raster characterization and display semantics."""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import rasterio
from rasterio.transform import from_origin

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.scene_characterization import (  # noqa: E402
    SAMPLE_GRID_SIZE,
    SAMPLE_WINDOW_SIZE,
    characterize_raster,
)
from services.scene_loader import get_scene_info  # noqa: E402
from services.scene_name_metadata import infer_scene_name_metadata  # noqa: E402


def _write_raster(path: pathlib.Path, data: np.ndarray) -> None:
    if data.ndim == 2:
        data = data[np.newaxis, ...]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype=data.dtype,
        crs="EPSG:32634",
        transform=from_origin(500_000, 5_800_000, 0.5, 0.5),
        tiled=True,
        blockxsize=128,
        blockysize=128,
    ) as dst:
        dst.write(data)


def test_uint16_eo_pan_uses_linear_display(tmp_path: pathlib.Path):
    path = tmp_path / "PHRNEO_20240817_PAN.tif"
    values = np.linspace(80, 3800, 512 * 512, dtype=np.uint16).reshape(512, 512)
    _write_raster(path, values)

    info = get_scene_info(path, modality="EO", product_type="PAN")

    assert info.spectral_layout == "panchromatic"
    assert info.display_mode is None
    assert 80 <= info.display_min < info.display_max <= 3800
    assert info.gsd_m == 0.5
    assert info.display_stats["method"] == "block_grid"
    assert info.characterization_source_size == path.stat().st_size


def test_uint16_sar_uses_log_display(tmp_path: pathlib.Path):
    path = tmp_path / "ICEYE_GRD_20240817T121314Z.tif"
    values = np.arange(1, 512 * 512 + 1, dtype=np.uint32).reshape(512, 512)
    values = np.clip(values, 1, 65535).astype(np.uint16)
    _write_raster(path, values)

    info = get_scene_info(path, modality="SAR")

    assert info.spectral_layout == "sar"
    assert info.display_mode == "uint16_log"
    assert info.display_min < info.display_max


def test_pansharpened_rgb_is_not_treated_as_sar(tmp_path: pathlib.Path):
    path = tmp_path / "WV03_20240817_PANSHARP.tif"
    base = np.linspace(100, 2400, 256 * 256, dtype=np.uint16).reshape(256, 256)
    data = np.stack((base, base + 50, base + 100))
    _write_raster(path, data)

    info = get_scene_info(path, modality="EO", product_type="PANSHARPENED_RGB")

    assert info.spectral_layout == "rgb"
    assert info.spectral_processing == "pansharpened"
    assert info.display_mode is None
    assert info.data_band_indexes == [1, 2, 3]


def test_filename_analysis_reuses_exact_date_and_spectral_hints():
    result = infer_scene_name_metadata("PNEO3_202106281222_PMS-FS_RGB.tif")

    assert result["sensor"] == "Pleiades Neo"
    assert result["modality"] == "EO"
    assert result["acquisition_datetime_utc"] == "2021-06-28T12:22:00+00:00"
    assert result["spectral_layout_hint"] == "rgb"
    assert result["spectral_processing_hint"] == "pansharpened"


def test_sampling_is_bounded_to_native_windows():
    class FakeDataset:
        width = 100_000
        height = 80_000
        count = 2
        dtypes = ("uint16", "uint8")
        nodata = None
        nodatavals = (None, None)
        colorinterp = (SimpleNamespace(name="gray"), SimpleNamespace(name="alpha"))
        name = "large_PAN.tif"

        def __init__(self):
            self.read_calls = []

        def read(self, indexes, *, window):
            self.read_calls.append((tuple(indexes), window))
            return np.ones((len(indexes), int(window.height), int(window.width)), dtype=np.uint16)

        def overviews(self, _index):
            return []

    src = FakeDataset()
    result = characterize_raster(src, modality="EO", product_type="PAN")

    assert result["spectral_layout"] == "panchromatic"
    assert len(src.read_calls) <= SAMPLE_GRID_SIZE**2
    assert all(call[0] == (1,) for call in src.read_calls)
    assert all(call[1].width <= SAMPLE_WINDOW_SIZE for call in src.read_calls)
    assert all(call[1].height <= SAMPLE_WINDOW_SIZE for call in src.read_calls)


def test_jp2_characterization_defers_pixel_decode():
    class FakeJp2:
        width = 60_000
        height = 40_000
        count = 1
        dtypes = ("uint16",)
        nodata = None
        nodatavals = (None,)
        colorinterp = (SimpleNamespace(name="gray"),)
        name = "large_PAN.jp2"
        driver = "JP2OpenJPEG"

        def read(self, *_args, **_kwargs):
            raise AssertionError("JP2 pixels must not be decoded during catalogue import")

        def overviews(self, _index):
            return [2, 4, 8, 16, 32, 64, 128]

    result = characterize_raster(FakeJp2(), modality="EO", product_type="PAN")

    assert result["spectral_layout"] == "panchromatic"
    assert result["display_mode"] == "linear_robust"
    assert result["display_stats"]["status"] == "deferred"
    assert result["display_stats"]["method"] == "deferred_jp2"
