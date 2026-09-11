"""Project-level annotation aggregation for the manager dashboard."""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json, project_paths

# --- Cache agregatu (DESIGN_DECISIONS.md, performance-audit E8c) ---
#
# Agregat czyta TRZY pliki JSON na scenę (`scene`, `scene_manifest`, `annotations`) i parsuje
# wszystkie adnotacje projektu. Zmierzone: DOTA (2423 sceny, 349 675 adnotacji) → 25,5 s,
# xView3 (50 scen) → 2,6 s. To najdroższa pojedyncza operacja przy wejściu w projekt, a woła ją
# także każde odświeżenie po mutacji — więc bez cache ta praca wraca kilkanaście razy na sesję.
#
# Cache w PAMIĘCI (nie na dysku), keyed po odcisku z mtime — ten sam wzorzec co `_scene_ctx_cache`
# w `routers/scenes.py`. Świadomie bez nowego artefaktu na dysku: nie ma schematu do wersjonowania
# ani pliku do sprzątania, a odcisk unieważnia się sam. Koszt odcisku to 3N `stat` (~0,8 s na DOTA)
# wobec 25,5 s pełnego przeliczenia.
_SUMMARY_CACHE_MAX = 8
_summary_cache_lock = threading.Lock()
_summary_cache: dict[str, tuple[tuple, dict[str, Any]]] = {}


def _summary_fingerprint(project_id: str) -> tuple:
    """Odcisk wejścia agregatu: najnowszy mtime + liczba scen + liczba widzianych plików.

    Sam najnowszy mtime nie wystarcza — USUNIĘCIE pliku (np. wyczyszczenie adnotacji przez
    skasowanie `annotations.json`) nie podnosi niczyjego mtime. Dlatego w odcisku jest też
    licznik faktycznie istniejących plików: ubytek zmienia odcisk i unieważnia cache.
    """
    paths = project_paths(project_id)
    root = paths.root
    try:
        classes_mtime = (root / "classes.json").stat().st_mtime_ns
    except OSError:
        classes_mtime = 0

    newest = 0
    scene_count = 0
    files_seen = 0
    for scene_id in list_scene_ids(project_id, paths=paths):
        scene_dir_path = root / "scenes" / scene_id
        scene_count += 1
        for name in ("scene.json", "scene_manifest.json", "annotations.json"):
            try:
                newest = max(newest, scene_dir_path.joinpath(name).stat().st_mtime_ns)
                files_seen += 1
            except OSError:
                pass
    return (classes_mtime, scene_count, files_seen, newest)


def compute_project_annotation_summary(project_id: str, *, use_cache: bool = True) -> dict[str, Any]:
    """Agregat adnotacji projektu; przy niezmienionym wejściu zwracany z cache."""
    if not use_cache:
        return _compute_project_annotation_summary(project_id)

    fingerprint = _summary_fingerprint(project_id)
    with _summary_cache_lock:
        hit = _summary_cache.get(project_id)
        if hit is not None and hit[0] == fingerprint:
            return hit[1]

    result = _compute_project_annotation_summary(project_id)
    with _summary_cache_lock:
        _summary_cache[project_id] = (fingerprint, result)
        if len(_summary_cache) > _SUMMARY_CACHE_MAX:
            # Wieku wpisów nie śledzimy — usuń dowolny; cache jest mały i odbudowuje się sam.
            _summary_cache.pop(next(iter(_summary_cache)))
    return result


def _compute_project_annotation_summary(project_id: str) -> dict[str, Any]:
    paths = project_paths(project_id)
    classes = load_json(project_id, "classes", default=[], paths=paths)
    class_names = {
        int(item["id"]): str(item.get("name") or item["id"])
        for item in classes
        if isinstance(item, dict) and item.get("id") is not None
    }
    class_counts: Counter[int] = Counter()
    author_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    import_counts: Counter[str] = Counter()
    package_counts: Counter[str] = Counter()
    scenes: list[dict[str, Any]] = []
    missing_author_ids: list[str] = []
    annotation_count = 0

    for scene_id in list_scene_ids(project_id, paths=paths):
        scene = load_scene_json(project_id, scene_id, "scene", default={}, paths=paths)
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={}, paths=paths)
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[], paths=paths)
        scene_class_ids: set[int] = set()
        scene_authors: set[str] = set()
        scene_sources: set[str] = set()
        scene_imports: set[str] = set()
        scene_packages: set[str] = set()
        for annotation in annotations:
            annotation_count += 1
            class_id = annotation.get("class_id")
            if class_id is not None:
                try:
                    numeric_class_id = int(class_id)
                    class_counts[numeric_class_id] += 1
                    scene_class_ids.add(numeric_class_id)
                except (TypeError, ValueError):
                    pass
            author = str(annotation.get("annotator_email") or "").strip().lower()
            if author:
                author_counts[author] += 1
                scene_authors.add(author)
            else:
                missing_author_ids.append(str(annotation.get("source_annotation_id") or annotation.get("id")))
            source = str(annotation.get("annotation_source") or "manual")
            source_counts[source] += 1
            scene_sources.add(source)
            import_id = annotation.get("import_id")
            if import_id:
                import_counts[str(import_id)] += 1
                scene_imports.add(str(import_id))
            package_id = annotation.get("source_package_id")
            if package_id:
                package_counts[str(package_id)] += 1
                scene_packages.add(str(package_id))

        scenes.append({
            "scene_id": scene_id,
            "source_scene_uid": manifest.get("source_scene_uid"),
            "filename": scene.get("filename") or manifest.get("filename"),
            "annotation_count": len(annotations),
            "class_ids": sorted(scene_class_ids),
            "class_names": [class_names.get(class_id, str(class_id)) for class_id in sorted(scene_class_ids)],
            "authors": sorted(scene_authors),
            "sources": sorted(scene_sources),
            "import_ids": sorted(scene_imports),
            "package_ids": sorted(scene_packages),
        })

    import_reports, import_warnings = read_import_reports(project_id, root=paths.root)
    import_totals = {
        "imported_annotation_count": sum(int(report.get("imported_annotation_count", 0)) for report in import_reports),
        "duplicate_annotation_count": sum(int(report.get("duplicate_annotation_count", 0)) for report in import_reports),
        "changed_annotation_count": sum(int(report.get("changed_annotation_count", 0)) for report in import_reports),
        "blocked_annotation_count": sum(int(report.get("blocked_annotation_count", 0)) for report in import_reports),
    }
    per_class = [
        {
            "class_id": int(item["id"]),
            "class_name": str(item.get("name") or item["id"]),
            "annotation_count": class_counts.get(int(item["id"]), 0),
        }
        for item in classes
        if isinstance(item, dict) and item.get("id") is not None
    ]
    known_class_ids = {item["class_id"] for item in per_class}
    per_class.extend({
        "class_id": class_id,
        "class_name": class_names.get(class_id, f"Unknown class {class_id}"),
        "annotation_count": count,
    } for class_id, count in sorted(class_counts.items()) if class_id not in known_class_ids)
    scenes.sort(key=lambda item: str(item.get("filename") or ""))
    scenes_without_annotations = [item["scene_id"] for item in scenes if item["annotation_count"] == 0]

    return {
        "schema_name": "geotile_project_annotation_summary",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "scene_count": len(scenes),
        "scenes_with_annotations": len(scenes) - len(scenes_without_annotations),
        "scenes_without_annotations": scenes_without_annotations,
        "annotation_count": annotation_count,
        "missing_author_count": len(missing_author_ids),
        "missing_author_annotation_ids": missing_author_ids,
        "per_scene": scenes,
        "per_class": per_class,
        "per_author": counter_rows(author_counts, "annotator_email"),
        "per_source": counter_rows(source_counts, "annotation_source"),
        "per_import": counter_rows(import_counts, "import_id"),
        "per_package": counter_rows(package_counts, "package_id"),
        "import_count": len(import_reports),
        "import_totals": import_totals,
        "last_import_report": import_reports[0] if import_reports else None,
        "recent_imports": import_reports[:10],
        "warnings": import_warnings,
    }


def read_import_reports(
    project_id: str,
    *,
    root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    root = (root or project_paths(project_id).root) / "annotation_imports"
    if not root.is_dir():
        return [], []
    reports: list[dict[str, Any]] = []
    warnings: list[str] = []
    for import_dir in root.iterdir():
        report_path = import_dir / "import_report.json"
        if not report_path.is_file():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            reports.append({
                "import_id": report.get("import_id") or import_dir.name,
                "applied_at": report.get("applied_at"),
                "status": report.get("status"),
                "package_count": report.get("valid_package_count", 0),
                "imported_annotation_count": report.get("imported_annotation_count", 0),
                "duplicate_annotation_count": report.get("duplicate_annotation_count", 0),
                # Reports written before the rename carry "collision_count".
                "changed_annotation_count": report.get("changed_annotation_count", report.get("collision_count", 0)),
                "blocked_annotation_count": report.get("blocked_annotation_count", 0),
                "authors": report.get("authors") or {},
            })
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Cannot read import report {report_path}: {exc}")
    reports.sort(key=lambda item: str(item.get("applied_at") or ""), reverse=True)
    return reports, warnings


def counter_rows(counter: Counter[str], key: str) -> list[dict[str, Any]]:
    return [
        {key: value, "annotation_count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]
