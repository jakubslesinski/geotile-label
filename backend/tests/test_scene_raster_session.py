"""Thread-affine sesja rastra — R0.3.

Testowana jest logika cyklu zycia uchwytu: ponowne uzycie, klucz z rewizja, LRU, TTL,
uniewaznianie miedzy watkami i izolacja miedzy watkami. Samo `rasterio` jest tu cienka
warstwa i zostaje podstawione atrapa — dzieki temu test nie wymaga GDAL-a i sprawdza
dokladnie to, co jest wlasna logika modulu.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from services import scene_raster_session as session_module
from services.scene_raster_session import (
    close_current_thread,
    display_raster_session,
    invalidate_all,
    session_stats,
)


class _FakeHandle:
    def __init__(self, label: str, registry: list[str]):
        self.label = label
        self.closed = False
        self._registry = registry

    def close(self) -> None:
        self.closed = True
        self._registry.append(self.label)


class _FakeDataset(_FakeHandle):
    nodata = None
    mask_flag_enums = ()


@pytest.fixture
def fake_rasterio(monkeypatch):
    """Podstaw minimalne `rasterio`, wystarczajace dla modulu sesji."""
    closed: list[str] = []
    opened: list[str] = []

    enums = types.SimpleNamespace(
        MaskFlags=types.SimpleNamespace(all_valid="all_valid"),
        Resampling=types.SimpleNamespace(bilinear="bilinear"),
    )

    def _open(path):
        opened.append(path)
        dataset = _FakeDataset(f"ds:{path}:{len(opened)}", closed)
        dataset.mask_flag_enums = (("all_valid",),)
        return dataset

    class _Env:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _warped(dataset, **kwargs):
        return _FakeHandle(f"vrt:{dataset.label}", closed)

    rasterio = types.ModuleType("rasterio")
    rasterio.open = _open
    rasterio.Env = _Env
    rasterio.enums = enums
    vrt_mod = types.ModuleType("rasterio.vrt")
    vrt_mod.WarpedVRT = _warped
    enums_mod = types.ModuleType("rasterio.enums")
    enums_mod.MaskFlags = enums.MaskFlags
    enums_mod.Resampling = enums.Resampling

    # Podmodul musi byc widoczny i przez `sys.modules`, i jako atrybut rodzica —
    # `from rasterio.vrt import WarpedVRT` korzysta z pierwszego, a `rasterio.vrt.X`
    # z drugiego.
    rasterio.vrt = vrt_mod
    rasterio.enums = enums_mod
    monkeypatch.setitem(sys.modules, "rasterio", rasterio)
    monkeypatch.setitem(sys.modules, "rasterio.vrt", vrt_mod)
    monkeypatch.setitem(sys.modules, "rasterio.enums", enums_mod)
    close_current_thread()
    yield types.SimpleNamespace(opened=opened, closed=closed)
    close_current_thread()


def test_same_path_and_revision_reuses_one_handle(fake_rasterio):
    """Sedno R0.3: sasiednie kafle nie moga otwierac rastra na nowo."""
    for _ in range(5):
        with display_raster_session("scene.tif", revision="rev1") as handle:
            assert handle.warped is not None
    assert len(fake_rasterio.opened) == 1
    assert session_stats()["entries_in_thread"] == 1


def test_new_revision_forces_new_handle(fake_rasterio):
    """Podmiana derywatu nie moze byc obsluzona starym uchwytem."""
    with display_raster_session("scene.tif", revision="rev1"):
        pass
    with display_raster_session("scene.tif", revision="rev2"):
        pass
    assert len(fake_rasterio.opened) == 2
    assert session_stats()["entries_in_thread"] == 2


def test_lru_closes_the_least_recently_used_entry(fake_rasterio, monkeypatch):
    monkeypatch.setattr(session_module, "MAX_ENTRIES_PER_THREAD", 2)
    for name in ("a.tif", "b.tif", "c.tif"):
        with display_raster_session(name, revision="r"):
            pass
    assert session_stats()["entries_in_thread"] == 2
    # Najdawniej uzywany wpis musi zostac ZAMKNIETY, nie tylko porzucony —
    # na Windows otwarty plik blokuje podmiane derywatu.
    assert any(label.startswith("ds:a.tif") for label in fake_rasterio.closed)


def test_idle_entries_expire(fake_rasterio, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(session_module.time, "monotonic", lambda: clock["now"])
    with display_raster_session("scene.tif", revision="r"):
        pass
    clock["now"] += session_module.IDLE_TTL_SECONDS + 1
    with display_raster_session("other.tif", revision="r"):
        pass
    assert any(label.startswith("ds:scene.tif") for label in fake_rasterio.closed)


def test_invalidate_all_drops_entries_on_next_use(fake_rasterio):
    with display_raster_session("scene.tif", revision="r"):
        pass
    invalidate_all()
    with display_raster_session("scene.tif", revision="r"):
        pass
    assert len(fake_rasterio.opened) == 2
    assert any(label.startswith("ds:scene.tif") for label in fake_rasterio.closed)


def test_handles_are_not_shared_between_threads(fake_rasterio):
    """Uchwyt GDAL nie moze opuscic swojego watku."""
    seen: dict[str, int] = {}

    def worker(name: str) -> None:
        with display_raster_session("scene.tif", revision="r"):
            seen[name] = session_stats()["entries_in_thread"]
        close_current_thread()

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {"t0": 1, "t1": 1, "t2": 1}
    # Kazdy watek otworzyl wlasny uchwyt, bo cache jest per-watek.
    assert len(fake_rasterio.opened) == 3


def test_failed_warp_closes_the_dataset(fake_rasterio, monkeypatch):
    """Blad budowy WarpedVRT nie moze zostawic wiszacego uchwytu."""
    def _boom(dataset, **kwargs):
        raise RuntimeError("warp failed")

    monkeypatch.setattr(sys.modules["rasterio.vrt"], "WarpedVRT", _boom)
    with pytest.raises(RuntimeError):
        with display_raster_session("scene.tif", revision="r"):
            pass
    assert any(label.startswith("ds:scene.tif") for label in fake_rasterio.closed)
    assert session_stats()["entries_in_thread"] == 0


def test_close_current_thread_reports_and_clears(fake_rasterio):
    with display_raster_session("a.tif", revision="r"):
        pass
    with display_raster_session("b.tif", revision="r"):
        pass
    assert close_current_thread() == 2
    assert session_stats()["entries_in_thread"] == 0
