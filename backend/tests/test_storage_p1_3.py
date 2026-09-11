"""P1.3 regression tests for the JSON storage hot path."""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from db import storage


@contextmanager
def temporary_data_dir():
    previous = storage.DATA_DIR
    previous_read_only = os.environ.get("GEOTILE_BENCHMARK_READ_ONLY")
    root = pathlib.Path(tempfile.mkdtemp(prefix="geotile-storage-p1-3-")) / "data"
    storage.DATA_DIR = root
    os.environ.pop("GEOTILE_BENCHMARK_READ_ONLY", None)
    storage._invalidate_project_root_cache()
    try:
        yield root
    finally:
        storage.DATA_DIR = previous
        if previous_read_only is None:
            os.environ.pop("GEOTILE_BENCHMARK_READ_ONLY", None)
        else:
            os.environ["GEOTILE_BENCHMARK_READ_ONLY"] = previous_read_only
        storage._invalidate_project_root_cache()


def test_read_paths_do_not_create_missing_directories():
    with temporary_data_dir() as root:
        assert storage.load_json("missing", "project", default={}) == {}
        assert storage.load_scene_json("missing", "scene", "annotations", default=[]) == []
        assert storage.list_scene_ids("missing") == []
        assert not root.exists()


def test_project_root_resolution_reads_unchanged_index_once(monkeypatch):
    with temporary_data_dir() as root:
        project_root = root / "external" / "project"
        project_root.mkdir(parents=True)
        storage.save_projects_index({
            "projects": [{"project_id": "p1", "project_root": str(project_root)}]
        })

        original = storage._read_json_file
        index_path = storage.projects_index_path().resolve(strict=False)
        reads = 0

        def counted(path, default=None):
            nonlocal reads
            if pathlib.Path(path).resolve(strict=False) == index_path:
                reads += 1
            return original(path, default)

        monkeypatch.setattr(storage, "_read_json_file", counted)
        storage._invalidate_project_root_cache()
        assert storage.resolve_project_dir("p1") == project_root
        assert storage.resolve_project_dir("p1") == project_root
        assert storage.project_paths("p1").root == project_root
        assert reads == 1


def test_annotation_schema_is_migrated_only_once(monkeypatch):
    with temporary_data_dir() as root:
        scene_root = root / "projects" / "p1" / "scenes" / "s1"
        scene_root.mkdir(parents=True)
        annotation_path = scene_root / "annotations.json"
        annotation_path.write_text(
            json.dumps([{"class_id": 2, "bbox": [1, 2, 3, 4]}]),
            encoding="utf-8",
        )

        original = storage._normalize_annotations
        calls = 0

        def counted(scene_id, annotations):
            nonlocal calls
            calls += 1
            return original(scene_id, annotations)

        monkeypatch.setattr(storage, "_normalize_annotations", counted)
        first = storage.load_scene_json("p1", "s1", "annotations", default=[])
        second = storage.load_scene_json("p1", "s1", "annotations", default=[])

        assert first == second
        assert first[0]["id"] and first[0]["scene_id"] == "s1"
        assert calls == 1
        meta = json.loads((scene_root / "annotations.meta.json").read_text(encoding="utf-8"))
        assert meta["annotation_schema_version"] == storage.ANNOTATION_SCHEMA_VERSION
        assert meta["revision"] >= 1


def test_expected_revision_rejects_stale_annotation_write():
    with temporary_data_dir():
        revision = storage.save_scene_json("p1", "s1", "annotations", [])
        assert revision == 1
        revision = storage.save_scene_json(
            "p1", "s1", "annotations", [], expected_revision=revision
        )
        assert revision == 2
        with pytest.raises(storage.StorageConflictError):
            storage.save_scene_json(
                "p1", "s1", "annotations", [], expected_revision=1
            )


def test_concurrent_annotation_mutations_do_not_lose_updates():
    with temporary_data_dir():
        storage.save_scene_json("p1", "s1", "annotations", [])

        def append(index: int) -> int:
            _items, revision = storage.mutate_scene_json(
                "p1",
                "s1",
                "annotations",
                lambda current: [
                    *current,
                    {
                        "id": f"ann-{index}",
                        "source_annotation_id": f"ann-{index}",
                        "scene_id": "s1",
                        "class_id": 1,
                        "geometry_type": "bbox",
                        "bbox": [index, index, 1, 1],
                    },
                ],
                default=[],
            )
            return revision

        with ThreadPoolExecutor(max_workers=8) as pool:
            revisions = list(pool.map(append, range(40)))

        annotations, revision = storage.read_scene_json_with_revision(
            "p1", "s1", "annotations", default=[]
        )
        assert len(annotations) == 40
        assert {item["id"] for item in annotations} == {f"ann-{index}" for index in range(40)}
        assert revision == 41
        assert sorted(revisions) == list(range(2, 42))
