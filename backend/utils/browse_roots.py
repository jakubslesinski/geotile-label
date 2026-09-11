"""Configured browse roots for scenes, classes and models."""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class BrowseRoot:
    id: str
    label: str
    path: Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def _normalize_relative_path(path: str) -> str:
    parts = [p for p in PurePosixPath(path.replace("\\", "/")).parts if p not in {"", ".", "/"}]
    return str(PurePosixPath(*parts)) if parts else ""


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _dedupe_roots(candidates: list[BrowseRoot]) -> list[BrowseRoot]:
    roots: list[BrowseRoot] = []
    seen: set[str] = set()
    for candidate in candidates:
        label = candidate.label.strip()
        resolved_path = candidate.path.resolve(strict=False)
        key = str(resolved_path)
        if not label or key in seen:
            continue
        seen.add(key)
        roots.append(BrowseRoot(id=candidate.id, label=label, path=resolved_path))
    return roots


def _configured_roots(
    *,
    primary_id: str,
    primary_label_env: str,
    primary_label_default: str,
    primary_path_env: str,
    primary_path_default: str,
    secondary_id: str,
    secondary_label_env: str,
    secondary_path_env: str,
    secondary_path_default: str,
) -> list[BrowseRoot]:
    return _dedupe_roots([
        BrowseRoot(
            id=primary_id,
            label=os.environ.get(primary_label_env, primary_label_default),
            path=_env_path(primary_path_env, primary_path_default),
        ),
        BrowseRoot(
            id=secondary_id,
            label=os.environ.get(secondary_label_env, ""),
            path=_env_path(secondary_path_env, secondary_path_default),
        ),
    ])


def get_scene_browse_roots() -> list[BrowseRoot]:
    return _configured_roots(
        primary_id="scene_root_1",
        primary_label_env="SCENE_ROOT_1_LABEL",
        primary_label_default="Scenes",
        primary_path_env="SCENES_ROOT",
        primary_path_default="/hostfs",
        secondary_id="scene_root_2",
        secondary_label_env="SCENE_ROOT_2_LABEL",
        secondary_path_env="SCENE_ROOT_2_TARGET",
        secondary_path_default="/browse/scenes/root_2",
    )


def get_class_browse_roots() -> list[BrowseRoot]:
    return _configured_roots(
        primary_id="class_root_1",
        primary_label_env="CLASS_ROOT_1_LABEL",
        primary_label_default="Classes",
        primary_path_env="CLASSES_ROOT",
        primary_path_default=os.environ.get("SCENES_ROOT", "/hostfs"),
        secondary_id="class_root_2",
        secondary_label_env="CLASS_ROOT_2_LABEL",
        secondary_path_env="CLASS_ROOT_2_TARGET",
        secondary_path_default="/browse/classes/root_2",
    )


def get_model_browse_roots() -> list[BrowseRoot]:
    return _configured_roots(
        primary_id="model_root_1",
        primary_label_env="MODEL_ROOT_1_LABEL",
        primary_label_default="Models",
        primary_path_env="MODELS_ROOT",
        primary_path_default="/app/data/models",
        secondary_id="model_root_2",
        secondary_label_env="MODEL_ROOT_2_LABEL",
        secondary_path_env="MODEL_ROOT_2_TARGET",
        secondary_path_default="/browse/models/root_2",
    )


def find_root_for_path(path: Path, roots: list[BrowseRoot]) -> BrowseRoot | None:
    resolved = path.resolve(strict=False)
    for root in roots:
        if _is_within(root.path, resolved):
            return root
    return None


def resolve_path_in_roots(path: str, roots: list[BrowseRoot]) -> tuple[Path | None, BrowseRoot | None]:
    if not roots:
        return None, None

    normalized = (path or "").strip()
    if not normalized:
        return roots[0].path.resolve(strict=False), roots[0]

    candidate = Path(normalized)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
        root = find_root_for_path(resolved, roots)
        return (resolved, root) if root else (None, None)

    root = roots[0]
    relative = _normalize_relative_path(normalized)
    resolved = (root.path / Path(relative)).resolve(strict=False)
    if _is_within(root.path, resolved):
        return resolved, root
    return None, None


def to_display_path(path: str, roots: list[BrowseRoot]) -> str:
    resolved, root = resolve_path_in_roots(path, roots)
    if not resolved or not root:
        return path or "/"

    try:
        relative = resolved.relative_to(root.path)
        relative_str = str(relative).replace("\\", "/")
    except ValueError:
        relative_str = ""

    return f"{root.label} / {relative_str}" if relative_str and relative_str != "." else f"{root.label} /"


def build_breadcrumbs(path: Path, root: BrowseRoot) -> list[dict]:
    breadcrumbs = [{
        "label": root.label,
        "path": str(root.path),
    }]

    try:
        relative = path.relative_to(root.path)
    except ValueError:
        return breadcrumbs

    if str(relative) == ".":
        return breadcrumbs

    current = root.path
    for part in relative.parts:
        current = current / part
        breadcrumbs.append({
            "label": part,
            "path": str(current.resolve(strict=False)),
        })
    return breadcrumbs
