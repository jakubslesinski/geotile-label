"""Provable single-scene updates for JSON Index v2.

The source JSON documents remain authoritative.  A delta is published only when
the current materialized documents describe exactly the source state from before
the write and this mutation is the sole pending mutation.  Every uncertain case
returns ``False`` so storage can use the P2.1 dirty/rebuild protocol.
"""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Any

from services.json_index import project_revision as revision_service
from services.json_index.locks import project_index_lock
from services.json_index.schema import (
    ANNOTATION_SUMMARY_SCHEMA,
    BUILDER_VERSION,
    INDEX_SCHEMA_VERSION,
    SCENES_INDEX_SCHEMA,
    SCENE_SUMMARY_SCHEMA,
    JsonIndexPaths,
    index_state_document,
    json_index_delta_enabled,
    utc_now,
)
from services.jobs.store import read_json, write_json_atomic
from services.scene_manifest import scene_index_entry


class DeltaNotApplicable(RuntimeError):
    """The old state cannot be proven equivalent to the materialized view."""


def _source_revision(value: dict[str, Any]) -> int:
    raw = value.get("source_revision")
    return -1 if raw is None else int(raw)


def _document_is_current(
    value: dict[str, Any],
    *,
    schema_name: str,
    project_id: str,
    revision: int,
) -> bool:
    return bool(
        value.get("schema_name") == schema_name
        and int(value.get("schema_version") or 0) == INDEX_SCHEMA_VERSION
        and str(value.get("project_id") or "") == project_id
        and _source_revision(value) == revision
    )


def _entry(scene_id: str, scene: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    value = scene_index_entry(manifest, scene)
    value["scene_id"] = scene_id
    value["filename"] = value.get("filename") or scene.get("filename")
    return value


def _find_unique(items: list[dict[str, Any]], key: str, value: str) -> tuple[int, dict[str, Any]]:
    matches = [(index, item) for index, item in enumerate(items) if str(item.get(key)) == value]
    if len(matches) != 1:
        raise DeltaNotApplicable(f"Expected exactly one {key}={value}")
    return matches[0]


def _class_names(summary: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for item in summary.get("per_class") or []:
        try:
            result[int(item["class_id"])] = str(item.get("class_name") or item["class_id"])
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _annotation_traits(annotations: list[dict[str, Any]]) -> dict[str, Any]:
    classes: Counter[int] = Counter()
    authors: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    imports: Counter[str] = Counter()
    packages: Counter[str] = Counter()
    missing_author_ids: list[str] = []
    for annotation in annotations:
        class_id = annotation.get("class_id")
        if class_id is not None:
            try:
                classes[int(class_id)] += 1
            except (TypeError, ValueError):
                pass
        author = str(annotation.get("annotator_email") or "").strip().lower()
        if author:
            authors[author] += 1
        else:
            missing_author_ids.append(
                str(annotation.get("source_annotation_id") or annotation.get("id"))
            )
        source = str(annotation.get("annotation_source") or "manual")
        sources[source] += 1
        if annotation.get("import_id"):
            imports[str(annotation["import_id"])] += 1
        if annotation.get("source_package_id"):
            packages[str(annotation["source_package_id"])] += 1
    return {
        "classes": classes,
        "authors": authors,
        "sources": sources,
        "imports": imports,
        "packages": packages,
        "missing_author_ids": missing_author_ids,
    }


def _annotation_row(
    scene_id: str,
    scene: dict[str, Any],
    manifest: dict[str, Any],
    annotations: list[dict[str, Any]],
    class_names: dict[int, str],
) -> dict[str, Any]:
    traits = _annotation_traits(annotations)
    class_ids = sorted(traits["classes"])
    return {
        "scene_id": scene_id,
        "source_scene_uid": manifest.get("source_scene_uid"),
        "filename": scene.get("filename") or manifest.get("filename"),
        "annotation_count": len(annotations),
        "class_ids": class_ids,
        "class_names": [class_names.get(class_id, str(class_id)) for class_id in class_ids],
        "authors": sorted(traits["authors"]),
        "sources": sorted(traits["sources"]),
        "import_ids": sorted(traits["imports"]),
        "package_ids": sorted(traits["packages"]),
    }


def _apply_counter_rows(
    rows: list[dict[str, Any]],
    *,
    key: str,
    before: Counter,
    after: Counter,
) -> list[dict[str, Any]]:
    counts: dict[Any, int] = {}
    for row in rows:
        value = row.get(key)
        if value in counts:
            raise DeltaNotApplicable(f"Duplicate aggregate row for {key}={value}")
        counts[value] = int(row.get("annotation_count") or 0)
    for value in set(before) | set(after):
        current = counts.get(value, 0)
        updated = current - int(before.get(value, 0)) + int(after.get(value, 0))
        if updated < 0:
            raise DeltaNotApplicable(f"Negative aggregate for {key}={value}")
        if updated:
            counts[value] = updated
        else:
            counts.pop(value, None)
    return [
        {key: value, "annotation_count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    ]


def _apply_class_rows(
    rows: list[dict[str, Any]],
    before: Counter[int],
    after: Counter[int],
) -> list[dict[str, Any]]:
    result = [copy.deepcopy(item) for item in rows]
    positions: dict[int, int] = {}
    for index, row in enumerate(result):
        try:
            class_id = int(row["class_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeltaNotApplicable("Invalid per_class aggregate") from exc
        if class_id in positions:
            raise DeltaNotApplicable(f"Duplicate class aggregate {class_id}")
        positions[class_id] = index

    new_rows: list[dict[str, Any]] = []
    for class_id in sorted(set(before) | set(after)):
        if class_id in positions:
            row = result[positions[class_id]]
            updated = (
                int(row.get("annotation_count") or 0)
                - int(before.get(class_id, 0))
                + int(after.get(class_id, 0))
            )
            if updated < 0:
                raise DeltaNotApplicable(f"Negative class aggregate {class_id}")
            row["annotation_count"] = updated
        else:
            if before.get(class_id, 0):
                raise DeltaNotApplicable(f"Missing old class aggregate {class_id}")
            new_rows.append(
                {
                    "class_id": class_id,
                    "class_name": f"Unknown class {class_id}",
                    "annotation_count": int(after.get(class_id, 0)),
                }
            )
    result.extend(new_rows)
    return result


def _replace_missing_ids(
    existing: list[Any],
    before: list[str],
    after: list[str],
) -> list[str]:
    removals = Counter(str(value) for value in before)
    result: list[str] = []
    for raw in existing:
        value = str(raw)
        if removals[value] > 0:
            removals[value] -= 1
        else:
            result.append(value)
    if any(removals.values()):
        raise DeltaNotApplicable("Missing-author aggregate does not contain the old scene contribution")
    result.extend(str(value) for value in after)
    return result


def _update_annotation_summary(
    summary: dict[str, Any],
    *,
    project_root: Path,
    scene_id: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scene = read_json(project_root / "scenes" / scene_id / "scene.json", default={}) or {}
    manifest = read_json(
        project_root / "scenes" / scene_id / "scene_manifest.json", default={}
    ) or {}
    if not isinstance(scene, dict) or not scene or not isinstance(manifest, dict) or not manifest:
        raise DeltaNotApplicable("Scene and manifest are required for an annotation delta")

    class_names = _class_names(summary)
    per_scene = [copy.deepcopy(item) for item in summary.get("per_scene") or []]
    position, materialized_old = _find_unique(per_scene, "scene_id", scene_id)
    expected_old = _annotation_row(scene_id, scene, manifest, before, class_names)
    if materialized_old != expected_old:
        raise DeltaNotApplicable("Old per-scene annotation summary does not match source")

    old_traits = _annotation_traits(before)
    new_traits = _annotation_traits(after)
    updated = copy.deepcopy(summary)
    updated_count = int(updated.get("annotation_count") or 0) - len(before) + len(after)
    if updated_count < 0:
        raise DeltaNotApplicable("Negative project annotation count")
    updated["annotation_count"] = updated_count
    updated["per_class"] = _apply_class_rows(
        updated.get("per_class") or [], old_traits["classes"], new_traits["classes"]
    )
    for field, key, trait in (
        ("per_author", "annotator_email", "authors"),
        ("per_source", "annotation_source", "sources"),
        ("per_import", "import_id", "imports"),
        ("per_package", "package_id", "packages"),
    ):
        updated[field] = _apply_counter_rows(
            updated.get(field) or [],
            key=key,
            before=old_traits[trait],
            after=new_traits[trait],
        )
    missing_ids = _replace_missing_ids(
        updated.get("missing_author_annotation_ids") or [],
        old_traits["missing_author_ids"],
        new_traits["missing_author_ids"],
    )
    updated["missing_author_annotation_ids"] = missing_ids
    updated["missing_author_count"] = len(missing_ids)

    new_row = _annotation_row(scene_id, scene, manifest, after, class_names)
    per_scene[position] = new_row
    per_scene.sort(
        key=lambda item: (
            str(item.get("filename") or "").casefold(),
            str(item.get("scene_id") or ""),
        )
    )
    updated["per_scene"] = per_scene
    without = [str(item["scene_id"]) for item in per_scene if not int(item.get("annotation_count") or 0)]
    updated["scenes_without_annotations"] = without
    updated["scenes_with_annotations"] = len(per_scene) - len(without)
    updated["scene_count"] = len(per_scene)
    updated["generated_at"] = utc_now()
    return updated, _entry(scene_id, scene, manifest), new_row


def _update_scene_documents(
    scenes_index: dict[str, Any],
    annotation_summary: dict[str, Any],
    *,
    project_root: Path,
    scene_id: str,
    document: str,
    before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    scene_path = project_root / "scenes" / scene_id / "scene.json"
    manifest_path = project_root / "scenes" / scene_id / "scene_manifest.json"
    scene = read_json(scene_path, default={}) or {}
    manifest = read_json(manifest_path, default={}) or {}
    if not isinstance(scene, dict) or not scene or not isinstance(manifest, dict) or not manifest:
        raise DeltaNotApplicable("Scene and manifest are required for a scene delta")

    old_scene = before if document == "scene" else scene
    old_manifest = before if document == "scene_manifest" else manifest
    if not isinstance(old_scene, dict) or not old_scene or not isinstance(old_manifest, dict) or not old_manifest:
        raise DeltaNotApplicable("Old scene document is unavailable")

    index_copy = copy.deepcopy(scenes_index)
    entries = index_copy.get("scenes") or []
    position, materialized_old = _find_unique(entries, "scene_id", scene_id)
    if materialized_old != _entry(scene_id, old_scene, old_manifest):
        raise DeltaNotApplicable("Old scene index entry does not match source")
    new_entry = _entry(scene_id, scene, manifest)
    entries[position] = new_entry
    entries.sort(
        key=lambda item: (
            str(item.get("filename") or "").casefold(),
            str(item.get("scene_id") or ""),
        )
    )
    index_copy["scenes"] = entries
    index_copy["scene_count"] = len(entries)

    summary_copy = copy.deepcopy(annotation_summary)
    per_scene = summary_copy.get("per_scene") or []
    summary_position, annotation_row = _find_unique(per_scene, "scene_id", scene_id)
    annotation_row["filename"] = scene.get("filename") or manifest.get("filename")
    annotation_row["source_scene_uid"] = manifest.get("source_scene_uid")
    per_scene[summary_position] = annotation_row
    per_scene.sort(
        key=lambda item: (
            str(item.get("filename") or "").casefold(),
            str(item.get("scene_id") or ""),
        )
    )
    summary_copy["per_scene"] = per_scene
    summary_copy["generated_at"] = utc_now()
    return index_copy, summary_copy, new_entry, annotation_row


def try_complete_single_scene_delta(
    project_root: Path,
    project_id: str,
    mutation: str,
    token: str,
    *,
    scene_id: str,
    document: str,
    before: Any,
    after: Any,
    safe: bool = True,
) -> bool:
    """Publish a one-scene delta or return ``False`` for the dirty/rebuild path.

    Exceptions during publication deliberately propagate.  At that point the
    authoritative source has already been written and the durable state remains
    dirty, so the next indexed read repairs it exactly like a P2.1 crash.
    """

    if not safe or not json_index_delta_enabled():
        return False
    if document not in {"annotations", "scene", "scene_manifest"}:
        return False
    if document == "annotations" and (not isinstance(before, list) or not isinstance(after, list)):
        return False
    if document != "annotations" and (not isinstance(before, dict) or not isinstance(after, dict)):
        return False

    paths = JsonIndexPaths(project_root)
    with project_index_lock(project_root):
        revision_doc = revision_service.read_project_revision(project_root, project_id)
        revision = int(revision_doc["revision"])
        state = revision_service.read_index_state(project_root)
        pending = [str(item) for item in state.get("pending_mutations") or []]
        if not (
            state.get("status") == "dirty"
            and _source_revision(state) == revision
            and pending == [token]
            and str(state.get("builder_version") or "") == BUILDER_VERSION
        ):
            return False

        scenes_index = read_json(paths.scenes, default={}) or {}
        annotation_summary = read_json(paths.annotations, default={}) or {}
        if not _document_is_current(
            scenes_index,
            schema_name=SCENES_INDEX_SCHEMA,
            project_id=project_id,
            revision=revision,
        ) or not _document_is_current(
            annotation_summary,
            schema_name=ANNOTATION_SUMMARY_SCHEMA,
            project_id=project_id,
            revision=revision,
        ):
            return False

        # Prepare complete replacement documents before the first derived write.
        # Any uncertainty here selects the ordinary dirty/rebuild path.
        try:
            if document == "annotations":
                summary_copy, current_entry, annotation_row = _update_annotation_summary(
                    annotation_summary,
                    project_root=project_root,
                    scene_id=scene_id,
                    before=before,
                    after=after,
                )
                index_copy = copy.deepcopy(scenes_index)
                _position, indexed_entry = _find_unique(index_copy.get("scenes") or [], "scene_id", scene_id)
                if indexed_entry != current_entry:
                    raise DeltaNotApplicable("Scene metadata changed before annotation delta")
                new_entry = indexed_entry
            else:
                index_copy, summary_copy, new_entry, annotation_row = _update_scene_documents(
                    scenes_index,
                    annotation_summary,
                    project_root=project_root,
                    scene_id=scene_id,
                    document=document,
                    before=before,
                )
        except DeltaNotApplicable:
            return False

        next_revision = revision + 1
        built_at = utc_now()
        for value in (index_copy, summary_copy):
            value["source_revision"] = next_revision
            value["built_at"] = built_at
        scene_summary = {
            "schema_name": SCENE_SUMMARY_SCHEMA,
            "schema_version": INDEX_SCHEMA_VERSION,
            "source_revision": next_revision,
            "built_at": built_at,
            "project_id": project_id,
            "scene_id": scene_id,
            "scene": new_entry,
            "annotations": annotation_row,
        }

        # Publish while state is dirty. A crash at any point is detectable. The
        # intermediate dirty state at the new revision also closes the small
        # revision-write -> ready-state window for explicit fault injection.
        write_json_atomic(paths.scene_summary(scene_id), scene_summary)
        write_json_atomic(paths.scenes, index_copy)
        write_json_atomic(paths.annotations, summary_copy)
        revision_service._write_revision_locked(
            project_root, project_id, next_revision, mutation
        )
        revision_service.write_json_atomic(
            paths.state,
            index_state_document(
                project_id,
                next_revision,
                status="dirty",
                dirty_at=state.get("dirty_at") or built_at,
                dirty_generation=max(1, int(state.get("dirty_generation") or 0)),
                pending_mutations=[],
                last_mutation=mutation,
            ),
        )
        revision_service.write_json_atomic(
            paths.state,
            index_state_document(
                project_id,
                next_revision,
                status="ready",
                built_at=built_at,
                dirty_at=None,
                dirty_generation=max(1, int(state.get("dirty_generation") or 0)),
                pending_mutations=[],
                last_mutation=mutation,
                delta_document=document,
                delta_scene_id=scene_id,
            ),
        )
        return True
