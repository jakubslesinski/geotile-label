"""Source-adjacent overview discovery and cache-coherency regressions."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import numpy as np


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="geotile_overviews_"))

from db.storage import create_project_root, load_scene_json, project_dir, save_scene_json, scene_dir  # noqa: E402
from services.scene_overviews import (  # noqa: E402
    apply_source_overview_metadata,
    describe_open_dataset_overviews,
    source_overviews_are_display_ready,
    source_overview_sidecar_snapshot,
)
from services.scene_raster_resolver import (  # noqa: E402
    RasterHandle,
    SceneRasterResolver,
    sync_scene_source_overviews,
)


def _fake_state(source: pathlib.Path, *, usable: bool) -> dict:
    snapshot = source_overview_sidecar_snapshot(source)
    overview_type = "external_gtiff_ovr" if snapshot["artifacts"] else "none"
    factors = [2, 4, 8] if usable else []
    return {
        "schema_version": 1,
        "type": overview_type,
        "driver": "GTiff",
        "usable": usable,
        "factors": factors,
        "factors_by_band": [factors],
        "sidecar_present": bool(snapshot["artifacts"]),
        "sidecar_fingerprint": snapshot["fingerprint"],
        "artifacts": snapshot["artifacts"],
        "fingerprint": f"state:{overview_type}:{snapshot['fingerprint']}",
        "read_error": None,
    }


def _write_display_caches(project_id: str, scene_id: str) -> None:
    directory = scene_dir(project_id, scene_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "histogram.json").write_text("{}", encoding="utf-8")
    (directory / "scene_thumbnail_v2.png").write_bytes(b"thumbnail")
    tile = directory / "geo_tile_cache" / "v7" / "0" / "0" / "0.png"
    tile.parent.mkdir(parents=True, exist_ok=True)
    tile.write_bytes(b"tile")


def _assert_display_caches_removed(project_id: str, scene_id: str) -> None:
    directory = scene_dir(project_id, scene_id)
    assert not (directory / "histogram.json").exists()
    assert not (directory / "scene_thumbnail_v2.png").exists()
    assert not (directory / "geo_tile_cache").exists()


def test_sidecar_snapshot_changes_on_add_replace_and_remove(tmp_path: pathlib.Path):
    source = tmp_path / "scene.tif"
    source.write_bytes(b"source")
    sidecar = pathlib.Path(str(source) + ".ovr")

    absent = source_overview_sidecar_snapshot(source)
    sidecar.write_bytes(b"overview-v1")
    added = source_overview_sidecar_snapshot(source)
    sidecar.write_bytes(b"overview-version-two")
    replaced = source_overview_sidecar_snapshot(source)
    sidecar.unlink()
    removed = source_overview_sidecar_snapshot(source)

    assert absent["artifacts"] == []
    assert added["artifacts"][0]["type"] == "external_gtiff_ovr"
    assert added["fingerprint"] != absent["fingerprint"]
    assert replaced["fingerprint"] != added["fingerprint"]
    assert removed["fingerprint"] == absent["fingerprint"]


def test_open_dataset_registers_external_and_native_types(tmp_path: pathlib.Path):
    class FakeDataset:
        count = 2
        driver = "GTiff"

        @staticmethod
        def overviews(_band_index):
            return [2, 4, 8]

    source = tmp_path / "scene.tif"
    source.write_bytes(b"source")
    pathlib.Path(str(source) + ".ovr").write_bytes(b"overview")

    external = describe_open_dataset_overviews(FakeDataset(), source)
    assert external["type"] == "external_gtiff_ovr"
    assert external["usable"] is True
    assert external["factors_by_band"] == [[2, 4, 8], [2, 4, 8]]

    pathlib.Path(str(source) + ".ovr").unlink()
    internal = describe_open_dataset_overviews(FakeDataset(), source)
    assert internal["type"] == "internal"

    FakeDataset.driver = "JP2ECW"
    native = describe_open_dataset_overviews(FakeDataset(), source)
    assert native["type"] == "native_multiresolution"


def test_large_native_jp2_requires_fast_external_display_overview(monkeypatch):
    native = {
        "type": "native_multiresolution",
        "usable": True,
        "width": 63_856,
        "height": 42_336,
    }
    external = {**native, "type": "external_gtiff_ovr"}

    monkeypatch.setenv("GEOTILE_JP2_EXTERNAL_OVERVIEWS", "1")
    monkeypatch.setenv("GEOTILE_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS", "268435456")

    assert source_overviews_are_display_ready(native) is False
    assert source_overviews_are_display_ready(
        native,
        width=8_192,
        height=8_192,
    ) is True
    assert source_overviews_are_display_ready(external) is True

    monkeypatch.setenv("GEOTILE_JP2_EXTERNAL_OVERVIEWS", "0")
    assert source_overviews_are_display_ready(native) is True


def test_apply_metadata_keeps_legacy_and_explicit_fields(tmp_path: pathlib.Path):
    source = tmp_path / "scene.tif"
    source.write_bytes(b"source")
    pathlib.Path(str(source) + ".ovr").write_bytes(b"overview")
    state = _fake_state(source, usable=True)
    scene = {"scene_info": {"display_stats": {"native_overview_factors": []}}}

    apply_source_overview_metadata(scene, state)

    assert scene["scene_info"]["native_overviews"] is True
    assert scene["scene_info"]["source_overviews"]["type"] == "external_gtiff_ovr"
    assert scene["overview_type"] == "external_gtiff_ovr"
    assert scene["overview_factors"] == [2, 4, 8]


def test_sync_invalidates_caches_on_add_replace_and_remove(tmp_path: pathlib.Path, monkeypatch):
    project_id = "overview-sync-project"
    scene_id = "scene-01"
    create_project_root(project_id, "Overview sync")
    source = tmp_path / "scene.tif"
    source.write_bytes(b"source")
    sidecar = pathlib.Path(str(source) + ".ovr")
    initial = _fake_state(source, usable=False)
    scene = {
        "id": scene_id,
        "filename": source.name,
        "raster_kind": "direct",
        "working_variant_id": "variant-a",
        "scene_info": {"width": 100, "height": 100, "source_overviews": initial},
        "overview_status": "ready",
    }
    save_scene_json(project_id, scene_id, "scene", scene)
    save_scene_json(project_id, scene_id, "scene_manifest", {"image": {}, "display": {}})
    monkeypatch.setattr(
        SceneRasterResolver,
        "resolve",
        staticmethod(
            lambda _project_id, _scene_id: RasterHandle(
                source,
                "direct",
                {"storage": "source", "relative_path": source.name},
                "variant-a",
            )
        ),
    )

    import services.scene_raster_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "inspect_source_overviews",
        lambda path: _fake_state(path, usable=pathlib.Path(str(path) + ".ovr").is_file()),
    )

    # Existing project-local pyramid must be discarded once a reusable source
    # sidecar appears.
    derived = project_dir(project_id) / "derived_scenes" / scene_id / "variant-a"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "overview.vrt").write_bytes(b"vrt")
    (derived / "overview.vrt.ovr").write_bytes(b"project overview")

    _write_display_caches(project_id, scene_id)
    sidecar.write_bytes(b"external-v1")
    added_scene, added_changed = sync_scene_source_overviews(
        project_id, scene_id, force=True
    )
    added_fingerprint = added_scene["overview_sidecar_fingerprint"]

    assert added_changed is True
    assert added_scene["overview_status"] == "native"
    assert added_scene["overview_type"] == "external_gtiff_ovr"
    assert not (derived / "overview.vrt").exists()
    assert not (derived / "overview.vrt.ovr").exists()
    _assert_display_caches_removed(project_id, scene_id)

    _write_display_caches(project_id, scene_id)
    sidecar.write_bytes(b"external-overview-version-two")
    replaced_scene, replaced_changed = sync_scene_source_overviews(
        project_id, scene_id, force=True
    )

    assert replaced_changed is True
    assert replaced_scene["overview_sidecar_fingerprint"] != added_fingerprint
    _assert_display_caches_removed(project_id, scene_id)

    _write_display_caches(project_id, scene_id)
    sidecar.unlink()
    removed_scene, removed_changed = sync_scene_source_overviews(
        project_id, scene_id, force=True
    )

    assert removed_changed is True
    assert removed_scene["overview_status"] == "pending"
    assert removed_scene["overview_type"] == "none"
    assert removed_scene["overview_factors"] == []
    _assert_display_caches_removed(project_id, scene_id)
    persisted = load_scene_json(project_id, scene_id, "scene", default={})
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    assert persisted["overview_type"] == "none"
    assert manifest["image"]["source_overviews"]["type"] == "none"


def test_sync_migrates_existing_large_native_jp2_to_pending(
    tmp_path: pathlib.Path,
    monkeypatch,
):
    project_id = "native-jp2-policy-project"
    scene_id = "scene-jp2"
    create_project_root(project_id, "Native JP2 policy")
    source = tmp_path / "large.jp2"
    source.write_bytes(b"source")
    snapshot = source_overview_sidecar_snapshot(source)
    state = {
        "schema_version": 1,
        "type": "native_multiresolution",
        "driver": "JP2OpenJPEG",
        "width": 63_856,
        "height": 42_336,
        "usable": True,
        "factors": [2, 4, 8, 16, 32, 64, 128, 255, 511],
        "factors_by_band": [[2, 4, 8, 16, 32, 64, 128, 255, 511]],
        "sidecar_present": False,
        "sidecar_fingerprint": snapshot["fingerprint"],
        "artifacts": [],
        "fingerprint": "native-state",
        "read_error": None,
    }
    save_scene_json(
        project_id,
        scene_id,
        "scene",
        {
            "id": scene_id,
            "filename": source.name,
            "raster_kind": "direct",
            "working_variant_id": "variant-jp2",
            "scene_info": {
                "width": 63_856,
                "height": 42_336,
                "source_overviews": state,
            },
            "overview_status": "native",
        },
    )
    monkeypatch.setattr(
        SceneRasterResolver,
        "resolve",
        staticmethod(
            lambda _project_id, _scene_id: RasterHandle(
                source,
                "direct",
                {"storage": "source", "relative_path": source.name},
                "variant-jp2",
            )
        ),
    )
    monkeypatch.setenv("GEOTILE_JP2_EXTERNAL_OVERVIEWS", "1")

    migrated, changed = sync_scene_source_overviews(
        project_id,
        scene_id,
        force=True,
    )

    assert changed is False
    assert migrated["overview_status"] == "pending"
    assert load_scene_json(project_id, scene_id, "scene")["overview_status"] == "pending"


def test_adjusted_geo_tile_variant_is_cached_and_deduplicated(tmp_path: pathlib.Path, monkeypatch):
    from routers import scenes

    cache_path = tmp_path / "geo_tile_cache" / "v8" / "display-key" / "16" / "1" / "2.png"
    calls = []

    def fake_uncached(*args):
        calls.append(args)
        return b"png-bytes", "no-cache"

    monkeypatch.setattr(scenes, "_produce_geo_tile_uncached", fake_uncached)
    monkeypatch.setattr(scenes, "_DISPLAY_VARIANT_CACHE", True)

    first = scenes._produce_geo_tile(
        "project", "scene", 16, 1, 2, str(cache_path),
        1.0, 1.0, 1.0, 2.0, 98.0, True,
    )
    second = scenes._produce_geo_tile(
        "project", "scene", 16, 1, 2, str(cache_path),
        1.0, 1.0, 1.0, 2.0, 98.0, True,
    )

    assert first == second == (b"png-bytes", "public, max-age=3600")
    assert len(calls) == 1
    assert cache_path.read_bytes() == b"png-bytes"
    assert scenes._display_cache_key(1.0, 1.0, 1.0, 2.0, 98.0) != "base"


def test_display_cache_revision_changes_with_project_overview(tmp_path: pathlib.Path, monkeypatch):
    from routers import scenes

    source = tmp_path / "source.jp2"
    source.write_bytes(b"source")
    overview = tmp_path / "overview.vrt"
    overview.write_text("vrt", encoding="utf-8")
    sidecar = tmp_path / "overview.vrt.ovr"
    sidecar.write_bytes(b"first")
    context = {
        "si": {
            "display_profile_version": 2,
            "display_mode": "linear_robust",
            "display_min": [10.0],
            "display_max": [1000.0],
        },
        "source_path": source,
        "raster_kind": "direct",
        "variant": "variant",
        "scene_mtime_key": (1, 1),
        "overview_fingerprint": "overview-a",
    }
    monkeypatch.setattr(scenes, "_scene_render_context", lambda *_args: context)
    monkeypatch.setattr(scenes, "_display_read_path_for", lambda *_args: overview)

    first = scenes._scene_display_cache_revision("project", "scene")
    sidecar.write_bytes(b"second-version")
    second = scenes._scene_display_cache_revision("project", "scene")

    assert first != second


def test_all_valid_vrt_tile_reads_coverage_without_a_second_mask_decode():
    from rasterio.windows import Window
    from routers import scenes

    class FakeVrt:
        width = 512
        height = 512
        dtypes = ("uint16",)
        mask_reads = 0

        @staticmethod
        def read(*, indexes, window, out_shape, resampling):
            result = np.full(out_shape, 1234, dtype=np.uint16)
            result[-1, :, :] = 65535
            return result

        @classmethod
        def read_masks(cls, **_kwargs):
            cls.mask_reads += 1
            raise AssertionError("all-valid rasters must not trigger read_masks")

    data, masks = scenes._read_vrt_tile_window(
        FakeVrt(),
        Window(0, 0, 128, 128),
        [1],
        1,
        coverage_band_index=2,
    )

    assert data.shape == (1, 256, 256)
    assert np.all(data == 1234)
    assert np.all(masks == 255)
    assert FakeVrt.mask_reads == 0
