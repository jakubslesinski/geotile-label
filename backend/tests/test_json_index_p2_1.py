"""P2.1 contracts for the rebuildable JSON Index v2 foundation."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from db import storage
from services.json_index import project_revision as revision_service
from services.json_index import rebuild as rebuild_service
from services.json_index.project_revision import begin_project_mutation, read_index_state
from services.json_index.queries import get_annotation_summary, get_scenes_index


@contextmanager
def temporary_index_data(*, enabled: bool):
    previous_root = storage.DATA_DIR
    previous_flag = os.environ.get("GEOTILE_JSON_INDEX_V2")
    previous_delta = os.environ.get("GEOTILE_JSON_INDEX_V2_DELTA")
    previous_diagnostic = os.environ.get("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC")
    root = pathlib.Path(tempfile.mkdtemp(prefix="geotile-json-index-p2-1-")) / "data"
    storage.DATA_DIR = root
    os.environ["GEOTILE_JSON_INDEX_V2"] = "1" if enabled else "0"
    # P2.1 tests exercise the baseline dirty/rebuild protocol in isolation.
    os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = "0"
    os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)
    storage._invalidate_project_root_cache()
    try:
        yield root
    finally:
        storage.DATA_DIR = previous_root
        if previous_flag is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2"] = previous_flag
        if previous_delta is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2_DELTA", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = previous_delta
        if previous_diagnostic is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2_DIAGNOSTIC"] = previous_diagnostic
        storage._invalidate_project_root_cache()
        shutil.rmtree(root.parent, ignore_errors=True)


def seed_project() -> pathlib.Path:
    storage.save_json("p1", "project", {"id": "p1", "name": "Index test", "schema_version": 2})
    storage.save_json("p1", "classes", [{"id": 1, "name": "vessel"}])
    storage.save_scene_json(
        "p1",
        "s1",
        "scene",
        {"id": "s1", "filename": "scene-1.tif", "annotation_count": 1, "status": "ready"},
    )
    storage.save_scene_json(
        "p1",
        "s1",
        "scene_manifest",
        {
            "schema_version": 5,
            "scene_id": "s1",
            "filename": "scene-1.tif",
            "image": {"width": 1024, "height": 512, "channels": 3, "dtype": "uint8"},
            "geospatial": {"has_geo": True, "crs": "EPSG:4326", "bounds_wgs84": [1, 2, 3, 4]},
            "source_identity": {"status": "complete", "source_scene_uid": "uid-s1"},
        },
    )
    storage.save_scene_json(
        "p1",
        "s1",
        "annotations",
        [
            {
                "id": "a1",
                "source_annotation_id": "a1",
                "scene_id": "s1",
                "class_id": 1,
                "geometry_type": "bbox",
                "bbox": [1, 2, 3, 4],
                "annotation_source": "manual",
                "annotator_email": "operator@example.com",
            }
        ],
    )
    return storage.project_paths("p1").root


def test_disabled_flag_uses_legacy_fallback_without_creating_indexes():
    with temporary_index_data(enabled=False):
        root = seed_project()
        expected = {"schema_name": "legacy", "scene_count": 7, "scenes": []}
        calls = 0

        def fallback():
            nonlocal calls
            calls += 1
            return expected

        assert get_scenes_index("p1", legacy_fallback=fallback) is expected
        assert calls == 1
        assert not (root / "indexes").exists()
        assert not (root / "project_revision.json").exists()


def test_old_project_builds_revision_zero_once_and_matches_source_counts(monkeypatch):
    with temporary_index_data(enabled=False):
        root = seed_project()
        os.environ["GEOTILE_JSON_INDEX_V2"] = "1"
        real_build = rebuild_service.build_project_projection
        builds = 0

        def counted_build(project_id, source_revision):
            nonlocal builds
            builds += 1
            return real_build(project_id, source_revision)

        monkeypatch.setattr(rebuild_service, "build_project_projection", counted_build)

        index = get_scenes_index("p1")
        summary = get_annotation_summary("p1")

        assert index["schema_version"] == 2
        assert index["scene_count"] == 1
        assert index["scenes"][0]["scene_id"] == "s1"
        assert summary["schema_version"] == 2
        assert summary["scene_count"] == 1
        assert summary["annotation_count"] == 1
        assert summary["per_class"][0]["annotation_count"] == 1
        assert (root / "scenes" / "s1" / "summary.json").is_file()
        state = json.loads((root / "indexes" / "index_state.json").read_text(encoding="utf-8"))
        revision = json.loads((root / "project_revision.json").read_text(encoding="utf-8"))
        assert state["status"] == "ready"
        assert state["source_revision"] == revision["revision"] == 0
        assert builds == 1


def test_deleting_indexes_is_lossless_and_rebuilds_from_source_json():
    with temporary_index_data(enabled=True):
        root = seed_project()
        first = get_scenes_index("p1")
        source_before = (root / "scenes" / "s1" / "annotations.json").read_bytes()

        shutil.rmtree(root / "indexes")
        second = get_scenes_index("p1")

        assert second["scene_count"] == first["scene_count"] == 1
        assert {item["scene_id"] for item in second["scenes"]} == {"s1"}
        assert (root / "scenes" / "s1" / "annotations.json").read_bytes() == source_before
        assert get_annotation_summary("p1")["annotation_count"] == 1


def test_source_mutation_marks_dirty_and_lazy_query_repairs_revision():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        ready_revision = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]

        storage.mutate_scene_json(
            "p1",
            "s1",
            "annotations",
            lambda current: [
                *current,
                {
                    "id": "a2",
                    "source_annotation_id": "a2",
                    "scene_id": "s1",
                    "class_id": 1,
                    "geometry_type": "bbox",
                    "bbox": [5, 6, 7, 8],
                },
            ],
            default=[],
        )
        dirty = read_index_state(root)
        changed_revision = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]
        assert dirty["status"] == "dirty"
        assert changed_revision == ready_revision + 1

        summary = get_annotation_summary("p1")
        repaired = read_index_state(root)
        assert summary["annotation_count"] == 2
        assert repaired["status"] == "ready"
        assert repaired["source_revision"] == changed_revision


def test_interrupted_mutation_marker_cannot_remain_silently_ready():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")

        token = begin_project_mutation(root, "p1", "test.crash_after_dirty")
        dirty = read_index_state(root)
        assert token in dirty["pending_mutations"]
        assert dirty["status"] == "dirty"

        rebuilt = get_scenes_index("p1")
        ready = read_index_state(root)
        assert rebuilt["scene_count"] == 1
        assert ready["status"] == "ready"
        assert ready["pending_mutations"] == []


def test_crash_after_source_write_is_repaired_from_authoritative_json():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        revision_before = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]

        token = begin_project_mutation(root, "p1", "test.crash_after_source_write")
        annotations_path = root / "scenes" / "s1" / "annotations.json"
        annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
        annotations.append(
            {
                "id": "a2",
                "source_annotation_id": "a2",
                "scene_id": "s1",
                "class_id": 1,
                "geometry_type": "bbox",
                "bbox": [9, 10, 11, 12],
            }
        )
        storage._write_json_file(annotations_path, annotations)

        dirty = read_index_state(root)
        assert token in dirty["pending_mutations"]
        assert dirty["status"] == "dirty"
        assert get_annotation_summary("p1")["annotation_count"] == 2
        repaired = read_index_state(root)
        assert repaired["status"] == "ready"
        assert repaired["pending_mutations"] == []
        final_revision = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]
        assert final_revision >= revision_before
        assert repaired["source_revision"] == final_revision


def test_missing_materialized_index_is_detected_and_rebuilt():
    with temporary_index_data(enabled=True):
        root = seed_project()
        expected = get_scenes_index("p1")
        (root / "indexes" / "scenes_index_v2.json").unlink()

        rebuilt = get_scenes_index("p1")

        assert rebuilt["scene_count"] == expected["scene_count"] == 1
        assert (root / "indexes" / "scenes_index_v2.json").is_file()
        assert read_index_state(root)["status"] == "ready"


def test_diagnostic_mode_detects_legacy_scene_parity_mismatch():
    with temporary_index_data(enabled=True):
        seed_project()
        os.environ["GEOTILE_JSON_INDEX_V2_DIAGNOSTIC"] = "1"

        try:
            get_scenes_index(
                "p1",
                legacy_fallback=lambda: {
                    "scene_count": 1,
                    "scenes": [{"scene_id": "different-scene"}],
                },
            )
        except RuntimeError as exc:
            assert "diagnostic mismatch" in str(exc)
        else:
            raise AssertionError("Diagnostic mode accepted divergent scene indexes")


def test_disable_mutate_reenable_cannot_reuse_stale_adopted_index():
    with temporary_index_data(enabled=True):
        root = seed_project()
        assert get_annotation_summary("p1")["annotation_count"] == 1
        ready_revision = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]

        os.environ["GEOTILE_JSON_INDEX_V2"] = "0"
        storage.mutate_scene_json(
            "p1",
            "s1",
            "annotations",
            lambda current: [
                *current,
                {
                    "id": "a2",
                    "source_annotation_id": "a2",
                    "scene_id": "s1",
                    "class_id": 1,
                    "geometry_type": "bbox",
                    "bbox": [13, 14, 15, 16],
                },
            ],
            default=[],
        )
        assert read_index_state(root)["status"] == "dirty"
        changed_revision = json.loads(
            (root / "project_revision.json").read_text(encoding="utf-8")
        )["revision"]
        assert changed_revision == ready_revision + 1

        os.environ["GEOTILE_JSON_INDEX_V2"] = "1"
        assert get_annotation_summary("p1")["annotation_count"] == 2
        repaired = read_index_state(root)
        assert repaired["status"] == "ready"
        assert repaired["source_revision"] == changed_revision


@pytest.mark.parametrize(
    "failure_point",
    ["scene_summary", "scenes_index", "annotation_summary", "ready_state"],
)
def test_publish_crash_matrix_remains_dirty_and_recovers(monkeypatch, failure_point: str):
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        storage.mutate_scene_json(
            "p1",
            "s1",
            "annotations",
            lambda current: [
                *current,
                {
                    "id": "a2",
                    "source_annotation_id": "a2",
                    "scene_id": "s1",
                    "class_id": 1,
                    "geometry_type": "bbox",
                    "bbox": [17, 18, 19, 20],
                },
            ],
            default=[],
        )
        paths = rebuild_service.JsonIndexPaths(root)
        real_write = rebuild_service.write_json_atomic
        failed = False

        def faulting_write(path, value):
            nonlocal failed
            matches = {
                "scene_summary": path == paths.scene_summary("s1"),
                "scenes_index": path == paths.scenes,
                "annotation_summary": path == paths.annotations,
                "ready_state": path == paths.state and value.get("status") == "ready",
            }[failure_point]
            if matches and not failed:
                failed = True
                raise RuntimeError(f"simulated crash at {failure_point}")
            return real_write(path, value)

        monkeypatch.setattr(rebuild_service, "write_json_atomic", faulting_write)
        with pytest.raises(RuntimeError, match="simulated crash"):
            rebuild_service.rebuild_project_indexes("p1", force=True)

        assert failed
        assert read_index_state(root)["status"] == "dirty"
        assert get_annotation_summary("p1")["annotation_count"] == 2
        assert read_index_state(root)["status"] == "ready"


def test_projector_failure_is_recorded_dirty_and_next_read_recovers(monkeypatch):
    with temporary_index_data(enabled=True):
        root = seed_project()
        real_build = rebuild_service.build_project_projection
        failed = False

        def fail_once(project_id, source_revision):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("simulated projector crash")
            return real_build(project_id, source_revision)

        monkeypatch.setattr(rebuild_service, "build_project_projection", fail_once)
        with pytest.raises(RuntimeError, match="projector crash"):
            rebuild_service.rebuild_project_indexes("p1", force=True)
        dirty = read_index_state(root)
        assert dirty["status"] == "dirty"
        assert "simulated projector crash" in dirty["last_error"]

        assert get_scenes_index("p1")["scene_count"] == 1
        assert read_index_state(root)["status"] == "ready"


def test_crash_after_revision_write_before_dirty_refresh_is_recoverable(monkeypatch):
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        initial_revision = revision_service.read_project_revision(root, "p1")["revision"]
        real_write = revision_service.write_json_atomic
        failed = False

        def faulting_write(path, value):
            nonlocal failed
            if (
                path.name == "index_state.json"
                and value.get("status") == "dirty"
                and int(value.get("source_revision") or 0) > initial_revision
                and not failed
            ):
                failed = True
                raise RuntimeError("simulated crash after revision")
            return real_write(path, value)

        monkeypatch.setattr(revision_service, "write_json_atomic", faulting_write)
        with pytest.raises(RuntimeError, match="after revision"):
            storage.mutate_scene_json(
                "p1",
                "s1",
                "annotations",
                lambda current: [
                    *current,
                    {
                        "id": "a2",
                        "source_annotation_id": "a2",
                        "scene_id": "s1",
                        "class_id": 1,
                        "geometry_type": "bbox",
                        "bbox": [21, 22, 23, 24],
                    },
                ],
                default=[],
            )

        assert failed
        assert revision_service.read_project_revision(root, "p1")["revision"] == initial_revision + 1
        assert read_index_state(root)["status"] == "dirty"
        assert get_annotation_summary("p1")["annotation_count"] == 2
        assert read_index_state(root)["status"] == "ready"


def test_scene_delete_crash_before_rename_keeps_source_and_rebuilds(monkeypatch):
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        real_replace = storage.os.replace
        failed = False

        def fail_scene_rename(source, destination):
            nonlocal failed
            if pathlib.Path(source).name == "s1" and ".trash" in pathlib.Path(destination).parts:
                failed = True
                raise RuntimeError("simulated crash before scene rename")
            return real_replace(source, destination)

        monkeypatch.setattr(storage.os, "replace", fail_scene_rename)
        with pytest.raises(RuntimeError, match="before scene rename"):
            storage.delete_scene_data("p1", "s1")

        assert failed
        assert (root / "scenes" / "s1" / "scene.json").is_file()
        assert read_index_state(root)["status"] == "dirty"
        assert get_scenes_index("p1")["scene_count"] == 1


def test_scene_delete_crash_after_rename_is_recoverable_from_tombstone(monkeypatch):
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        real_complete = storage._complete_json_index_mutation
        failed = False

        def fail_once(project_id, project_root, mutation, token):
            nonlocal failed
            if mutation == "scene.delete" and not failed:
                failed = True
                raise RuntimeError("simulated crash after scene rename")
            return real_complete(project_id, project_root, mutation, token)

        monkeypatch.setattr(storage, "_complete_json_index_mutation", fail_once)
        with pytest.raises(RuntimeError, match="after scene rename"):
            storage.delete_scene_data("p1", "s1")

        assert failed
        assert not (root / "scenes" / "s1").exists()
        assert any((root / ".trash" / "scenes").iterdir())
        assert read_index_state(root)["status"] == "dirty"
        assert get_scenes_index("p1")["scene_count"] == 0
        assert get_annotation_summary("p1")["annotation_count"] == 0


def test_mutation_during_rebuild_forces_retry_before_ready(monkeypatch):
    with temporary_index_data(enabled=True):
        root = seed_project()
        real_build = rebuild_service.build_project_projection
        calls = 0

        def mutate_after_first_projection(project_id, source_revision):
            nonlocal calls
            calls += 1
            projection = real_build(project_id, source_revision)
            if calls == 1:
                storage.mutate_scene_json(
                    "p1",
                    "s1",
                    "annotations",
                    lambda current: [
                        *current,
                        {
                            "id": "a2",
                            "source_annotation_id": "a2",
                            "scene_id": "s1",
                            "class_id": 1,
                            "geometry_type": "bbox",
                            "bbox": [25, 26, 27, 28],
                        },
                    ],
                    default=[],
                )
            return projection

        monkeypatch.setattr(
            rebuild_service,
            "build_project_projection",
            mutate_after_first_projection,
        )
        rebuild_service.rebuild_project_indexes("p1", force=True)

        assert calls == 2
        assert get_annotation_summary("p1")["annotation_count"] == 2
        state = read_index_state(root)
        revision = revision_service.read_project_revision(root, "p1")
        assert state["status"] == "ready"
        assert state["source_revision"] == revision["revision"]


def test_concurrent_lazy_reads_share_one_full_rebuild(monkeypatch):
    with temporary_index_data(enabled=True):
        seed_project()
        real_build = rebuild_service.build_project_projection
        calls = 0
        calls_lock = threading.Lock()

        def counted_build(project_id, source_revision):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return real_build(project_id, source_revision)

        monkeypatch.setattr(rebuild_service, "build_project_projection", counted_build)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _item: get_scenes_index("p1"), range(4)))

        assert calls == 1
        assert all(item["scene_count"] == 1 for item in results)


def test_tracked_direct_batch_failure_leaves_dirty_and_repairs():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        annotations_path = root / "scenes" / "s1" / "annotations.json"
        annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
        annotations.append(
            {
                "id": "a2",
                "source_annotation_id": "a2",
                "scene_id": "s1",
                "class_id": 1,
                "geometry_type": "bbox",
                "bbox": [29, 30, 31, 32],
                "annotation_source": "import",
            }
        )

        with pytest.raises(RuntimeError, match="batch crash"):
            with storage.track_project_mutation("p1", "test.direct_batch"):
                storage._write_json_file(annotations_path, annotations)
                raise RuntimeError("simulated batch crash")

        assert read_index_state(root)["status"] == "dirty"
        assert get_annotation_summary("p1")["annotation_count"] == 2
        assert read_index_state(root)["status"] == "ready"


def test_successful_scene_delete_advances_revision_and_removes_tombstone():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        revision_before = revision_service.read_project_revision(root, "p1")["revision"]

        assert storage.delete_scene_data("p1", "s1") is True

        assert not (root / "scenes" / "s1").exists()
        tombstones = root / ".trash" / "scenes"
        assert not tombstones.exists() or not any(tombstones.iterdir())
        assert revision_service.read_project_revision(root, "p1")["revision"] == revision_before + 1
        assert read_index_state(root)["status"] == "dirty"
        assert get_scenes_index("p1")["scene_count"] == 0


def test_materialized_document_with_stale_revision_is_rebuilt():
    with temporary_index_data(enabled=True):
        root = seed_project()
        get_scenes_index("p1")
        scenes_path = root / "indexes" / "scenes_index_v2.json"
        stale = json.loads(scenes_path.read_text(encoding="utf-8"))
        stale["source_revision"] = max(0, int(stale["source_revision"]) - 1)
        storage._write_json_file(scenes_path, stale)

        repaired = get_scenes_index("p1")
        revision = revision_service.read_project_revision(root, "p1")["revision"]
        assert repaired["source_revision"] == revision
        assert read_index_state(root)["status"] == "ready"
