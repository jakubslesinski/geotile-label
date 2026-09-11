"""Feature-flagged access to JSON Index v2 with cached, paged queries."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any, Callable

from db.storage import project_paths
from services.json_index.project_revision import read_index_state, read_project_revision
from services.json_index.rebuild import rebuild_project_indexes
from services.json_index.schema import (
    ANNOTATION_SUMMARY_SCHEMA,
    BUILDER_VERSION,
    INDEX_SCHEMA_VERSION,
    SCENES_INDEX_SCHEMA,
    JsonIndexPaths,
    json_index_diagnostic_enabled,
    json_index_enabled,
)
from services.jobs.store import read_json


class InvalidCursorError(ValueError):
    """The cursor is malformed or belongs to a different query/revision."""


_DOCUMENT_CACHE_MAX = 24
_document_cache_lock = threading.RLock()
_document_cache: "OrderedDict[tuple[str, str, int, int, int], dict[str, Any]]" = OrderedDict()


def clear_project_cache(project_id: str | None = None) -> None:
    """Invalidate parsed read models; source revision remains the primary cache key."""

    with _document_cache_lock:
        if project_id is None:
            _document_cache.clear()
            return
        doomed = [key for key in _document_cache if key[0] == project_id]
        for key in doomed:
            _document_cache.pop(key, None)


def _read_cached_document(
    project_id: str,
    path,
    *,
    source_revision: int,
) -> dict[str, Any]:
    try:
        stat = path.stat()
        file_signature = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        file_signature = (-1, -1)
    # Revision is the semantic invalidator. The fixed-cost file signature also
    # detects manual/corrupt replacement of this one derived document without the
    # legacy 3N per-scene stat walk.
    key = (project_id, str(path), int(source_revision), *file_signature)
    with _document_cache_lock:
        hit = _document_cache.get(key)
        if hit is not None:
            _document_cache.move_to_end(key)
            return hit

    value = read_json(path, default={}) or {}
    with _document_cache_lock:
        _document_cache[key] = value
        _document_cache.move_to_end(key)
        while len(_document_cache) > _DOCUMENT_CACHE_MAX:
            _document_cache.popitem(last=False)
    return value


def _source_revision(value: dict[str, Any]) -> int:
    raw = value.get("source_revision")
    return -1 if raw is None else int(raw)


def _is_current(project_id: str) -> bool:
    root = project_paths(project_id).root
    paths = JsonIndexPaths(root)
    revision = read_project_revision(root, project_id)
    state = read_index_state(root)
    return bool(
        state.get("status") == "ready"
        and int(state.get("schema_version") or 0) == INDEX_SCHEMA_VERSION
        and str(state.get("builder_version") or "") == BUILDER_VERSION
        and _source_revision(state) == int(revision["revision"])
        and paths.scenes.is_file()
        and paths.annotations.is_file()
    )


def ensure_current(project_id: str) -> None:
    if not _is_current(project_id):
        rebuild_project_indexes(project_id)


def _assert_scene_parity(index: dict[str, Any], legacy: dict[str, Any]) -> None:
    indexed_ids = {str(item.get("scene_id")) for item in index.get("scenes") or []}
    legacy_ids = {str(item.get("scene_id")) for item in legacy.get("scenes") or []}
    if indexed_ids != legacy_ids or int(index.get("scene_count") or 0) != int(
        legacy.get("scene_count") or 0
    ):
        raise RuntimeError("JSON Index v2 diagnostic mismatch against legacy scene scan")


def _document_is_current(
    value: dict[str, Any],
    *,
    schema_name: str,
    project_id: str,
) -> bool:
    revision = read_project_revision(project_paths(project_id).root, project_id)
    return bool(
        value.get("schema_name") == schema_name
        and int(value.get("schema_version") or 0) == INDEX_SCHEMA_VERSION
        and str(value.get("project_id") or "") == project_id
        and _source_revision(value) == int(revision["revision"])
    )


def get_scenes_index(
    project_id: str,
    *,
    legacy_fallback: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not json_index_enabled():
        if legacy_fallback is None:
            raise RuntimeError("JSON Index v2 is disabled and no legacy fallback was supplied")
        return legacy_fallback()

    ensure_current(project_id)
    root = project_paths(project_id).root
    path = JsonIndexPaths(root).scenes
    revision = int(read_project_revision(root, project_id)["revision"])
    index = _read_cached_document(project_id, path, source_revision=revision)
    if not _document_is_current(
        index,
        schema_name=SCENES_INDEX_SCHEMA,
        project_id=project_id,
    ):
        clear_project_cache(project_id)
        index = rebuild_project_indexes(project_id)
    if json_index_diagnostic_enabled() and legacy_fallback is not None:
        _assert_scene_parity(index, legacy_fallback())
    return index


def get_annotation_summary(project_id: str) -> dict[str, Any]:
    if not json_index_enabled():
        from services.annotation_summary import compute_project_annotation_summary

        return compute_project_annotation_summary(project_id)
    ensure_current(project_id)
    root = project_paths(project_id).root
    path = JsonIndexPaths(root).annotations
    revision = int(read_project_revision(root, project_id)["revision"])
    value = _read_cached_document(project_id, path, source_revision=revision)
    if not _document_is_current(
        value,
        schema_name=ANNOTATION_SUMMARY_SCHEMA,
        project_id=project_id,
    ):
        clear_project_cache(project_id)
        rebuild_project_indexes(project_id)
        revision = int(read_project_revision(root, project_id)["revision"])
        value = _read_cached_document(project_id, path, source_revision=revision)
    return value


_SORT_FIELDS = {
    "filename": "filename",
    "acquisition_datetime_utc": "acquisition_datetime_utc",
    "annotation_count": "annotation_count",
    "tile_count": "tile_count",
    "gsd_m": "gsd_m",
    "status": "status",
    "scene_id": "scene_id",
}


def _query_signature(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _encode_cursor(*, revision: int, offset: int, signature: str) -> str:
    payload = json.dumps(
        {"v": 1, "revision": int(revision), "offset": int(offset), "query": signature},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if int(value.get("v")) != 1:
            raise ValueError("unsupported cursor version")
        return value
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Invalid scenes cursor") from exc


def _searchable_text(scene: dict[str, Any]) -> str:
    return " ".join(
        str(scene.get(key) or "")
        for key in (
            "filename",
            "display_name",
            "scene_id",
            "source_id",
            "package_id",
            "provider_scene_id",
            "provider",
            "sensor",
            "product_type",
        )
    ).casefold()


def _matches_annotation_filters(
    annotation: dict[str, Any],
    *,
    author: str | None,
    class_id: int | None,
    annotation_source: str | None,
    import_id: str | None,
    package_id: str | None,
) -> bool:
    if author and author.casefold() not in {
        str(value).casefold() for value in annotation.get("authors") or []
    }:
        return False
    if class_id is not None and int(class_id) not in {
        int(value) for value in annotation.get("class_ids") or []
    }:
        return False
    if annotation_source and annotation_source not in (annotation.get("sources") or []):
        return False
    if import_id and import_id not in (annotation.get("import_ids") or []):
        return False
    if package_id and package_id not in (annotation.get("package_ids") or []):
        return False
    return True


def _sort_value(scene: dict[str, Any], field: str) -> tuple[int, Any, str]:
    value = scene.get(_SORT_FIELDS[field])
    missing = value is None or value == ""
    if field in {"annotation_count", "tile_count", "gsd_m"}:
        try:
            normalized: Any = float(value)
        except (TypeError, ValueError):
            normalized = 0.0
    else:
        normalized = str(value or "").casefold()
    return (1 if missing else 0, normalized, str(scene.get("scene_id") or "").casefold())


def _scene_api_row(scene: dict[str, Any]) -> dict[str, Any]:
    """Adapt an index entry to the established dashboard ``Scene`` shape."""

    row = dict(scene)
    row["id"] = str(scene.get("scene_id") or scene.get("id") or "")
    if not isinstance(row.get("scene_info"), dict):
        row["scene_info"] = {
            "width": scene.get("width"),
            "height": scene.get("height"),
            "channels": scene.get("channels"),
            "dtype": scene.get("dtype"),
            "has_geo": scene.get("has_geo"),
            "crs": scene.get("crs"),
            "bounds": scene.get("bbox_lonlat"),
        }
    row["status"] = str(scene.get("status") or "cataloged")
    row["annotation_count"] = int(scene.get("annotation_count") or 0)
    row["tile_count"] = int(scene.get("tile_count") or 0)
    return row


def get_scenes_page(
    project_id: str,
    *,
    legacy_fallback: Callable[[], dict[str, Any]] | None = None,
    cursor: str | None = None,
    limit: int = 100,
    sort: str = "filename",
    order: str = "asc",
    filter: str | None = None,
    author: str | None = None,
    class_id: int | None = None,
    annotation_source: str | None = None,
    import_id: str | None = None,
    package_id: str | None = None,
) -> dict[str, Any]:
    """Return a stable page without reading per-scene source documents.

    Cursors are opaque offsets bound to the source revision and canonical query.
    For a fixed revision the complete order is deterministic because ``scene_id`` is
    the final sort key. A mutation invalidates the cursor explicitly.
    """

    if sort not in _SORT_FIELDS:
        raise ValueError(f"Unsupported scenes sort field: {sort}")
    order = order.casefold()
    if order not in {"asc", "desc"}:
        raise ValueError("Scenes order must be 'asc' or 'desc'")
    limit = max(1, min(500, int(limit)))
    search = str(filter or "").strip().casefold()

    index = get_scenes_index(project_id, legacy_fallback=legacy_fallback)
    revision = int(index.get("source_revision") or 0)
    query = {
        "sort": sort,
        "order": order,
        "filter": search,
        "author": str(author or "").casefold(),
        "class_id": class_id,
        "annotation_source": annotation_source or "",
        "import_id": import_id or "",
        "package_id": package_id or "",
    }
    signature = _query_signature(query)
    offset = 0
    if cursor:
        decoded = _decode_cursor(cursor)
        if int(decoded.get("revision", -1)) != revision:
            raise InvalidCursorError("Scenes cursor is stale because the project changed")
        if str(decoded.get("query") or "") != signature:
            raise InvalidCursorError("Scenes cursor belongs to different filters or sorting")
        offset = max(0, int(decoded.get("offset") or 0))

    annotation_filters = any(
        value is not None and value != ""
        for value in (author, class_id, annotation_source, import_id, package_id)
    )
    annotations_by_scene: dict[str, dict[str, Any]] = {}
    if annotation_filters:
        summary = get_annotation_summary(project_id)
        annotations_by_scene = {
            str(item.get("scene_id")): item
            for item in summary.get("per_scene") or []
            if item.get("scene_id")
        }

    all_scenes = list(index.get("scenes") or [])
    selected: list[dict[str, Any]] = []
    for scene in all_scenes:
        if search and search not in _searchable_text(scene):
            continue
        if annotation_filters and not _matches_annotation_filters(
            annotations_by_scene.get(str(scene.get("scene_id"))) or {},
            author=author,
            class_id=class_id,
            annotation_source=annotation_source,
            import_id=import_id,
            package_id=package_id,
        ):
            continue
        selected.append(scene)

    selected.sort(key=lambda item: _sort_value(item, sort), reverse=order == "desc")
    page_items = selected[offset : offset + limit]
    next_offset = offset + len(page_items)
    next_cursor = (
        _encode_cursor(revision=revision, offset=next_offset, signature=signature)
        if next_offset < len(selected)
        else None
    )
    return {
        "schema_name": "geotile_scenes_page",
        "schema_version": 1,
        "project_id": project_id,
        "source_revision": revision,
        "total_count": int(index.get("scene_count") or len(all_scenes)),
        "filtered_count": len(selected),
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "order": order,
        "next_cursor": next_cursor,
        "scenes": [_scene_api_row(scene) for scene in page_items],
    }
