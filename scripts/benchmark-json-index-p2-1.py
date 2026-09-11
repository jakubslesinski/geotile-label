"""Validate JSON Index v2 parity and rebuild cost on an isolated metadata copy.

The source project is never registered or opened through GeoTile Label storage.
Only authoritative JSON metadata is copied to a temporary DATA_DIR. Rasters,
thumbnails, pyramids, tile caches and derived products are deliberately omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from db.storage import list_scene_ids, load_scene_json, project_paths  # noqa: E402
from services.annotation_summary import compute_project_annotation_summary  # noqa: E402
from services.json_index.queries import get_annotation_summary, get_scenes_index  # noqa: E402
from services.json_index.rebuild import rebuild_project_indexes  # noqa: E402

SOURCE_SCENE_FILES = (
    "scene.json",
    "scene_manifest.json",
    "annotations.json",
    "annotations.meta.json",
)
SUMMARY_KEYS = (
    "scene_count",
    "scenes_with_annotations",
    "scenes_without_annotations",
    "annotation_count",
    "missing_author_count",
    "per_scene",
    "per_class",
    "per_author",
    "per_source",
    "per_import",
    "per_package",
    "import_count",
    "import_totals",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warm-reads", type=int, default=5)
    parser.add_argument("--copy-workers", type=int, default=8)
    parser.add_argument("--keep-copy", action="store_true")
    return parser.parse_args()


def iter_source_json(root: Path):
    for name in ("project.json", "classes.json"):
        path = root / name
        if path.is_file():
            yield path
    scenes = root / "scenes"
    if scenes.is_dir():
        for directory in sorted(path for path in scenes.iterdir() if path.is_dir()):
            for name in SOURCE_SCENE_FILES:
                path = directory / name
                if path.is_file():
                    yield path
    imports = root / "annotation_imports"
    if imports.is_dir():
        yield from sorted(imports.rglob("*.json"))


def metadata_fingerprint(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path in iter_source_json(root):
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))
        count += 1
        total_bytes += stat.st_size
    return {
        "file_count": count,
        "bytes": total_bytes,
        "metadata_digest": digest.hexdigest(),
    }


def copy_metadata(source: Path, destination: Path, *, workers: int) -> dict[str, Any]:
    started = time.perf_counter()
    paths = list(iter_source_json(source))

    def copy_one(path: Path) -> int:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return path.stat().st_size

    with ThreadPoolExecutor(max_workers=max(1, min(16, int(workers)))) as pool:
        sizes = list(pool.map(copy_one, paths))
    return {
        "wall_s": time.perf_counter() - started,
        "workers": max(1, min(16, int(workers))),
        "file_count": len(paths),
        "bytes": sum(sizes),
    }


class ResourceSampler:
    def __init__(self) -> None:
        import psutil

        self.process = psutil.Process()
        self.stop_event = threading.Event()
        self.peak_rss = self.process.memory_info().rss
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _tree_rss(self) -> int:
        total = self.process.memory_info().rss
        for child in self.process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except Exception:
                continue
        return total

    def _run(self) -> None:
        while not self.stop_event.wait(0.025):
            self.peak_rss = max(self.peak_rss, self._tree_rss())

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.peak_rss = max(self.peak_rss, self._tree_rss())
        self.stop_event.set()
        self.thread.join(timeout=2.0)


def measure(operation: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    import psutil

    process = psutil.Process()
    io_before = process.io_counters()
    cpu_before = time.process_time()
    started = time.perf_counter()
    with ResourceSampler() as sampler:
        value = operation()
    elapsed = time.perf_counter() - started
    io_after = process.io_counters()
    return value, {
        "wall_s": elapsed,
        "cpu_s": time.process_time() - cpu_before,
        "peak_rss_bytes": sampler.peak_rss,
        "read_bytes": max(0, io_after.read_bytes - io_before.read_bytes),
        "write_bytes": max(0, io_after.write_bytes - io_before.write_bytes),
    }


def canonical_rows(rows: Any, keys: tuple[str, ...]) -> list[tuple[Any, ...]]:
    return sorted(tuple(item.get(key) for key in keys) for item in (rows or []))


def canonical_summary(summary: dict[str, Any]) -> dict[str, Any]:
    value = {key: summary.get(key) for key in SUMMARY_KEYS}
    value["scenes_without_annotations"] = sorted(value["scenes_without_annotations"] or [])
    value["per_scene"] = canonical_rows(
        value["per_scene"],
        (
            "scene_id",
            "source_scene_uid",
            "filename",
            "annotation_count",
            "class_ids",
            "class_names",
            "authors",
            "sources",
            "import_ids",
            "package_ids",
        ),
    )
    value["per_class"] = canonical_rows(
        value["per_class"], ("class_id", "class_name", "annotation_count")
    )
    value["per_author"] = canonical_rows(
        value["per_author"], ("annotator_email", "annotation_count")
    )
    value["per_source"] = canonical_rows(
        value["per_source"], ("annotation_source", "annotation_count")
    )
    value["per_import"] = canonical_rows(
        value["per_import"], ("import_id", "annotation_count")
    )
    value["per_package"] = canonical_rows(
        value["per_package"], ("package_id", "annotation_count")
    )
    return value


def legacy_scene_scan(project_id: str) -> dict[str, Any]:
    """Independent read-only scene counter used as the parity reference."""

    paths = project_paths(project_id)
    scenes = []
    for scene_id in list_scene_ids(project_id, paths=paths):
        scene = load_scene_json(project_id, scene_id, "scene", default={}, paths=paths)
        if isinstance(scene, dict) and scene:
            scenes.append({"scene_id": scene_id})
    return {"scene_count": len(scenes), "scenes": scenes}


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.project_root.expanduser().resolve(strict=True)
    project = json.loads((source / "project.json").read_text(encoding="utf-8"))
    project_id = str(project.get("id") or source.name)
    source_before = metadata_fingerprint(source)
    temp_owner = Path(tempfile.mkdtemp(prefix="geotile-p2-1-benchmark-"))
    data_root = temp_owner / "data"
    copy_root = data_root / "projects" / project_id
    copy_stats = copy_metadata(source, copy_root, workers=args.copy_workers)

    previous_data_dir = storage.DATA_DIR
    previous_flag = os.environ.get("GEOTILE_JSON_INDEX_V2")
    previous_diagnostic = os.environ.get("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC")
    try:
        storage.DATA_DIR = data_root
        storage._invalidate_project_root_cache()
        os.environ["GEOTILE_JSON_INDEX_V2"] = "0"
        os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)

        legacy_index, legacy_index_metrics = measure(lambda: legacy_scene_scan(project_id))
        legacy_summary, legacy_summary_metrics = measure(
            lambda: compute_project_annotation_summary(project_id, use_cache=False)
        )

        os.environ["GEOTILE_JSON_INDEX_V2"] = "1"
        v2_index, first_rebuild_metrics = measure(
            lambda: rebuild_project_indexes(project_id, force=True)
        )
        v2_summary = get_annotation_summary(project_id)

        warm_scene_times = []
        warm_summary_times = []
        for _ in range(max(1, int(args.warm_reads))):
            started = time.perf_counter()
            get_scenes_index(project_id)
            warm_scene_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            get_annotation_summary(project_id)
            warm_summary_times.append(time.perf_counter() - started)

        index_root = copy_root / "indexes"
        resolved_index_root = index_root.resolve(strict=True)
        resolved_index_root.relative_to(temp_owner.resolve(strict=True))
        shutil.rmtree(resolved_index_root)
        rebuilt_after_delete, delete_rebuild_metrics = measure(
            lambda: rebuild_project_indexes(project_id)
        )
        rebuilt_summary = get_annotation_summary(project_id)

        legacy_scene_ids = {str(row.get("scene_id")) for row in legacy_index.get("scenes") or []}
        v2_scene_ids = {str(row.get("scene_id")) for row in v2_index.get("scenes") or []}
        rebuilt_scene_ids = {
            str(row.get("scene_id")) for row in rebuilt_after_delete.get("scenes") or []
        }
        summary_parity = canonical_summary(legacy_summary) == canonical_summary(v2_summary)
        delete_summary_parity = canonical_summary(legacy_summary) == canonical_summary(
            rebuilt_summary
        )
        source_after = metadata_fingerprint(source)
        result = {
            "schema_name": "geotile_json_index_p2_1_benchmark",
            "schema_version": 1,
            "source_project": {
                "project_id": project_id,
                "name": project.get("name"),
                "root": str(source),
                "fingerprint_before": source_before,
                "fingerprint_after": source_after,
                "unchanged": source_before == source_after,
            },
            "isolated_copy": {
                "root": str(copy_root) if args.keep_copy else None,
                **copy_stats,
            },
            "counts": {
                "legacy_scene_count": legacy_index.get("scene_count"),
                "v2_scene_count": v2_index.get("scene_count"),
                "legacy_annotation_count": legacy_summary.get("annotation_count"),
                "v2_annotation_count": v2_summary.get("annotation_count"),
                "legacy_class_count": len(legacy_summary.get("per_class") or []),
                "v2_class_count": len(v2_summary.get("per_class") or []),
            },
            "parity": {
                "scene_ids": legacy_scene_ids == v2_scene_ids,
                "annotation_summary": summary_parity,
                "after_index_delete_scene_ids": legacy_scene_ids == rebuilt_scene_ids,
                "after_index_delete_annotation_summary": delete_summary_parity,
            },
            "metrics": {
                "legacy_scene_index": legacy_index_metrics,
                "legacy_annotation_summary": legacy_summary_metrics,
                "v2_first_rebuild": first_rebuild_metrics,
                "v2_rebuild_after_index_delete": delete_rebuild_metrics,
                "v2_warm_scenes_median_s": statistics.median(warm_scene_times),
                "v2_warm_summary_median_s": statistics.median(warm_summary_times),
            },
        }
        result["passed"] = bool(
            result["source_project"]["unchanged"]
            and all(result["parity"].values())
            and result["counts"]["legacy_scene_count"] == result["counts"]["v2_scene_count"]
            and result["counts"]["legacy_annotation_count"]
            == result["counts"]["v2_annotation_count"]
        )
        return result
    finally:
        storage.DATA_DIR = previous_data_dir
        storage._invalidate_project_root_cache()
        if previous_flag is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2"] = previous_flag
        if previous_diagnostic is None:
            os.environ.pop("GEOTILE_JSON_INDEX_V2_DIAGNOSTIC", None)
        else:
            os.environ["GEOTILE_JSON_INDEX_V2_DIAGNOSTIC"] = previous_diagnostic
        if not args.keep_copy:
            shutil.rmtree(temp_owner, ignore_errors=True)


def main() -> int:
    args = parse_args()
    result = run(args)
    output = args.output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
