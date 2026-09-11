"""P1.1 contracts for provider-scoped identity and source invalidation."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-provider-identity-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import load_scene_json, project_dir, save_json, save_scene_json, scene_dir  # noqa: E402
from routers.scene_import import _scene_characterization_matches_selection  # noqa: E402
from services.scene_loader import SCENE_INFO_VERSION  # noqa: E402
from services.scene_packages import identity as identity_service  # noqa: E402
from services.scene_packages.identity import compute_scene_package_identity  # noqa: E402
from services.scene_packages.working_view import (  # noqa: E402
    build_working_variant_definition,
    working_variant_id,
)
from services.scene_sources import save_scene_sources  # noqa: E402


def _asset(asset_id: str, role: str, relative_path: str, path: pathlib.Path, **extra):
    stat = path.stat()
    return {
        "asset_id": asset_id,
        "role": role,
        "relative_path": relative_path,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": None,
        **extra,
    }


def _package_project(project_id: str):
    source_root = TEST_ROOT / project_id / "delivery"
    source_root.mkdir(parents=True, exist_ok=True)
    contents = {
        "part_a.tif": b"selected measurement A" * 100,
        "part_b.tif": b"selected measurement B" * 100,
        "scene.imd": b"defining metadata" * 40,
        "browse.jpg": b"browse preview" * 30,
        "alternate.tif": b"unselected alternative" * 100,
    }
    for name, payload in contents.items():
        (source_root / name).write_bytes(payload)

    selection = {
        "status": "ready",
        "product_type": "MOSAIC",
        "raster_kind": "virtual_mosaic",
        "asset_ids": ["part-a", "part-b"],
        "identity_asset_ids": ["part-a", "part-b"],
        "metadata_asset_ids": ["metadata"],
        "mosaic_parts_order": ["R1C1", "R1C2"],
    }
    assets = [
        _asset("part-a", "primary_raster", "part_a.tif", source_root / "part_a.tif", part_id="R1C1"),
        _asset("part-b", "primary_raster", "part_b.tif", source_root / "part_b.tif", part_id="R1C2"),
        _asset("metadata", "metadata", "scene.imd", source_root / "scene.imd"),
        _asset("browse", "browse", "browse.jpg", source_root / "browse.jpg"),
        _asset("alternate", "raster_candidate", "alternate.tif", source_root / "alternate.tif"),
    ]
    variant_definition = build_working_variant_definition(selection)
    variant_id = working_variant_id(variant_definition)
    relative_vrt = f"derived_scenes/scene/{variant_id}/scene.vrt"
    vrt = project_dir(project_id) / relative_vrt
    vrt.parent.mkdir(parents=True, exist_ok=True)
    vrt.write_text("<VRTDataset/>", encoding="utf-8")

    save_json(project_id, "project", {"id": project_id, "name": project_id})
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src",
        "provider": "worldview",
        "root_path": str(source_root),
        "enabled": True,
    }]})
    save_scene_json(project_id, "scene", "scene", {
        "id": "scene",
        "filename": "part_a.tif",
        "scene_info": {"width": 100, "height": 100},
        "scene_info_version": SCENE_INFO_VERSION,
        "overview_status": "ready",
        "working_grid_uid": "grid-old",
    })
    save_scene_json(project_id, "scene", "scene_manifest", {
        "scene_id": "scene",
        "source_package": {
            "source_id": "src",
            "provider": "worldview",
            "provider_scene_id": "provider-scene",
            "identity_asset_ids": ["part-a", "part-b"],
            "defining_metadata_asset_ids": ["metadata"],
            "selection": selection,
            "assets": assets,
        },
        "source_identity": {"status": "pending"},
        "working_view": {
            "variant_id": variant_id,
            "variant_definition": variant_definition,
            "raster_kind": "virtual_mosaic",
            "raster_ref": {"storage": "project", "relative_path": relative_vrt},
            "working_grid_uid": "grid-old",
            "preparation_status": "ready",
        },
    })
    return source_root, vrt


def test_sampling_reads_only_selected_measurements_and_defining_metadata(monkeypatch):
    project_id = "scoped-sampling"
    _source_root, _vrt = _package_project(project_id)
    sampled: list[str] = []
    original = identity_service.sampled_content_signature

    def record_sample(path, *args, **kwargs):
        sampled.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(identity_service, "sampled_content_signature", record_sample)
    result = compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})

    assert set(sampled) == {"part_a.tif", "part_b.tif", "scene.imd"}
    assert result["identity_scope"] == {
        "measurement_asset_ids": ["part-a", "part-b"],
        "defining_metadata_asset_ids": ["metadata"],
        "candidate_asset_ids": ["part-a", "part-b", "metadata"],
    }
    assert result["delivery_inventory_asset_count"] == 5
    assert result["delivery_inventory_fingerprint"]
    assert result["scene_candidate_fingerprint"]
    by_id = {asset["asset_id"]: asset for asset in manifest["source_package"]["assets"]}
    assert by_id["browse"].get("content_signature") is None
    assert by_id["alternate"].get("content_signature") is None


def test_exact_lineage_hashes_every_measurement_and_no_other_asset(monkeypatch):
    project_id = "scoped-exact"
    _source_root, _vrt = _package_project(project_id)
    hashed: list[str] = []
    original = identity_service.sha256_file

    def record_hash(path, *args, **kwargs):
        hashed.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(identity_service, "sha256_file", record_hash)
    result = compute_scene_package_identity(
        project_id, "scene", rebuild_index=False, require_exact=True
    )
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})
    by_id = {asset["asset_id"]: asset for asset in manifest["source_package"]["assets"]}

    assert set(hashed) == {"part_a.tif", "part_b.tif"}
    assert result["identity_strength"] == "exact"
    assert result["source_scene_uid"].startswith("scene-sha256:")
    assert by_id["part-a"]["sha256"] and by_id["part-b"]["sha256"]
    assert by_id["metadata"].get("sha256") is None
    assert by_id["browse"].get("sha256") is None
    assert by_id["alternate"].get("sha256") is None


def test_browse_change_updates_delivery_inventory_without_invalidating_scene():
    project_id = "browse-change"
    source_root, vrt = _package_project(project_id)
    first = compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    first_working = first["working_variant_fingerprint"]

    (source_root / "browse.jpg").write_bytes(b"replaced browse" * 100)
    second = compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    scene = load_scene_json(project_id, "scene", "scene", default={})

    assert second["status"] == "complete"
    assert second["delivery_inventory_fingerprint"] != first["delivery_inventory_fingerprint"]
    assert second["scene_candidate_fingerprint"] == first["scene_candidate_fingerprint"]
    assert second["working_variant_fingerprint"] == first_working
    assert scene.get("scene_info")
    assert scene["working_grid_uid"] == "grid-old"
    assert vrt.exists()


def test_any_changed_mosaic_part_invalidates_vrt_characterization_and_caches():
    project_id = "mosaic-change"
    source_root, vrt = _package_project(project_id)
    compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    directory = scene_dir(project_id, "scene")
    (directory / "histogram.json").write_text("{}", encoding="utf-8")
    (directory / "geo_tile_cache").mkdir(parents=True, exist_ok=True)
    (directory / "geo_tile_cache" / "tile.bin").write_bytes(b"cache")
    (directory / "scene_thumbnail.png").write_bytes(b"png")

    (source_root / "part_b.tif").write_bytes(b"changed selected measurement B" * 100)
    changed = compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    scene = load_scene_json(project_id, "scene", "scene", default={})
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})

    assert changed["status"] == "changed"
    assert changed["change_detection_strength"] == "heuristic"
    assert not vrt.exists()
    assert "scene_info" not in scene and "scene_info_version" not in scene
    assert scene["overview_status"] == "pending"
    assert scene["preparation_status"] == "source_changed"
    assert scene["working_grid_uid"] is None
    assert manifest["working_view"]["preparation_status"] == "source_changed"
    assert manifest["working_view"]["working_grid_uid"] is None
    assert not (directory / "histogram.json").exists()
    assert not (directory / "geo_tile_cache").exists()
    assert not (directory / "scene_thumbnail.png").exists()


def test_defining_metadata_change_updates_working_fingerprint_after_exact_hashing():
    project_id = "metadata-change"
    source_root, _vrt = _package_project(project_id)
    first = compute_scene_package_identity(
        project_id, "scene", rebuild_index=False, require_exact=True
    )

    (source_root / "scene.imd").write_bytes(b"updated defining metadata" * 40)
    second = compute_scene_package_identity(
        project_id, "scene", rebuild_index=False, require_exact=True
    )

    assert second["source_scene_fingerprint"] == first["source_scene_fingerprint"]
    assert second["scene_candidate_fingerprint"] != first["scene_candidate_fingerprint"]
    assert second["working_variant_fingerprint"] != first["working_variant_fingerprint"]
    assert second["status"] == "changed"
    assert second["change_detection_strength"] == "heuristic"


def test_resume_compares_all_selected_parts_but_ignores_browse_snapshot():
    project_id = "resume-scope"
    _source_root, _vrt = _package_project(project_id)
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})
    previous = manifest["source_package"]
    selection = previous["selection"]
    scene = load_scene_json(project_id, "scene", "scene", default={})

    browse_changed = {
        **previous,
        "assets": [
            {**asset, "size": int(asset["size"]) + 1}
            if asset["asset_id"] == "browse" else dict(asset)
            for asset in previous["assets"]
        ],
    }
    assert _scene_characterization_matches_selection(
        scene, browse_changed, selection, previous_package=previous
    )

    part_changed = {
        **previous,
        "assets": [
            {**asset, "mtime_ns": int(asset["mtime_ns"]) + 1}
            if asset["asset_id"] == "part-b" else dict(asset)
            for asset in previous["assets"]
        ],
    }
    assert not _scene_characterization_matches_selection(
        scene, part_changed, selection, previous_package=previous
    )
