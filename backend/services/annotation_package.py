"""Build the lightweight annotation package exchanged between analysts and managers."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unicodedata
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json, save_json
from models.project import APP_VERSION
from services.source_annotation_export import export_source_annotations_geoparquet
from services.scene_identity import manifest_identity_strength

# v2 adds `scope` (owner_email, scene_uids, authoritative_as_of, supersedes_package_id).
# v1 packages are still importable and are treated as additive-only (no deletion or
# replacement can be inferred from them, because they never declared a scope).
ANNOTATION_PACKAGE_SCHEMA_VERSION = 2
SUPPORTED_ANNOTATION_PACKAGE_VERSIONS = (1, 2)
PACKAGE_HISTORY_NAME = "annotation_package_history"


class AnnotationPackagePrepareCancelled(RuntimeError):
    """Raised when package preparation is cancelled between or inside scenes."""


def _needs_exact_identity(manifest: dict[str, Any]) -> bool:
    """Does this scene still block the export on identity?

    Mirrors the preflight in `preview_annotation_package` — one predicate, so the
    button can never disagree with the check it is meant to satisfy.
    """
    if not manifest.get("source_scene_uid"):
        return True
    status = manifest.get("source_identity_status") or (
        manifest.get("source_identity") or {}
    ).get("status")
    return manifest_identity_strength(manifest) != "exact" or status not in {None, "complete"}


def _measurement_bytes(manifest: dict[str, Any]) -> int:
    """Bytes that a forced refresh would actually read for this scene.

    Only measurement assets without a usable full hash are counted: a cached digest
    survives an unchanged size/mtime snapshot, so a second preparation of the same
    project reads nothing and must not promise otherwise.
    """
    from services.scene_identity import is_full_sha256

    source_package = manifest.get("source_package") or {}
    measurement_ids = set(
        (manifest.get("source_identity") or {}).get("identity_scope", {}).get(
            "measurement_asset_ids"
        )
        or source_package.get("identity_asset_ids")
        or []
    )
    total = 0
    for asset in source_package.get("assets") or []:
        if str(asset.get("asset_id") or "") not in measurement_ids:
            continue
        if is_full_sha256(asset.get("sha256")):
            continue
        total += int(asset.get("size") or 0)
    return total


def plan_annotation_package_preparation(project_id: str) -> dict[str, Any]:
    """What a forced identity refresh would cost, without reading a single byte."""

    scenes: list[dict[str, Any]] = []
    total_bytes = 0
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if not manifest or not _needs_exact_identity(manifest):
            continue
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        scene_bytes = _measurement_bytes(manifest)
        total_bytes += scene_bytes
        scenes.append({
            "scene_id": scene_id,
            "filename": scene.get("filename") or scene_id,
            "bytes": scene_bytes,
        })
    return {
        "project_id": project_id,
        "scene_count": len(scenes),
        "total_bytes": total_bytes,
        "scenes": scenes,
    }


def _identity_scene_path(project_id: str, scene_id: str, scene_data: dict[str, Any]) -> Path:
    """Sciezka rastra dla `refresh_scene_identity` — wymagana tylko czasem.

    Scena z `source_package` liczy tozsamosc z ZASOBOW DOSTAWY (`resolve_source_asset`),
    a nie z rastra roboczego; tamten argument jest w tej galezi nieuzywany. Zadanie
    `resolve_scene_raster` bezwarunkowo wywracaloby przygotowanie paczki na scenach,
    ktorych derywat nie jest jeszcze zbudowany (`status: not_ready`) — mimo ze pelne
    sha256 i tak czyta plik zrodlowy. Raster rozwiazujemy wiec tylko dla scen bez paczki,
    gdzie faktycznie jest jedynym zrodlem tozsamosci.
    """
    from services.scene_raster_resolver import resolve_scene_raster

    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if manifest.get("source_package"):
        return Path(str(scene_data.get("path") or scene_data.get("filename") or scene_id))
    return resolve_scene_raster(project_id, scene_id)


def prepare_annotation_package(
    project_id: str,
    *,
    progress: Any = None,
    should_cancel: Any = None,
) -> dict[str, Any]:
    """Give every blocking scene an exact source identity, then report the outcome.

    This is the work behind the „prepare package" button. It is deliberately a job and
    not a request: the measurement asset of a single scene can be several gigabytes,
    and the full SHA-256 is the only evidence that separates two reprocessed variants
    of the same acquisition — which is exactly what annotation coordinates must not be
    attached to by mistake.
    """

    from services.scene_manifest import refresh_scene_identity
    from services.scene_packages.identity import IdentityHashCancelled

    plan = plan_annotation_package_preparation(project_id)
    project_data = load_json(project_id, "project", default={})
    total_bytes = int(plan["total_bytes"])
    total_scenes = int(plan["scene_count"])
    done_bytes = 0
    results: list[dict[str, Any]] = []

    def report(scene_index: int, filename: str, scene_done: int) -> None:
        if progress is None:
            return
        progress({
            "phase": "identity",
            "scene_index": scene_index,
            "scene_count": total_scenes,
            "filename": filename,
            "done_bytes": done_bytes + scene_done,
            "total_bytes": total_bytes,
        })

    for index, item in enumerate(plan["scenes"], start=1):
        if should_cancel is not None and should_cancel():
            raise AnnotationPackagePrepareCancelled("Package preparation cancelled")
        scene_id = str(item["scene_id"])
        filename = str(item["filename"])
        scene_done = 0
        report(index, filename, scene_done)

        def on_chunk(size: int, _index: int = index, _name: str = filename) -> None:
            nonlocal scene_done
            scene_done += size
            report(_index, _name, scene_done)

        scene_data = load_scene_json(project_id, scene_id, "scene", default={})
        try:
            manifest = refresh_scene_identity(
                project_id,
                scene_id,
                project_data,
                scene_data,
                _identity_scene_path(project_id, scene_id, scene_data),
                force=True,
                progress=on_chunk,
                should_cancel=should_cancel,
            )
        except IdentityHashCancelled as exc:
            raise AnnotationPackagePrepareCancelled(str(exc)) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            results.append({
                "scene_id": scene_id,
                "filename": filename,
                "exact": False,
                "error": str(exc),
            })
            done_bytes += int(item["bytes"])
            continue

        done_bytes += int(item["bytes"])
        results.append({
            "scene_id": scene_id,
            "filename": filename,
            "exact": not _needs_exact_identity(manifest),
            "source_scene_uid": manifest.get("source_scene_uid"),
            "error": manifest.get("source_identity_error"),
        })

    preview = preview_annotation_package(project_id)
    return {
        "project_id": project_id,
        "scene_count": total_scenes,
        "total_bytes": total_bytes,
        "exact": sum(1 for item in results if item.get("exact")),
        "failed": sum(1 for item in results if not item.get("exact")),
        "scenes": results,
        "can_export": preview.get("can_export", False),
        "errors": preview.get("errors", []),
        "warnings": preview.get("warnings", []),
    }


def preview_annotation_package(project_id: str) -> dict[str, Any]:
    project = load_json(project_id, "project", default={})
    profile = project.get("profile") or {}
    classes = load_json(project_id, "classes", default=[])
    class_names = {
        int(item["id"]): str(item.get("name") or item["id"])
        for item in classes
        if isinstance(item, dict) and item.get("id") is not None
    }
    errors: list[str] = []
    warnings: list[str] = []
    scenes: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    author_counts: Counter[str] = Counter()
    seen_uids: set[str] = set()
    annotation_count = 0

    author_email = profile.get("labeling_author_email")
    if not author_email:
        errors.append("Project labeling author email is missing")

    for scene_id in list_scene_ids(project_id):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        source_uid = manifest.get("source_scene_uid")
        identity_strength = manifest_identity_strength(manifest)
        identity_status = manifest.get("source_identity_status") or (
            manifest.get("source_identity") or {}
        ).get("status")
        if not source_uid:
            errors.append(
                f"Scene {scene.get('filename') or scene_id} has no exact source_scene_uid; "
                f"run source identity refresh with force=true"
            )
        elif identity_strength != "exact" or identity_status not in {None, "complete"}:
            errors.append(
                f"Scene {scene.get('filename') or scene_id} source identity is not exact and current; "
                f"run source identity refresh with force=true"
            )
        if source_uid and source_uid in seen_uids:
            errors.append(f"Duplicate source_scene_uid in project: {source_uid}")
        if source_uid:
            seen_uids.add(str(source_uid))

        has_geo = bool((manifest.get("geospatial") or {}).get("has_geo"))
        if annotations and not has_geo:
            warnings.append(f"Scene {scene.get('filename') or scene_id} is NO_GEO")
        if annotations and manifest.get("metadata_status") not in {None, "ok"}:
            warnings.append(f"Scene {scene.get('filename') or scene_id} has metadata status {manifest.get('metadata_status')}")

        for annotation in annotations:
            class_id = annotation.get("class_id")
            try:
                class_name = class_names.get(int(class_id), str(class_id)) if class_id is not None else "unknown"
            except (TypeError, ValueError):
                class_name = str(class_id or "unknown")
            class_counts[class_name] += 1
            author_counts[str(annotation.get("annotator_email") or author_email or "unknown")] += 1
        annotation_count += len(annotations)
        image = manifest.get("image") or {}
        source_package = manifest.get("source_package") or {}
        working_view = manifest.get("working_view") or {}
        scenes.append({
            "scene_id": scene_id,
            "source_scene_uid": source_uid,
            "source_scene_candidate_uid": manifest.get("source_scene_candidate_uid"),
            "source_file_sha256": manifest.get("source_file_sha256"),
            "source_file_content_signature": manifest.get("source_file_content_signature"),
            "source_file_content_signature_method": manifest.get("source_file_content_signature_method"),
            "source_identity_method": manifest.get("source_identity_method"),
            "source_identity_strength": identity_strength,
            "provider": source_package.get("provider") or manifest.get("provider"),
            "provider_scene_id": source_package.get("provider_scene_id"),
            "working_grid_uid": working_view.get("working_grid_uid"),
            "source_file_size": manifest.get("source_file_size") or image.get("file_size"),
            "filename": scene.get("filename") or manifest.get("filename"),
            "width": image.get("width"),
            "height": image.get("height"),
            "sensor": manifest.get("sensor"),
            "acquisition_datetime_utc": manifest.get("acquisition_datetime_utc"),
            "has_geo": has_geo,
            "annotation_count": len(annotations),
        })

    if annotation_count == 0:
        errors.append("Project has no source annotations")

    # The scope claims authority for one owner. Annotations owned by someone else
    # (imported, or taken over) are still exported, but fall outside that claim —
    # surface it rather than letting the manager assume the package covers them.
    foreign_owners = sorted(
        owner for owner in author_counts
        if author_email and owner and owner != author_email
    )
    if foreign_owners:
        warnings.append(
            f"Project contains annotations owned by {', '.join(foreign_owners)}; "
            f"the package scope claims authority only for {author_email}"
        )

    package_id = uuid.uuid4().hex
    export_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    project_slug = safe_filename_component(project.get("name") or project_id, "project")
    author_slug = safe_filename_component(author_email or "analyst", "analyst")

    return {
        "package_id": package_id,
        "suggested_filename": (
            f"GeoTileAnnotations_{project_slug}_{author_slug}_"
            f"{export_timestamp}_{package_id[:8]}.zip"
        ),
        "can_export": not errors,
        "annotator_email": author_email,
        "scene_count": len(scenes),
        "annotation_count": annotation_count,
        "class_count": len(class_counts),
        "class_counts": dict(sorted(class_counts.items())),
        "author_counts": dict(sorted(author_counts.items())),
        "geo_scene_count": sum(1 for item in scenes if item["has_geo"]),
        "no_geo_scene_count": sum(1 for item in scenes if not item["has_geo"]),
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "scenes": scenes,
    }


def package_history(project_id: str) -> list[dict[str, Any]]:
    """Exports made from this project, newest last. Drives the supersedes chain."""
    history = load_json(project_id, PACKAGE_HISTORY_NAME, default={}) or {}
    entries = history.get("packages") if isinstance(history, dict) else None
    return [item for item in (entries or []) if isinstance(item, dict)]


def _auto_supersedes(history: list[dict[str, Any]], scene_uids: set[str]) -> str | None:
    """Newest earlier package that covered any of the same scenes.

    Only an overlapping package is superseded: a later export covering a disjoint set
    of scenes replaces nothing, and claiming otherwise would misrepresent the chain.
    """
    for entry in reversed(history):
        previous = {str(uid) for uid in (entry.get("scene_uids") or [])}
        if previous & scene_uids and entry.get("package_id"):
            return str(entry["package_id"])
    return None


def _record_package_export(project_id: str, entry: dict[str, Any]) -> None:
    history = package_history(project_id)
    history.append(entry)
    save_json(project_id, PACKAGE_HISTORY_NAME, {
        "schema_name": "geotile_annotation_package_history",
        "schema_version": 1,
        "project_id": project_id,
        "packages": history,
    })


def save_annotation_package(
    project_id: str,
    output_path: str | Path,
    package_id: str | None = None,
    supersedes_package_id: str | None = None,
) -> dict[str, Any]:
    preview = preview_annotation_package(project_id)
    if not preview["can_export"]:
        raise ValueError("; ".join(preview["errors"]))

    destination = Path(output_path).expanduser()
    if not destination.is_absolute():
        raise ValueError("output_path must be an absolute path")
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)

    project = load_json(project_id, "project", default={})
    profile = project.get("profile") or {}
    classes = load_json(project_id, "classes", default=[])
    package_id = package_id or preview["package_id"]
    created_at = datetime.now(timezone.utc).isoformat()
    scene_uids = {str(item["source_scene_uid"]) for item in preview["scenes"] if item.get("source_scene_uid")}
    if supersedes_package_id is None:
        supersedes_package_id = _auto_supersedes(package_history(project_id), scene_uids)

    with tempfile.TemporaryDirectory(prefix="geotile-annotation-package-") as temp_dir:
        root = Path(temp_dir)
        export_source_annotations_geoparquet(project_id, root / "annotations_wgs84.geoparquet")
        summary_path = root / "annotation_summary.json"
        annotation_summary = _read_json(summary_path)
        annotation_summary["files"] = {
            "annotations_wgs84": "annotations_wgs84.geoparquet",
            "annotations_native": "annotations_native.geoparquet",
            "annotation_summary": "annotation_summary.json",
        }
        _write_json(summary_path, annotation_summary)
        _write_json(root / "project_profile.json", profile)
        _write_json(root / "classes.json", classes)

        scene_entries: list[dict[str, Any]] = []
        scene_index_entries: list[dict[str, Any]] = []
        for item in preview["scenes"]:
            scene_id = str(item["scene_id"])
            source_uid = str(item["source_scene_uid"])
            safe_uid = safe_scene_uid(source_uid)
            manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
            annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
            manifest_rel = f"scene_manifests/{safe_uid}.scene_manifest.json"
            annotations_rel = f"annotations/{safe_uid}.annotations.json"
            manifest_path = root / manifest_rel
            annotations_path = root / annotations_rel
            _write_json(manifest_path, manifest)
            _write_json(annotations_path, annotations)
            scene_entry = {
                **item,
                "scene_manifest_file": manifest_rel,
                "annotations_file": annotations_rel,
                "annotations_sha256": file_sha256(annotations_path),
            }
            scene_entries.append(scene_entry)
            scene_index_entries.append({
                key: scene_entry.get(key)
                for key in (
                    "scene_id", "source_scene_uid", "source_scene_candidate_uid",
                    "source_file_sha256", "source_file_content_signature",
                    "source_file_content_signature_method", "source_identity_method",
                    "source_identity_strength", "source_file_size", "filename",
                    "provider", "provider_scene_id", "working_grid_uid",
                    "width", "height", "sensor", "acquisition_datetime_utc", "has_geo",
                    "annotation_count", "scene_manifest_file", "annotations_file",
                )
            })

        scenes_index = {
            "schema_name": "geotile_annotation_package_scenes_index",
            "schema_version": 1,
            "scene_count": len(scene_index_entries),
            "scenes": scene_index_entries,
        }
        _write_json(root / "scenes_index.json", scenes_index)

        package_manifest = {
            "schema_name": "geotile_annotation_package",
            "schema_version": ANNOTATION_PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "created_at": created_at,
            "app_version": APP_VERSION,
            # What this package claims authority over: one owner, this set of scenes,
            # as of this instant. Import uses it to detect conflicting selections and
            # (from T5) to scope replacement. Explicit, never inferred from contents.
            "scope": {
                "owner_email": preview["annotator_email"],
                "scene_uids": sorted(scene_uids),
                "authoritative_as_of": created_at,
                "supersedes_package_id": supersedes_package_id,
            },
            # Annotations the owner has given up authority over (e.g. taken over by a
            # manager). Populated by the review loop; empty until then.
            "superseded_source_annotation_ids": [],
            "source_project_id": project_id,
            "source_project_name": project.get("name"),
            "archive_filename": destination.name,
            "annotator_email": preview["annotator_email"],
            "project_profile_hash": canonical_sha256(profile),
            "classes_hash": canonical_sha256(classes),
            "scene_count": preview["scene_count"],
            "annotation_count": preview["annotation_count"],
            "class_count": preview["class_count"],
            "warnings": preview["warnings"],
            "scenes": scene_entries,
        }
        _write_json(root / "annotation_package_manifest.json", package_manifest)
        (root / "README_ANNOTATIONS.md").write_text(
            _annotation_package_readme(package_manifest),
            encoding="utf-8",
        )
        write_checksums(root)

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())

    # Recorded only after the archive is on disk, so a failed export never claims a
    # link in the chain that no package actually occupies.
    _record_package_export(project_id, {
        "package_id": package_id,
        "created_at": created_at,
        "owner_email": preview["annotator_email"],
        "scene_uids": sorted(scene_uids),
        "supersedes_package_id": supersedes_package_id,
        "annotation_count": preview["annotation_count"],
        "archive_filename": destination.name,
    })

    return {
        "status": "ok",
        "output_path": str(destination),
        "package_id": package_id,
        "supersedes_package_id": supersedes_package_id,
        **{key: preview[key] for key in (
            "annotator_email", "scene_count", "annotation_count", "class_count",
            "geo_scene_count", "no_geo_scene_count", "warnings",
        )},
    }


def write_checksums(root: Path) -> None:
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{file_sha256(path)}  {path.relative_to(root).as_posix()}")
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_scene_uid(source_uid: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", source_uid).strip("._-")
    return value or hashlib.sha256(source_uid.encode("utf-8")).hexdigest()


def safe_filename_component(value: str, fallback: str, max_length: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", normalized).strip("_-.")
    component = re.sub(r"_+", "_", component)
    return (component or fallback)[:max_length].rstrip("_-") or fallback


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _annotation_package_readme(manifest: dict[str, Any]) -> str:
    return f"""# GeoTile Label annotation package

This package contains source-scene annotations only. It does not contain satellite scenes,
tiles, predictions, dataset runs or contextual tables.

- Package ID: `{manifest['package_id']}`
- Project: `{manifest.get('source_project_name') or manifest.get('source_project_id')}`
- Annotator: `{manifest.get('annotator_email')}`
- Scenes: {manifest.get('scene_count', 0)}
- Annotations: {manifest.get('annotation_count', 0)}

Use **Import annotations into this project** in GeoTile Label to validate checksums, match
source scenes and classes, preview changes and apply the import. GeoParquet files can be
opened directly in QGIS for independent quality control.
"""
