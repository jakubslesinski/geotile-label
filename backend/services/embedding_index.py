"""Scalable, resumable object-embedding index artifacts (P1.4).

The extraction cursor counts *eligible annotation candidates*, not only successfully
read chips.  That distinction makes a retry deterministic even when a raster window is
unreadable.  A committed checkpoint is the source of truth: on resume any bytes/Parquet
parts written after the last checkpoint are rolled back before work continues.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import numpy as np

from db.storage import list_scene_ids, load_scene_json, project_paths
from services.jobs.store import write_json_atomic


ARTIFACT_SCHEMA_VERSION = 2
DEFAULT_EXTRACTION_BATCH = 256
DEFAULT_ANN_BATCH = 8192
DEFAULT_EXACT_LIMIT = 10_000
DEFAULT_ANN_CANDIDATES = 64
ANN_RECALL_TARGET = 0.98
ANN_RECALL_QUERIES = 20
ANN_RECALL_K = 10
ANN_MAX_EXPANSION_SEARCH = 16_384

EMBEDDINGS_FILE = "embeddings.f32"
MANIFEST_FILE = "embedding_index.json"
CHECKPOINT_FILE = "embedding_index.checkpoint.json"
OBJECT_PARTS_DIR = "objects.parquet.parts"
OBJECTS_MANIFEST_FILE = "objects.manifest.json"
LOOKUP_FILE = "annotation_rows.json"
ANN_FILE = "objects.usearch"
ANN_STATE_FILE = "objects.usearch.state.json"


@dataclass(frozen=True)
class ObjectChipBatch:
    """One bounded extraction unit and the durable input cursor after that unit."""

    processed_candidates: int
    objects: list[dict[str, Any]]
    chips: list[np.ndarray]


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return -1, -1


def source_signature(project_id: str) -> str:
    """Cheap revision fingerprint used to reject an unsafe stale resume.

    Annotation and scene JSON signatures cover geometry, class and display settings.
    The raster path/stat is included where it resolves successfully, so replacing a
    source image also invalidates the partial index.
    """

    from services.scene_raster_resolver import resolve_scene_raster

    paths = project_paths(project_id)
    rows: list[tuple[Any, ...]] = []
    for scene_id in sorted(list_scene_ids(project_id, paths=paths)):
        annotation_path = paths.scene_json(scene_id, "annotations")
        scene_path = paths.scene_json(scene_id, "scene")
        raster_value = ""
        raster_state = (-1, -1)
        try:
            raster = Path(resolve_scene_raster(project_id, scene_id))
            raster_value = str(raster.resolve(strict=False))
            raster_state = _file_signature(raster)
        except Exception:  # noqa: BLE001 - an unreadable scene is a stable skipped input
            pass
        rows.append(
            (
                scene_id,
                *_file_signature(annotation_path),
                *_file_signature(scene_path),
                raster_value,
                *raster_state,
            )
        )
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _eligible_object(
    scene_id: str,
    annotation: dict[str, Any],
    width: int,
    height: int,
    min_size_px: int,
) -> tuple[dict[str, Any], tuple[int, int, int, int]] | None:
    if annotation.get("is_negative") or not annotation.get("bbox"):
        return None
    if annotation.get("class_id") is None:
        return None
    try:
        raw_bbox = [float(value) for value in annotation["bbox"]]
        if len(raw_bbox) != 4:
            return None
        x0, y0, x1, y1 = (int(round(value)) for value in raw_bbox)
    except (TypeError, ValueError):
        return None
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 - x0 < min_size_px or y1 - y0 < min_size_px:
        return None
    return (
        {
            "scene_id": scene_id,
            "annotation_id": annotation.get("id"),
            "source_annotation_id": annotation.get("source_annotation_id") or annotation.get("id"),
            "class_id": int(annotation["class_id"]),
            "bbox": raw_bbox,
        },
        (x0, y0, x1, y1),
    )


def _read_open_raster_window(
    source: Any,
    scene_info: dict[str, Any],
    bounds: tuple[int, int, int, int],
    chip: int,
) -> np.ndarray:
    """Equivalent to ``scenes._read_geotiff_window`` with an already-open dataset."""

    from rasterio.windows import Window
    from routers.scenes import ensure_rgb_uint8

    x0, y0, x1, y1 = bounds
    data = source.read(
        window=Window(col_off=x0, row_off=y0, width=x1 - x0, height=y1 - y0),
        out_shape=(source.count, chip, chip),
    )
    array = np.transpose(data, (1, 2, 0))
    return ensure_rgb_uint8(
        array,
        scene_info.get("display_min"),
        scene_info.get("display_max"),
        scene_info.get("display_mode"),
    )


def iter_object_chip_batches(
    project_id: str,
    *,
    chip: int = 64,
    min_size_px: int = 6,
    batch_size: int = DEFAULT_EXTRACTION_BATCH,
    skip_candidates: int = 0,
) -> Iterator[ObjectChipBatch]:
    """Yield bounded chip batches, opening every source raster at most once per pass."""

    import rasterio

    from services.scene_raster_resolver import resolve_scene_raster

    batch_size = max(1, int(batch_size))
    skip_candidates = max(0, int(skip_candidates))
    processed = 0
    paths = project_paths(project_id)
    for scene_id in sorted(list_scene_ids(project_id, paths=paths)):
        annotations = load_scene_json(
            project_id, scene_id, "annotations", default=[], paths=paths
        )
        if not annotations:
            continue
        scene = load_scene_json(project_id, scene_id, "scene", default={}, paths=paths)
        scene_info = scene.get("scene_info") or {}
        width, height = int(scene_info.get("width") or 0), int(scene_info.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        candidates = [
            candidate
            for annotation in annotations
            if (candidate := _eligible_object(scene_id, annotation, width, height, min_size_px))
            is not None
        ]
        if not candidates:
            continue
        if processed + len(candidates) <= skip_candidates:
            processed += len(candidates)
            continue

        start = max(0, skip_candidates - processed)
        processed += start
        try:
            raster = resolve_scene_raster(project_id, scene_id)
            source_context = rasterio.open(raster)
        except Exception:  # noqa: BLE001 - persist the skipped cursor and continue
            processed += len(candidates) - start
            if processed > skip_candidates:
                yield ObjectChipBatch(processed, [], [])
            continue

        with source_context as source:
            for offset in range(start, len(candidates), batch_size):
                objects: list[dict[str, Any]] = []
                chips: list[np.ndarray] = []
                chunk = candidates[offset : offset + batch_size]
                for obj, bounds in chunk:
                    try:
                        array = _read_open_raster_window(source, scene_info, bounds, chip)
                    except Exception:  # noqa: BLE001 - one corrupt window must not abort a run
                        continue
                    objects.append(obj)
                    chips.append(array)
                processed += len(chunk)
                yield ObjectChipBatch(processed, objects, chips)


def _artifact_config(
    *, backbone: str, chip: int, min_size_px: int, source: str
) -> dict[str, Any]:
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "backbone": backbone,
        "chip": int(chip),
        "min_size_px": int(min_size_px),
        "source_signature": source,
    }


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _reset_partial_artifacts(run_dir: Path) -> None:
    for name in (
        EMBEDDINGS_FILE,
        MANIFEST_FILE,
        CHECKPOINT_FILE,
        OBJECTS_MANIFEST_FILE,
        LOOKUP_FILE,
        ANN_FILE,
        ANN_STATE_FILE,
        # P1.4 replaces these legacy all-in-memory artifacts.
        "embeddings.npy",
        "objects.json",
    ):
        (run_dir / name).unlink(missing_ok=True)
    parts = run_dir / OBJECT_PARTS_DIR
    if parts.is_dir():
        shutil.rmtree(parts)


def _write_object_part(path: Path, objects: Sequence[dict[str, Any]], row_start: int) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [{"row_index": row_start + index, **obj} for index, obj in enumerate(objects)]
    schema = pa.schema(
        [
            ("row_index", pa.int64()),
            ("scene_id", pa.string()),
            ("annotation_id", pa.string()),
            ("source_annotation_id", pa.string()),
            ("class_id", pa.int64()),
            ("bbox", pa.list_(pa.float64(), 4)),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        pq.write_table(table, temp, compression="zstd")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _rollback_to_checkpoint(run_dir: Path, checkpoint: dict[str, Any]) -> None:
    dimension = int(checkpoint.get("dimension") or 0)
    count = int(checkpoint.get("n_objects") or 0)
    expected_bytes = count * dimension * np.dtype("<f4").itemsize
    embedding_path = run_dir / EMBEDDINGS_FILE
    embedding_path.parent.mkdir(parents=True, exist_ok=True)
    with embedding_path.open("a+b") as handle:
        handle.truncate(expected_bytes)
        handle.flush()
        os.fsync(handle.fileno())

    committed_parts = int(checkpoint.get("part_count") or 0)
    parts_dir = run_dir / OBJECT_PARTS_DIR
    if parts_dir.is_dir():
        for temp in parts_dir.glob(".part-*.tmp"):
            temp.unlink(missing_ok=True)
        for part in parts_dir.glob("part-*.parquet"):
            try:
                number = int(part.stem.split("-")[-1])
            except ValueError:
                number = committed_parts
            if number >= committed_parts:
                part.unlink(missing_ok=True)


def _append_embeddings(path: Path, embeddings: np.ndarray) -> None:
    values = np.ascontiguousarray(embeddings, dtype="<f4")
    with path.open("ab") as handle:
        handle.write(values.tobytes(order="C"))
        handle.flush()
        os.fsync(handle.fileno())


def build_streaming_object_index(
    project_id: str,
    embedder: Any,
    run_dir: Path,
    *,
    chip: int = 64,
    min_size_px: int = 6,
    batch_size: int = DEFAULT_EXTRACTION_BATCH,
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Build or resume the durable object index without retaining chips in RAM."""

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    backbone = str(getattr(embedder, "checkpoint_name", "unknown"))
    signature = source_signature(project_id)
    config = _artifact_config(
        backbone=backbone, chip=chip, min_size_px=min_size_px, source=signature
    )
    manifest_path = run_dir / MANIFEST_FILE
    existing_manifest = _read_json(manifest_path, {}) or {}
    if existing_manifest.get("config") == config and existing_manifest.get("status") == "completed":
        try:
            return load_object_index(run_dir, project_id=project_id)
        except (OSError, ValueError, FileNotFoundError):
            _reset_partial_artifacts(run_dir)

    checkpoint_path = run_dir / CHECKPOINT_FILE
    checkpoint = _read_json(checkpoint_path, {}) or {}
    if checkpoint.get("config") != config:
        _reset_partial_artifacts(run_dir)
        checkpoint = {
            "schema_name": "geotile_embedding_index_checkpoint",
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "status": "building",
            "config": config,
            "processed_candidates": 0,
            "n_objects": 0,
            "dimension": 0,
            "part_count": 0,
        }
        write_json_atomic(checkpoint_path, checkpoint)
    _rollback_to_checkpoint(run_dir, checkpoint)

    processed = int(checkpoint.get("processed_candidates") or 0)
    count = int(checkpoint.get("n_objects") or 0)
    dimension = int(checkpoint.get("dimension") or 0)
    part_count = int(checkpoint.get("part_count") or 0)
    embeddings_path = run_dir / EMBEDDINGS_FILE
    parts_dir = run_dir / OBJECT_PARTS_DIR
    parts_dir.mkdir(parents=True, exist_ok=True)

    for batch in iter_object_chip_batches(
        project_id,
        chip=chip,
        min_size_px=min_size_px,
        batch_size=batch_size,
        skip_candidates=processed,
    ):
        if batch.chips:
            vectors = np.asarray(embedder.embed_chips(batch.chips), dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(batch.objects):
                raise RuntimeError("Embedding backbone returned an invalid batch shape")
            if dimension == 0:
                dimension = int(vectors.shape[1])
            if int(vectors.shape[1]) != dimension:
                raise RuntimeError("Embedding dimension changed while building the index")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.all(np.isfinite(vectors)) or np.any(norms < 1e-8):
                raise RuntimeError("Embedding backbone returned non-finite or zero vectors")

            _append_embeddings(embeddings_path, vectors)
            part_path = parts_dir / f"part-{part_count:08d}.parquet"
            _write_object_part(part_path, batch.objects, count)
            count += len(batch.objects)
            part_count += 1

        processed = batch.processed_candidates
        checkpoint.update(
            {
                "processed_candidates": processed,
                "n_objects": count,
                "dimension": dimension,
                "part_count": part_count,
            }
        )
        write_json_atomic(checkpoint_path, checkpoint)
        if progress is not None:
            progress(processed_candidates=processed, n_objects=count)

    object_manifest = {
        "schema_name": "geotile_embedding_objects",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "format": "parquet_dataset",
        "directory": OBJECT_PARTS_DIR,
        "part_count": part_count,
        "count": count,
        "row_key": "row_index",
    }
    write_json_atomic(run_dir / OBJECTS_MANIFEST_FILE, object_manifest)
    objects = load_objects(run_dir)
    lookup = {
        str(obj["annotation_id"]): row
        for row, obj in enumerate(objects)
        if obj.get("annotation_id") is not None
    }
    write_json_atomic(run_dir / LOOKUP_FILE, lookup)

    manifest = {
        "schema_name": "geotile_embedding_index",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "status": "completed",
        "config": config,
        "embedding_file": EMBEDDINGS_FILE,
        "dtype": "float32-le",
        "shape": [count, dimension],
        "objects_manifest": OBJECTS_MANIFEST_FILE,
        "annotation_lookup": LOOKUP_FILE,
    }
    write_json_atomic(manifest_path, manifest)
    checkpoint["status"] = "completed"
    write_json_atomic(checkpoint_path, checkpoint)
    return {
        "embeddings": open_embeddings(run_dir, manifest=manifest),
        "objects": objects,
        "class_names": {
            item["id"]: item["name"]
            for item in load_scene_independent_classes(project_id)
        },
        "backbone": backbone,
        "artifact_manifest": manifest,
    }


def load_scene_independent_classes(project_id: str) -> list[dict[str, Any]]:
    from db.storage import load_json

    return load_json(project_id, "classes", default=[])


def open_embeddings(
    run_dir: Path, *, manifest: dict[str, Any] | None = None, mode: str = "r"
) -> np.ndarray:
    manifest = manifest or _read_json(Path(run_dir) / MANIFEST_FILE, {}) or {}
    shape = manifest.get("shape") or [0, 0]
    count, dimension = int(shape[0]), int(shape[1])
    if count == 0 or dimension == 0:
        return np.zeros((count, dimension), dtype=np.float32)
    return np.memmap(
        Path(run_dir) / str(manifest.get("embedding_file") or EMBEDDINGS_FILE),
        dtype="<f4",
        mode=mode,
        shape=(count, dimension),
    )


def _objects_dataset(run_dir: Path):
    import pyarrow.dataset as ds

    path = Path(run_dir) / OBJECT_PARTS_DIR
    if not path.is_dir() or not any(path.glob("part-*.parquet")):
        return None
    return ds.dataset(path, format="parquet")


def load_objects(run_dir: Path) -> list[dict[str, Any]]:
    dataset = _objects_dataset(run_dir)
    if dataset is None:
        legacy = _read_json(Path(run_dir) / "objects.json", [])
        return legacy if isinstance(legacy, list) else []
    table = dataset.to_table().sort_by([("row_index", "ascending")])
    result = table.to_pylist()
    for row in result:
        row.pop("row_index", None)
    return result


def load_object_rows(run_dir: Path, indices: Sequence[int]) -> dict[int, dict[str, Any]]:
    wanted = sorted({int(index) for index in indices if int(index) >= 0})
    if not wanted:
        return {}
    dataset = _objects_dataset(run_dir)
    if dataset is None:
        objects = load_objects(run_dir)
        return {index: objects[index] for index in wanted if index < len(objects)}
    import pyarrow.dataset as ds

    table = dataset.to_table(filter=ds.field("row_index").isin(wanted))
    result: dict[int, dict[str, Any]] = {}
    for row in table.to_pylist():
        index = int(row.pop("row_index"))
        result[index] = row
    return result


def load_object_index(run_dir: Path, project_id: str | None = None) -> dict[str, Any]:
    manifest = _read_json(Path(run_dir) / MANIFEST_FILE, {}) or {}
    if manifest.get("status") != "completed":
        raise FileNotFoundError(f"Incomplete embedding index: {run_dir}")
    config = manifest.get("config") or {}
    return {
        "embeddings": open_embeddings(run_dir, manifest=manifest),
        "objects": load_objects(run_dir),
        "class_names": (
            {
                item["id"]: item["name"]
                for item in load_scene_independent_classes(project_id)
            }
            if project_id
            else {}
        ),
        "backbone": config.get("backbone"),
        "artifact_manifest": manifest,
    }


def ann_runtime_available() -> bool:
    try:
        from usearch.index import Index  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 - native import failures count as unavailable
        return False


def _save_ann_atomic(index: Any, path: Path) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        index.save(str(temp))
        for attempt in range(12):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def build_ann_index(
    run_dir: Path,
    embeddings: np.ndarray,
    *,
    exact_limit: int = DEFAULT_EXACT_LIMIT,
    batch_size: int = DEFAULT_ANN_BATCH,
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Build/resume a persisted USearch HNSW index in bounded vector batches."""

    run_dir = Path(run_dir)
    count = int(embeddings.shape[0])
    dimension = int(embeddings.shape[1]) if embeddings.ndim == 2 else 0
    if count <= int(exact_limit):
        return {"backend": "exact", "count": count, "exact_limit": int(exact_limit)}
    try:
        from usearch.index import Index
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "USearch is required for embedding indexes larger than "
            f"{exact_limit} objects"
        ) from exc

    path = run_dir / ANN_FILE
    state_path = run_dir / ANN_STATE_FILE
    for temp in run_dir.glob(f".{ANN_FILE}.*.tmp"):
        temp.unlink(missing_ok=True)
    state = _read_json(state_path, {}) or {}
    expected = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "count": count,
        "dimension": dimension,
        "metric": "cos",
        "dtype": "f32",
        "connectivity": 24,
        "expansion_add": 192,
        "expansion_search": 512,
    }
    indexed = 0
    index = None
    if state.get("config") == expected and path.is_file():
        try:
            index = Index.restore(str(path), view=False)
            indexed = min(int(state.get("indexed") or 0), len(index), count)
            if len(index) != indexed:
                index = None
                indexed = 0
        except Exception:  # noqa: BLE001 - rebuild a corrupt native artifact
            index = None
            indexed = 0
    if index is None:
        path.unlink(missing_ok=True)
        index = Index(
            ndim=dimension,
            metric="cos",
            dtype="f32",
            connectivity=24,
            expansion_add=192,
            expansion_search=512,
        )
        state = {
            "schema_name": "geotile_embedding_ann_state",
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "status": "building",
            "config": expected,
            "indexed": 0,
        }
        write_json_atomic(state_path, state)

    batch_size = max(1, int(batch_size))
    checkpoint_every = max(batch_size, 50_000)
    last_saved = indexed
    for start in range(indexed, count, batch_size):
        stop = min(start + batch_size, count)
        keys = np.arange(start, stop, dtype=np.uint64)
        index.add(keys, np.asarray(embeddings[start:stop], dtype=np.float32))
        indexed = stop
        if indexed - last_saved >= checkpoint_every or indexed == count:
            _save_ann_atomic(index, path)
            state.update({"indexed": indexed, "status": "building"})
            write_json_atomic(state_path, state)
            last_saved = indexed
        if progress is not None:
            progress(indexed=indexed, total=count)

    previous_validation = state.get("validation") or {}
    if (
        state.get("status") == "completed"
        and int(state.get("indexed") or 0) == count
        and previous_validation.get("gate_pass")
    ):
        validation = previous_validation
        index.expansion_search = int(validation["expansion_search"])
    else:
        validation = validate_ann_recall(
            index,
            embeddings,
            queries=ANN_RECALL_QUERIES,
            k=ANN_RECALL_K,
            target=ANN_RECALL_TARGET,
            initial_expansion=int(expected["expansion_search"]),
            max_expansion=ANN_MAX_EXPANSION_SEARCH,
        )
        # Expansion is a query-time property. Save the validated setting and also persist
        # it in JSON because native serialization behavior differs across versions.
        index.expansion_search = int(validation["expansion_search"])
        _save_ann_atomic(index, path)
    state.update(
        {
            "indexed": count,
            "status": "completed",
            "validation": validation,
        }
    )
    write_json_atomic(state_path, state)
    return {
        "backend": "usearch",
        "path": ANN_FILE,
        "count": count,
        "dimension": dimension,
        "metric": "cos",
        "dtype": "f32",
        "connectivity": 24,
        "expansion_add": 192,
        "expansion_search": int(validation["expansion_search"]),
        "recall_at_k": validation["recall_at_k"],
        "recall_k": validation["k"],
        "recall_queries": validation["queries"],
        "recall_gate_pass": validation["gate_pass"],
    }


def restore_ann_index(run_dir: Path):
    from usearch.index import Index

    run_dir = Path(run_dir)
    index = Index.restore(str(run_dir / ANN_FILE), view=True)
    state = _read_json(run_dir / ANN_STATE_FILE, {}) or {}
    validation = state.get("validation") or {}
    if validation.get("expansion_search"):
        index.expansion_search = int(validation["expansion_search"])
    return index


def validate_ann_recall(
    index: Any,
    embeddings: np.ndarray,
    *,
    queries: int = ANN_RECALL_QUERIES,
    k: int = ANN_RECALL_K,
    target: float = ANN_RECALL_TARGET,
    initial_expansion: int = 512,
    max_expansion: int = ANN_MAX_EXPANSION_SEARCH,
) -> dict[str, Any]:
    """Tune query effort against exact neighbors on a deterministic full-index sample."""

    count = int(embeddings.shape[0])
    if count <= 1:
        return {
            "recall_at_k": 1.0,
            "k": 0,
            "queries": 0,
            "target": float(target),
            "gate_pass": True,
            "expansion_search": int(initial_expansion),
        }
    used_k = min(max(1, int(k)), count - 1)
    query_count = min(max(1, int(queries)), count)
    rng = np.random.default_rng(42)
    query_rows = rng.choice(count, query_count, replace=False)
    exact_neighbors: list[set[int]] = []
    matrix = np.asarray(embeddings)
    for query in query_rows:
        similarities = matrix @ matrix[int(query)]
        similarities[int(query)] = -np.inf
        candidates = np.argpartition(similarities, -used_k)[-used_k:]
        exact_neighbors.append({int(value) for value in candidates})

    expansion = max(1, int(initial_expansion))
    recall = 0.0
    while True:
        index.expansion_search = expansion
        recalls: list[float] = []
        for position, query in enumerate(query_rows):
            keys = [
                int(value)
                for value in np.asarray(
                    index.search(matrix[int(query)], min(count, used_k + 1)).keys
                )
                if int(value) != int(query)
            ][:used_k]
            recalls.append(len(exact_neighbors[position].intersection(keys)) / used_k)
        recall = float(np.mean(recalls))
        if recall >= float(target) or expansion >= int(max_expansion):
            break
        expansion = min(int(max_expansion), expansion * 2)
    return {
        "recall_at_k": round(recall, 6),
        "k": used_k,
        "queries": query_count,
        "target": float(target),
        "gate_pass": recall >= float(target),
        "expansion_search": expansion,
    }


def annotation_row(run_dir: Path, annotation_id: str) -> int | None:
    lookup = _read_json(Path(run_dir) / LOOKUP_FILE, {}) or {}
    value = lookup.get(str(annotation_id))
    return int(value) if value is not None else None


def nearest_from_artifacts(
    run_dir: Path,
    query_row: int,
    *,
    k: int = 20,
    class_names: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Query persisted artifacts without loading the complete metadata table."""

    run_dir = Path(run_dir)
    embeddings = open_embeddings(run_dir)
    count = int(embeddings.shape[0])
    if count <= 1 or not 0 <= int(query_row) < count:
        return []
    wanted = min(max(1, int(k)), count - 1)
    rows: list[int] = []
    scores: list[float] = []
    ann_path = run_dir / ANN_FILE
    ann_state = _read_json(run_dir / ANN_STATE_FILE, {}) or {}
    if ann_path.is_file() and ann_state.get("status") == "completed":
        index = restore_ann_index(run_dir)
        matches = index.search(
            np.asarray(embeddings[int(query_row)], dtype=np.float32),
            min(count, wanted + 1),
        )
        for key, distance in zip(np.asarray(matches.keys), np.asarray(matches.distances)):
            row = int(key)
            if row == int(query_row):
                continue
            rows.append(row)
            scores.append(1.0 - float(distance))
            if len(rows) == wanted:
                break
    else:
        similarities = np.asarray(embeddings) @ np.asarray(embeddings[int(query_row)])
        similarities[int(query_row)] = -np.inf
        if wanted < count - 1:
            candidates = np.argpartition(similarities, -wanted)[-wanted:]
            order = candidates[np.argsort(similarities[candidates])[::-1]]
        else:
            order = np.argsort(similarities)[::-1][:wanted]
        rows = [int(row) for row in order]
        scores = [float(similarities[row]) for row in rows]

    metadata = load_object_rows(run_dir, rows)
    names = class_names or {}
    result: list[dict[str, Any]] = []
    for row, score in zip(rows, scores):
        obj = metadata.get(row)
        if obj is None:
            continue
        class_id = obj.get("class_id")
        result.append(
            {
                **obj,
                "class_name": names.get(class_id, str(class_id)),
                "similarity": round(score, 4),
            }
        )
    return result
