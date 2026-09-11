"""Zakres rozciagniecia „Widok" (DESIGN_DECISIONS.md, display-stretch D).

Progi liczone ze statystyk BIEZACEGO WIDOKU i podawane jawnie w URL kazdego kafla. Te testy
pilnuja trzech rzeczy: statystyki widoku mowia ta sama liczba co numpy na tej samej probce
(bez nodata), jawne okno ma pierwszenstwo i nie miesza sie w cache z innymi widokami, a kafle
z oknem nie zapelniaja cache dyskowego.

Run: python -m pytest backend/tests/test_view_stretch.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="geotile-view-stretch-"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import rasterio  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from services.scene_view_stats import compute_window_stats, snap_window  # noqa: E402


def _sar(size: int, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(rng.lognormal(7.0, 0.4, (size, size)), 1, 65535).astype(np.uint16)


def _write(path: pathlib.Path, data: np.ndarray, *, nodata=None, overviews=None) -> pathlib.Path:
    with rasterio.open(
        path, "w", driver="GTiff", width=data.shape[1], height=data.shape[0], count=1,
        dtype=data.dtype, transform=from_origin(20.0, 60.0, 1e-4, 1e-4), crs="EPSG:4326",
        nodata=nodata,
    ) as dst:
        dst.write(data[np.newaxis])
        if overviews:
            dst.build_overviews(overviews)
    return path


def test_view_stats_match_numpy_and_leave_nodata_out(tmp_path):
    values = _sar(512)
    values[:64, :] = 0  # pas bez danych, jak brzeg obroconego footprintu
    path = _write(tmp_path / "view.tif", values, nodata=0)

    stats = compute_window_stats(path, (0, 0, 512, 512), display_mode="uint16_log",
                                 sample_limit=512 * 512)

    valid = values > 0
    logs = np.log1p(values[valid].astype(np.float64))
    for key, point in (("p2", 2.0), ("p98", 98.0), ("p99.8", 99.8)):
        assert abs(stats["percentiles"][key] - np.percentile(logs, point)) < 1e-4, key
    # log1p(0) = 0: gdyby nodata weszlo do probki, dolny prog spadlby do zera.
    assert stats["percentiles"]["p0"] > 5.0
    assert abs(stats["valid_fraction"] - valid.mean()) < 1e-3
    assert stats["scope"] == "view" and stats["domain"] == "display"
    assert stats["insufficient"] is False and stats["sample_shape"] == [512, 512]


def test_view_with_too_few_valid_pixels_is_insufficient(tmp_path):
    values = np.zeros((256, 256), dtype=np.uint16)
    values[:20, :20] = 1500  # 400 pikseli danych — za malo na wiarygodne percentyle
    path = _write(tmp_path / "sparse.tif", values, nodata=0)

    stats = compute_window_stats(path, (0, 0, 256, 256), display_mode="uint16_log")
    assert stats["insufficient"] is True
    assert stats["reason"] == "too_few_valid_pixels"


def test_large_window_without_a_pyramid_declines_instead_of_reading_full_resolution(tmp_path):
    values = _sar(1024)
    flat = _write(tmp_path / "flat.tif", values)
    pyramid = _write(tmp_path / "pyramid.tif", values, overviews=[2, 4, 8, 16])

    # Krotnosc 16: bez piramidy oznaczalaby odczyt calego okna w pelnej rozdzielczosci.
    declined = compute_window_stats(flat, (0, 0, 1024, 1024), sample_limit=4096)
    assert declined["insufficient"] is True and declined["reason"] == "no_pyramid"
    served = compute_window_stats(pyramid, (0, 0, 1024, 1024), sample_limit=4096)
    assert served["insufficient"] is False and served["sample_shape"] == [64, 64]


def test_snap_window_clips_to_the_scene_and_groups_nearby_views():
    assert snap_window((-10, -10, 40, 40), 50, 50) == (0.0, 0.0, 40.0, 40.0)
    assert snap_window((600, 600, 700, 700), 500, 500) is None
    snapped = snap_window((1000.3, 2000.7, 5096.2, 6096.9), 20000, 20000)
    assert snapped == (960.0, 1984.0, 5120.0, 6144.0)
    # Przesuniecie o kilka pikseli przy oknie ~4000 px daje ten sam klucz cache.
    assert snap_window((1003.0, 2004.0, 5099.0, 6100.0), 20000, 20000) == snapped


def test_explicit_window_is_validated():
    from routers.scenes import _explicit_display_window

    assert _explicit_display_window(None, None) is None
    assert _explicit_display_window(6.5, 7.5) == (6.5, 7.5)
    for bad in ((6.5, None), (None, 7.5), (7.5, 6.5), (7.0, 7.0),
                (float("nan"), 7.0), (6.0, float("inf"))):
        with pytest.raises(HTTPException) as info:
            _explicit_display_window(*bad)
        assert info.value.status_code == 422, bad


def test_explicit_window_takes_precedence_over_the_scene_percentiles():
    from routers.scenes import _tile_display_window

    ctx = {"si": {"display_min": 5.0, "display_max": 10.0}, "scene_mtime_key": 1}
    window = _tile_display_window("p", "view-1", ctx, 1.0, 1.0, 1.0, 2.0, 98.0, (6.5, 7.5))
    assert window == (6.5, 7.5)


def test_display_cache_key_separates_view_windows():
    from routers.scenes import _display_cache_key

    base = _display_cache_key(1.0, 1.0, 1.0, 0.0, 100.0)
    first = _display_cache_key(1.0, 1.0, 1.0, 0.0, 100.0, (6.5, 7.5))
    second = _display_cache_key(1.0, 1.0, 1.0, 0.0, 100.0, (6.5, 7.6))
    assert base == "base"
    assert first.startswith("display-") and first != second


def test_view_window_geo_tile_is_browser_cacheable_but_not_written_to_disk(tmp_path, monkeypatch):
    from routers import scenes

    cache_path = tmp_path / "geo_tile_cache" / "v9" / "display-key" / "16" / "1" / "2.png"
    calls = []

    def fake_uncached(*args):
        calls.append(args)
        return b"png-bytes", "no-cache"

    monkeypatch.setattr(scenes, "_produce_geo_tile_uncached", fake_uncached)
    monkeypatch.setattr(scenes, "_DISPLAY_VARIANT_CACHE", True)

    result = scenes._produce_geo_tile(
        "project", "scene", 16, 1, 2, str(cache_path),
        1.0, 1.0, 1.0, 0.0, 100.0, True, (6.5, 7.5),
    )
    assert result == (b"png-bytes", "public, max-age=3600")
    assert not cache_path.exists()
    assert calls and calls[0][-1] == (6.5, 7.5)
    assert scenes._geo_disk_cache_enabled(True, (6.5, 7.5)) is False
    assert scenes._geo_disk_cache_enabled(False, None) is True


def _fake_scene(monkeypatch, path: pathlib.Path, size: int):
    from routers import scenes

    ctx = {
        "si": {"width": size, "height": size, "display_mode": "uint16_log"},
        "source_path": path,
        "variant": "variant",
        "raster_kind": "direct",
    }
    contract = SimpleNamespace(
        assets=SimpleNamespace(display_requires_jp2_decode=False, finest_display_factor=1)
    )
    monkeypatch.setattr(scenes, "_scene_render_context", lambda *_args: ctx)
    monkeypatch.setattr(scenes, "_scene_display_cache_revision", lambda *_args: "rev-1")
    monkeypatch.setattr(scenes, "_display_read_path_for", lambda *_args: path)
    monkeypatch.setattr(scenes, "direct_preview_asset", lambda *_args: None)
    monkeypatch.setattr(scenes, "_zoom_contract", lambda *_args, **_kwargs: contract)
    scenes._VIEW_STATS_CACHE.clear()
    return scenes


def test_view_stats_producer_clips_snaps_and_caches(tmp_path, monkeypatch):
    import services.scene_view_stats as view_stats

    path = _write(tmp_path / "scene.tif", _sar(512), nodata=0)
    scenes = _fake_scene(monkeypatch, path, 512)
    reads = []
    original = view_stats.compute_window_stats

    def counting(*args, **kwargs):
        reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(view_stats, "compute_window_stats", counting)

    first = scenes._produce_view_stats("p", "s-view", -20, -20, 300, 300)
    # Kilka pikseli przesuniecia — to samo okno po przyciagnieciu, bez drugiego odczytu.
    second = scenes._produce_view_stats("p", "s-view", -18, -19, 299, 298)
    assert first["window"] == [0.0, 0.0, 300.0, 300.0]
    assert first["display_revision"] == "rev-1" and first["insufficient"] is False
    assert second is first and len(reads) == 1

    outside = scenes._produce_view_stats("p", "s-view", 600, 600, 700, 700)
    assert outside["insufficient"] is True and outside["reason"] == "outside_scene"


def test_view_stats_endpoint_honours_the_rollback_switch(monkeypatch):
    from routers import scenes

    monkeypatch.setattr(scenes, "project_exists", lambda *_args: True)
    monkeypatch.setattr(scenes, "_VIEW_STRETCH_ENABLED", False)
    with pytest.raises(HTTPException) as info:
        asyncio.run(scenes.get_scene_view_stats("p", "s", None, 0.0, 0.0, 10.0, 10.0))
    assert info.value.status_code == 404
