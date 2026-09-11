"""Server-side folder/file browser for configured scene/class roots."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from utils.browse_roots import (
    BrowseRoot,
    build_breadcrumbs,
    get_class_browse_roots,
    get_scene_browse_roots,
    resolve_path_in_roots,
)

router = APIRouter()

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
CLASSES_EXTENSIONS = {".json"}

HIDDEN_DIRS = {
    "proc", "sys", "dev", "run", "snap", "boot", "lost+found",
    "System Volume Information", "$Recycle.Bin", "$RECYCLE.BIN",
    "Recovery", "PerfLogs",
    "wsl", "wslg",
}


def _get_roots(scope: str) -> list[BrowseRoot]:
    if scope == "class":
        return get_class_browse_roots()
    return get_scene_browse_roots()


def _count_images_shallow(directory: Path) -> int:
    try:
        return sum(
            1 for f in directory.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        )
    except (PermissionError, OSError):
        return 0


@router.get("/")
async def browse(
    path: str = Query("", description="Absolute path within a configured browse root"),
    scope: str = Query("scene", pattern="^(scene|class)$"),
):
    roots = _get_roots(scope)
    if not roots:
        raise HTTPException(500, "No browse roots configured")

    if not path:
        dirs = [{
            "name": root.label,
            "path": str(root.path),
            "image_count": _count_images_shallow(root.path),
            "is_root": True,
        } for root in roots]
        return {
            "path": "",
            "parent_path": "",
            "breadcrumbs": [],
            "dirs": dirs,
            "files": [],
            "image_count": 0,
        }

    target, root = resolve_path_in_roots(path, roots)
    if not target or not root:
        raise HTTPException(400, "Path outside configured roots")
    if not target.exists():
        raise HTTPException(404, "Path not found")
    if not target.is_dir():
        raise HTTPException(400, "Path is not a directory")

    dirs = []
    files = []
    image_count = 0

    try:
        raw_entries = list(target.iterdir())
    except (PermissionError, OSError):
        raise HTTPException(403, "Permission denied")

    entries = []
    for entry in raw_entries:
        name = entry.name
        if name.startswith(".") or name in HIDDEN_DIRS:
            continue
        try:
            is_dir = entry.is_dir()
        except (PermissionError, OSError):
            continue
        entries.append((entry, name, is_dir))

    entries.sort(key=lambda item: (not item[2], item[1].lower()))

    for entry, name, is_dir in entries:
        if is_dir:
            dirs.append({
                "name": name,
                "path": str(entry.resolve(strict=False)),
                "image_count": _count_images_shallow(entry),
                "is_root": False,
            })
            continue

        ext = entry.suffix.lower()
        try:
            size = entry.stat().st_size
        except (PermissionError, OSError):
            size = 0

        if ext in IMAGE_EXTENSIONS:
            image_count += 1
            files.append({
                "name": name,
                "path": str(entry.resolve(strict=False)),
                "size": size,
                "type": "image",
            })
        elif ext in CLASSES_EXTENSIONS:
            files.append({
                "name": name,
                "path": str(entry.resolve(strict=False)),
                "size": size,
                "type": "json",
            })

    breadcrumbs = build_breadcrumbs(target, root)
    parent_path = breadcrumbs[-2]["path"] if len(breadcrumbs) > 1 else ""

    return {
        "path": str(target),
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "dirs": dirs,
        "files": files,
        "image_count": image_count,
    }
