"""Versioned dataset run storage and latest-dataset cache management."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import _atomic_write_bytes, load_projects_index, project_dir
from services.scene_identity import is_full_sha256

DATASET_RUN_SCHEMA_VERSION = 2

# Cykl życia wersji datasetu. Warsztat (DatasetView) produkuje `draft`; publikacja
# wyróżnia wersje, na których warto trenować; `deprecated` wycofuje wersję z nowych
# treningów, nie zrywając rodowodu modeli już na niej wytrenowanych.
PUBLICATION_STATUSES = ("draft", "published", "deprecated")
PUBLICATION_SCHEMA_VERSION = 1
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,47}$")


class DatasetPublicationError(ValueError):
    pass


def dataset_identity_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize the source-identity evidence frozen into a dataset run.

    Empty legacy test/utility manifests have no ``scenes`` field and are reported as
    ``not_recorded`` rather than retroactively rejected. Real generated runs include
    the field; every such scene must carry exact evidence before publication/export.
    """

    value = manifest or {}
    if "scenes" not in value:
        return {
            "status": "not_recorded",
            "scene_count": 0,
            "exact_scene_count": 0,
            "issue_count": 0,
            "issues": [],
        }

    scenes = [item for item in (value.get("scenes") or []) if isinstance(item, dict)]
    issues: list[dict[str, Any]] = []
    exact_count = 0
    for scene in scenes:
        strength = scene.get("source_identity_strength")
        exact_file = is_full_sha256(scene.get("source_file_sha256"))
        exact_multi_asset = (
            strength == "exact"
            and is_full_sha256(scene.get("source_scene_fingerprint"))
            and str(scene.get("source_scene_uid") or "").startswith("scene-sha256:")
        )
        if exact_file or exact_multi_asset:
            exact_count += 1
            continue
        issues.append({
            "scene_id": scene.get("scene_id"),
            "filename": scene.get("filename"),
            "identity_strength": strength or "unknown",
            "reason": "full_sha256_required",
        })
    return {
        "status": "exact" if not issues else "incomplete",
        "scene_count": len(scenes),
        "exact_scene_count": exact_count,
        "issue_count": len(issues),
        "issues": issues,
    }


def require_dataset_exact_identities(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Raise when a recorded dataset lineage contains non-exact source evidence."""

    summary = dataset_identity_summary(manifest)
    if summary["issue_count"]:
        examples = ", ".join(
            str(item.get("filename") or item.get("scene_id") or "?")
            for item in summary["issues"][:3]
        )
        suffix = "..." if summary["issue_count"] > 3 else ""
        raise DatasetPublicationError(
            f"Dataset source identity is not exact for {summary['issue_count']} scene(s): "
            f"{examples}{suffix}. Refresh source identities with force=true and rebuild the dataset run."
        )
    return summary


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dataset_runs_dir(project_id: str) -> Path:
    path = project_dir(project_id) / "dataset_runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def dataset_run_dir(project_id: str, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid dataset run id")
    return dataset_runs_dir(project_id) / run_id


def dataset_run_partial_dir(project_id: str, run_id: str) -> Path:
    """Internal staging directory on the same volume as the published run."""
    final_dir = dataset_run_dir(project_id, run_id)
    return final_dir.with_name(f"{final_dir.name}.partial")


def prepare_dataset_run_partial(project_id: str, run_id: str) -> Path:
    """Create an empty staging directory, removing only this run's stale staging data."""
    partial_dir = dataset_run_partial_dir(project_id, run_id)
    if partial_dir.exists():
        shutil.rmtree(partial_dir)
    partial_dir.mkdir(parents=True, exist_ok=False)
    return partial_dir


def cleanup_dataset_run_partial(project_id: str, run_id: str) -> bool:
    partial_dir = dataset_run_partial_dir(project_id, run_id)
    if not partial_dir.exists():
        return False
    shutil.rmtree(partial_dir)
    return True


def cleanup_stale_dataset_run_partials() -> int:
    """Remove app-owned staging directories left by a terminated backend."""
    removed = 0
    index = load_projects_index()
    for entry in index.get("projects", []):
        root_value = entry.get("project_root") if isinstance(entry, dict) else None
        if not root_value:
            continue
        runs_dir = Path(root_value).expanduser() / "dataset_runs"
        if not runs_dir.is_dir():
            continue
        for candidate in runs_dir.iterdir():
            base_name = candidate.name.removesuffix(".partial")
            if (
                candidate.is_dir()
                and candidate.name.endswith(".partial")
                and re.fullmatch(r"[A-Za-z0-9_-]+", base_name)
            ):
                shutil.rmtree(candidate)
                removed += 1
    return removed


def publish_dataset_run(project_id: str, run_id: str) -> Path:
    """Atomically expose a fully built run; never replace an existing publication."""
    partial_dir = dataset_run_partial_dir(project_id, run_id)
    final_dir = dataset_run_dir(project_id, run_id)
    manifest = read_run_json(partial_dir, "dataset_run_manifest", default={})
    if manifest.get("run_id") != run_id or manifest.get("status") != "complete":
        raise ValueError("Dataset staging directory does not contain a complete run manifest")
    if final_dir.exists():
        raise FileExistsError(f"Dataset run already exists: {run_id}")
    partial_dir.replace(final_dir)
    return final_dir


def create_dataset_run_id(input_payload: dict[str, Any]) -> tuple[str, str]:
    input_hash = dataset_input_hash(input_payload)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{input_hash[:8]}", input_hash


def dataset_input_hash(input_payload: dict[str, Any]) -> str:
    encoded = json.dumps(input_payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_run_json(run_dir: Path, name: str, value: Any) -> Path:
    path = run_dir / f"{name}.json"
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(path, payload)
    return path


def read_run_json(run_dir: Path, name: str, default: Any = None) -> Any:
    path = run_dir / f"{name}.json"
    if not path.exists():
        return default if default is not None else {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sync_latest_dataset_cache(project_id: str, run_dir: Path) -> Path:
    cache_dir = project_dir(project_id) / "dataset"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    shutil.copytree(run_dir, cache_dir)
    return cache_dir


def register_dataset_run(project_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    index_path = project_dir(project_id) / "dataset_runs_index.json"
    index = read_json_path(index_path, {
        "schema_name": "geotile_dataset_runs_index",
        "schema_version": DATASET_RUN_SCHEMA_VERSION,
        "project_id": project_id,
        "latest_run_id": None,
        "updated_at": utc_now(),
        "runs": [],
    })
    run_id = manifest["run_id"]
    summary = dataset_run_summary(manifest)
    runs = [item for item in index.get("runs", []) if item.get("run_id") != run_id]
    runs.append(summary)
    runs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    index["runs"] = runs
    if manifest.get("status") == "complete":
        index["latest_run_id"] = run_id
    index["updated_at"] = utc_now()
    write_json_path(index_path, index)
    return index


def list_dataset_runs(project_id: str) -> dict[str, Any]:
    index_path = project_dir(project_id) / "dataset_runs_index.json"
    index = read_json_path(index_path, None)
    if not isinstance(index, dict):
        index = {
            "schema_name": "geotile_dataset_runs_index",
            "schema_version": DATASET_RUN_SCHEMA_VERSION,
            "project_id": project_id,
            "latest_run_id": None,
            "updated_at": utc_now(),
            "runs": [],
        }

    known_ids = {item.get("run_id") for item in index.get("runs", [])}
    changed = False
    for candidate in dataset_runs_dir(project_id).iterdir():
        if (
            not candidate.is_dir()
            or candidate.name.endswith(".partial")
            or candidate.name in known_ids
        ):
            continue
        manifest = read_run_json(candidate, "dataset_run_manifest", default={})
        if manifest:
            index.setdefault("runs", []).append(dataset_run_summary(manifest))
            changed = True

    # Publikacja żyje w katalogu runu, indeks jest tylko projekcją — odświeżamy ją
    # przy każdym listowaniu, żeby te dwa źródła nie mogły się rozjechać.
    for item in index.get("runs", []):
        run_id = item.get("run_id")
        if not run_id:
            continue
        fields = _publication_fields(read_publication(project_id, run_id))
        if any(item.get(key) != value for key, value in fields.items()):
            item.update(fields)
            changed = True

    index["runs"] = sorted(
        index.get("runs", []),
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    if changed:
        index["updated_at"] = utc_now()
        write_json_path(index_path, index)
    return index


def delete_dataset_run(project_id: str, run_id: str) -> dict[str, Any]:
    """Remove a dataset run's files and its index entry, repairing latest + cache.

    Pure filesystem/index operation — dependency and publication guards are the
    caller's responsibility (see the router). Publication state lives inside the run
    directory, so it is removed together with the run.
    """
    run_dir = dataset_run_dir(project_id, run_id)
    existed = run_dir.exists()
    if existed:
        shutil.rmtree(run_dir)

    index_path = project_dir(project_id) / "dataset_runs_index.json"
    index = read_json_path(index_path, None)
    removed_was_latest = False
    if isinstance(index, dict):
        removed_was_latest = index.get("latest_run_id") == run_id
        index["runs"] = sorted(
            [item for item in index.get("runs", []) if item.get("run_id") != run_id],
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        if removed_was_latest:
            # Repoint latest to the newest remaining complete run (mirrors register).
            index["latest_run_id"] = next(
                (
                    item.get("run_id")
                    for item in index["runs"]
                    if item.get("status") == "complete"
                ),
                None,
            )
        index["updated_at"] = utc_now()
        write_json_path(index_path, index)

    # The `project_dir/dataset` cache is a copy of the latest run — only touch it
    # when we just deleted the run it mirrored.
    if removed_was_latest:
        cache_dir = project_dir(project_id) / "dataset"
        new_latest = (index or {}).get("latest_run_id")
        new_latest_dir = dataset_run_dir(project_id, new_latest) if new_latest else None
        if new_latest_dir and new_latest_dir.exists():
            sync_latest_dataset_cache(project_id, new_latest_dir)
        elif cache_dir.exists():
            shutil.rmtree(cache_dir)

    return {
        "deleted": existed,
        "run_id": run_id,
        "was_latest": removed_was_latest,
        "latest_run_id": (index or {}).get("latest_run_id"),
    }


def publication_path(project_id: str, run_id: str) -> Path:
    return dataset_run_dir(project_id, run_id) / "publication.json"


def default_publication(run_id: str) -> dict[str, Any]:
    return {
        "schema_name": "geotile_dataset_publication",
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "draft",
        "label": None,
        "updated_at": None,
        "updated_by": None,
        "source_identity": None,
        "published_with_known_issues": False,
        "acknowledged_issues": None,
    }


def read_publication(project_id: str, run_id: str) -> dict[str, Any]:
    """Publication state of one run.

    Stored next to the manifest, never inside it: the manifest records what the
    generation produced and stays immutable, while publication is a later decision
    that can change (publish, then deprecate). Living in the run directory also means
    it survives a rebuild of `dataset_runs_index.json`, which is only a projection.
    """
    value = read_json_path(publication_path(project_id, run_id), None)
    if not isinstance(value, dict):
        return default_publication(run_id)
    merged = default_publication(run_id)
    merged.update({key: value.get(key, merged[key]) for key in merged})
    if merged.get("status") not in PUBLICATION_STATUSES:
        merged["status"] = "draft"
    return merged


def _write_publication(project_id: str, run_id: str, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    _atomic_write_bytes(publication_path(project_id, run_id), payload)


def dataset_run_dependents(project_id: str, run_id: str) -> list[str]:
    """Training runs that depend on this dataset.

    Zwraca pustą listę do czasu M3 (przebiegi treningowe jeszcze nie istnieją).
    Wydzielone teraz, żeby reguła ochrony rodowodu miała jeden punkt prawdy i nie
    trzeba było jej szukać po kodzie, gdy trening dojdzie.
    """
    training_root = project_dir(project_id) / "training_runs"
    if not training_root.is_dir():
        return []
    dependents: list[str] = []
    for candidate in training_root.iterdir():
        if not candidate.is_dir():
            continue
        manifest = read_json_path(candidate / "training_manifest.json", None)
        if isinstance(manifest, dict) and manifest.get("dataset_run_id") == run_id:
            dependents.append(candidate.name)
    return sorted(dependents)


def set_dataset_run_publication(
    project_id: str,
    run_id: str,
    *,
    status: str,
    label: str | None = None,
    actor: str | None = None,
    acknowledge_issues: bool = False,
) -> dict[str, Any]:
    """Publish, deprecate or return a dataset run to draft.

    Bramki jakości (błędy audytu/walidacji) są SOFT — publikacja jest decyzją człowieka:
    blokujemy tylko, gdy nie potwierdzono ich świadomie (``acknowledge_issues``), a po
    potwierdzeniu zapisujemy ślad w manifeście publikacji. Blokady strukturalne
    (run niekompletny, brak/niepoprawna etykieta) pozostają twarde.
    """
    if status not in PUBLICATION_STATUSES:
        raise DatasetPublicationError(f"Unknown publication status: {status}")

    manifest = get_dataset_run_manifest(project_id, run_id)
    if not manifest:
        raise FileNotFoundError("Dataset run not found")

    current = read_publication(project_id, run_id)
    resolved_label = (label if label is not None else current.get("label")) or None
    if resolved_label is not None:
        resolved_label = str(resolved_label).strip() or None

    known_issues: dict[str, Any] | None = None
    if status == "published":
        summary = dataset_run_summary(manifest)
        # Blokada STRUKTURALNA (nie ocena jakości): niekompletnego runu nie da się publikować.
        if manifest.get("status") != "complete":
            raise DatasetPublicationError("Only a completed dataset run can be published")
        identity_summary = require_dataset_exact_identities(manifest)
        # Bramki JAKOŚCI są SOFT — decyzja człowieka. Blokujemy tylko bez potwierdzenia.
        has_quality_issue = (
            summary.get("validation_status") == "error"
            or summary.get("audit_readiness") == "not_ready"
        )
        if has_quality_issue:
            if not acknowledge_issues:
                raise DatasetPublicationError(
                    "Dataset run has quality issues "
                    f"(audit={summary.get('audit_readiness')}, "
                    f"validation={summary.get('validation_status')}). "
                    "Publishing requires explicit confirmation."
                )
            known_issues = {
                "validation_status": summary.get("validation_status"),
                "validation_error_count": summary.get("validation_error_count"),
                "audit_status": summary.get("audit_status"),
                "audit_readiness": summary.get("audit_readiness"),
                "audit_quality_score": summary.get("audit_quality_score"),
                "acknowledged_by": actor,
                "acknowledged_at": utc_now(),
            }
        if not resolved_label:
            raise DatasetPublicationError("A published dataset needs a label")
        if not LABEL_PATTERN.match(resolved_label):
            raise DatasetPublicationError(
                "Label may contain letters, digits, spaces, dot, underscore and dash (max 48 chars)"
            )
        taken = {
            str(item.get("publication_label")).casefold()
            for item in list_dataset_runs(project_id).get("runs", [])
            if item.get("publication_label") and item.get("run_id") != run_id
        }
        if resolved_label.casefold() in taken:
            raise DatasetPublicationError(f"Label '{resolved_label}' is already used in this project")

    updated = {
        **current,
        "status": status,
        "label": resolved_label,
        "updated_at": utc_now(),
        "updated_by": actor,
    }
    if status == "published":
        updated["published_with_known_issues"] = known_issues is not None
        updated["acknowledged_issues"] = known_issues
        updated["source_identity"] = identity_summary
    else:
        # Powrót do draft/deprecated czyści ślad potwierdzenia.
        updated["published_with_known_issues"] = False
        updated["acknowledged_issues"] = None
    _write_publication(project_id, run_id, updated)
    _refresh_index_publication(project_id)
    return updated


def _refresh_index_publication(project_id: str) -> None:
    index_path = project_dir(project_id) / "dataset_runs_index.json"
    index = read_json_path(index_path, None)
    if not isinstance(index, dict):
        return
    for item in index.get("runs", []):
        run_id = item.get("run_id")
        if not run_id:
            continue
        item.update(_publication_fields(read_publication(project_id, run_id)))
    index["updated_at"] = utc_now()
    write_json_path(index_path, index)


def _publication_fields(publication: dict[str, Any]) -> dict[str, Any]:
    return {
        "publication_status": publication.get("status", "draft"),
        "publication_label": publication.get("label"),
        "published_at": publication.get("updated_at"),
        "published_by": publication.get("updated_by"),
    }


def get_dataset_run_manifest(project_id: str, run_id: str) -> dict[str, Any] | None:
    run_dir = dataset_run_dir(project_id, run_id)
    manifest = read_run_json(run_dir, "dataset_run_manifest", default=None)
    return manifest if isinstance(manifest, dict) else None


def resolve_dataset_path(project_id: str, run_id: str | None = None) -> Path:
    if not run_id:
        return project_dir(project_id) / "dataset"
    path = dataset_run_dir(project_id, run_id)
    if not path.exists():
        raise FileNotFoundError(f"Dataset run not found: {run_id}")
    return path


def dataset_run_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    stats = manifest.get("statistics") or {}
    config = manifest.get("dataset_config") or {}
    preprocessing = manifest.get("preprocessing_profile") or {}
    audit_summary = manifest.get("audit_summary") or {}
    return {
        "run_id": manifest.get("run_id"),
        "created_at": manifest.get("created_at"),
        "completed_at": manifest.get("completed_at"),
        "status": manifest.get("status"),
        "storage_mode": manifest.get("storage_mode", "copy"),
        "input_hash": manifest.get("input_hash"),
        "tile_catalog_id": manifest.get("tile_catalog_id"),
        "selection": manifest.get("selection") or {},
        "split_mode": config.get("split_mode"),
        "split_seed": config.get("split_seed"),
        "tile_size": (manifest.get("tiling_config") or {}).get("tile_size"),
        "preprocessing_profile_id": preprocessing.get("profile_id"),
        "preprocessing_profile_hash": preprocessing.get("profile_hash"),
        "validation_status": (stats.get("validation_report") or {}).get("status"),
        "validation_warning_count": len((stats.get("validation_report") or {}).get("warnings") or []),
        "validation_error_count": len((stats.get("validation_report") or {}).get("errors") or []),
        "audit_status": audit_summary.get("status"),
        "audit_readiness": audit_summary.get("readiness"),
        "audit_quality_score": audit_summary.get("quality_score"),
        "total_tiles": stats.get("total_tiles", 0),
        "positive_tiles": stats.get("positive_tiles", 0),
        "negative_tiles": stats.get("negative_tiles", 0),
        "total_annotations": stats.get("total_annotations", 0),
    }


def write_json_path(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)


def read_json_path(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
