"""Manager → analyst review channel: scene verdicts travelling as a signed-ish ZIP.

A review package carries **verdicts only**. It never contains geometry and importing
one never modifies an annotation — the verdict and any pins land on the scene record.
That is what makes the return leg of the loop safe to run at any time.

Scope mirrors the annotation package: one owner, a set of scenes. Each scene carries a
`review_round`; an incoming verdict from an older round is ignored, so re-importing an
old package can never undo a newer decision.
"""

from __future__ import annotations

import json
import tempfile
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json, save_scene_json
from models.project import APP_VERSION
from services.annotation_package import (
    canonical_sha256,
    file_sha256,
    safe_filename_component,
    write_checksums,
)
from services.annotation_import import (
    AnnotationPackageError,
    compare_source_identity,
    read_zip_json,
    validate_zip_member,
    verify_package_checksums,
)
from services.scene_identity import manifest_identity_strength

REVIEW_PACKAGE_SCHEMA_VERSION = 1
REVIEW_STATUSES = ("none", "accepted", "rejected", "needs_fix")
MAX_REVIEW_PACKAGE_FILES = 5_000
MAX_REVIEW_PACKAGE_BYTES = 32 * 1024 * 1024


class ReviewPackageError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _annotation_owners(project_id: str, scene_id: str) -> set[str]:
    return {
        str(item.get("annotator_email") or "")
        for item in load_scene_json(project_id, scene_id, "annotations", default=[])
        if item.get("annotator_email")
    }


def set_scene_review(
    project_id: str,
    scene_id: str,
    *,
    status: str,
    comment: str | None = None,
    pins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record a verdict on one scene and bump its review round."""
    if status not in REVIEW_STATUSES:
        raise ReviewPackageError(f"Unknown review status: {status}")
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    if not scene:
        raise FileNotFoundError("Scene not found")

    reviewer = ((load_json(project_id, "project", default={}) or {}).get("profile") or {}).get(
        "labeling_author_email"
    )
    known_ids = {
        str(item.get("source_annotation_id") or item.get("id"))
        for item in load_scene_json(project_id, scene_id, "annotations", default=[])
        if item.get("source_annotation_id") or item.get("id")
    }
    normalized_pins: list[dict[str, Any]] = []
    for pin in pins or []:
        annotation_id = str(pin.get("source_annotation_id") or "").strip()
        if not annotation_id:
            continue
        if annotation_id not in known_ids:
            raise ReviewPackageError(f"Pinned annotation {annotation_id} is not in this scene")
        normalized_pins.append({
            "source_annotation_id": annotation_id,
            "comment": str(pin.get("comment") or "").strip() or None,
        })

    scene["review_status"] = status
    scene["review_comment"] = (comment or "").strip() or None
    scene["reviewed_by"] = reviewer
    scene["reviewed_at"] = _utc_now()
    scene["review_round"] = int(scene.get("review_round") or 0) + 1
    scene["review_pins"] = normalized_pins
    save_scene_json(project_id, scene_id, "scene", scene)
    return scene


def preview_review_package(project_id: str, owner_email: str) -> dict[str, Any]:
    """Scenes with a verdict that hold work by ``owner_email``."""
    owner = str(owner_email or "").strip().lower()
    project = load_json(project_id, "project", default={})
    reviewer = (project.get("profile") or {}).get("labeling_author_email")
    errors: list[str] = []
    warnings: list[str] = []
    scenes: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    pin_count = 0

    # owner puste = tryb „Wszystkie recenzje": nie filtrujemy po analityku (poniższy filtr
    # `if owner and ...` sam się wtedy nie wykonuje), więc paczka obejmuje wszystkie
    # sprawdzone sceny. Import u każdego analityka dopasuje tylko jego sceny po
    # source_scene_uid, resztę zgłosi jako nieszkodliwe ostrzeżenie.
    if not reviewer:
        errors.append("Project labeling author email is missing")

    for scene_id in list_scene_ids(project_id):
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        status = str(scene.get("review_status") or "none")
        if status == "none":
            continue
        if owner and owner not in _annotation_owners(project_id, scene_id):
            continue
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        source_uid = manifest.get("source_scene_uid")
        identity_strength = manifest_identity_strength(manifest)
        identity_status = manifest.get("source_identity_status") or (
            manifest.get("source_identity") or {}
        ).get("status")
        if (
            not source_uid
            or identity_strength != "exact"
            or identity_status not in {None, "complete"}
        ):
            errors.append(
                f"Scene {scene.get('filename') or scene_id} has no exact, current source identity; "
                f"run source identity refresh with force=true"
            )
            continue
        pins = [dict(item) for item in (scene.get("review_pins") or [])]
        pin_count += len(pins)
        status_counts[status] += 1
        scenes.append({
            "scene_id": scene_id,
            "source_scene_uid": str(source_uid),
            "source_scene_fingerprint": (manifest.get("source_identity") or {}).get("source_scene_fingerprint"),
            "source_file_sha256": manifest.get("source_file_sha256"),
            "source_identity_method": manifest.get("source_identity_method"),
            "source_identity_strength": identity_strength,
            "filename": scene.get("filename") or manifest.get("filename"),
            "review_status": status,
            "review_comment": scene.get("review_comment"),
            "reviewed_by": scene.get("reviewed_by") or reviewer,
            "reviewed_at": scene.get("reviewed_at"),
            "review_round": int(scene.get("review_round") or 0),
            "pins": pins,
        })

    if not scenes and not errors:
        errors.append("No reviewed scenes for this analyst" if owner else "No reviewed scenes to export")
    if any(item["review_status"] == "needs_fix" and not item["review_comment"] for item in scenes):
        warnings.append("Some scenes are marked needs_fix without a comment")

    package_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "package_id": package_id,
        "suggested_filename": (
            f"GeoTileReview_{safe_filename_component(project.get('name') or project_id, 'project')}_"
            f"{safe_filename_component(owner or 'all', 'all')}_{stamp}_{package_id[:8]}.zip"
        ),
        "can_export": not errors,
        "reviewer_email": reviewer,
        "owner_email": owner,
        "scene_count": len(scenes),
        "pin_count": pin_count,
        "status_counts": dict(sorted(status_counts.items())),
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "scenes": scenes,
    }


def save_review_package(
    project_id: str,
    output_path: str | Path,
    owner_email: str,
    package_id: str | None = None,
) -> dict[str, Any]:
    preview = preview_review_package(project_id, owner_email)
    if not preview["can_export"]:
        raise ReviewPackageError("; ".join(preview["errors"]))

    destination = Path(output_path).expanduser()
    if not destination.is_absolute():
        raise ReviewPackageError("output_path must be an absolute path")
    if destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)

    project = load_json(project_id, "project", default={})
    package_id = package_id or preview["package_id"]
    created_at = _utc_now()

    scene_rows = [
        {key: item[key] for key in (
            "source_scene_uid", "source_scene_fingerprint", "source_file_sha256",
            "source_identity_method", "source_identity_strength",
            "filename", "review_status", "review_comment",
            "reviewed_by", "reviewed_at", "review_round",
        )}
        for item in preview["scenes"]
    ]
    pins = [
        {
            "source_scene_uid": item["source_scene_uid"],
            "source_annotation_id": pin["source_annotation_id"],
            "comment": pin.get("comment"),
        }
        for item in preview["scenes"]
        for pin in item["pins"]
    ]

    with tempfile.TemporaryDirectory(prefix="geotile-review-package-") as temp_dir:
        root = Path(temp_dir)
        scenes_index = {
            "schema_name": "geotile_review_package_scenes_index",
            "schema_version": 1,
            "scene_count": len(scene_rows),
            "scenes": scene_rows,
        }
        _write_json(root / "scenes_index.json", scenes_index)
        _write_json(root / "pins.json", {"pin_count": len(pins), "pins": pins})

        manifest = {
            "schema_name": "geotile_review_package",
            "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "created_at": created_at,
            "app_version": APP_VERSION,
            "reviewer_email": preview["reviewer_email"],
            "source_project_id": project_id,
            "source_project_name": project.get("name"),
            "archive_filename": destination.name,
            "scope": {
                "owner_email": preview["owner_email"],
                "scene_uids": sorted({row["source_scene_uid"] for row in scene_rows}),
            },
            "scene_count": len(scene_rows),
            "pin_count": len(pins),
            "status_counts": preview["status_counts"],
            "scenes_hash": canonical_sha256(scenes_index),
            "warnings": preview["warnings"],
        }
        _write_json(root / "review_package_manifest.json", manifest)
        (root / "README_REVIEW.md").write_text(_readme(manifest), encoding="utf-8")
        write_checksums(root)

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())

    return {
        "status": "ok",
        "output_path": str(destination),
        "package_id": package_id,
        "reviewer_email": preview["reviewer_email"],
        "owner_email": preview["owner_email"],
        "scene_count": preview["scene_count"],
        "pin_count": preview["pin_count"],
        "warnings": preview["warnings"],
    }


def load_review_package(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ReviewPackageError("Package path does not exist or is not absolute")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_REVIEW_PACKAGE_FILES:
            raise ReviewPackageError("Package contains too many files")
        if sum(info.file_size for info in infos) > MAX_REVIEW_PACKAGE_BYTES:
            raise ReviewPackageError("Package is too large")
        names = set()
        for info in infos:
            try:
                validate_zip_member(info)
            except AnnotationPackageError as exc:
                raise ReviewPackageError(str(exc)) from exc
            if not info.is_dir():
                if info.filename in names:
                    raise ReviewPackageError(f"Duplicate ZIP entry: {info.filename}")
                names.add(info.filename)

        required = {
            "review_package_manifest.json", "scenes_index.json", "pins.json",
            "README_REVIEW.md", "SHA256SUMS.txt",
        }
        missing = sorted(required - names)
        if missing:
            raise ReviewPackageError(f"Package is missing required files: {', '.join(missing)}")
        try:
            verify_package_checksums(archive, names)
        except AnnotationPackageError as exc:
            raise ReviewPackageError(str(exc)) from exc

        manifest = read_zip_json(archive, "review_package_manifest.json")
        if manifest.get("schema_name") != "geotile_review_package":
            raise ReviewPackageError("Unsupported review package schema")
        if manifest.get("schema_version") != REVIEW_PACKAGE_SCHEMA_VERSION:
            raise ReviewPackageError(
                f"Unsupported review package version {manifest.get('schema_version')}"
            )
        scenes_index = read_zip_json(archive, "scenes_index.json")
        if manifest.get("scenes_hash") != canonical_sha256(scenes_index):
            raise ReviewPackageError("scenes_hash does not match scenes_index.json")
        scenes = scenes_index.get("scenes") or []
        if len(scenes) != int(manifest.get("scene_count", -1)):
            raise ReviewPackageError("Scene count does not match package manifest")
        for row in scenes:
            if str(row.get("review_status")) not in REVIEW_STATUSES:
                raise ReviewPackageError(f"Unknown review status: {row.get('review_status')}")

        return {
            "path": path.resolve(strict=False),
            "manifest": manifest,
            "scenes": scenes,
            "pins": (read_zip_json(archive, "pins.json") or {}).get("pins") or [],
        }


def _target_scene_identities(project_id: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if manifest:
            result[scene_id] = manifest
    return result


def analyze_review_packages(project_id: str, package_paths: list[str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    packages: list[dict[str, Any]] = []
    package_results: list[dict[str, Any]] = []
    for path_string in package_paths:
        try:
            package = load_review_package(path_string)
            packages.append(package)
            package_results.append({
                "path": str(package["path"]),
                "package_id": package["manifest"].get("package_id"),
                "reviewer_email": package["manifest"].get("reviewer_email"),
                "owner_email": (package["manifest"].get("scope") or {}).get("owner_email"),
                "scene_count": package["manifest"].get("scene_count", 0),
                "status": "valid",
            })
        except (OSError, zipfile.BadZipFile, ReviewPackageError, json.JSONDecodeError) as exc:
            errors.append(f"{Path(path_string).name}: {exc}")
            package_results.append({"path": path_string, "status": "invalid", "error": str(exc)})

    target_identities = _target_scene_identities(project_id)
    pins_by_uid: dict[str, list[dict[str, Any]]] = {}
    for package in packages:
        for pin in package["pins"]:
            pins_by_uid.setdefault(str(pin.get("source_scene_uid")), []).append(pin)

    updates: list[dict[str, Any]] = []
    missing_scenes: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for package in packages:
        for row in package["scenes"]:
            source_uid = str(row.get("source_scene_uid") or "")
            candidates = [
                scene_id
                for scene_id, manifest in target_identities.items()
                if compare_source_identity(row, manifest) == "exact_match"
            ]
            target_scene_id = candidates[0] if len(candidates) == 1 else None
            entry = {
                "package_id": package["manifest"].get("package_id"),
                "source_scene_uid": source_uid,
                "filename": row.get("filename"),
                "review_status": row.get("review_status"),
                "review_comment": row.get("review_comment"),
                "reviewed_by": row.get("reviewed_by") or package["manifest"].get("reviewer_email"),
                "reviewed_at": row.get("reviewed_at"),
                "review_round": int(row.get("review_round") or 0),
                "target_scene_id": target_scene_id,
                "candidate_scene_ids": candidates,
                "pins": pins_by_uid.get(source_uid, []),
            }
            if not target_scene_id:
                missing_scenes.append(entry)
                continue
            scene = load_scene_json(project_id, target_scene_id, "scene", default={})
            local_round = int(scene.get("review_round") or 0)
            if entry["review_round"] <= local_round:
                # A verdict from an older or equal round must never overwrite a newer
                # one — this is what makes re-importing a stale package harmless.
                entry["local_round"] = local_round
                stale.append(entry)
                continue
            entry["previous_status"] = scene.get("review_status") or "none"
            updates.append(entry)

    if missing_scenes:
        warnings.append(f"{len(missing_scenes)} reviewed scenes were not found in this project")
    if stale:
        warnings.append(f"{len(stale)} verdicts are from an older review round and were skipped")

    status_counts = Counter(str(item["review_status"]) for item in updates)
    return {
        "package_count": len(package_paths),
        "valid_package_count": len(packages),
        "packages": package_results,
        "can_apply": bool(updates) and not errors,
        "update_count": len(updates),
        "missing_scene_count": len(missing_scenes),
        "stale_verdict_count": len(stale),
        "pin_count": sum(len(item["pins"]) for item in updates),
        "status_counts": dict(sorted(status_counts.items())),
        "updates": updates,
        "missing_scenes": missing_scenes,
        "stale_verdicts": stale,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }


def preview_review_import(project_id: str, package_paths: list[str]) -> dict[str, Any]:
    if not package_paths:
        raise ReviewPackageError("Select at least one review package")
    return analyze_review_packages(project_id, package_paths)


def apply_review_import(project_id: str, package_paths: list[str]) -> dict[str, Any]:
    """Write verdicts onto local scenes. Annotations are never read or modified."""
    analysis = analyze_review_packages(project_id, package_paths)
    if analysis["errors"]:
        raise ReviewPackageError("; ".join(analysis["errors"]))

    applied: list[dict[str, Any]] = []
    for entry in analysis["updates"]:
        scene_id = entry["target_scene_id"]
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        scene["review_status"] = entry["review_status"]
        scene["review_comment"] = entry["review_comment"]
        scene["reviewed_by"] = entry["reviewed_by"]
        scene["reviewed_at"] = entry["reviewed_at"]
        scene["review_round"] = entry["review_round"]
        scene["review_pins"] = [
            {
                "source_annotation_id": pin.get("source_annotation_id"),
                "comment": pin.get("comment"),
            }
            for pin in entry["pins"]
        ]
        save_scene_json(project_id, scene_id, "scene", scene)
        applied.append({
            "scene_id": scene_id,
            "review_status": entry["review_status"],
            "review_round": entry["review_round"],
        })

    return {
        **{key: value for key, value in analysis.items() if key != "updates"},
        "status": "applied" if applied else "no_changes",
        "applied_at": _utc_now(),
        "applied_scene_count": len(applied),
        "applied": applied,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _readme(manifest: dict[str, Any]) -> str:
    scope = manifest.get("scope") or {}
    return f"""# GeoTile Label review package

Manager feedback on labeling work. This package contains **verdicts only** — no
scenes, no annotations, no geometry. Importing it flags scenes in your project; it
never changes an annotation.

- Package ID: `{manifest['package_id']}`
- Reviewer: `{manifest.get('reviewer_email')}`
- Addressed to: `{scope.get('owner_email')}`
- Scenes: {manifest.get('scene_count', 0)}
- Pins: {manifest.get('pin_count', 0)}

Use **Import review** in GeoTile Label. Verdicts older than the round already
recorded on a scene are skipped, so importing an old package is harmless.
"""
