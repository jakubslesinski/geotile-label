"""Przegląd zawartości opublikowanego datasetu (DESIGN_DECISIONS.md, dataset-intelligence DI-F).

Warstwa **zaufania do artefaktu**: czyta pliki gotowego, zamrożonego runu na dysku
(`<run_dir>/<split>/images/*.png` + `labels/*.txt`) i wystawia je do podglądu z boxami,
klasami i pochodzeniem kafla (scena + okno) do deep-linku w edytorze. Zero modelu, zero
zależności od DI0 — deterministyczne.

Run jest niezmienny (identyfikator to hash zawartości), więc indeks kafli budujemy raz i
cache'ujemy per run bez inwalidacji.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from services.dataset_runs import read_run_json, resolve_dataset_path

SPLITS = ("train", "val", "test")

_index_cache: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
_INDEX_CACHE_MAX = 4
_index_lock = threading.Lock()


def _index_disk_path(run_dir: Path) -> Path:
    return run_dir / "metadata" / "content_index.json"


def _write_persisted_index(run_dir: Path, index: dict[str, Any]) -> None:
    """Zapisz indeks kafli na dysk (run niezmienny → liczymy raz).

    Dzięki temu pierwsze otwarcie po restarcie backendu nie przebudowuje indeksu
    z tysięcy plików etykiet, tylko czyta gotowy JSON.
    """
    path = _index_disk_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_persisted_index(run_dir: Path) -> dict[str, Any] | None:
    path = _index_disk_path(run_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # klucze class_map po JSON są stringami — przywróć int
    raw["class_map"] = {int(k): v for k, v in (raw.get("class_map") or {}).items()}
    return raw


def _parse_label(path: Path) -> list[dict[str, float | int]]:
    """Etykieta YOLO `<class_id> cx cy w h` (znormalizowane 0..1) -> lista boxów."""
    boxes: list[dict[str, float | int]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return boxes
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cid = int(float(parts[0]))
            cx, cy, w, h = (float(p) for p in parts[1:5])
        except ValueError:
            continue
        boxes.append({"class_id": cid, "cx": cx, "cy": cy, "w": w, "h": h})
    return boxes


def _build_index(project_id: str, run_id: str) -> dict[str, Any]:
    run_dir = resolve_dataset_path(project_id, run_id)
    if not run_dir.is_dir() or not (run_dir / "dataset_run_manifest.json").is_file():
        raise FileNotFoundError("Dataset run not found")

    manifest = read_run_json(run_dir, "dataset_run_manifest", default={}) or {}
    tile_size = int((manifest.get("tiling_config") or {}).get("tile_size") or 0)

    # Mapa klasa->nazwa/kolor z taksonomii zamrożonej w runie (nie z żywego projektu).
    class_map: dict[int, dict[str, Any]] = {}
    for entry in manifest.get("classes") or []:
        if "id" in entry:
            class_map[int(entry["id"])] = {
                "name": entry.get("name", str(entry["id"])),
                "color": entry.get("color"),
            }

    # Pochodzenie kafla: filename -> scena + okno w pikselach sceny (do deep-linku).
    provenance: dict[str, dict[str, Any]] = {}
    for tile in read_run_json(run_dir, "tiles", default=[]) or []:
        name = tile.get("filename")
        if name:
            provenance[name] = {
                "scene_id": tile.get("scene_id"),
                "x0": tile.get("x0"),
                "y0": tile.get("y0"),
            }

    splits: dict[str, list[dict[str, Any]]] = {}
    for split in SPLITS:
        labels_dir = run_dir / split / "labels"
        images_dir = run_dir / split / "images"
        if not labels_dir.is_dir() or not images_dir.is_dir():
            continue
        items: list[dict[str, Any]] = []
        for label_path in sorted(labels_dir.glob("*.txt")):
            image_name = f"{label_path.stem}.png"
            if not (images_dir / image_name).is_file():
                continue
            boxes = _parse_label(label_path)
            items.append({
                "filename": image_name,
                "boxes": boxes,
                # lista (nie set) — serializowalna do JSON; membership/iteracja działają tak samo
                "class_ids": sorted({b["class_id"] for b in boxes}),
            })
        splits[split] = items

    index = {
        "tile_size": tile_size,
        "class_map": class_map,
        "provenance": provenance,
        "splits": splits,
    }
    try:
        _write_persisted_index(run_dir, index)  # best-effort — cache na dysku
    except OSError:
        pass
    return index


def _get_index(project_id: str, run_id: str) -> dict[str, Any]:
    key = (project_id, run_id)
    with _index_lock:
        cached = _index_cache.get(key)
        if cached is not None:
            _index_cache.move_to_end(key)
            return cached
    # IO poza lockiem: najpierw dysk (tani odczyt), inaczej pełna przebudowa (persistuje).
    run_dir = resolve_dataset_path(project_id, run_id)
    index = _read_persisted_index(run_dir) if run_dir.is_dir() else None
    if index is None:
        index = _build_index(project_id, run_id)
    with _index_lock:
        _index_cache[key] = index
        _index_cache.move_to_end(key)
        while len(_index_cache) > _INDEX_CACHE_MAX:
            _index_cache.popitem(last=False)
    return index


def persist_content_index(project_id: str, run_id: str) -> None:
    """Zbuduj i zapisz indeks kafli na dysk (wywoływane po zakończeniu buildu).

    Best-effort — błąd nie może przewrócić generowania datasetu.
    """
    try:
        _get_index(project_id, run_id)
    except Exception:
        pass


def content_summary(project_id: str, run_id: str) -> dict[str, Any]:
    """Splity i klasy obecne w runie z licznikami kafli — do filtrów podglądu."""
    index = _get_index(project_id, run_id)
    class_map = index["class_map"]

    split_counts: dict[str, int] = {}
    tiles_with_class: dict[int, dict[str, int]] = {}
    for split, items in index["splits"].items():
        split_counts[split] = len(items)
        for item in items:
            for cid in item["class_ids"]:
                bucket = tiles_with_class.setdefault(cid, {})
                bucket[split] = bucket.get(split, 0) + 1

    classes = [
        {
            "class_id": cid,
            "name": class_map.get(cid, {}).get("name", str(cid)),
            "color": class_map.get(cid, {}).get("color"),
            "tiles": sum(per_split.values()),
            "per_split": per_split,
        }
        for cid, per_split in sorted(
            tiles_with_class.items(), key=lambda kv: sum(kv[1].values()), reverse=True
        )
    ]
    return {
        "run_id": run_id,
        "tile_size": index["tile_size"],
        "splits": [{"split": s, "tiles": split_counts.get(s, 0)} for s in SPLITS if s in split_counts],
        "classes": classes,
    }


def list_samples(
    project_id: str,
    run_id: str,
    split: str,
    class_id: int | None = None,
    offset: int = 0,
    limit: int = 60,
) -> dict[str, Any]:
    """Stronicowana lista kafli danego splitu (opcjonalnie filtr po klasie) z boxami."""
    if split not in SPLITS:
        raise ValueError(f"Unknown split: {split}")
    index = _get_index(project_id, run_id)
    items = index["splits"].get(split, [])
    if class_id is not None:
        items = [item for item in items if class_id in item["class_ids"]]

    total = len(items)
    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    page = items[offset : offset + limit]
    class_map = index["class_map"]
    provenance = index["provenance"]
    tile_size = index["tile_size"]

    result = []
    for item in page:
        prov = provenance.get(item["filename"], {})
        result.append({
            "filename": item["filename"],
            "split": split,
            "boxes": [
                {
                    "class_id": box["class_id"],
                    "name": class_map.get(box["class_id"], {}).get("name", str(box["class_id"])),
                    "color": class_map.get(box["class_id"], {}).get("color"),
                    "cx": box["cx"],
                    "cy": box["cy"],
                    "w": box["w"],
                    "h": box["h"],
                }
                for box in item["boxes"]
            ],
            "scene_id": prov.get("scene_id"),
            "tile_x0": prov.get("x0"),
            "tile_y0": prov.get("y0"),
            "tile_size": tile_size,
        })

    return {
        "run_id": run_id,
        "split": split,
        "class_id": class_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "tile_size": tile_size,
        "items": result,
    }


def tile_split_map(project_id: str, run_id: str) -> dict[str, Any]:
    """Dla każdego kafla runu: scena, okno (x0,y0) i split ('train'/'val'/'test').

    Split kafla wynika z katalogu, w którym leży jego etykieta; okno i scena z prowieniencji.
    Używane do mapowania obiektów źródłowych na split (wykrywanie przecieku train/val w DI2).
    """
    index = _get_index(project_id, run_id)
    provenance = index["provenance"]
    tiles: list[dict[str, Any]] = []
    for split, items in index["splits"].items():
        for item in items:
            prov = provenance.get(item["filename"])
            if prov and prov.get("scene_id") is not None and prov.get("x0") is not None and prov.get("y0") is not None:
                tiles.append({
                    "scene_id": prov["scene_id"],
                    "x0": int(prov["x0"]),
                    "y0": int(prov["y0"]),
                    "split": split,
                })
    return {"tile_size": index["tile_size"], "tiles": tiles}


def sample_image_path(project_id: str, run_id: str, split: str, filename: str) -> Path:
    """Ścieżka do obrazu kafla — z twardą walidacją przeciw path traversal."""
    if split not in SPLITS:
        raise ValueError(f"Unknown split: {split}")
    name = Path(filename).name
    if name != filename or not name:
        raise ValueError("Invalid tile filename")
    run_dir = resolve_dataset_path(project_id, run_id)
    path = run_dir / split / "images" / name
    if not path.is_file():
        raise FileNotFoundError("Tile image not found")
    return path
