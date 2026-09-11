"""Build the canonical, self-describing dataset package."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from db.storage import load_json
from services.export_coco import export_coco_dataset
from services.export_sidecars import generate_export_sidecars
from services.export_voc import export_voc_dataset
from services.export_yolo import export_yolo_dataset
from services.dataset_runs import DatasetPublicationError, require_dataset_exact_identities


PACKAGE_SCHEMA_VERSION = 2
PACKAGE_EXPORTER_VERSION = "2"
PACKAGE_FORMATS = ("yolo", "coco", "voc")


class DatasetPackageCancelled(RuntimeError):
    """Raised when a durable export job asks package generation to stop."""


def normalize_package_formats(formats: Iterable[str] | None) -> tuple[str, ...]:
    requested_values = PACKAGE_FORMATS if formats is None else formats
    requested = tuple(str(value).strip().lower() for value in requested_values)
    invalid = sorted(set(requested) - set(PACKAGE_FORMATS))
    if invalid:
        raise ValueError(f"Unsupported dataset export format(s): {', '.join(invalid)}")
    normalized = tuple(value for value in PACKAGE_FORMATS if value in set(requested))
    if not normalized:
        raise ValueError("Select at least one dataset export format")
    return normalized


def build_dataset_package(
    project_id: str,
    dataset_dir: str | Path,
    *,
    run_id: str | None = None,
    formats: Iterable[str] | None = None,
    package_dir: str | Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> Path:
    run_dir = Path(dataset_dir)
    selected_formats = normalize_package_formats(formats)
    cancel = should_cancel or (lambda: False)
    report = progress or (lambda _phase, _current, _total: None)
    if cancel():
        raise DatasetPackageCancelled("Dataset package generation cancelled")
    manifest = read_json(run_dir / "dataset_run_manifest.json", {})
    try:
        require_dataset_exact_identities(manifest)
    except DatasetPublicationError as exc:
        raise ValueError(str(exc)) from exc
    project = load_json(project_id, "project", default={})
    classes = manifest.get("classes") or load_json(project_id, "classes", default=[])
    tiling = manifest.get("tiling_config") or load_json(project_id, "tiling_config", default={})
    tile_size = int(tiling.get("tile_size", 640))
    tile_annotations = read_json(run_dir / "tile_annotations.json", {})
    link_manifest = read_json(run_dir / "tile_annotation_links.json", {})
    links = link_manifest.get("annotations", []) if isinstance(link_manifest, dict) else []
    class_names = {int(item["id"]): str(item["name"]) for item in classes}

    target_dir = Path(package_dir) if package_dir is not None else run_dir / "package"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    copy_metadata(run_dir, target_dir)
    generate_export_sidecars(
        project_id,
        run_dir,
        "package",
        run_id=run_id,
        output_dir=target_dir,
        persist_run_manifest=False,
    )
    export_manifest = target_dir / "geotile_export_manifest.json"
    if export_manifest.is_file():
        metadata_dir = target_dir / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(export_manifest, metadata_dir / export_manifest.name)

    total_steps = len(selected_formats) + 1
    for index, export_format in enumerate(selected_formats, start=1):
        if cancel():
            raise DatasetPackageCancelled("Dataset package generation cancelled")
        report(f"export_{export_format}", index - 1, total_steps)
        if export_format == "yolo":
            export_yolo_dataset(
                tile_annotations,
                run_dir,
                tile_size,
                class_names,
                output_dir=target_dir / "yolo",
                tile_links=links,
            )
        elif export_format == "coco":
            export_coco_dataset(
                tile_annotations,
                run_dir,
                tile_size,
                class_names,
                output_dir=target_dir / "coco",
                tile_links=links,
            )
        else:
            export_voc_dataset(
                tile_annotations,
                run_dir,
                tile_size,
                class_names,
                output_dir=target_dir / "voc",
            )

    project_profile = manifest.get("project_profile") or project.get("profile") or {}
    write_json(target_dir / "project_profile.json", project_profile)
    write_json(target_dir / "classes.json", classes)
    preprocessing = manifest.get("preprocessing_profile") or read_json(
        run_dir / "preprocessing_profile.json", {}
    )
    write_json(target_dir / "preprocessing_profiles.json", {
        "schema_name": "geotile_preprocessing_profiles_snapshot",
        "schema_version": 1,
        "profiles": [preprocessing] if preprocessing else [],
    })

    package_manifest = build_package_manifest(
        project_id,
        run_id or manifest.get("run_id"),
        project,
        manifest,
        selected_formats,
    )
    write_json(target_dir / "dataset_run_manifest.json", package_manifest)
    write_readme(target_dir / "README_DATASET.md", package_manifest)
    copy_scene_manifests(run_dir, target_dir)
    write_export_log(target_dir / "logs" / "export_log.txt", package_manifest)
    if cancel():
        raise DatasetPackageCancelled("Dataset package generation cancelled")
    report("checksums", len(selected_formats), total_steps)
    write_checksums(target_dir)
    report("package_ready", total_steps, total_steps)
    return target_dir


def build_package_manifest(
    project_id: str,
    run_id: str | None,
    project: dict[str, Any],
    run_manifest: dict[str, Any],
    formats: Iterable[str] | None = None,
) -> dict[str, Any]:
    selected = normalize_package_formats(formats)
    format_paths: dict[str, str] = {}
    format_artifacts: dict[str, str] = {}
    if "yolo" in selected:
        format_paths.update({"yolo_aabb": "yolo/labels", "yolo_obb": "yolo/labels_obb"})
        format_artifacts.update(format_paths)
    if "coco" in selected:
        format_paths["coco"] = "coco/annotations"
        format_artifacts["coco"] = "coco/annotations"
    if "voc" in selected:
        format_paths["pascal_voc"] = "voc"
        format_artifacts["pascal_voc"] = "voc"
    return {
        **run_manifest,
        "schema_name": "geotile_dataset_package_manifest",
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "package_exporter_version": PACKAGE_EXPORTER_VERSION,
        "project_id": project_id,
        "project_name": run_manifest.get("project_name") or project.get("name"),
        "run_id": run_id,
        "packaged_at": utc_now(),
        "selected_formats": list(selected),
        "formats": format_paths,
        "metadata_root": "metadata",
        "source_scenes_embedded": False,
        "artifacts": {
            "readme": "README_DATASET.md",
            "project_profile": "project_profile.json",
            "classes": "classes.json",
            "preprocessing_profiles": "preprocessing_profiles.json",
            **format_artifacts,
            "tile_metadata": "metadata/tile_metadata.parquet",
            "annotation_links": "metadata/annotation_links.parquet",
            "annotations_wgs84": "metadata/annotations_wgs84.geoparquet",
            "annotations_native": "metadata/annotations_native.geoparquet",
            "split_manifest": "metadata/split_manifest.json",
            "dataset_selection": "metadata/dataset_selection.json",
            "validation_report": "metadata/validation_report.json",
            "dataset_statistics": "metadata/dataset_statistics.json",
            "dataset_audit": "metadata/dataset_audit.json",
            "checksums": "SHA256SUMS.txt",
        },
    }


def copy_metadata(run_dir: Path, package_dir: Path) -> None:
    metadata_dir = package_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    source_metadata = run_dir / "metadata"
    if source_metadata.exists():
        for path in source_metadata.iterdir():
            destination = metadata_dir / path.name
            if path.is_dir():
                shutil.copytree(path, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(path, destination)
    canonical_files = {
        "split_manifest.json": "split_manifest.json",
        "dataset_selection.json": "dataset_selection.json",
        "validation_report.json": "validation_report.json",
        "dataset_stats.json": "dataset_statistics.json",
        "geotile_export_manifest.json": "geotile_export_manifest.json",
        "tile_annotation_links.json": "tile_annotation_links.json",
    }
    for source_name, target_name in canonical_files.items():
        source = run_dir / source_name
        if source.is_file():
            shutil.copy2(source, metadata_dir / target_name)


def copy_scene_manifests(run_dir: Path, package_dir: Path) -> None:
    manifests = read_json(run_dir / "scene_manifests.json", {})
    output = package_dir / "scene_manifests"
    output.mkdir(parents=True, exist_ok=True)
    for scene_id, manifest in sorted(manifests.items()):
        write_json(output / f"{safe_name(scene_id)}.scene_manifest.json", manifest)


def write_readme(path: Path, manifest: dict[str, Any]) -> None:
    selected = set(manifest.get("selected_formats") or PACKAGE_FORMATS)
    format_lines = []
    if "yolo" in selected:
        format_lines.extend([
            "- `yolo/labels/` - axis-aligned YOLO detection labels.",
            "- `yolo/labels_obb/` - YOLO OBB labels (`class x1 y1 ... x4 y4`).",
        ])
    if "coco" in selected:
        format_lines.append("- `coco/annotations/` - COCO annotations; rotated boxes retain polygon segmentation.")
    if "voc" in selected:
        format_lines.append("- `voc/` - Pascal VOC compatibility export using axis-aligned boxes.")
    text = f"""# GeoTile Label Dataset

Project: **{manifest.get('project_name') or manifest.get('project_id')}**
Run ID: `{manifest.get('run_id') or 'legacy'}`
Packaged at: `{manifest.get('packaged_at')}`

This package contains derived ML tiles in the selected formats plus
canonical geospatial provenance. Source satellite scenes are not embedded.
## Formats

{chr(10).join(format_lines)}

## Geospatial metadata

- `metadata/annotations_wgs84.geoparquet` - all georeferenced source annotations for GIS.
- `metadata/annotations_native.geoparquet` - source annotations in native scene coordinates.
- `metadata/tile_metadata.parquet` and `annotation_links.parquet` - tile lineage.
- `scene_manifests/` - one immutable scene manifest per source scene.

Use `SHA256SUMS.txt` to verify file integrity. The full run and preprocessing settings
are stored in `dataset_run_manifest.json` and `preprocessing_profiles.json`.
"""
    path.write_text(text, encoding="utf-8", newline="\n")


def write_export_log(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{manifest.get('packaged_at')} package_started",
        f"project_id={manifest.get('project_id')}",
        f"run_id={manifest.get('run_id')}",
        f"formats={','.join(manifest.get('selected_formats') or PACKAGE_FORMATS)}",
        "source_scenes_embedded=false",
        f"{utc_now()} package_completed",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_checksums(package_dir: Path) -> None:
    checksum_path = package_dir / "SHA256SUMS.txt"
    rows = []
    for file_path in sorted(path for path in package_dir.rglob("*") if path.is_file()):
        if file_path == checksum_path:
            continue
        rows.append(f"{sha256(file_path)}  {file_path.relative_to(package_dir).as_posix()}")
    checksum_path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def safe_name(value: Any) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in str(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
