"""P1.4 regression tests: bounded artifacts, resume and ANN quality."""

from __future__ import annotations

import gc
import json
from pathlib import Path

import numpy as np
import pytest

from services import embedding_index as artifacts
from services.embedding_analysis import near_duplicate_pairs


class _FakeEmbedder:
    checkpoint_name = "fixture-dino.pth"

    def __init__(self) -> None:
        self.calls = 0

    def embed_chips(self, chips):
        self.calls += 1
        values = np.asarray([np.asarray(chip).reshape(-1)[:4] for chip in chips], dtype=np.float32)
        return values / (np.linalg.norm(values, axis=1, keepdims=True) + 1e-9)


def test_chip_iterator_opens_each_scene_once_and_keeps_batches_bounded(monkeypatch):
    monkeypatch.setattr(artifacts, "project_paths", lambda _project_id: object())
    monkeypatch.setattr(artifacts, "list_scene_ids", lambda *_args, **_kwargs: ["b", "a"])

    def load_scene(_project_id, scene_id, name, default=None, **_kwargs):
        if name == "scene":
            return {"scene_info": {"width": 64, "height": 64}}
        if name == "annotations":
            return [
                {
                    "id": f"{scene_id}-{row}",
                    "class_id": 1,
                    "bbox": [row * 8, 0, row * 8 + 8, 8],
                }
                for row in range(3)
            ]
        return default

    monkeypatch.setattr(artifacts, "load_scene_json", load_scene)
    import services.scene_raster_resolver as resolver

    monkeypatch.setattr(resolver, "resolve_scene_raster", lambda _project, scene: Path(f"{scene}.tif"))

    opened = []
    reads = []

    class FakeRaster:
        count = 3

        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *, window, out_shape):
            reads.append((self.path, window))
            return np.zeros(out_shape, dtype=np.uint8)

    import rasterio

    def open_raster(path):
        opened.append(str(path))
        return FakeRaster(str(path))

    monkeypatch.setattr(rasterio, "open", open_raster)
    batches = list(
        artifacts.iter_object_chip_batches(
            "project", chip=16, min_size_px=2, batch_size=2
        )
    )
    assert opened == ["a.tif", "b.tif"]
    assert len(reads) == 6
    assert all(len(batch.chips) <= 2 for batch in batches)
    assert batches[-1].processed_candidates == 6

    opened.clear()
    reads.clear()
    resumed = list(
        artifacts.iter_object_chip_batches(
            "project", chip=16, min_size_px=2, batch_size=2, skip_candidates=1
        )
    )
    assert resumed[0].processed_candidates == 3
    assert [obj["annotation_id"] for obj in resumed[0].objects] == ["a-1", "a-2"]
    assert resumed[-1].processed_candidates == 6


def _batch(cursor: int, start: int) -> artifacts.ObjectChipBatch:
    objects = [
        {
            "scene_id": "scene-a",
            "annotation_id": f"ann-{row}",
            "source_annotation_id": f"source-{row}",
            "class_id": row % 2,
            "bbox": [float(row), 0.0, float(row + 1), 1.0],
        }
        for row in range(start, start + 2)
    ]
    chips = [
        np.asarray([row + 1, row + 2, row + 3, row + 4], dtype=np.float32)
        for row in range(start, start + 2)
    ]
    return artifacts.ObjectChipBatch(cursor, objects, chips)


def test_streaming_index_rolls_back_uncommitted_batch_and_resumes(tmp_path, monkeypatch):
    batches = [_batch(2, 0), _batch(4, 2)]

    def iter_batches(_project_id, *, skip_candidates=0, **_kwargs):
        yield from (batch for batch in batches if batch.processed_candidates > skip_candidates)

    monkeypatch.setattr(artifacts, "iter_object_chip_batches", iter_batches)
    monkeypatch.setattr(artifacts, "source_signature", lambda _project_id: "source-v1")
    monkeypatch.setattr(
        artifacts,
        "load_scene_independent_classes",
        lambda _project_id: [{"id": 0, "name": "zero"}, {"id": 1, "name": "one"}],
    )

    original_write = artifacts._write_object_part
    writes = 0

    def fail_second_part(path, objects, row_start):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("simulated interruption")
        original_write(path, objects, row_start)

    monkeypatch.setattr(artifacts, "_write_object_part", fail_second_part)
    embedder = _FakeEmbedder()
    with pytest.raises(RuntimeError, match="simulated interruption"):
        artifacts.build_streaming_object_index("project", embedder, tmp_path)

    checkpoint = json.loads(
        (tmp_path / artifacts.CHECKPOINT_FILE).read_text(encoding="utf-8")
    )
    assert checkpoint["processed_candidates"] == 2
    assert checkpoint["n_objects"] == 2
    # Batch 2 was appended before the simulated Parquet failure.
    assert (tmp_path / artifacts.EMBEDDINGS_FILE).stat().st_size == 4 * 4 * 4

    monkeypatch.setattr(artifacts, "_write_object_part", original_write)
    result = artifacts.build_streaming_object_index("project", embedder, tmp_path)
    assert result["embeddings"].shape == (4, 4)
    assert [obj["annotation_id"] for obj in result["objects"]] == [
        "ann-0",
        "ann-1",
        "ann-2",
        "ann-3",
    ]
    assert (tmp_path / artifacts.EMBEDDINGS_FILE).stat().st_size == 4 * 4 * 4
    assert len(list((tmp_path / artifacts.OBJECT_PARTS_DIR).glob("part-*.parquet"))) == 2

    calls_after_completion = embedder.calls
    reused = artifacts.build_streaming_object_index("project", embedder, tmp_path)
    assert reused["embeddings"].shape == (4, 4)
    assert embedder.calls == calls_after_completion


def test_source_geometry_candidates_precede_exact_vector_search():
    vectors = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    objects = [
        {
            "annotation_id": f"a-{row}",
            "source_annotation_id": f"source-{row}",
            "scene_id": "scene",
            "class_id": row,
            "bbox": [0.0, 0.0, 2.0, 2.0] if row < 2 else [4.0, 4.0, 6.0, 6.0],
        }
        for row in range(3)
    ]
    pairs = near_duplicate_pairs(
        {"embeddings": vectors, "objects": objects, "class_names": {}},
        threshold=0.99,
        max_pairs=1,
        exact_limit=10,
    )
    assert len(pairs) == 1
    assert pairs[0]["candidate_source"] == "source_geometry"


def test_usearch_recall_at_k_and_persisted_query(tmp_path):
    pytest.importorskip("usearch")
    rng = np.random.default_rng(42)
    count, dimensions, k = 5000, 96, 10
    vectors = rng.normal(size=(count, dimensions)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9

    info = artifacts.build_ann_index(tmp_path, vectors, exact_limit=0, batch_size=1000)
    assert info["backend"] == "usearch"
    index = artifacts.restore_ann_index(tmp_path)
    recalls = []
    for query in rng.choice(count, 100, replace=False):
        similarities = vectors @ vectors[query]
        exact = set(np.argsort(similarities)[::-1][1 : k + 1].tolist())
        approximate = [
            int(value)
            for value in np.asarray(index.search(vectors[query], k + 1).keys)
            if int(value) != int(query)
        ][:k]
        recalls.append(len(exact.intersection(approximate)) / k)
    assert float(np.mean(recalls)) >= 0.98
    del index
    gc.collect()  # release the memory-mapped USearch file on Windows


def test_large_input_requires_ann_instead_of_quadratic_fallback(monkeypatch):
    vectors = np.eye(3, dtype=np.float32)
    objects = [
        {
            "annotation_id": str(row),
            "source_annotation_id": str(row),
            "scene_id": "scene",
            "class_id": 0,
            "bbox": [float(row * 3), 0.0, float(row * 3 + 1), 1.0],
        }
        for row in range(3)
    ]
    with pytest.raises(RuntimeError, match="ANN index required"):
        near_duplicate_pairs(
            {"embeddings": vectors, "objects": objects, "class_names": {}},
            exact_limit=2,
            large_mode="ann",
        )


def test_lsh_near_duplicate_recall_is_at_least_098():
    rng = np.random.default_rng(9)
    count, dimensions, duplicate_count = 2000, 64, 50
    vectors = rng.normal(size=(count, dimensions)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    expected: set[frozenset[str]] = set()
    for offset in range(duplicate_count):
        source = offset
        target = count - duplicate_count + offset
        noise = rng.normal(size=dimensions).astype(np.float32)
        noise -= float(noise @ vectors[source]) * vectors[source]
        noise /= np.linalg.norm(noise) + 1e-9
        vectors[target] = 0.98 * vectors[source] + np.sqrt(1.0 - 0.98**2) * noise
        vectors[target] /= np.linalg.norm(vectors[target]) + 1e-9
        expected.add(frozenset((str(source), str(target))))
    objects = [
        {
            "annotation_id": str(row),
            "source_annotation_id": str(row),
            "scene_id": "scene",
            "class_id": 0,
            "bbox": [float(row * 3), 0.0, float(row * 3 + 1), 1.0],
        }
        for row in range(count)
    ]
    pairs = near_duplicate_pairs(
        {"embeddings": vectors, "objects": objects, "class_names": {}},
        threshold=0.97,
        max_pairs=duplicate_count * 2,
        exact_limit=1000,
        large_mode="lsh",
    )
    found = {
        frozenset((str(pair["a"]["annotation_id"]), str(pair["b"]["annotation_id"])))
        for pair in pairs
    }
    assert len(found.intersection(expected)) / len(expected) >= 0.98
