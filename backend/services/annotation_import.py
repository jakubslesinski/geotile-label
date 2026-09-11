"""Validate and import analyst annotation packages into an existing project."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from db.storage import (
    _atomic_write_bytes,
    list_scene_ids,
    load_json,
    load_scene_json,
    project_dir,
    track_project_mutation,
)
from services.annotation_package import (
    SUPPORTED_ANNOTATION_PACKAGE_VERSIONS,
    canonical_sha256,
    file_sha256,
)
from services.attribute_engine import recompute_scene_attributes
from services.scene_identity import is_full_sha256, manifest_identity_strength

MAX_PACKAGE_FILES = 20_000
MAX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
# Share of an owner's annotations in a scene whose removal demands an explicit
# per-scene decision from the manager instead of riding along with "apply".
DESTRUCTIVE_REMOVAL_RATIO = 0.5


def annotation_owner(annotation: dict[str, Any], fallback: str = "") -> str:
    """Current owner of an annotation.

    ``annotator_email`` is the mutable owner (a manager edit reassigns it);
    ``created_by`` keeps the original author. Unattributed annotations get "", which
    matches no package owner — so they are never touched by a scoped replacement.
    """
    return str(annotation.get("annotator_email") or fallback or "")


class AnnotationPackageError(ValueError):
    pass


def preview_annotation_import(
    project_id: str,
    package_paths: list[str],
    *,
    identity_policy: str = "exact_only",
) -> dict[str, Any]:
    if not package_paths:
        raise ValueError("Select at least one annotation package")
    preview_id = uuid.uuid4().hex
    analysis = analyze_annotation_packages(
        project_id,
        package_paths,
        create_missing_classes=False,
        identity_policy=identity_policy,
    )
    analysis.update({
        "preview_id": preview_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_state_hash": project_state_hash(project_id),
        "package_paths": [str(Path(path).expanduser().resolve(strict=False)) for path in package_paths],
        "package_hashes": {
            str(Path(path).expanduser().resolve(strict=False)): file_sha256(Path(path).expanduser())
            for path in package_paths
            if Path(path).expanduser().is_file()
        },
    })
    preview_path = _preview_path(project_id, preview_id)
    _atomic_write_json(preview_path, analysis)
    return public_import_report(analysis)


def select_applicable_plans(
    plans: list[dict[str, Any]],
    accepted_scene_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Which scene plans this apply actually writes.

    ``None`` means "everything safe": plans that would destroy a large share of the
    owner's work are held back until the manager names their scene explicitly, so a
    plain apply can never silently wipe a scene. Blocked plans are never applied.
    """
    applicable = [item for item in plans if not item["block_reason"]]
    if accepted_scene_ids is None:
        return [item for item in applicable if not item["requires_confirmation"]]
    allowed = {str(value) for value in accepted_scene_ids}
    return [item for item in applicable if item["target_scene_id"] in allowed]


def apply_annotation_import(
    project_id: str,
    preview_id: str,
    *,
    create_missing_classes: bool = False,
    accepted_scene_ids: list[str] | None = None,
) -> dict[str, Any]:
    preview_path = _preview_path(project_id, preview_id)
    if not preview_path.is_file():
        raise FileNotFoundError("Annotation import preview not found")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    if preview.get("project_state_hash") != project_state_hash(project_id):
        raise RuntimeError("Project changed after preview. Generate a new import preview.")
    for path_string, expected_hash in (preview.get("package_hashes") or {}).items():
        path = Path(path_string)
        if not path.is_file() or file_sha256(path) != expected_hash:
            raise RuntimeError("An annotation package changed after preview. Generate a new preview.")

    package_paths = preview.get("package_paths") or []
    analysis = analyze_annotation_packages(
        project_id,
        package_paths,
        create_missing_classes=create_missing_classes,
        include_plans=True,
        identity_policy=str(preview.get("identity_policy") or "exact_only"),
    )
    if analysis["errors"]:
        raise AnnotationPackageError("; ".join(analysis["errors"]))

    import_id = uuid.uuid4().hex
    import_root = project_dir(project_id) / "annotation_imports" / import_id
    snapshot_root = import_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    updates: dict[Path, Any] = {}
    classes_path = project_dir(project_id) / "classes.json"
    classes = analysis.pop("_target_classes")
    if create_missing_classes and analysis.get("created_classes"):
        updates[classes_path] = classes

    plans = analysis.pop("_scene_plans")
    accepted_plans = select_applicable_plans(plans, accepted_scene_ids)
    plans_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for plan in accepted_plans:
        plans_by_scene[plan["target_scene_id"]].append(plan)

    for scene_id, scene_plan_group in plans_by_scene.items():
        annotations_path = project_dir(project_id) / "scenes" / scene_id / "annotations.json"
        existing = load_scene_json(project_id, scene_id, "annotations", default=[])
        # Only the owners actually being replaced are cleared. Everything else in the
        # scene — the manager's own work, another analyst's, unattributed rows — is
        # carried over untouched, which is what makes wholesale replacement safe.
        replaced_owners = {
            plan["owner_email"] for plan in scene_plan_group if plan["mode"] == "replace"
        }
        merged = [item for item in existing if annotation_owner(item) not in replaced_owners]
        for plan in scene_plan_group:
            payload = plan["_incoming"] if plan["mode"] == "replace" else plan["_added"]
            for annotation in payload:
                annotation["import_id"] = import_id
                annotation["source_package_id"] = plan["package_id"]
            merged.extend(payload)
        updates[annotations_path] = merged

    original_bytes: dict[Path, bytes | None] = {}
    with track_project_mutation(project_id, "annotations.import_batch"):
        try:
            for path in updates:
                original_bytes[path] = path.read_bytes() if path.exists() else None
                snapshot_name = f"{path.parent.name}.{path.name}" if path.name == "annotations.json" else path.name
                if path.exists():
                    shutil.copy2(path, snapshot_root / snapshot_name)
            for path, value in updates.items():
                _atomic_write_json(path, value)
        except Exception:
            for path, content in original_bytes.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write_bytes(path, content)
            raise

        recompute_warnings: list[str] = []
        for scene_id in plans_by_scene:
            try:
                recompute_scene_attributes(project_id, scene_id)
                scene_path = project_dir(project_id) / "scenes" / scene_id / "scene.json"
                scene = load_scene_json(project_id, scene_id, "scene", default={})
                scene["annotation_count"] = len(load_scene_json(project_id, scene_id, "annotations", default=[]))
                _atomic_write_json(scene_path, scene)
            except Exception as exc:
                recompute_warnings.append(f"Could not recompute attributes for scene {scene_id}: {exc}")

    applied_added = sum(plan["added_count"] for plan in accepted_plans)
    applied_changed = sum(plan["changed_count"] for plan in accepted_plans)
    applied_removed = sum(plan["removed_count"] for plan in accepted_plans)
    accepted_keys = {(plan["target_scene_id"], plan["owner_email"]) for plan in accepted_plans}
    skipped_plans = [
        plan for plan in plans
        if (plan["target_scene_id"], plan["owner_email"]) not in accepted_keys
    ]

    report = {
        **public_import_report(analysis),
        "import_id": import_id,
        "preview_id": preview_id,
        "status": "imported" if (applied_added or applied_changed or applied_removed) else "no_changes",
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "imported_annotation_count": applied_added,
        "updated_annotation_count": applied_changed,
        "deleted_annotation_count": applied_removed,
        "applied_scene_count": len(plans_by_scene),
        "skipped_scene_plans": [
            {
                "target_scene_id": plan["target_scene_id"],
                "owner_email": plan["owner_email"],
                "reason": plan["block_reason"] or (
                    "awaiting explicit confirmation" if plan["requires_confirmation"]
                    else "not selected"
                ),
            }
            for plan in skipped_plans
        ],
        "recompute_warnings": recompute_warnings,
    }
    source_manifests = analysis.pop("_package_manifests", [])
    import_manifest = {
        "schema_name": "geotile_annotation_import",
        "schema_version": 1,
        "import_id": import_id,
        "preview_id": preview_id,
        "created_at": report["applied_at"],
        "package_hashes": preview.get("package_hashes") or {},
        "identity_policy": str(preview.get("identity_policy") or "exact_only"),
        "create_missing_classes": create_missing_classes,
        "snapshot_directory": "snapshots",
    }
    _atomic_write_json(import_root / "import_manifest.json", import_manifest)
    _atomic_write_json(import_root / "import_report.json", report)
    _atomic_write_json(import_root / "source_package_manifest.json", {"packages": source_manifests})
    return report


def get_annotation_import_report(project_id: str, report_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "annotation_imports" / report_id / "import_report.json"
    if not path.is_file():
        raise FileNotFoundError("Annotation import report not found")
    return json.loads(path.read_text(encoding="utf-8"))


def analyze_annotation_packages(
    project_id: str,
    package_paths: list[str],
    *,
    create_missing_classes: bool,
    include_plans: bool = False,
    identity_policy: str = "exact_only",
) -> dict[str, Any]:
    if identity_policy not in {"exact_only", "allow_probable"}:
        raise ValueError(f"Unsupported annotation import identity policy: {identity_policy}")
    allow_probable = identity_policy == "allow_probable"
    errors: list[str] = []
    warnings: list[str] = []
    packages: list[dict[str, Any]] = []
    package_results: list[dict[str, Any]] = []
    for path_string in package_paths:
        try:
            package = load_annotation_package(path_string)
            packages.append(package)
            package_results.append({
                "path": str(package["path"]),
                "package_id": package["manifest"].get("package_id"),
                "annotator_email": package["manifest"].get("annotator_email"),
                "status": "valid",
                "scene_count": package["manifest"].get("scene_count", 0),
                "annotation_count": package["manifest"].get("annotation_count", 0),
                "schema_version": package["manifest"].get("schema_version"),
                "owner_email": package["scope"]["owner_email"],
                "authoritative_as_of": package["scope"]["authoritative_as_of"],
                "supersedes_package_id": package["scope"]["supersedes_package_id"],
                "additive_only": package["scope"]["additive_only"],
            })
        except (OSError, zipfile.BadZipFile, AnnotationPackageError, json.JSONDecodeError) as exc:
            errors.append(f"{Path(path_string).name}: {exc}")
            package_results.append({"path": path_string, "status": "invalid", "error": str(exc)})

    # The same package selected twice (or two copies of one file) is a duplicate
    # selection, not a conflict — keep one and say so.
    unique_packages: list[dict[str, Any]] = []
    seen_package_ids: set[str] = set()
    for package in packages:
        identifier = str(package["manifest"].get("package_id") or "")
        if identifier and identifier in seen_package_ids:
            warnings.append(f"Package {identifier[:8]} was selected more than once; using it once")
            continue
        seen_package_ids.add(identifier)
        unique_packages.append(package)
    # Deterministic order (oldest scope first) so which package counts as "existing"
    # and which as "changed" never depends on file-selection order.
    unique_packages.sort(key=lambda item: (
        str(item["scope"].get("authoritative_as_of") or ""),
        str(item["manifest"].get("package_id") or ""),
    ))
    packages = unique_packages
    errors.extend(detect_scope_conflicts(packages))

    target_scenes = build_target_scene_cache(project_id)
    target_classes = [dict(item) for item in load_json(project_id, "classes", default=[])]
    class_lookup, ambiguous_target_classes = build_class_lookup(target_classes)
    missing_class_defs: dict[str, dict[str, Any]] = {}
    for package in packages:
        package_classes = {
            str(item.get("id")): item
            for item in package["classes"]
            if item.get("id") is not None
        }
        for package_scene in package["scenes"]:
            target_scene_id, _method, _confidence, _candidates = match_scene(
                package_scene,
                target_scenes,
                allow_probable=allow_probable,
            )
            if not target_scene_id:
                continue
            for annotation in package_scene["annotations"]:
                item = package_classes.get(str(annotation.get("class_id")))
                normalized = normalize_class_name((item or {}).get("name"))
                if normalized and normalized not in class_lookup and normalized not in ambiguous_target_classes:
                    missing_class_defs.setdefault(normalized, item)

    created_classes: list[dict[str, Any]] = []
    if create_missing_classes:
        next_id = max((int(item.get("id", -1)) for item in target_classes), default=-1) + 1
        for normalized, source_class in sorted(missing_class_defs.items()):
            created = {
                "id": next_id,
                "name": str(source_class.get("name") or normalized).strip(),
                "color": source_class.get("color") or deterministic_color(normalized),
            }
            next_id += 1
            target_classes.append(created)
            created_classes.append(created)
        class_lookup, ambiguous_target_classes = build_class_lookup(target_classes)

    scene_matches: list[dict[str, Any]] = []
    missing_scenes: list[dict[str, Any]] = []
    ambiguous_scenes: list[dict[str, Any]] = []
    incompatible_scenes: list[dict[str, Any]] = []
    approval_required_scenes: list[dict[str, Any]] = []
    missing_classes: set[str] = set()
    ambiguous_classes: set[str] = set(ambiguous_target_classes)
    authors: Counter[str] = Counter()
    # One plan per (target scene, owner). A later package from the same owner for the
    # same scene overwrites the earlier plan — T4 guarantees such a pair is chained,
    # and the newest scope is the authoritative one.
    scene_plans: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates = 0
    changed = 0
    removed_total = 0
    taken_over_total = 0
    out_of_scope_total = 0
    blocked = 0

    for package in packages:
        package_classes = {
            str(item.get("id")): item
            for item in package["classes"]
            if item.get("id") is not None
        }
        package_owner = str(
            package["scope"].get("owner_email") or package["manifest"].get("annotator_email") or ""
        )
        for package_scene in package["scenes"]:
            target_scene_id, method, confidence, candidates = match_scene(
                package_scene,
                target_scenes,
                allow_probable=allow_probable,
            )
            match_entry = {
                "package_id": package["manifest"].get("package_id"),
                "source_scene_uid": package_scene.get("source_scene_uid"),
                "source_filename": package_scene.get("filename"),
                "target_scene_id": target_scene_id,
                "method": method,
                "confidence": confidence,
                "candidate_scene_ids": candidates,
                "annotation_count": len(package_scene["annotations"]),
            }
            # Split the "has candidates but no confident target" cases: a working-grid
            # mismatch (exactly one scene, different working view) and a provider-id-only
            # match are NOT ambiguity — reporting them as "matched more than one" is
            # misleading and hides what the manager should actually do.
            if target_scene_id:
                scene_matches.append(match_entry)
            elif confidence == "incompatible":
                incompatible_scenes.append(match_entry)
            elif confidence == "approval_required":
                approval_required_scenes.append(match_entry)
            elif candidates:
                ambiguous_scenes.append(match_entry)
            else:
                missing_scenes.append(match_entry)

            if not target_scene_id:
                # No target: nothing can be planned, so the scene's annotations are
                # simply blocked. Building a plan keyed on a missing scene would put a
                # phantom entry into the report.
                for annotation in package_scene["annotations"]:
                    authors[annotation_owner(annotation, package_owner) or "unknown"] += 1
                    blocked += 1
                continue

            # v1 declared no scope, so nothing about completeness (and therefore about
            # deletions) can be inferred from it — it stays strictly additive.
            mode = "append" if package["scope"].get("additive_only") else "replace"
            superseded_ids = {
                str(value) for value in (package["manifest"].get("superseded_source_annotation_ids") or [])
            }

            existing_all = (target_scenes.get(target_scene_id) or {}).get("annotations") or []
            existing_by_sid = {
                str(item.get("source_annotation_id") or item.get("id")): item
                for item in existing_all
                if item.get("source_annotation_id") or item.get("id")
            }
            existing_in_scope = {
                sid: item for sid, item in existing_by_sid.items()
                if annotation_owner(item) == package_owner
            }

            incoming: list[dict[str, Any]] = []
            plan_block_reasons: set[str] = set()
            taken_over = 0
            out_of_scope = 0

            for annotation in package_scene["annotations"]:
                source_id = annotation.get("source_annotation_id") or annotation.get("id")
                ann_owner = annotation_owner(annotation, package_owner)
                authors[ann_owner or "unknown"] += 1
                if not source_id:
                    blocked += 1
                    plan_block_reasons.add("an annotation has no stable id")
                    continue
                if ann_owner != package_owner:
                    # The package speaks for exactly one owner; anything else inside it
                    # (e.g. data returned by an earlier review round) is outside its
                    # declared authority and is not re-imported.
                    out_of_scope += 1
                    continue
                if str(source_id) in superseded_ids:
                    taken_over += 1
                    continue
                local = existing_by_sid.get(str(source_id))
                if local is not None and annotation_owner(local) != package_owner:
                    # Ownership was reassigned locally (the manager edited it), so the
                    # owner's older copy must not come back as a duplicate.
                    taken_over += 1
                    continue

                source_class = package_classes.get(str(annotation.get("class_id")))
                normalized_class = normalize_class_name((source_class or {}).get("name"))
                if not normalized_class:
                    errors.append(f"Annotation {source_id} references an undefined class")
                    blocked += 1
                    plan_block_reasons.add("an annotation references an undefined class")
                    continue
                if normalized_class in ambiguous_target_classes:
                    ambiguous_classes.add(normalized_class)
                    blocked += 1
                    plan_block_reasons.add("a class name is ambiguous in this project")
                    continue
                target_class = class_lookup.get(normalized_class)
                if not target_class:
                    missing_classes.add(str((source_class or {}).get("name") or normalized_class))
                    blocked += 1
                    plan_block_reasons.add("a class is missing in this project")
                    continue

                imported = dict(annotation)
                imported["id"] = imported.get("id") or source_id
                imported["source_annotation_id"] = str(source_id)
                imported["scene_id"] = target_scene_id
                imported["class_id"] = target_class["id"]
                imported["annotator_email"] = ann_owner or None
                incoming.append(imported)

            incoming_by_sid = {str(item["source_annotation_id"]): item for item in incoming}
            added_ids = sorted(set(incoming_by_sid) - set(existing_in_scope))
            common_ids = set(incoming_by_sid) & set(existing_in_scope)
            changed_ids = sorted(
                sid for sid in common_ids
                if annotation_fingerprint(existing_in_scope[sid]) != annotation_fingerprint(incoming_by_sid[sid])
            )
            unchanged_ids = sorted(common_ids - set(changed_ids))
            # A v1 package makes no claim about what is missing, so "removed" is only
            # meaningful for a scoped (v2) package.
            removed_ids = sorted(set(existing_in_scope) - set(incoming_by_sid)) if mode == "replace" else []

            before_count = len(existing_in_scope)
            after_count = len(incoming_by_sid) if mode == "replace" else before_count + len(added_ids)
            # Wholesale replacement can wipe a scene. Demand an explicit per-scene
            # decision when it would remove a large share of the owner's work.
            requires_confirmation = bool(
                mode == "replace"
                and removed_ids
                and before_count > 0
                and (after_count == 0 or len(removed_ids) / before_count >= DESTRUCTIVE_REMOVAL_RATIO)
            )
            # Never replace from an incomplete picture: if any incoming annotation was
            # blocked, applying the plan would delete existing work because of a class
            # problem rather than an analyst decision.
            block_reason = ""
            if plan_block_reasons and mode == "replace":
                block_reason = (
                    "not applied because " + "; ".join(sorted(plan_block_reasons))
                    + " — replacing would delete existing annotations based on an incomplete package"
                )

            scene_plans[(target_scene_id, package_owner)] = {
                "target_scene_id": target_scene_id,
                "owner_email": package_owner,
                "package_id": package["manifest"].get("package_id"),
                "source_scene_uid": package_scene.get("source_scene_uid"),
                "source_filename": package_scene.get("filename"),
                "mode": mode,
                "before_count": before_count,
                "after_count": after_count,
                "added_count": len(added_ids),
                "changed_count": len(changed_ids),
                "removed_count": len(removed_ids),
                "unchanged_count": len(unchanged_ids),
                "taken_over_count": taken_over,
                "out_of_scope_count": out_of_scope,
                "requires_confirmation": requires_confirmation,
                "block_reason": block_reason,
                "_incoming": incoming,
                "_added": [incoming_by_sid[sid] for sid in added_ids],
            }

    if missing_scenes:
        warnings.append(f"{len(missing_scenes)} package scenes were not found in the target project")
    if ambiguous_scenes:
        warnings.append(f"{len(ambiguous_scenes)} package scenes matched more than one target scene")
    if incompatible_scenes:
        warnings.append(
            f"{len(incompatible_scenes)} package scenes match a scene prepared with a different "
            f"working view; re-prepare the scene to the same working grid before importing"
        )
    if approval_required_scenes:
        warnings.append(
            f"{len(approval_required_scenes)} package scenes have only heuristic/provider identity "
            f"evidence and need manual confirmation; they were not imported"
        )
    probable_matches = [item for item in scene_matches if item.get("confidence") == "probable"]
    if probable_matches:
        warnings.append(
            f"{len(probable_matches)} package scenes were matched using heuristic identity evidence "
            f"under the explicit allow_probable policy"
        )
    if missing_classes:
        warnings.append(f"Missing target classes: {', '.join(sorted(missing_classes))}")
    if ambiguous_classes:
        warnings.append(f"Ambiguous target classes: {', '.join(sorted(ambiguous_classes))}")

    plans = sorted(
        scene_plans.values(),
        key=lambda item: (item["target_scene_id"], item["owner_email"]),
    )
    applicable = [item for item in plans if not item["block_reason"]]
    duplicates = sum(item["unchanged_count"] for item in plans)
    changed = sum(item["changed_count"] for item in plans)
    added_total = sum(item["added_count"] for item in plans)
    removed_total = sum(item["removed_count"] for item in plans)
    taken_over_total = sum(item["taken_over_count"] for item in plans)
    out_of_scope_total = sum(item["out_of_scope_count"] for item in plans)
    confirm_plans = [item for item in applicable if item["requires_confirmation"]]
    blocked_plans = [item for item in plans if item["block_reason"]]

    changed_append = sum(item["changed_count"] for item in plans if item["mode"] == "append")
    changed_replace = sum(item["changed_count"] for item in plans if item["mode"] == "replace")
    if changed_append:
        warnings.append(
            f"{changed_append} annotations exist in a different version and were NOT updated "
            f"(their package predates package scope and is additive only)"
        )
    if changed_replace:
        warnings.append(f"{changed_replace} annotations will be updated to the package version")
    if removed_total:
        warnings.append(
            f"{removed_total} annotations are absent from the package and will be REMOVED "
            f"from the accepted scenes"
        )
    for item in confirm_plans:
        warnings.append(
            f"Scene {item['source_filename'] or item['target_scene_id']} would drop from "
            f"{item['before_count']} to {item['after_count']} annotations owned by "
            f"{item['owner_email'] or 'unknown'} — confirm this scene explicitly to apply it"
        )
    for item in blocked_plans:
        warnings.append(
            f"Scene {item['source_filename'] or item['target_scene_id']} {item['block_reason']}"
        )
    if taken_over_total:
        warnings.append(
            f"{taken_over_total} annotations were taken over in this project and were not "
            f"restored from the package"
        )

    has_effect = any(
        item["added_count"] or item["changed_count"] or item["removed_count"]
        for item in applicable
    )
    can_apply = not errors and has_effect
    can_apply_with_class_creation = not errors and bool(has_effect or missing_classes)
    result = {
        "identity_policy": identity_policy,
        "can_apply": can_apply,
        "can_apply_with_class_creation": can_apply_with_class_creation,
        "package_count": len(package_paths),
        "valid_package_count": len(packages),
        "packages": package_results,
        "matched_scene_count": len(scene_matches),
        "missing_scene_count": len(missing_scenes),
        "ambiguous_scene_count": len(ambiguous_scenes),
        "incompatible_scene_count": len(incompatible_scenes),
        "approval_required_scene_count": len(approval_required_scenes),
        "scene_matches": scene_matches,
        "missing_scenes": missing_scenes,
        "ambiguous_scenes": ambiguous_scenes,
        "incompatible_scenes": incompatible_scenes,
        "approval_required_scenes": approval_required_scenes,
        "missing_classes": sorted(missing_classes),
        "ambiguous_classes": sorted(ambiguous_classes),
        "created_classes": created_classes,
        "new_annotation_count": added_total,
        "duplicate_annotation_count": duplicates,
        "changed_annotation_count": changed,
        "removed_annotation_count": removed_total,
        "taken_over_annotation_count": taken_over_total,
        "out_of_scope_annotation_count": out_of_scope_total,
        "blocked_annotation_count": blocked,
        "scene_plans": [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in plans
        ],
        "confirmation_required_scene_ids": sorted(
            {item["target_scene_id"] for item in confirm_plans}
        ),
        "blocked_scene_ids": sorted({item["target_scene_id"] for item in blocked_plans}),
        "authors": dict(sorted(authors.items())),
        "requires_missing_class_decision": bool(missing_classes) and not create_missing_classes,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "_target_classes": target_classes,
        "_package_manifests": [package["manifest"] for package in packages],
    }
    if include_plans:
        result["_scene_plans"] = plans
    return result


def load_annotation_package(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise AnnotationPackageError("Package path does not exist or is not absolute")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise AnnotationPackageError("Package contains too many files")
        if sum(info.file_size for info in infos) > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise AnnotationPackageError("Package is too large")
        names = set()
        for info in infos:
            validate_zip_member(info)
            if not info.is_dir():
                if info.filename in names:
                    raise AnnotationPackageError(f"Duplicate ZIP entry: {info.filename}")
                names.add(info.filename)
        required = {
            "annotation_package_manifest.json", "project_profile.json", "classes.json",
            "scenes_index.json", "annotation_summary.json", "annotations_wgs84.geoparquet",
            "annotations_native.geoparquet", "README_ANNOTATIONS.md", "SHA256SUMS.txt",
        }
        missing = sorted(required - names)
        if missing:
            raise AnnotationPackageError(f"Package is missing required files: {', '.join(missing)}")
        verify_package_checksums(archive, names)
        manifest = read_zip_json(archive, "annotation_package_manifest.json")
        if manifest.get("schema_name") != "geotile_annotation_package":
            raise AnnotationPackageError("Unsupported annotation package schema")
        if manifest.get("schema_version") not in SUPPORTED_ANNOTATION_PACKAGE_VERSIONS:
            supported = ", ".join(str(v) for v in SUPPORTED_ANNOTATION_PACKAGE_VERSIONS)
            raise AnnotationPackageError(
                f"Unsupported annotation package version {manifest.get('schema_version')} "
                f"(supported: {supported})"
            )
        classes = read_zip_json(archive, "classes.json")
        profile = read_zip_json(archive, "project_profile.json")
        if manifest.get("classes_hash") != canonical_sha256(classes):
            raise AnnotationPackageError("classes_hash does not match classes.json")
        if manifest.get("project_profile_hash") != canonical_sha256(profile):
            raise AnnotationPackageError("project_profile_hash does not match project_profile.json")

        scenes = []
        for item in manifest.get("scenes") or []:
            annotations_file = item.get("annotations_file")
            manifest_file = item.get("scene_manifest_file")
            if not str(annotations_file or "").startswith("annotations/"):
                raise AnnotationPackageError("Scene annotations_file is outside annotations/")
            if not str(manifest_file or "").startswith("scene_manifests/"):
                raise AnnotationPackageError("Scene manifest_file is outside scene_manifests/")
            if annotations_file not in names or manifest_file not in names:
                raise AnnotationPackageError("Scene entry references a missing file")
            annotations = read_zip_json(archive, annotations_file)
            annotations_digest = hashlib.sha256(archive.read(annotations_file)).hexdigest()
            if item.get("annotations_sha256") != annotations_digest:
                raise AnnotationPackageError(f"Annotation hash mismatch for {annotations_file}")
            scenes.append({
                **item,
                "annotations": annotations,
                "scene_manifest": read_zip_json(archive, manifest_file),
            })
        if len(scenes) != int(manifest.get("scene_count", -1)):
            raise AnnotationPackageError("Scene count does not match package manifest")
        if sum(len(item["annotations"]) for item in scenes) != int(manifest.get("annotation_count", -1)):
            raise AnnotationPackageError("Annotation count does not match package manifest")
        return {
            "path": path.resolve(strict=False),
            "manifest": manifest,
            "scope": normalized_package_scope(manifest),
            "classes": classes,
            "profile": profile,
            "scenes": scenes,
        }


def normalized_package_scope(manifest: dict[str, Any]) -> dict[str, Any]:
    """One scope shape for every package version.

    v2 declares `scope` explicitly. v1 never did, so it is synthesized from the
    manifest and flagged ``additive_only``: a v1 package says "here are annotations",
    not "this is the complete set for these scenes", so no deletion or replacement may
    ever be inferred from it.
    """
    version = int(manifest.get("schema_version") or 0)
    raw = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    scene_uids = raw.get("scene_uids") or [
        item.get("source_scene_uid")
        for item in (manifest.get("scenes") or [])
        if isinstance(item, dict) and item.get("source_scene_uid")
    ]
    return {
        "owner_email": str(raw.get("owner_email") or manifest.get("annotator_email") or ""),
        "scene_uids": sorted({str(uid) for uid in scene_uids if uid}),
        "authoritative_as_of": raw.get("authoritative_as_of") or manifest.get("created_at"),
        "supersedes_package_id": raw.get("supersedes_package_id"),
        "additive_only": version < 2,
    }


def _supersedes_chain(package: dict[str, Any], by_id: dict[str, Any], limit: int = 64) -> set[str]:
    """Package ids this package claims to supersede, following the chain.

    Ids that are not among the selected packages still count (the older package simply
    was not selected); the walk just cannot continue past them.
    """
    seen: set[str] = set()
    current = package["scope"].get("supersedes_package_id")
    while current and current not in seen and len(seen) < limit:
        seen.add(str(current))
        known = by_id.get(str(current))
        current = known["scope"].get("supersedes_package_id") if known else None
    return seen


def detect_scope_conflicts(packages: list[dict[str, Any]]) -> list[str]:
    """Two packages from one owner covering the same scene must be chained.

    Without a supersedes link there is no way to tell which one is current, and the
    previous behaviour resolved it arbitrarily by iteration order — silently keeping
    whichever package happened to be processed first.
    """
    errors: list[str] = []
    by_id = {
        str(item["manifest"].get("package_id")): item
        for item in packages
        if item["manifest"].get("package_id")
    }
    by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in packages:
        by_owner[item["scope"]["owner_email"]].append(item)

    for owner, group in sorted(by_owner.items()):
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                overlap = set(first["scope"]["scene_uids"]) & set(second["scope"]["scene_uids"])
                if not overlap:
                    continue
                first_id = str(first["manifest"].get("package_id"))
                second_id = str(second["manifest"].get("package_id"))
                if second_id in _supersedes_chain(first, by_id) or first_id in _supersedes_chain(second, by_id):
                    continue
                errors.append(
                    f"Packages {first_id[:8]} and {second_id[:8]} from {owner or 'unknown owner'} "
                    f"both cover {len(overlap)} scene(s) and neither supersedes the other; "
                    f"select only the newest package"
                )
    return errors


def validate_zip_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    if "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise AnnotationPackageError(f"Unsafe ZIP path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise AnnotationPackageError(f"Unsafe ZIP path: {name}")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == 0o120000:
        raise AnnotationPackageError(f"ZIP symlinks are not allowed: {name}")


def verify_package_checksums(archive: zipfile.ZipFile, names: set[str]) -> None:
    lines = archive.read("SHA256SUMS.txt").decode("utf-8").splitlines()
    expected: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise AnnotationPackageError("Invalid SHA256SUMS.txt")
        validate_checksum_path(parts[1])
        if parts[1] in expected:
            raise AnnotationPackageError(f"Duplicate checksum entry: {parts[1]}")
        expected[parts[1]] = parts[0].lower()
    payload_names = names - {"SHA256SUMS.txt"}
    if set(expected) != payload_names:
        raise AnnotationPackageError("SHA256SUMS.txt does not cover every package file")
    for name, expected_hash in expected.items():
        if hashlib.sha256(archive.read(name)).hexdigest() != expected_hash:
            raise AnnotationPackageError(f"Checksum mismatch: {name}")


def validate_checksum_path(name: str) -> None:
    if (
        "\\" in name
        or re.match(r"^[A-Za-z]:", name)
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
    ):
        raise AnnotationPackageError(f"Unsafe checksum path: {name}")


def read_zip_json(archive: zipfile.ZipFile, name: str) -> Any:
    return json.loads(archive.read(name).decode("utf-8"))


def build_target_scene_cache(project_id: str) -> dict[str, dict[str, Any]]:
    result = {}
    for scene_id in list_scene_ids(project_id):
        result[scene_id] = {
            "scene": load_scene_json(project_id, scene_id, "scene", default={}),
            "manifest": load_scene_json(project_id, scene_id, "scene_manifest", default={}),
            "annotations": load_scene_json(project_id, scene_id, "annotations", default=[]),
        }
    return result


def _identity_manifest(value: dict[str, Any]) -> dict[str, Any]:
    embedded = value.get("scene_manifest")
    if isinstance(embedded, dict):
        # The compact scene entry is authoritative for values copied at export time,
        # while the embedded manifest supplies multi-file evidence and legacy assets.
        return {**embedded, **{key: item for key, item in value.items() if key != "scene_manifest"}}
    return value


def _exact_identity_tokens(value: dict[str, Any]) -> dict[str, str]:
    manifest = _identity_manifest(value)
    identity = manifest.get("source_identity") if isinstance(manifest.get("source_identity"), dict) else {}
    tokens: dict[str, str] = {}
    file_hash = manifest.get("source_file_sha256")
    if is_full_sha256(file_hash):
        tokens["source_file_sha256"] = str(file_hash).casefold()
    source_uid = str(manifest.get("source_scene_uid") or "")
    for prefix in ("sha256:", "nitf-sha256:"):
        if source_uid.startswith(prefix) and is_full_sha256(source_uid[len(prefix):]):
            tokens["source_scene_uid_sha256"] = source_uid[len(prefix):].casefold()
    if (
        manifest_identity_strength(manifest) == "exact"
        and is_full_sha256(
            identity.get("source_scene_fingerprint") or manifest.get("source_scene_fingerprint")
        )
    ):
        tokens["source_scene_fingerprint"] = str(
            identity.get("source_scene_fingerprint") or manifest.get("source_scene_fingerprint")
        ).casefold()
    return tokens


def _heuristic_identity_tokens(value: dict[str, Any]) -> dict[str, str]:
    manifest = _identity_manifest(value)
    identity = manifest.get("source_identity") if isinstance(manifest.get("source_identity"), dict) else {}
    tokens: dict[str, str] = {}
    candidate_uid = (
        manifest.get("source_scene_candidate_uid")
        or identity.get("source_scene_candidate_uid")
    )
    if candidate_uid:
        tokens["source_scene_candidate_uid"] = str(candidate_uid)
    signature = manifest.get("source_file_content_signature")
    if str(signature or "").startswith("sig1:"):
        tokens["source_file_content_signature"] = str(signature)
    # Lazy migration compatibility: old package identities put a sampled aggregate
    # under source_scene_uid. It remains useful as a candidate but never as exact.
    if manifest_identity_strength(manifest) == "heuristic" and not tokens:
        legacy_uid = manifest.get("source_scene_uid") or identity.get("source_scene_uid")
        if legacy_uid:
            tokens["legacy_source_scene_candidate_uid"] = str(legacy_uid)
    return tokens


def compare_source_identity(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Compare source evidence as exact, probable, mismatch or unknown.

    Only complete SHA-256 evidence can produce ``exact_match``. Matching sampled
    signatures produce ``probable_match`` and must not be applied automatically.
    """

    left_exact = _exact_identity_tokens(left)
    right_exact = _exact_identity_tokens(right)
    exact_kinds = set(left_exact) & set(right_exact)
    if any(left_exact[kind] == right_exact[kind] for kind in exact_kinds):
        return "exact_match"
    if exact_kinds:
        return "mismatch"

    left_heuristic = _heuristic_identity_tokens(left)
    right_heuristic = _heuristic_identity_tokens(right)
    heuristic_kinds = set(left_heuristic) & set(right_heuristic)
    if any(left_heuristic[kind] == right_heuristic[kind] for kind in heuristic_kinds):
        return "probable_match"
    if heuristic_kinds:
        return "mismatch"
    return "unknown"


def match_scene(
    package_scene: dict[str, Any],
    target_scenes: dict[str, dict[str, Any]],
    *,
    allow_probable: bool = False,
) -> tuple[str | None, str | None, str | None, list[str]]:
    source_grid_uid = package_scene.get("working_grid_uid") or (
        (package_scene.get("scene_manifest") or {}).get("working_view") or {}
    ).get("working_grid_uid")
    comparisons = {
        scene_id: compare_source_identity(package_scene, context.get("manifest") or {})
        for scene_id, context in target_scenes.items()
    }
    exact_identity_candidates = [
        scene_id for scene_id, comparison in comparisons.items()
        if comparison == "exact_match"
    ]
    candidates = [
        scene_id for scene_id in exact_identity_candidates
        if ((target_scenes[scene_id].get("manifest") or {}).get("working_view") or {}).get("working_grid_uid")
        == source_grid_uid
    ]
    if len(candidates) == 1:
        return candidates[0], "source_identity", "exact", candidates
    if len(candidates) > 1:
        return None, "source_identity", "ambiguous", candidates

    if exact_identity_candidates:
        return None, "working_grid_uid", "incompatible", exact_identity_candidates

    probable_identity_candidates = [
        scene_id for scene_id, comparison in comparisons.items()
        if comparison == "probable_match"
    ]
    probable_candidates = [
        scene_id for scene_id in probable_identity_candidates
        if ((target_scenes[scene_id].get("manifest") or {}).get("working_view") or {}).get(
            "working_grid_uid"
        ) == source_grid_uid
    ]
    if allow_probable and len(probable_candidates) == 1:
        return probable_candidates[0], "source_scene_candidate_uid", "probable", probable_candidates
    if probable_identity_candidates:
        return None, "source_scene_candidate_uid", "approval_required", probable_identity_candidates

    provider = package_scene.get("provider") or (
        (package_scene.get("scene_manifest") or {}).get("source_package") or {}
    ).get("provider")
    provider_scene_id = package_scene.get("provider_scene_id") or (
        (package_scene.get("scene_manifest") or {}).get("source_package") or {}
    ).get("provider_scene_id")
    candidates = [
        scene_id for scene_id, context in target_scenes.items()
        if provider and provider_scene_id
        and comparisons.get(scene_id) != "mismatch"
        and ((context["manifest"] or {}).get("source_package") or {}).get("provider") == provider
        and ((context["manifest"] or {}).get("source_package") or {}).get("provider_scene_id") == provider_scene_id
    ]
    if candidates:
        return None, "provider_scene_id", "approval_required", candidates

    required = (
        package_scene.get("filename"), package_scene.get("source_file_size"),
        package_scene.get("width"), package_scene.get("height"), package_scene.get("sensor"),
        package_scene.get("acquisition_datetime_utc"),
    )
    if any(value in {None, ""} for value in required):
        return None, None, None, []
    candidates = []
    for scene_id, context in target_scenes.items():
        if comparisons.get(scene_id) == "mismatch":
            continue
        scene = context["scene"] or {}
        manifest = context["manifest"] or {}
        image = manifest.get("image") or {}
        target_values = (
            scene.get("filename") or manifest.get("filename"),
            manifest.get("source_file_size") or image.get("file_size"),
            image.get("width"), image.get("height"), manifest.get("sensor"),
            manifest.get("acquisition_datetime_utc"),
        )
        if tuple(str(value) for value in target_values) == tuple(str(value) for value in required):
            candidates.append(scene_id)
    if len(candidates) == 1:
        return candidates[0], "metadata_fallback", "controlled", candidates
    if len(candidates) > 1:
        return None, "metadata_fallback", "ambiguous", candidates
    return None, None, None, []


def build_class_lookup(classes: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in classes:
        normalized = normalize_class_name(item.get("name"))
        if normalized:
            grouped[normalized].append(item)
    ambiguous = {name for name, items in grouped.items() if len(items) > 1}
    return {name: items[0] for name, items in grouped.items() if len(items) == 1}, ambiguous


def normalize_class_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def annotation_fingerprint(annotation: dict[str, Any]) -> str:
    payload = {
        "class_id": annotation.get("class_id"),
        "geometry_type": annotation.get("geometry_type") or "bbox",
        "bbox": annotation.get("bbox"),
        "rotated_bbox": annotation.get("rotated_bbox"),
        "polygon_scene_px": annotation.get("polygon_scene_px"),
        "front_edge_scene_px": annotation.get("front_edge_scene_px"),
        "front_vector_scene_px": annotation.get("front_vector_scene_px"),
        "orientation_angle_deg": annotation.get("orientation_angle_deg"),
        "is_negative": bool(annotation.get("is_negative", False)),
    }
    return canonical_sha256(payload)


def project_state_hash(project_id: str) -> str:
    payload = {
        "classes": load_json(project_id, "classes", default=[]),
        "scenes": [],
    }
    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        payload["scenes"].append({
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid"),
            "annotations": load_scene_json(project_id, scene_id, "annotations", default=[]),
        })
    return canonical_sha256(payload)


def deterministic_color(value: str) -> str:
    return f"#{hashlib.sha256(value.encode('utf-8')).hexdigest()[:6]}"


def public_import_report(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_") and key not in {
        "package_paths", "package_hashes", "project_state_hash",
    }}


def _preview_path(project_id: str, preview_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", preview_id):
        raise ValueError("Invalid preview_id")
    path = project_dir(project_id) / "annotation_import_previews" / f"{preview_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_json(path: Path, value: Any) -> None:
    # ensure_ascii=False keeps import reports/manifests readable (QGIS-adjacent QC);
    # the durable byte-writer is shared with the daily project data path.
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)
