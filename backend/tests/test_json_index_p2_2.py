"""P2.2 contracts for cached summaries and stable server-side scene paging."""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from db import storage
from services.annotation_summary import compute_project_annotation_summary
from services.json_index import delta as delta_service
from services.json_index import project_revision as revision_service
from services.json_index import rebuild as rebuild_service
from services.json_index.project_revision import read_index_state
from services.json_index import queries
from services.json_index.queries import InvalidCursorError, get_scenes_page


@contextmanager
def temporary_index_data():
    previous_root = storage.DATA_DIR
    previous_flag = os.environ.get("GEOTILE_JSON_INDEX_V2")
    previous_delta = os.environ.get("GEOTILE_JSON_INDEX_V2_DELTA")
    root = pathlib.Path(tempfile.mkdtemp(prefix="geotile-json-index-p2-2-")) / "data"
    storage.DATA_DIR = root
    os.environ["GEOTILE_JSON_INDEX_V2"] = "1"
    os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = "1"
    storage._invalidate_project_root_cache()
    queries.clear_project_cache()
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
        storage._invalidate_project_root_cache()
        queries.clear_project_cache()
        shutil.rmtree(root.parent, ignore_errors=True)


def _annotation(scene_id: str, annotation_id: str, *, class_id: int, author: str) -> dict:
    return {
        "id": annotation_id,
        "source_annotation_id": annotation_id,
        "scene_id": scene_id,
        "class_id": class_id,
        "geometry_type": "bbox",
        "bbox": [1, 2, 3, 4],
        "annotation_source": "manual",
        "annotator_email": author,
    }


def seed_project() -> pathlib.Path:
    storage.save_json("p2", "project", {"id": "p2", "name": "Paging", "schema_version": 2})
    storage.save_json(
        "p2",
        "classes",
        [{"id": 1, "name": "vessel"}, {"id": 2, "name": "aircraft"}],
    )
    rows = [
        ("s3", "bravo.tif", "v2", [_annotation("s3", "a3", class_id=2, author="b@example.com")]),
        ("s2", "alpha.tif", "v1", [_annotation("s2", "a2", class_id=1, author="a@example.com")]),
        ("s1", "alpha.tif", "v1", [_annotation("s1", "a1", class_id=1, author="a@example.com")]),
        ("s4", "charlie.tif", None, []),
    ]
    for scene_id, filename, version, annotations in rows:
        storage.save_scene_json(
            "p2",
            scene_id,
            "scene",
            {
                "id": scene_id,
                "filename": filename,
                "annotation_count": len(annotations),
                "tile_count": len(annotations) * 10,
                "status": "cataloged",
                "review_status": "accepted" if scene_id == "s1" else "none",
            },
        )
        storage.save_scene_json(
            "p2",
            scene_id,
            "scene_manifest",
            {
                "schema_version": 5,
                "scene_id": scene_id,
                "filename": filename,
                "image": {"width": 100, "height": 50, "channels": 3, "dtype": "uint8"},
                "geospatial": {"has_geo": True, "crs": "EPSG:4326"},
            },
        )
        storage.save_scene_json("p2", scene_id, "annotations", annotations)
    return storage.project_paths("p2").root


def _summary_payload(value: dict) -> dict:
    ignored = {"schema_name", "schema_version", "generated_at", "built_at", "source_revision"}
    return {key: item for key, item in value.items() if key not in ignored}


def test_pages_are_stable_and_scene_id_breaks_equal_sort_values():
    with temporary_index_data():
        seed_project()
        first = get_scenes_page("p2", limit=2)
        second = get_scenes_page("p2", limit=2, cursor=first["next_cursor"])

        assert [item["id"] for item in first["scenes"]] == ["s1", "s2"]
        assert [item["id"] for item in second["scenes"]] == ["s3", "s4"]
        assert first["filtered_count"] == first["total_count"] == 4
        assert second["next_cursor"] is None
        assert first["scenes"][0]["scene_info"]["width"] == 100
        assert first["scenes"][0]["review_status"] == "accepted"


def test_cursor_is_bound_to_query_and_source_revision():
    with temporary_index_data():
        seed_project()
        first = get_scenes_page("p2", limit=1)

        with pytest.raises(InvalidCursorError, match="different filters"):
            get_scenes_page("p2", limit=1, cursor=first["next_cursor"], filter="alpha")

        storage.mutate_scene_json(
            "p2",
            "s1",
            "annotations",
            lambda current: [
                *current,
                _annotation("s1", "a-new", class_id=2, author="c@example.com"),
            ],
            default=[],
        )
        with pytest.raises(InvalidCursorError, match="project changed"):
            get_scenes_page("p2", limit=1, cursor=first["next_cursor"])


def test_server_filters_use_materialized_annotation_summary():
    with temporary_index_data():
        seed_project()

        by_author = get_scenes_page("p2", limit=20, author="A@EXAMPLE.COM")
        by_class = get_scenes_page("p2", limit=20, class_id=2)
        by_text = get_scenes_page("p2", limit=20, filter="BRAVO")

        assert [item["id"] for item in by_author["scenes"]] == ["s1", "s2"]
        assert [item["id"] for item in by_class["scenes"]] == ["s3"]
        assert [item["id"] for item in by_text["scenes"]] == ["s3"]


def test_warm_queries_parse_each_index_once_and_never_load_source_scenes(monkeypatch):
    with temporary_index_data():
        seed_project()
        get_scenes_page("p2", limit=2, author="a@example.com")
        queries.clear_project_cache("p2")

        reads: list[str] = []
        real_read = queries.read_json

        def counted_read(path, *args, **kwargs):
            reads.append(path.name)
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(queries, "read_json", counted_read)
        monkeypatch.setattr(
            storage,
            "load_scene_json",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("source scene scan")),
        )

        get_scenes_page("p2", limit=2, author="a@example.com")
        get_scenes_page("p2", limit=2, author="a@example.com")

        assert reads.count("scenes_index_v2.json") == 1
        assert reads.count("annotation_summary_v2.json") == 1


def test_compact_summary_omits_per_scene_payload(monkeypatch):
    with temporary_index_data():
        seed_project()
        from routers import annotation_workflow

        full = queries.get_annotation_summary("p2")
        monkeypatch.setattr(annotation_workflow, "get_annotation_summary", lambda _project_id: full)
        compact = annotation_workflow.get_project_annotation_summary("p2", detail="compact")

        assert compact["annotation_count"] == 3
        assert "per_scene" not in compact
        assert "missing_author_annotation_ids" not in compact
        assert compact["scenes_without_annotations"] == ["s4"]


def test_annotation_and_followup_scene_saves_publish_deltas_without_rebuild(monkeypatch):
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")
        initial_revision = revision_service.read_project_revision(root, "p2")["revision"]

        def reject_rebuild(*_args, **_kwargs):
            raise AssertionError("full rebuild on a provable single-scene delta")

        monkeypatch.setattr(rebuild_service, "build_project_projection", reject_rebuild)
        updated, _annotation_revision = storage.mutate_scene_json(
            "p2",
            "s1",
            "annotations",
            lambda current: [
                {**current[0], "class_id": 2, "annotator_email": "changed@example.com"},
                {
                    **_annotation("s1", "a-extra", class_id=1, author=""),
                    "annotation_source": "import",
                    "import_id": "import-1",
                    "source_package_id": "package-1",
                },
            ],
            default=[],
        )

        after_annotation = queries.get_annotation_summary("p2")
        assert read_index_state(root)["status"] == "ready"
        assert read_index_state(root)["delta_document"] == "annotations"
        assert after_annotation["annotation_count"] == 4
        assert after_annotation["missing_author_count"] == 1
        assert after_annotation["per_import"] == [
            {"import_id": "import-1", "annotation_count": 1}
        ]
        assert after_annotation["per_package"] == [
            {"package_id": "package-1", "annotation_count": 1}
        ]

        scene = storage.load_scene_json("p2", "s1", "scene", default={})
        scene["annotation_count"] = len(updated)
        scene["review_status"] = "needs_fix"
        storage.save_scene_json("p2", "s1", "scene", scene)

        index = queries.get_scenes_index("p2")
        indexed_scene = next(item for item in index["scenes"] if item["scene_id"] == "s1")
        assert indexed_scene["annotation_count"] == 2
        assert indexed_scene["review_status"] == "needs_fix"
        assert read_index_state(root)["status"] == "ready"
        assert read_index_state(root)["delta_document"] == "scene"
        assert revision_service.read_project_revision(root, "p2")["revision"] == initial_revision + 2

        expected = compute_project_annotation_summary("p2", use_cache=False)
        assert _summary_payload(queries.get_annotation_summary("p2")) == _summary_payload(expected)


def test_manifest_delta_updates_scene_index_and_annotation_identity_without_rebuild(monkeypatch):
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")

        def reject_rebuild(*_args, **_kwargs):
            raise AssertionError("full rebuild on a provable manifest delta")

        monkeypatch.setattr(rebuild_service, "build_project_projection", reject_rebuild)
        manifest = storage.load_scene_json("p2", "s2", "scene_manifest", default={})
        manifest["sensor"] = "PNEO"
        manifest["provider"] = "AIRBUS"
        manifest["source_scene_uid"] = "uid-after-delta"
        storage.save_scene_json("p2", "s2", "scene_manifest", manifest)

        index = queries.get_scenes_index("p2")
        scene = next(item for item in index["scenes"] if item["scene_id"] == "s2")
        summary = queries.get_annotation_summary("p2")
        scene_summary = next(item for item in summary["per_scene"] if item["scene_id"] == "s2")
        assert scene["sensor"] == "PNEO"
        assert scene["provider"] == "AIRBUS"
        assert scene_summary["source_scene_uid"] == "uid-after-delta"
        assert read_index_state(root)["status"] == "ready"
        assert read_index_state(root)["delta_document"] == "scene_manifest"


def test_stale_annotation_sidecar_forces_dirty_rebuild_instead_of_unsafe_delta():
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")
        path = root / "scenes" / "s1" / "annotations.json"
        externally_changed = storage._read_json_file(path, [])
        externally_changed.append(_annotation("s1", "external", class_id=2, author="x@example.com"))
        storage._write_json_file(path, externally_changed)

        storage.mutate_scene_json(
            "p2",
            "s1",
            "annotations",
            lambda current: [
                *current,
                _annotation("s1", "normal", class_id=1, author="y@example.com"),
            ],
            default=[],
        )

        assert read_index_state(root)["status"] == "dirty"
        assert queries.get_annotation_summary("p2")["annotation_count"] == 5
        assert read_index_state(root)["status"] == "ready"


def test_concurrent_scene_mutations_fall_back_without_losing_updates(monkeypatch):
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")
        barrier = threading.Barrier(2)
        real_begin = storage._begin_json_index_mutation

        def synchronized_begin(project_id, project_root, mutation):
            token = real_begin(project_id, project_root, mutation)
            if mutation == "annotations.mutate":
                barrier.wait(timeout=5)
            return token

        monkeypatch.setattr(storage, "_begin_json_index_mutation", synchronized_begin)

        def add(scene_id: str, annotation_id: str):
            return storage.mutate_scene_json(
                "p2",
                scene_id,
                "annotations",
                lambda current: [
                    *current,
                    _annotation(scene_id, annotation_id, class_id=2, author="parallel@example.com"),
                ],
                default=[],
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(add, "s1", "parallel-1")
            second = pool.submit(add, "s2", "parallel-2")
            first.result(timeout=10)
            second.result(timeout=10)

        assert read_index_state(root)["status"] == "dirty"
        summary = queries.get_annotation_summary("p2")
        assert summary["annotation_count"] == 5
        assert next(item for item in summary["per_author"] if item["annotator_email"] == "parallel@example.com")[
            "annotation_count"
        ] == 2
        assert read_index_state(root)["status"] == "ready"


@pytest.mark.parametrize(
    "failure_point",
    ["scene_summary", "scenes_index", "annotation_summary", "revision", "dirty_state", "ready_state"],
)
def test_delta_publish_crash_remains_dirty_and_recovers(monkeypatch, failure_point: str):
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")
        initial_revision = revision_service.read_project_revision(root, "p2")["revision"]
        real_delta_write = delta_service.write_json_atomic
        real_revision_write = revision_service.write_json_atomic
        failed = False

        def fault_delta_write(path, value):
            nonlocal failed
            target = None
            if path.name == "summary.json":
                target = "scene_summary"
            elif path.name == "scenes_index_v2.json":
                target = "scenes_index"
            elif path.name == "annotation_summary_v2.json":
                target = "annotation_summary"
            if target == failure_point and not failed:
                failed = True
                raise RuntimeError(f"simulated delta crash at {failure_point}")
            return real_delta_write(path, value)

        def fault_revision_write(path, value):
            nonlocal failed
            target = None
            if path.name == "project_revision.json":
                target = "revision"
            elif (
                path.name == "index_state.json"
                and value.get("status") == "dirty"
                and int(value.get("source_revision") or 0) > initial_revision
            ):
                target = "dirty_state"
            elif path.name == "index_state.json" and value.get("status") == "ready":
                target = "ready_state"
            if target == failure_point and not failed:
                failed = True
                raise RuntimeError(f"simulated delta crash at {failure_point}")
            return real_revision_write(path, value)

        monkeypatch.setattr(delta_service, "write_json_atomic", fault_delta_write)
        monkeypatch.setattr(revision_service, "write_json_atomic", fault_revision_write)
        with pytest.raises(RuntimeError, match="simulated delta crash"):
            storage.mutate_scene_json(
                "p2",
                "s1",
                "annotations",
                lambda current: [
                    *current,
                    _annotation("s1", "after-crash", class_id=2, author="z@example.com"),
                ],
                default=[],
            )

        assert failed
        assert read_index_state(root)["status"] == "dirty"
        assert queries.get_annotation_summary("p2")["annotation_count"] == 4
        assert read_index_state(root)["status"] == "ready"


def test_delta_kill_switch_preserves_p2_1_dirty_rebuild_path():
    with temporary_index_data():
        root = seed_project()
        queries.get_scenes_index("p2")
        os.environ["GEOTILE_JSON_INDEX_V2_DELTA"] = "0"
        storage.mutate_scene_json(
            "p2",
            "s1",
            "annotations",
            lambda current: [
                *current,
                _annotation("s1", "kill-switch", class_id=1, author="a@example.com"),
            ],
            default=[],
        )

        assert read_index_state(root)["status"] == "dirty"
        assert queries.get_annotation_summary("p2")["annotation_count"] == 4
