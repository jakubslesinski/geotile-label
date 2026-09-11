"""Scene path resolution helpers with mount-prefix compatibility."""

from functools import lru_cache
from pathlib import Path, PurePosixPath
import re

from utils.browse_roots import get_scene_browse_roots, resolve_path_in_roots

WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:$")
DRIVE_LETTER = re.compile(r"^[A-Za-z]$")


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _normalize_parts(scene_folder: str) -> tuple[str, ...]:
    normalized = scene_folder.replace("\\", "/")
    parts = [p for p in PurePosixPath(normalized).parts if p not in {"", ".", "/"}]
    return tuple(parts)


def _canonicalize_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    if not parts:
        return parts
    out = list(parts)
    if WINDOWS_DRIVE.match(out[0]):
        out[0] = out[0][0].lower()
    return tuple(out)


def _strip_mount_prefixes(parts: tuple[str, ...]) -> tuple[str, ...]:
    if not parts:
        return parts
    out = list(_canonicalize_parts(parts))

    changed = True
    while changed and len(out) >= 2:
        changed = False
        head = out[0].lower()
        if head in {"host", "hostfs"}:
            out = out[1:]
            changed = True
            continue
        if head == "mnt" and DRIVE_LETTER.match(out[1]):
            out = out[1:]
            changed = True
            continue

    return tuple(out)


def _candidate_dirs(
    scenes_root: Path,
    scene_folder: str,
    include_root_fallback: bool = True,
) -> list[Path]:
    parts = _normalize_parts(scene_folder)
    variants: list[tuple[str, ...]] = [
        _canonicalize_parts(parts),
        _strip_mount_prefixes(parts),
    ]

    # de-duplicate while preserving order
    unique_variants: list[tuple[str, ...]] = []
    for variant in variants:
        if variant not in unique_variants:
            unique_variants.append(variant)

    candidates: list[Path] = []
    for variant in unique_variants:
        folder = (scenes_root / Path(*variant)).resolve(strict=False)
        if _is_within(scenes_root, folder):
            candidates.append(folder)

    if include_root_fallback and scenes_root not in candidates:
        candidates.append(scenes_root)

    return candidates


@lru_cache(maxsize=2048)
def _resolve_scene_file_cached(
    scenes_root_str: str,
    scene_folder: str,
    filename: str,
) -> str | None:
    if not filename:
        return None

    scenes_root = Path(scenes_root_str).resolve(strict=False)
    candidates = _candidate_dirs(scenes_root, scene_folder, include_root_fallback=False)

    # Fast path: direct lookup in candidate folders.
    for folder in candidates:
        direct = (folder / filename).resolve(strict=False)
        if _is_within(scenes_root, direct) and direct.is_file():
            return str(direct)

    # Fallback: recursive search, but only inside candidate folders.
    for folder in candidates:
        if not folder.is_dir():
            continue
        try:
            for candidate in folder.rglob(filename):
                if candidate.is_file() and _is_within(scenes_root, candidate):
                    return str(candidate.resolve(strict=False))
        except (PermissionError, OSError):
            continue

    return None


def resolve_scene_file(
    scenes_root: Path,
    scene_folder: str,
    filename: str,
) -> Path | None:
    direct_folder = Path(scene_folder).expanduser()
    if direct_folder.is_absolute():
        direct = (direct_folder / filename).resolve(strict=False)
        if direct.is_file():
            return direct
        if direct_folder.is_dir():
            try:
                for candidate in direct_folder.rglob(filename):
                    if candidate.is_file():
                        return candidate.resolve(strict=False)
            except (PermissionError, OSError):
                pass

    scene_roots = get_scene_browse_roots()
    resolved_folder, root = resolve_path_in_roots(scene_folder, scene_roots)
    if resolved_folder and root:
        direct = (resolved_folder / filename).resolve(strict=False)
        if _is_within(root.path, direct) and direct.is_file():
            return direct
        if resolved_folder.is_dir():
            try:
                for candidate in resolved_folder.rglob(filename):
                    if candidate.is_file() and _is_within(root.path, candidate):
                        return candidate.resolve(strict=False)
            except (PermissionError, OSError):
                pass

    resolved = _resolve_scene_file_cached(
        str(scenes_root.resolve(strict=False)),
        scene_folder,
        filename,
    )
    return Path(resolved) if resolved else None


def resolve_scene_folder(
    scenes_root: Path,
    scene_folder: str,
) -> Path | None:
    direct_folder = Path(scene_folder).expanduser()
    if direct_folder.is_absolute() and direct_folder.is_dir():
        return direct_folder.resolve(strict=False)

    scene_roots = get_scene_browse_roots()
    resolved_folder, _root = resolve_path_in_roots(scene_folder, scene_roots)
    if resolved_folder and resolved_folder.is_dir():
        return resolved_folder

    root = scenes_root.resolve(strict=False)
    for folder in _candidate_dirs(root, scene_folder, include_root_fallback=False):
        if folder.is_dir():
            return folder
    if not scene_folder and root.is_dir():
        return root
    return None
