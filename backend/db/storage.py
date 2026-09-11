"""JSON-file persistence per project."""

import copy
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent.parent.parent / "data")))
SCENES_ROOT = Path(os.environ.get("SCENES_ROOT", "/scenes"))
APP_VERSION = os.environ.get("GEOTILE_APP_VERSION", "0.1.4")
SCHEMA_VERSION = 2
ANNOTATION_SCHEMA_VERSION = 1


class StorageConflictError(RuntimeError):
    """Optimistic write rejected because the persisted revision changed."""


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project paths reused by batch operations.

    Constructing this value is read-only unless ``project_paths(..., create=True)`` is
    requested explicitly.  Keeping the resolved root in the caller avoids re-reading
    ``projects_index.json`` for every scene in large loops.
    """

    project_id: str
    root: Path

    @property
    def scenes(self) -> Path:
        return self.root / "scenes"

    def project_json(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def scene(self, scene_id: str) -> Path:
        return self.scenes / scene_id

    def scene_json(self, scene_id: str, name: str) -> Path:
        return self.scene(scene_id) / f"{name}.json"


_PROJECT_ROOT_CACHE_LOCK = threading.RLock()
_PROJECT_ROOT_CACHE_KEY: tuple[str, int, int] | None = None
_PROJECT_ROOT_CACHE: dict[str, Path] = {}


def _invalidate_project_root_cache() -> None:
    global _PROJECT_ROOT_CACHE_KEY, _PROJECT_ROOT_CACHE
    with _PROJECT_ROOT_CACHE_LOCK:
        _PROJECT_ROOT_CACHE_KEY = None
        _PROJECT_ROOT_CACHE = {}


def _projects_index_signature(path: Path) -> tuple[str, int, int]:
    data_root = str(DATA_DIR.expanduser().resolve(strict=False)).casefold()
    try:
        stat = path.stat()
        return data_root, int(stat.st_mtime_ns), int(stat.st_size)
    except OSError:
        return data_root, -1, -1


def _cached_project_roots() -> dict[str, Path]:
    """Return project roots, reloading only when the index file revision changes."""

    global _PROJECT_ROOT_CACHE_KEY, _PROJECT_ROOT_CACHE
    path = projects_index_path()
    signature = _projects_index_signature(path)
    with _PROJECT_ROOT_CACHE_LOCK:
        if signature == _PROJECT_ROOT_CACHE_KEY:
            return _PROJECT_ROOT_CACHE
        index = _read_json_file(path, {})
        roots = {
            str(entry["project_id"]): Path(entry["project_root"]).expanduser()
            for entry in (index.get("projects", []) if isinstance(index, dict) else [])
            if entry.get("project_id") and entry.get("project_root")
        }
        _PROJECT_ROOT_CACHE_KEY = signature
        _PROJECT_ROOT_CACHE = roots
        return roots


@contextmanager
def _storage_file_lock(path: Path) -> Iterator[None]:
    """Reuse the durable JSON Job Manager lock for project storage writes."""

    # Lazy import avoids a module cycle: jobs.store itself imports storage helpers.
    from services.jobs.store import interprocess_lock

    with interprocess_lock(path, timeout_s=15.0) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out locking JSON storage: {path}")
        yield


def _begin_json_index_mutation(
    project_id: str,
    project_root: Path,
    mutation: str,
) -> str | None:
    """Mark JSON Index v2 dirty before an authoritative project write.

    Imports stay lazy because the index implementation itself reads through this
    storage module. With the feature flag disabled this is a zero-write no-op.
    """

    from services.json_index.schema import json_index_tracking_required

    if not json_index_tracking_required(project_root):
        return None
    from services.json_index.project_revision import begin_project_mutation

    return begin_project_mutation(project_root, project_id, mutation)


def _complete_json_index_mutation(
    project_id: str,
    project_root: Path,
    mutation: str,
    token: str | None,
) -> None:
    if token is None:
        return
    from services.json_index.project_revision import complete_project_mutation

    complete_project_mutation(project_root, project_id, mutation, token)


def _complete_json_index_scene_mutation(
    project_id: str,
    project_root: Path,
    mutation: str,
    token: str | None,
    *,
    scene_id: str,
    document: str,
    before: Any,
    after: Any,
    delta_safe: bool = True,
) -> None:
    """Try the P2.2 delta, otherwise retain the P2.1 dirty/rebuild contract."""

    if token is None:
        return
    from services.json_index.delta import try_complete_single_scene_delta

    completed = try_complete_single_scene_delta(
        project_root,
        project_id,
        mutation,
        token,
        scene_id=scene_id,
        document=document,
        before=before,
        after=after,
        safe=delta_safe,
    )
    if not completed:
        _complete_json_index_mutation(project_id, project_root, mutation, token)


@contextmanager
def track_project_mutation(project_id: str, mutation: str) -> Iterator[None]:
    """Bracket authoritative writes that cannot use the regular save helpers.

    On failure the durable dirty marker intentionally remains. The next indexed
    read rebuilds from whichever atomic source documents were actually published.
    """

    root = project_paths(project_id, create=True).root
    token = _begin_json_index_mutation(project_id, root, mutation)
    try:
        yield
    except BaseException:
        raise
    else:
        _complete_json_index_mutation(project_id, root, mutation, token)


def delete_scene_data(project_id: str, scene_id: str) -> bool:
    """Logically delete a scene with a crash-safe, recoverable directory rename."""

    paths = project_paths(project_id, create=True)
    source = paths.scene(scene_id)
    delete_lock = paths.scenes / f".{scene_id}.delete.lock"
    with _storage_file_lock(delete_lock):
        if not source.is_dir():
            return False
        mutation = "scene.delete"
        token = _begin_json_index_mutation(project_id, paths.root, mutation)
        tombstone_root = paths.root / ".trash" / "scenes"
        tombstone_root.mkdir(parents=True, exist_ok=True)
        tombstone = tombstone_root / f"{scene_id}.{token or uuid.uuid4().hex}"
        os.replace(source, tombstone)
        _complete_json_index_mutation(project_id, paths.root, mutation, token)
    # Rename commits the logical deletion. Cleanup is deliberately outside the
    # locks; a leftover tombstone after a crash remains recoverable.
    shutil.rmtree(tombstone, ignore_errors=True)
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_utc(value: Any) -> str | None:
    """Coerce a datetime or ISO string to canonical aware-UTC isoformat.

    Every timestamp producer in this codebase means UTC (they used ``utcnow``), so a
    naive value is read as UTC. Aware values are converted to UTC. Returns None for
    empty; leaves a genuinely unparseable string untouched. Idempotent on canonical
    input, so re-normalizing already-fixed records is a no-op.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return str(value)
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.isoformat()


def _json_default(obj: Any) -> str:
    """json.dump fallback: serialize datetimes as canonical aware-UTC isoformat.

    Model timestamps arrive here as naive ``datetime`` objects (``model_dump`` keeps
    them as objects); ``str(dt)`` would emit a space-separated, offset-less string that
    sorts inconsistently against ``isoformat()`` output. Canonicalizing at the write
    boundary makes every datetime written through storage comparable.
    """
    if isinstance(obj, datetime):
        dt = obj.replace(tzinfo=timezone.utc) if obj.tzinfo is None else obj.astimezone(timezone.utc)
        return dt.isoformat()
    return str(obj)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write bytes durably: temp file in the same dir, fsync, atomic replace.

    A crash or antivirus lock mid-write leaves the destination either fully old or
    fully new, never truncated. Annotations are the only unrecoverable artifact in a
    project, so every JSON write on the project data path goes through here. A killed
    process may leak a hidden ``.<name>.<uuid>.tmp`` file; it never ends in ``.json``,
    so the ``*.json`` globs and ``project.json`` lookups elsewhere ignore it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _replace_with_retry(temp_path: Path, path: Path, attempts: int = 12) -> None:
    """os.replace with a short backoff for Windows WinError 5.

    On Windows os.replace fails with PermissionError (Access denied) when another
    thread is concurrently replacing the same file, or a reader briefly holds it
    without share-delete. Tile rendering runs many workers in parallel, so the same
    scene.json is written concurrently — retry briefly instead of surfacing a 500.
    """
    import time

    for attempt in range(attempts):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def _write_json_file(path: Path, data: Any) -> None:
    payload = json.dumps(data, indent=2, default=_json_default).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _read_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def projects_index_path() -> Path:
    """Resolve the index path without creating directories on a read path."""

    return DATA_DIR / "projects_index.json"


def ensure_projects_index_path() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return projects_index_path()


def _empty_projects_index() -> dict:
    return {
        "schema_name": "geotile_projects_index",
        "schema_version": SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "updated_at": _utc_now(),
        "projects": [],
    }


def load_projects_index() -> dict:
    index = _read_json_file(projects_index_path(), _empty_projects_index())
    if not isinstance(index, dict):
        return _empty_projects_index()
    index.setdefault("schema_name", "geotile_projects_index")
    index.setdefault("schema_version", SCHEMA_VERSION)
    index.setdefault("app_version", APP_VERSION)
    index.setdefault("projects", [])
    return index


def _prepare_projects_index(index: dict) -> None:
    index["schema_name"] = "geotile_projects_index"
    index["schema_version"] = SCHEMA_VERSION
    index["app_version"] = APP_VERSION
    index["updated_at"] = _utc_now()


def _save_projects_index_unlocked(index: dict, path: Path) -> None:
    _prepare_projects_index(index)
    _write_json_file(path, index)
    _invalidate_project_root_cache()


def save_projects_index(index: dict) -> None:
    path = ensure_projects_index_path()
    with _storage_file_lock(path.with_name(f".{path.name}.lock")):
        _save_projects_index_unlocked(index, path)


def _project_index_entry(project_id: str) -> dict | None:
    index = load_projects_index()
    for entry in index.get("projects", []):
        if entry.get("project_id") == project_id:
            return entry
    return None


def _project_root_from_index(project_id: str) -> Path | None:
    return _cached_project_roots().get(project_id)


def _legacy_project_root(project_id: str) -> Path:
    return resolve_projects_dir() / project_id


def _is_in_legacy_projects_dir(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(resolve_projects_dir().resolve(strict=False))
        return True
    except ValueError:
        return False


def _safe_dir_name(name: str, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._-")
    return safe or fallback


def create_project_root(project_id: str, name: str, project_location: str | None = None) -> Path:
    if project_location:
        parent = Path(project_location).expanduser().resolve(strict=False)
        parent.mkdir(parents=True, exist_ok=True)
        base_name = _safe_dir_name(name, "project")
        root = parent / base_name
        if root.exists():
            root = parent / f"{base_name}_{project_id}"
        created_in_appdata = False
    else:
        root = _legacy_project_root(project_id)
        created_in_appdata = True

    root.mkdir(parents=True, exist_ok=True)
    register_project(project_id, root, name=name, created_in_appdata=created_in_appdata)
    return root


def register_project(
    project_id: str,
    project_root: Path,
    name: str = "",
    created_in_appdata: bool | None = None,
) -> None:
    path = ensure_projects_index_path()
    with _storage_file_lock(path.with_name(f".{path.name}.lock")):
        index = load_projects_index()
        projects = index.setdefault("projects", [])
        resolved_root = str(project_root.resolve(strict=False))
        now = _utc_now()

        entry = None
        for candidate in projects:
            if candidate.get("project_id") == project_id:
                entry = candidate
                break
        if entry is None:
            entry = {"project_id": project_id, "created_at": now}
            projects.append(entry)

        entry["name"] = name or entry.get("name") or project_id
        entry["project_root"] = resolved_root
        entry["last_opened_at"] = now
        entry["available"] = (project_root / "project.json").exists()
        entry["storage_mode"] = entry.get("storage_mode", "managed")
        if created_in_appdata is not None:
            entry["created_in_appdata"] = created_in_appdata
        else:
            entry.setdefault("created_in_appdata", _is_in_legacy_projects_dir(project_root))
        _save_projects_index_unlocked(index, path)


def unregister_project(project_id: str) -> None:
    path = ensure_projects_index_path()
    with _storage_file_lock(path.with_name(f".{path.name}.lock")):
        index = load_projects_index()
        index["projects"] = [
            entry for entry in index.get("projects", [])
            if entry.get("project_id") != project_id
        ]
        _save_projects_index_unlocked(index, path)


def resolve_project_dir(project_id: str, create: bool = False) -> Path:
    root = _project_root_from_index(project_id)
    if root is None:
        root = _legacy_project_root(project_id)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_project_dir(project_id: str) -> Path:
    root = resolve_project_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_projects_dir() -> Path:
    return DATA_DIR / "projects"


def ensure_projects_dir() -> Path:
    d = resolve_projects_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_dir() -> Path:
    """Compatibility alias for callers that intend to create/manage projects."""

    return ensure_projects_dir()


def project_dir(project_id: str) -> Path:
    """Compatibility alias for callers that intend to write below the project."""

    return ensure_project_dir(project_id)


def project_paths(project_id: str, *, create: bool = False) -> ProjectPaths:
    root = ensure_project_dir(project_id) if create else resolve_project_dir(project_id)
    return ProjectPaths(project_id=project_id, root=root)


def _json_path(project_id: str, name: str, create: bool = False) -> Path:
    root = ensure_project_dir(project_id) if create else resolve_project_dir(project_id)
    return root / f"{name}.json"


def load_json(
    project_id: str,
    name: str,
    default: Any = None,
    *,
    paths: ProjectPaths | None = None,
) -> Any:
    path = (paths or project_paths(project_id)).project_json(name)
    data = _read_json_file(path, default)
    if name == "project" and isinstance(data, dict) and data:
        data, changed = _normalize_project_record(project_id, data)
        if changed and os.environ.get("GEOTILE_BENCHMARK_READ_ONLY") != "1":
            lock_path = path.with_name(f".{path.name}.lock")
            with _storage_file_lock(lock_path):
                current = _read_json_file(path, default)
                if isinstance(current, dict) and current:
                    current_source_version = _coerce_schema_version(current.get("schema_version"))
                    current, current_changed = _normalize_project_record(project_id, current)
                    if current_changed:
                        _create_migration_snapshot(
                            project_id,
                            reason="project_schema_upgrade",
                            source_schema_version=current_source_version,
                        )
                        _write_json_file(path, current)
                    data = current
    return data


def save_json(project_id: str, name: str, data: Any) -> None:
    if name == "project" and isinstance(data, dict):
        data, _changed = _normalize_project_record(project_id, data)
    path = _json_path(project_id, name, create=True)
    with _storage_file_lock(path.with_name(f".{path.name}.lock")):
        mutation = f"project.{name}.save"
        token = (
            _begin_json_index_mutation(project_id, path.parent, mutation)
            if name in {"project", "classes"}
            else None
        )
        _write_json_file(path, data)
        _complete_json_index_mutation(project_id, path.parent, mutation, token)
    if name == "project" and isinstance(data, dict):
        register_project(
            project_id,
            path.parent,
            name=str(data.get("name") or project_id),
            created_in_appdata=bool(data.get("created_in_appdata", _is_in_legacy_projects_dir(path.parent))),
        )


def project_exists(project_id: str) -> bool:
    return (_json_path(project_id, "project", create=False)).exists()


def _resolved_root_str(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _dedupe_projects_index(index: dict) -> bool:
    """Scala wpisy indeksu wskazujące ten sam katalog (project_root). Historycznie skan
    katalogów dorejestrowywał folder pod `project_id = nazwa_folderu`, gdy folder nazywał
    się nazwą projektu, a nie hex-id → dwa kafle na ten sam projekt. Zostawiamy wpis, którego
    `project_id` zgadza się z `id` z project.json (albo pierwszy). Zwraca True, gdy coś zmieniono."""
    projects = index.get("projects", [])
    by_root: dict[str, dict] = {}
    order: list[str] = []
    changed = False
    for entry in projects:
        root_value = entry.get("project_root")
        if not root_value:
            key = f"__noroot__{id(entry)}"
            by_root[key] = entry
            order.append(key)
            continue
        key = _resolved_root_str(root_value)
        existing = by_root.get(key)
        if existing is None:
            by_root[key] = entry
            order.append(key)
            continue
        changed = True
        # preferuj wpis z project_id == id z project.json
        try:
            disk_id = str((_read_json_file(Path(root_value).expanduser() / "project.json", {}) or {}).get("id") or "")
        except Exception:
            disk_id = ""
        if disk_id and entry.get("project_id") == disk_id and existing.get("project_id") != disk_id:
            by_root[key] = entry
    if changed:
        index["projects"] = [by_root[k] for k in order]
    return changed


def list_project_ids() -> list[str]:
    ids: list[str] = []
    index = load_projects_index()
    changed = _dedupe_projects_index(index)
    known_roots: set[str] = set()

    for entry in index.get("projects", []):
        pid = entry.get("project_id")
        root_value = entry.get("project_root")
        if not pid or not root_value:
            continue
        root = Path(root_value).expanduser()
        known_roots.add(_resolved_root_str(root_value))
        available = (root / "project.json").exists()
        if entry.get("available") != available:
            entry["available"] = available
            changed = True
        if available:
            ids.append(pid)

    if changed:
        save_projects_index(index)

    legacy_root = resolve_projects_dir()
    if not legacy_root.is_dir():
        return ids
    for project_path in legacy_root.iterdir():
        project_json = project_path / "project.json"
        if not project_path.is_dir() or not project_json.exists():
            continue
        # Dedup po ŚCIEŻCE, nie po nazwie folderu: folder nazwany nazwą projektu (nie hex-id)
        # jest już pokryty wpisem indeksu i nie może tworzyć drugiego wpisu.
        if _resolved_root_str(project_path) in known_roots:
            continue
        data = _read_json_file(project_json, {})
        pid = str(data.get("id") or project_path.name)  # prawdziwe id, nie nazwa folderu
        if pid not in ids:
            register_project(
                pid,
                project_path,
                name=str(data.get("name") or pid),
                created_in_appdata=True,
            )
            known_roots.add(_resolved_root_str(project_path))
            ids.append(pid)
    return ids


def delete_project_dir(project_id: str) -> None:
    import shutil
    d = resolve_project_dir(project_id, create=False)
    if d.exists():
        shutil.rmtree(d)
    unregister_project(project_id)
    _invalidate_project_root_cache()


def _normalize_project_record(project_id: str, data: dict) -> tuple[dict, bool]:
    changed = False
    project = dict(data)
    root = resolve_project_dir(project_id, create=False)
    root_string = str(root.resolve(strict=False))
    from models.project import default_project_profile

    defaults = {
        "schema_version": SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "project_root": root_string,
        "storage_mode": "managed",
        "created_in_appdata": _is_in_legacy_projects_dir(root),
    }
    for key, value in defaults.items():
        if project.get(key) != value and key not in project:
            project[key] = value
            changed = True

    source_schema_version = _coerce_schema_version(project.get("schema_version"))
    if source_schema_version != SCHEMA_VERSION:
        project["schema_version"] = SCHEMA_VERSION
        project["migration"] = {
            "migrated_from_schema_version": source_schema_version,
            "migrated_to_schema_version": SCHEMA_VERSION,
            "migrated_at": _utc_now(),
            "migrated_by_app_version": APP_VERSION,
        }
        changed = True
    if not project.get("app_version"):
        project["app_version"] = APP_VERSION
        changed = True
    if project.get("project_root") != root_string:
        project["project_root"] = root_string
        changed = True
    if "created_at" not in project:
        project["created_at"] = _utc_now()
        changed = True
    if "updated_at" not in project:
        project["updated_at"] = project.get("created_at") or _utc_now()
        changed = True
    if not project.get("profile"):
        project["profile"] = default_project_profile().model_dump()
        changed = True
    else:
        try:
            from models.project import ProjectProfile

            normalized_profile = ProjectProfile(**project["profile"]).model_dump()
            if project["profile"] != normalized_profile:
                project["profile"] = normalized_profile
                changed = True
        except Exception:
            project["profile"] = default_project_profile().model_dump()
            changed = True

    return project, changed


def _project_schema_version(project_id: str) -> int:
    data = _read_json_file(_json_path(project_id, "project", create=False), {})
    return _coerce_schema_version(data.get("schema_version"))


def _coerce_schema_version(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _create_migration_snapshot(
    project_id: str,
    *,
    reason: str,
    source_schema_version: int,
) -> Path | None:
    root = resolve_project_dir(project_id, create=False)
    project_file = root / "project.json"
    if not project_file.is_file():
        return None
    state_path = root / ".migration_state.json"
    state = _read_json_file(state_path, {})
    if int(state.get("target_schema_version") or 0) >= SCHEMA_VERSION:
        backup_value = state.get("backup_path")
        return Path(backup_value) if backup_value else None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = root / ".migration_backups" / f"schema-{source_schema_version}-to-{SCHEMA_VERSION}-{timestamp}"
    # exist_ok=True: gdy dwa rownolegle odczyty projektu trafia w ten sam znacznik czasu,
    # kopiowanie tych samych plikow zrodlowych jest idempotentne — nie przewracamy zadania.
    backup_root.mkdir(parents=True, exist_ok=True)
    copied_files: list[str] = []

    for path in root.glob("*.json"):
        if path.name.startswith(".migration_"):
            continue
        destination = backup_root / path.name
        shutil.copy2(path, destination)
        copied_files.append(path.name)
    scenes_root = root / "scenes"
    if scenes_root.is_dir():
        for path in scenes_root.glob("*/*.json"):
            relative = path.relative_to(root)
            destination = backup_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied_files.append(relative.as_posix())

    _write_json_file(backup_root / "migration_manifest.json", {
        "schema_name": "geotile_project_migration_backup",
        "schema_version": 1,
        "project_id": project_id,
        "created_at": _utc_now(),
        "reason": reason,
        "source_schema_version": source_schema_version,
        "target_schema_version": SCHEMA_VERSION,
        "files": sorted(copied_files),
    })
    _write_json_file(state_path, {
        "schema_name": "geotile_project_migration_state",
        "schema_version": 1,
        "project_id": project_id,
        "target_schema_version": SCHEMA_VERSION,
        "completed_at": _utc_now(),
        "backup_path": str(backup_root),
        "reason": reason,
    })
    return backup_root


# --- Scene-level helpers ---

def resolve_scenes_dir(project_id: str, *, paths: ProjectPaths | None = None) -> Path:
    return (paths or project_paths(project_id)).scenes


def ensure_scenes_dir(project_id: str, *, paths: ProjectPaths | None = None) -> Path:
    d = (paths or project_paths(project_id, create=True)).scenes
    d.mkdir(parents=True, exist_ok=True)
    return d


def scenes_dir(project_id: str) -> Path:
    """Compatibility alias for callers that intend to write scene data."""

    return ensure_scenes_dir(project_id)


def resolve_scene_dir(
    project_id: str,
    scene_id: str,
    *,
    paths: ProjectPaths | None = None,
) -> Path:
    return (paths or project_paths(project_id)).scene(scene_id)


def ensure_scene_dir(
    project_id: str,
    scene_id: str,
    *,
    paths: ProjectPaths | None = None,
) -> Path:
    d = (paths or project_paths(project_id, create=True)).scene(scene_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def scene_dir(project_id: str, scene_id: str) -> Path:
    """Compatibility alias for callers that intend to write below a scene."""

    return ensure_scene_dir(project_id, scene_id)


def _annotation_meta_path(annotation_path: Path) -> Path:
    return annotation_path.with_name("annotations.meta.json")


def _annotation_source_state(annotation_path: Path) -> tuple[int, int] | None:
    try:
        stat = annotation_path.stat()
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return None


def _annotation_meta_is_current(annotation_path: Path, meta: Any) -> bool:
    if not isinstance(meta, dict):
        return False
    state = _annotation_source_state(annotation_path)
    return bool(
        state is not None
        and int(meta.get("annotation_schema_version") or 0) == ANNOTATION_SCHEMA_VERSION
        and int(meta.get("source_size") or -1) == state[0]
        and int(meta.get("source_mtime_ns") or -1) == state[1]
    )


def _annotation_revision(meta: Any) -> int:
    return int(meta.get("revision") or 0) if isinstance(meta, dict) else 0


def _write_annotation_meta(annotation_path: Path, revision: int) -> None:
    state = _annotation_source_state(annotation_path)
    if state is None:
        return
    _write_json_file(_annotation_meta_path(annotation_path), {
        "schema_name": "geotile_scene_annotations_meta",
        "schema_version": 1,
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "revision": max(1, int(revision)),
        "source_size": state[0],
        "source_mtime_ns": state[1],
        "updated_at": _utc_now(),
    })


def _normalize_annotations_for_storage(
    project_id: str,
    scene_id: str,
    path: Path,
    annotations: list[dict],
    *,
    create_snapshot: bool,
) -> tuple[list[dict], bool]:
    normalized, changed = _normalize_annotations(scene_id, annotations)
    if changed and create_snapshot:
        _create_migration_snapshot(
            project_id,
            reason="annotation_schema_upgrade",
            source_schema_version=_project_schema_version(project_id),
        )
    return normalized, changed


def load_scene_json(
    project_id: str,
    scene_id: str,
    name: str,
    default: Any = None,
    *,
    paths: ProjectPaths | None = None,
) -> Any:
    path = (paths or project_paths(project_id)).scene_json(scene_id, name)
    data = _read_json_file(path, default)
    if name == "annotations" and isinstance(data, list):
        if not path.is_file():
            return data
        meta_path = _annotation_meta_path(path)
        meta = _read_json_file(meta_path, {})
        if _annotation_meta_is_current(path, meta):
            return data

        # A read-only benchmark must never create migration sidecars or rewrite user data.
        if os.environ.get("GEOTILE_BENCHMARK_READ_ONLY") == "1":
            data, _changed = _normalize_annotations_for_storage(
                project_id, scene_id, path, data, create_snapshot=False
            )
            return data

        lock_path = path.with_name(f".{path.name}.lock")
        with _storage_file_lock(lock_path):
            # A concurrent reader/writer may have completed the migration while we waited.
            current = _read_json_file(path, default)
            current_meta = _read_json_file(meta_path, {})
            if isinstance(current, list) and _annotation_meta_is_current(path, current_meta):
                return current
            if not isinstance(current, list):
                return current
            current, changed = _normalize_annotations_for_storage(
                project_id, scene_id, path, current, create_snapshot=True
            )
            revision = _annotation_revision(current_meta) + (1 if changed else 0)
            mutation = "annotations.migrate"
            token = (
                _begin_json_index_mutation(project_id, path.parent.parent.parent, mutation)
                if changed
                else None
            )
            if changed:
                _write_json_file(path, current)
            _write_annotation_meta(path, max(1, revision))
            _complete_json_index_mutation(
                project_id,
                path.parent.parent.parent,
                mutation,
                token,
            )
            return current
    return data


def save_scene_json(
    project_id: str,
    scene_id: str,
    name: str,
    data: Any,
    *,
    expected_revision: int | None = None,
    paths: ProjectPaths | None = None,
) -> int | None:
    path = (paths or project_paths(project_id, create=True)).scene_json(scene_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _storage_file_lock(lock_path):
        mutation = f"{name}.save"
        if name == "annotations" and isinstance(data, list):
            before = copy.deepcopy(_read_json_file(path, []))
            meta = _read_json_file(_annotation_meta_path(path), {})
            meta_current = _annotation_meta_is_current(path, meta)
            revision = _annotation_revision(meta)
            if expected_revision is not None and revision != int(expected_revision):
                raise StorageConflictError(
                    f"Annotation revision changed: expected {expected_revision}, current {revision}"
                )
            data, _changed = _normalize_annotations_for_storage(
                project_id, scene_id, path, data, create_snapshot=False
            )
            token = _begin_json_index_mutation(
                project_id,
                path.parent.parent.parent,
                mutation,
            )
            _write_json_file(path, data)
            revision += 1
            _write_annotation_meta(path, revision)
            _complete_json_index_scene_mutation(
                project_id,
                path.parent.parent.parent,
                mutation,
                token,
                scene_id=scene_id,
                document=name,
                before=before,
                after=data,
                delta_safe=meta_current and isinstance(before, list),
            )
            return revision
        if expected_revision is not None:
            raise ValueError("expected_revision is currently supported for annotations only")
        before = copy.deepcopy(_read_json_file(path, {}))
        token = (
            _begin_json_index_mutation(project_id, path.parent.parent.parent, mutation)
            if name in {"scene", "scene_manifest"}
            else None
        )
        _write_json_file(path, data)
        if name in {"scene", "scene_manifest"}:
            _complete_json_index_scene_mutation(
                project_id,
                path.parent.parent.parent,
                mutation,
                token,
                scene_id=scene_id,
                document=name,
                before=before,
                after=data,
                delta_safe=isinstance(before, dict) and bool(before),
            )
        else:
            _complete_json_index_mutation(
                project_id,
                path.parent.parent.parent,
                mutation,
                token,
            )
        return None


def read_scene_json_with_revision(
    project_id: str,
    scene_id: str,
    name: str,
    default: Any = None,
    *,
    paths: ProjectPaths | None = None,
) -> tuple[Any, int | None]:
    resolved = paths or project_paths(project_id)
    data = load_scene_json(project_id, scene_id, name, default, paths=resolved)
    if name != "annotations":
        return data, None
    path = resolved.scene_json(scene_id, name)
    meta = _read_json_file(_annotation_meta_path(path), {})
    return data, _annotation_revision(meta)


def mutate_scene_json(
    project_id: str,
    scene_id: str,
    name: str,
    updater: Callable[[Any], Any | None],
    *,
    default: Any = None,
    expected_revision: int | None = None,
    paths: ProjectPaths | None = None,
) -> tuple[Any, int]:
    """Atomically read-modify-write a scene JSON document.

    The initial P1.3 contract targets annotations, whose raw list representation stays
    backward-compatible.  Callers that modify annotations should use this function so
    concurrent requests apply to the latest persisted list instead of losing updates.
    """

    if name != "annotations":
        raise ValueError("mutate_scene_json currently supports annotations only")
    resolved = paths or project_paths(project_id, create=True)
    path = resolved.scene_json(scene_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = _annotation_meta_path(path)
    lock_path = path.with_name(f".{path.name}.lock")
    with _storage_file_lock(lock_path):
        current = _read_json_file(path, default)
        if not isinstance(current, list):
            current = [] if default is None else default
        meta = _read_json_file(meta_path, {})
        meta_current = _annotation_meta_is_current(path, meta)
        revision = _annotation_revision(meta)
        if not meta_current and revision:
            # A direct/external write invalidated the sidecar; make the change visible
            # to optimistic writers even before the repaired metadata is persisted.
            revision += 1
        if expected_revision is not None and revision != int(expected_revision):
            raise StorageConflictError(
                f"Annotation revision changed: expected {expected_revision}, current {revision}"
            )
        current, changed = _normalize_annotations_for_storage(
            project_id, scene_id, path, current, create_snapshot=True
        )
        delta_before = copy.deepcopy(current)
        delta_safe = meta_current and not changed
        updated = updater(current)
        if updated is None:
            if changed or not meta_current:
                mutation = "annotations.migrate"
                token = (
                    _begin_json_index_mutation(
                        project_id,
                        path.parent.parent.parent,
                        mutation,
                    )
                    if changed
                    else None
                )
                if changed:
                    _write_json_file(path, current)
                _write_annotation_meta(path, max(1, revision))
                _complete_json_index_mutation(
                    project_id,
                    path.parent.parent.parent,
                    mutation,
                    token,
                )
            return current, max(1, revision)
        if not isinstance(updated, list):
            raise TypeError("Annotations updater must return a list or None")
        updated, _changed = _normalize_annotations_for_storage(
            project_id, scene_id, path, updated, create_snapshot=False
        )
        mutation = "annotations.mutate"
        token = _begin_json_index_mutation(
            project_id,
            path.parent.parent.parent,
            mutation,
        )
        _write_json_file(path, updated)
        revision = max(0, revision) + 1
        _write_annotation_meta(path, revision)
        _complete_json_index_scene_mutation(
            project_id,
            path.parent.parent.parent,
            mutation,
            token,
            scene_id=scene_id,
            document=name,
            before=delta_before,
            after=updated,
            delta_safe=delta_safe,
        )
        return updated, revision


def list_scene_ids(project_id: str, *, paths: ProjectPaths | None = None) -> list[str]:
    d = resolve_scenes_dir(project_id, paths=paths)
    if not d.is_dir():
        return []
    return [
        p.name for p in d.iterdir()
        if p.is_dir() and (p / "scene.json").exists()
    ]


def _normalize_annotations(scene_id: str, annotations: list[dict]) -> tuple[list[dict], bool]:
    changed = False
    normalized: list[dict] = []
    for index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict):
            normalized.append(annotation)
            continue
        ann = dict(annotation)
        created_at = _canonical_utc(ann.get("created_at")) or _utc_now()
        if not ann.get("id"):
            seed = json.dumps(
                {
                    "scene_id": scene_id,
                    "index": index,
                    "class_id": ann.get("class_id"),
                    "bbox": ann.get("bbox"),
                    "created_at": created_at,
                },
                sort_keys=True,
                default=str,
            )
            ann["id"] = hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]
            changed = True
        if not ann.get("source_annotation_id"):
            ann["source_annotation_id"] = ann["id"]
            changed = True
        if ann.get("scene_id") != scene_id:
            ann["scene_id"] = scene_id
            changed = True
        if not ann.get("geometry_type"):
            ann["geometry_type"] = "bbox"
            changed = True
        for obsolete_key in ("difficulty", "ambiguous", "ambiguity", "quality_notes"):
            if obsolete_key in ann:
                ann.pop(obsolete_key)
                changed = True
        for nullable_key in (
            "rotated_bbox",
            "polygon_scene_px",
            "front_edge_scene_px",
            "front_vector_scene_px",
            "orientation_angle_deg",
        ):
            if nullable_key not in ann:
                ann[nullable_key] = None
                changed = True
        if not ann.get("annotation_source"):
            ann["annotation_source"] = "manual"
            changed = True
        for nullable_key in (
            "annotator_email", "created_by", "updated_by", "source_model",
            "import_id", "source_package_id",
            "copied_from_scene_id", "copied_from_annotation_id",
        ):
            if nullable_key not in ann:
                ann[nullable_key] = None
                changed = True
        # Canonicalize (not just fill-if-missing): legacy rows and the create path
        # can carry naive or space-separated timestamps that sort inconsistently.
        if ann.get("created_at") != created_at:
            ann["created_at"] = created_at
            changed = True
        updated_at = _canonical_utc(ann.get("updated_at")) or ann["created_at"]
        if ann.get("updated_at") != updated_at:
            ann["updated_at"] = updated_at
            changed = True
        normalized.append(ann)
    return normalized, changed
