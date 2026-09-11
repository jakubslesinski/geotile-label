"""CRUD classes with name, color, hotkey + import from file."""

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.storage import load_json, save_json, project_exists
from models.annotation import LabelClass, ClassCreate, ClassUpdate
from services.attribute_engine import recompute_project_attributes
from utils.browse_roots import get_class_browse_roots, resolve_path_in_roots

router = APIRouter()

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")

DEFAULT_COLORS = [
    "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF",
    "#00FFFF", "#FF8000", "#8000FF", "#00FF80",
]


def _get_classes(project_id: str) -> list[dict]:
    return load_json(project_id, "classes", default=[])


def _save_classes(project_id: str, classes: list[dict]) -> None:
    save_json(project_id, "classes", classes)


class ImportClassesBody(BaseModel):
    file_path: str


def _normalize_imported_classes(raw: list) -> list[dict]:
    """Sprawdz i znormalizuj elementy pliku klas.

    Do tej pory import weryfikowal WYLACZNIE to, ze najwyzszy poziom jest tablica, i
    zapisywal jej zawartosc doslownie. Plik skladniowo poprawny, ale o innym ksztalcie
    elementow — lista napisow, nazwy kluczy z innego narzedzia, kategorie w stylu COCO —
    przechodzil bez slowa i ujawnial sie dopiero dwa ekrany dalej jako lista z poprawnym
    licznikiem i pustymi wierszami. Blad ma byc zglaszany tam, gdzie powstaje.

    `color` jest uzupelniany z palety domyslnej tak samo jak w `create_class`, bo plik
    z samym `id` i `name` (np. `categories` z COCO) jest realistyczny i nie ma powodu go
    odrzucac — brak koloru jest brakiem prezentacji, nie brakiem danych.
    """
    normalized: list[dict] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HTTPException(
                400,
                f"Class #{index} must be an object with 'id' and 'name', got "
                f"{type(item).__name__}",
            )
        missing = [key for key in ("id", "name") if item.get(key) in (None, "")]
        if missing:
            raise HTTPException(
                400,
                f"Class #{index} is missing required field(s): {', '.join(missing)}. "
                f"Found keys: {', '.join(sorted(item)) or 'none'}",
            )
        try:
            class_id = int(item["id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"Class #{index} has a non-numeric 'id'") from exc
        if class_id in seen_ids:
            # Zduplikowane `id` rozjezdzaja przypisanie adnotacji do klas, a w UI daja
            # powielone klucze Reacta — to musi byc odrzucone, nie naprawione po cichu.
            raise HTTPException(400, f"Duplicate class id {class_id} at #{index}")
        seen_ids.add(class_id)

        color = str(item.get("color") or "").strip()
        if not _HEX_COLOR.fullmatch(color):
            color = DEFAULT_COLORS[class_id % len(DEFAULT_COLORS)]

        hotkey = item.get("hotkey")
        if hotkey is not None:
            try:
                hotkey = int(hotkey)
            except (TypeError, ValueError):
                hotkey = None

        normalized.append(
            LabelClass(
                id=class_id, name=str(item["name"]), color=color, hotkey=hotkey
            ).model_dump()
        )
    return normalized


@router.get("/")
async def list_classes(project_id: str):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")
    return _get_classes(project_id)


@router.post("/")
async def create_class(project_id: str, body: ClassCreate):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    classes = _get_classes(project_id)
    next_id = max((c["id"] for c in classes), default=-1) + 1

    color = body.color
    if color is None:
        color = DEFAULT_COLORS[next_id] if next_id < len(DEFAULT_COLORS) else "#FF0000"

    hotkey = body.hotkey
    if hotkey is None:
        hotkey = next_id + 1 if next_id < 9 else None

    cls = LabelClass(id=next_id, name=body.name, color=color, hotkey=hotkey)
    classes.append(cls.model_dump())
    _save_classes(project_id, classes)
    recompute_project_attributes(project_id)
    return cls.model_dump()


@router.post("/import")
async def import_classes(project_id: str, body: ImportClassesBody):
    """Import classes from a JSON file on the server."""
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    direct = Path(body.file_path).expanduser()
    if direct.is_absolute():
        resolved = direct.resolve(strict=False) if direct.is_file() else None
    else:
        resolved, _root = resolve_path_in_roots(body.file_path, get_class_browse_roots())
    if not resolved:
        raise HTTPException(400, "file_path not found")
    if not resolved.exists():
        raise HTTPException(404, f"File not found: {body.file_path}")

    # `utf-8-sig` zamiast `utf-8`: pliki eksportowane z narzedzi Windows czesto maja BOM,
    # a wtedy `json.load` wywala sie na pierwszym znaku z komunikatem, ktory niczego nie
    # tlumaczy uzytkownikowi.
    try:
        with open(resolved, "r", encoding="utf-8-sig") as f:
            classes = json.load(f)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Classes file is not valid JSON: {exc}") from exc

    if not isinstance(classes, list):
        raise HTTPException(400, "Classes file must contain a JSON array")

    classes = _normalize_imported_classes(classes)
    _save_classes(project_id, classes)
    recompute_project_attributes(project_id)
    return classes


@router.put("/{class_id}")
async def update_class(project_id: str, class_id: int, body: ClassUpdate):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    classes = _get_classes(project_id)
    for c in classes:
        if c["id"] == class_id:
            if body.name is not None:
                c["name"] = body.name
            if body.color is not None:
                c["color"] = body.color
            if body.hotkey is not None:
                c["hotkey"] = body.hotkey
            _save_classes(project_id, classes)
            recompute_project_attributes(project_id)
            return c
    raise HTTPException(404, "Class not found")


@router.delete("/{class_id}")
async def delete_class(project_id: str, class_id: int):
    if not project_exists(project_id):
        raise HTTPException(404, "Project not found")

    classes = _get_classes(project_id)
    new_classes = [c for c in classes if c["id"] != class_id]
    if len(new_classes) == len(classes):
        raise HTTPException(404, "Class not found")
    _save_classes(project_id, new_classes)
    recompute_project_attributes(project_id)
    return {"status": "deleted"}
