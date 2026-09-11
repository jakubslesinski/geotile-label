"""P0.1 regression tests for exact versus sampled source identity.

Run: python backend/tests/test_content_identity.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-content-identity-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import load_scene_json, save_json, save_scene_json  # noqa: E402
from services.annotation_import import compare_source_identity, match_scene  # noqa: E402
from services.scene_identity import identity_is_exact, is_full_sha256  # noqa: E402
from services.scene_packages.identity import (  # noqa: E402
    SAMPLED_SIGNATURE_METHOD,
    compute_scene_package_identity,
    sampled_content_signature,
)
from services.scene_sources import save_scene_sources  # noqa: E402
from services.dataset_runs import (  # noqa: E402
    DatasetPublicationError,
    dataset_identity_summary,
    require_dataset_exact_identities,
)


def _package_project(project_id: str, payload: bytes) -> pathlib.Path:
    source_root = TEST_ROOT / project_id / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    source_path = source_root / "scene.bin"
    source_path.write_bytes(payload)
    stat = source_path.stat()
    save_json(project_id, "project", {"id": project_id, "name": project_id})
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src",
        "provider": "generic",
        "root_path": str(source_root),
        "enabled": True,
    }]})
    save_scene_json(project_id, "scene", "scene", {"id": "scene", "filename": source_path.name})
    save_scene_json(project_id, "scene", "scene_manifest", {
        "scene_id": "scene",
        "source_package": {
            "source_id": "src",
            "provider_scene_id": "provider-scene",
            "identity_asset_ids": ["raster"],
            "assets": [{
                "asset_id": "raster",
                "role": "primary_raster",
                "part_id": None,
                "relative_path": source_path.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": None,
            }],
        },
        "source_identity": {"status": "pending"},
        "working_view": {"working_grid_uid": "grid"},
    })
    return source_path


def test_sampled_change_is_never_exact():
    sample = 1024
    path = TEST_ROOT / "sampled.bin"
    path.write_bytes(b"\x00" * (50 * sample))
    signature_before = sampled_content_signature(path, sample_bytes=sample)
    data = bytearray(path.read_bytes())
    data[10 * sample] = 1
    path.write_bytes(data)
    signature_after = sampled_content_signature(path, sample_bytes=sample)
    assert signature_before == signature_after

    left = {
        "source_scene_candidate_uid": f"scene-signature-v1:{signature_before}",
        "source_identity_strength": "heuristic",
    }
    right = {
        "source_scene_candidate_uid": f"scene-signature-v1:{signature_after}",
        "source_identity_strength": "heuristic",
    }
    assert compare_source_identity(left, right) == "probable_match"


def test_legacy_sig1_is_migrated_out_of_sha256():
    project_id = "legacy-sig"
    source_path = _package_project(project_id, b"legacy-content" * 100)
    legacy = sampled_content_signature(source_path, sample_bytes=16)
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})
    manifest["source_package"]["assets"][0]["sha256"] = legacy
    save_scene_json(project_id, "scene", "scene_manifest", manifest)

    identity = compute_scene_package_identity(project_id, "scene", rebuild_index=False)
    migrated = load_scene_json(project_id, "scene", "scene_manifest", default={})
    asset = migrated["source_package"]["assets"][0]
    assert asset["sha256"] is None
    assert asset["content_signature"] == legacy
    assert asset["content_signature_method"] == SAMPLED_SIGNATURE_METHOD
    assert migrated["source_file_sha256"] is None
    assert identity["identity_strength"] == "heuristic"
    assert identity["source_scene_uid"] is None
    assert identity["source_scene_candidate_uid"].startswith("scene-signature-v1:")


def test_full_hash_detects_change_outside_sampled_ranges():
    project_id = "full-sha"
    source_path = _package_project(project_id, b"\x00" * (3 * 1024 * 1024))
    first = compute_scene_package_identity(
        project_id, "scene", rebuild_index=False, require_exact=True
    )
    assert first["identity_strength"] == "exact"
    assert first["source_scene_uid"].startswith("scene-sha256:")
    manifest = load_scene_json(project_id, "scene", "scene_manifest", default={})
    assert is_full_sha256(manifest["source_file_sha256"])
    assert identity_is_exact(manifest)

    with source_path.open("r+b") as handle:
        handle.seek(1024 * 1024 + 123)
        handle.write(b"\xff")
    os.utime(source_path, None)
    changed = compute_scene_package_identity(
        project_id, "scene", rebuild_index=False, require_exact=True
    )
    assert changed["status"] == "changed"
    assert changed["change_detection_strength"] == "exact"
    assert changed["source_scene_uid"] != first["source_scene_uid"]


def test_compare_source_identity_requires_complete_sha256_for_exact():
    digest = "a" * 64
    exact_left = {"source_file_sha256": digest, "source_scene_uid": f"sha256:{digest}"}
    exact_right = {"source_file_sha256": digest, "source_scene_uid": f"sha256:{digest}"}
    assert compare_source_identity(exact_left, exact_right) == "exact_match"

    fake_left = {"source_file_sha256": "sig1:" + "b" * 64}
    fake_right = {"source_file_sha256": "sig1:" + "b" * 64}
    assert compare_source_identity(fake_left, fake_right) != "exact_match"


def test_probable_import_match_requires_explicit_policy():
    package_scene = {
        "source_scene_candidate_uid": "scene-signature-v1:candidate",
        "source_identity_strength": "heuristic",
        "working_grid_uid": "grid",
    }
    targets = {
        "target": {
            "manifest": {
                "source_scene_candidate_uid": "scene-signature-v1:candidate",
                "source_identity_strength": "heuristic",
                "working_view": {"working_grid_uid": "grid"},
            }
        }
    }
    blocked = match_scene(package_scene, targets)
    assert blocked == (
        None,
        "source_scene_candidate_uid",
        "approval_required",
        ["target"],
    )
    accepted = match_scene(package_scene, targets, allow_probable=True)
    assert accepted == (
        "target",
        "source_scene_candidate_uid",
        "probable",
        ["target"],
    )


def test_dataset_lineage_gate_rejects_heuristic_identity():
    heuristic = {
        "scenes": [{
            "scene_id": "scene",
            "filename": "scene.bin",
            "source_scene_candidate_uid": "scene-signature-v1:candidate",
            "source_identity_strength": "heuristic",
        }]
    }
    summary = dataset_identity_summary(heuristic)
    assert summary["status"] == "incomplete" and summary["issue_count"] == 1
    try:
        require_dataset_exact_identities(heuristic)
        raise AssertionError("heuristic lineage must be blocked")
    except DatasetPublicationError:
        pass

    exact = {
        "scenes": [{
            "scene_id": "scene",
            "source_scene_uid": "sha256:" + "c" * 64,
            "source_file_sha256": "c" * 64,
            "source_identity_strength": "exact",
        }]
    }
    assert require_dataset_exact_identities(exact)["status"] == "exact"


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all content identity tests passed")
