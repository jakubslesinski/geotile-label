"""Migracja projektow na kontrakt grafu v2 (DESIGN_DECISIONS.md, scene-import P2.2).

Wlaczenie `GEOTILE_SCENE_PACKAGE_GRAPH_V2` nie jest przelacznikiem kosmetycznym: zmienia
GRANICE PAKIETU, a wiec i `package_id`, ktorym sa kluczowane sceny. Pomiar na rzeczywistych
zrodlach pokazal cztery rozne klasy skutkow, i to one podyktowaly ksztalt tego modulu:

| Zrodlo | Bez flagi | Z flaga | Skutek dla scen |
| --- | ---: | ---: | --- |
| ICEYE | 1 pakiet (`.`, 1043 assety) | 84 pakiety | **zaden `package_id` nie przezywa** |
| Capella | 19 | 20 produktow | 18 tych samych id, 1 dostawa rozpada sie na dwie akwizycje |
| Airbus | 10 | 10 | bez zmian tozsamosci |
| WorldView | 2 | 2 | bez zmian tozsamosci, zmiana typu produktu |
| Generic | 39 | 39 | bez zmian |

Dlatego migracja NIE zgaduje. Scena, ktorej nie da sie jednoznacznie przypisac do nowego
pakietu, dostaje `migration_required` i czeka na decyzje uzytkownika — zamiast dostac
arbitralnie wybrany produkt. Dotyczy to w szczegolnosci ICEYE: stara scena BYLA calym
zrodlem scalonym w jedno, wiec nie ma jednego nastepcy.

Plan jest liczony na sucho i nic nie zapisuje. Zapis (`apply`) robi kopie zapasowa manifestow,
a derywaty (VRT, piramidy, produkty pochodne) zostawia nietkniete do czasu potwierdzenia
nowego odcisku — kasowanie ich przed migracja zamienia odwracalna zmiane w nieodwracalna.
"""

from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from db.storage import list_scene_ids, load_scene_json, project_dir, save_scene_json
from services.jobs.store import write_json_atomic
from services.scene_packages.base import canonical_hash
from services.scene_packages.contracts import FLAG_GRAPH_V2, decision_uid
from services.scene_packages.identity import scene_identity_scope

MIGRATION_SCHEMA_NAME = "geotile_scene_package_migration_plan"
MIGRATION_SCHEMA_VERSION = 3

#: Status sceny, ktorej nie da sie zmigrowac automatycznie.
STATUS_MIGRATION_REQUIRED = "migration_required"

CLASS_UNCHANGED = "unchanged"
CLASS_AUTO = "auto"
CLASS_NEEDS_SELECTION = "needs_selection"
CLASS_SOURCE_UNAVAILABLE = "source_unavailable"

#: Statusy selekcji, ktore wolno zastosowac bez pytania uzytkownika.
_UNAMBIGUOUS = {"ready", "prepare_required"}

PersistAutomaticMigration = Callable[
    [str, str, dict[str, Any], dict[str, Any]],
    None,
]


@contextmanager
def graph_v2_scan() -> Iterator[None]:
    """Wymus kontrakt v2 na czas skanu.

    Plan ma pokazac, co sie STANIE po wlaczeniu flagi, wiec musi skanowac tak, jakby byla
    wlaczona — niezaleznie od tego, jak jest ustawiona teraz. Zmiana jest lokalna i cofana.
    """
    previous = os.environ.get(FLAG_GRAPH_V2)
    os.environ[FLAG_GRAPH_V2] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(FLAG_GRAPH_V2, None)
        else:
            os.environ[FLAG_GRAPH_V2] = previous


def _measurement_paths(source_package: dict[str, Any]) -> set[str]:
    selection = source_package.get("selection") or {}
    chosen = set(
        selection.get("identity_asset_ids")
        or selection.get("asset_ids")
        or source_package.get("identity_asset_ids")
        or []
    )
    return {
        str(asset.get("relative_path") or "")
        for asset in source_package.get("assets") or []
        if asset.get("asset_id") in chosen
    }


def _normalized_asset_role(asset: dict[str, Any]) -> str | None:
    """Return a stable role for comparison between scan and stored manifest.

    The import writer changes a selected raster's presentation role from
    ``raster_candidate`` to ``primary_raster``.  That does not change its identity and
    must not make every post-import migration look dirty.
    """
    role = asset.get("role")
    if role in {"raster_candidate", "primary_raster"}:
        return "raster_candidate"
    return str(role) if role is not None else None


def _asset_identity_fingerprint(package: dict[str, Any]) -> str:
    """Fingerprint the complete identity scope, not merely the number of rasters."""
    scope = scene_identity_scope(package)
    by_id = {
        str(asset.get("asset_id") or ""): asset
        for asset in package.get("assets") or []
    }
    records: list[dict[str, Any]] = []
    for asset_id in scope["candidate_asset_ids"]:
        asset = by_id.get(str(asset_id))
        if asset is None:
            records.append({"asset_id": asset_id, "missing": True})
            continue
        records.append({
            "asset_id": asset.get("asset_id"),
            "relative_path": asset.get("relative_path"),
            "role": _normalized_asset_role(asset),
            "asset_role": asset.get("asset_role"),
            "part_id": asset.get("part_id"),
            "component": asset.get("component"),
            "format": asset.get("format"),
            "size": asset.get("size"),
            "mtime_ns": asset.get("mtime_ns"),
            "content_signature": asset.get("content_signature"),
            "sha256": asset.get("sha256"),
            "missing": False,
        })
    records.sort(key=lambda item: (
        str(item.get("relative_path") or "").casefold(),
        str(item.get("asset_id") or "").casefold(),
    ))
    return canonical_hash({
        "measurement_asset_ids": sorted(
            (str(value) for value in scope["measurement_asset_ids"]),
            key=str.casefold,
        ),
        "defining_metadata_asset_ids": sorted(
            (str(value) for value in scope["defining_metadata_asset_ids"]),
            key=str.casefold,
        ),
        "assets": records,
    })


def _package_summary(package: dict[str, Any]) -> dict[str, Any]:
    selection = package.get("selection") or {}
    return {
        "package_id": package.get("package_id"),
        "package_root_relative": package.get("package_root_relative"),
        "product_type": selection.get("product_type"),
        "status": selection.get("status"),
        "raster_kind": selection.get("raster_kind"),
        "asset_count": len(selection.get("asset_ids") or []),
        "asset_identity_fingerprint": _asset_identity_fingerprint(package),
    }


def _migration_package(package: dict[str, Any], source_id: str) -> dict[str, Any]:
    """Attach source-scoped fields normally added by the preview pipeline."""
    prepared = copy.deepcopy(package)
    prepared["source_id"] = source_id
    selection = prepared.setdefault("selection", {})
    product = str(selection.get("product_uid") or prepared.get("package_id") or "")
    uid = decision_uid(source_id, product)
    prepared["decision_uid"] = uid
    selection["decision_uid"] = uid
    return prepared


def _alternative_summary(
    package: dict[str, Any],
    current: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    proposed = _package_summary(package)
    selection = package.get("selection") or {}
    changes = _changes(current, proposed)
    return {
        **proposed,
        "label": (
            selection.get("display_name")
            or selection.get("provider_scene_id")
            or selection.get("product_type")
            or package.get("package_root_relative")
            or package.get("package_id")
        ),
        "changes": changes,
        "rebuild": _rebuild_targets(manifest, changes),
    }


def _match_by_content(
    old_paths: set[str],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Nowe pakiety zawierajace WSZYSTKIE stare assety measurement.

    Porownanie po sciezkach, a nie po `package_id`, jest jedynym sposobem, zeby przesledzic
    rozpad pakietu na akwizycje: identyfikator sie zmienia, pliki nie.
    """
    if not old_paths:
        return []
    matches: list[dict[str, Any]] = []
    for package in candidates:
        available = {
            str(asset.get("relative_path") or "") for asset in package.get("assets") or []
        }
        if old_paths <= available:
            matches.append(package)
    return matches


def _changes(current: dict[str, Any], proposed: dict[str, Any] | None) -> list[str]:
    if proposed is None:
        return ["package_missing"]
    changed = [
        key
        for key in (
            "package_id",
            "product_type",
            "status",
            "raster_kind",
            "asset_count",
            "asset_identity_fingerprint",
        )
        if current.get(key) != proposed.get(key)
    ]
    return changed


def _rebuild_targets(manifest: dict[str, Any], changes: list[str]) -> list[str]:
    """Ktore derywaty traca waznosc przy tej zmianie.

    Zmiana samego typu produktu nie rusza pikseli, ale zmiana zestawu assetow albo rodzaju
    rastra unieważnia widok roboczy i piramide zbudowana z poprzedniego zestawu.
    """
    if not ({"asset_count", "asset_identity_fingerprint", "raster_kind", "package_id"} & set(changes)):
        return []
    working = manifest.get("working_view") or {}
    targets: list[str] = []
    if working.get("raster_ref") or working.get("variant_id"):
        targets.append("working_view")
    if (manifest.get("display") or {}).get("overview_type") == "project_vrt_ovr":
        targets.append("overview")
    return targets


def plan(project_id: str, scan: Any) -> dict[str, Any]:
    """Policz plan migracji projektu bez zapisywania czegokolwiek.

    `scan` to funkcja `(root, provider) -> lista pakietow`; wstrzykiwana, zeby plan dal sie
    policzyc bez sieci w testach i zeby wolajacy decydowal o cache.
    """
    from services.scene_sources import load_scene_sources, resolve_source_root

    sources = {
        str(source.get("source_id") or ""): source
        for source in (load_scene_sources(project_id).get("sources") or [])
    }
    scanned: dict[str, list[dict[str, Any]] | None] = {}
    entries: list[dict[str, Any]] = []

    for scene_id in list_scene_ids(project_id):
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        source_package = manifest.get("source_package") or {}
        source_id = str(source_package.get("source_id") or scene.get("source_id") or "")
        current = _package_summary(source_package)
        entry: dict[str, Any] = {
            "scene_id": scene_id,
            "filename": scene.get("filename") or manifest.get("filename"),
            "source_id": source_id,
            "current": current,
        }

        source = sources.get(source_id)
        if source is None or resolve_source_root(source) is None:
            entry.update({
                "classification": CLASS_SOURCE_UNAVAILABLE,
                "proposed": None,
                "changes": [],
                "rebuild": [],
                "reason": "Źródło sceny jest niedostępne - migracji nie da się policzyć",
            })
            entries.append(entry)
            continue

        if source_id not in scanned:
            root = resolve_source_root(source)
            try:
                with graph_v2_scan():
                    scanned[source_id] = scan(root, str(source.get("provider") or "generic"))
            except Exception as exc:  # skan jest I/O — brak dostepu nie moze wywrocic planu
                scanned[source_id] = None
                entry["scan_error"] = f"{type(exc).__name__}: {exc}"
        candidates = scanned.get(source_id)
        if candidates is None:
            entry.update({
                "classification": CLASS_SOURCE_UNAVAILABLE,
                "proposed": None,
                "changes": [],
                "rebuild": [],
                "reason": "Skan źródła nie powiódł się",
            })
            entries.append(entry)
            continue

        products = [item for item in candidates if item.get("package_kind") != "archive"]
        by_id = {str(item.get("package_id")): item for item in products}
        proposed_package = by_id.get(str(current["package_id"]))
        matched_by_content: list[dict[str, Any]] = []
        old_paths = _measurement_paths(source_package)
        if proposed_package is None:
            matched_by_content = _match_by_content(old_paths, products)
            if len(matched_by_content) == 1:
                proposed_package = matched_by_content[0]

        proposed = _package_summary(proposed_package) if proposed_package else None
        changes = _changes(current, proposed)
        rebuild = _rebuild_targets(manifest, changes)

        if proposed is None:
            classification = CLASS_NEEDS_SELECTION
            if matched_by_content:
                reason = f"Stary pakiet rozpada się na {len(matched_by_content)} nowych"
            elif not old_paths:
                # Scena `decision_required` nigdy nie wybrala assetow, wiec nie ma po czym
                # sledzic jej ciaglosci. To NIE jest to samo co „pliki zniknely".
                reason = (
                    "Scena nie miała wybranych assetów (czekała na decyzję), "
                    "więc nie da się wskazać jej odpowiednika"
                )
            else:
                reason = "Nowy skan nie zawiera pakietu odpowiadającego tej scenie"
        elif not changes:
            classification, reason = CLASS_UNCHANGED, "Bez zmian"
        elif str(proposed.get("status")) in _UNAMBIGUOUS:
            classification = CLASS_AUTO
            reason = "Nowy wybór jest jednoznaczny: " + ", ".join(changes)
        else:
            classification = CLASS_NEEDS_SELECTION
            reason = f"Nowy wybór wymaga decyzji ({proposed.get('status')})"

        entry.update({
            "classification": classification,
            "proposed": proposed,
            "changes": changes,
            "rebuild": rebuild,
            "reason": reason,
        })
        if proposed_package is not None:
            # Internal payload used only by apply().  The public dry-run strips it so the
            # API remains a compact review document rather than a duplicate inventory.
            entry["_proposed_package"] = _migration_package(proposed_package, source_id)
        if classification == CLASS_NEEDS_SELECTION and products:
            eligible = [
                package
                for package in products
                if str((package.get("selection") or {}).get("status")) in _UNAMBIGUOUS
            ]
            entry["alternatives"] = [
                _alternative_summary(package, current, manifest)
                for package in eligible
            ]
            entry["_candidate_packages"] = {
                str(package.get("package_id")): _migration_package(package, source_id)
                for package in eligible
            }
        entries.append(entry)

    return {
        "schema_name": MIGRATION_SCHEMA_NAME,
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "project_id": project_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(entries),
        "scenes": entries,
    }


def public_plan(migration_plan: dict[str, Any]) -> dict[str, Any]:
    """Return the reviewable plan without apply-only package inventories."""
    result = copy.deepcopy(migration_plan)
    for entry in result.get("scenes") or []:
        entry.pop("_proposed_package", None)
        entry.pop("_candidate_packages", None)
    return result


def apply_decisions(
    migration_plan: dict[str, Any],
    decisions: dict[str, str] | None,
) -> dict[str, Any]:
    """Resolve reviewed scene successors without accepting an unoffered package."""
    if not decisions:
        return migration_plan
    result = copy.deepcopy(migration_plan)
    entries = {str(entry.get("scene_id") or ""): entry for entry in result.get("scenes") or []}
    unknown_scenes = sorted(set(decisions) - set(entries))
    if unknown_scenes:
        raise ValueError(f"Migration decisions reference unknown scenes: {', '.join(unknown_scenes)}")
    for scene_id, package_id in decisions.items():
        entry = entries[scene_id]
        if entry.get("classification") != CLASS_NEEDS_SELECTION:
            raise ValueError(f"Scene {scene_id} does not require a migration decision")
        candidates = entry.get("_candidate_packages") or {}
        package = candidates.get(str(package_id))
        if not isinstance(package, dict):
            raise ValueError(f"Package {package_id} is not an offered successor for scene {scene_id}")
        package.setdefault("selection", {})["selected_by"] = "user"
        alternative = next(
            (
                item
                for item in entry.get("alternatives") or []
                if str(item.get("package_id") or "") == str(package_id)
            ),
            None,
        )
        if not isinstance(alternative, dict):
            raise ValueError(f"Migration alternative {package_id} is incomplete for scene {scene_id}")
        entry.update({
            "classification": CLASS_AUTO,
            "proposed": {
                key: alternative.get(key)
                for key in (
                    "package_id",
                    "package_root_relative",
                    "product_type",
                    "status",
                    "raster_kind",
                    "asset_count",
                    "asset_identity_fingerprint",
                )
            },
            "changes": list(alternative.get("changes") or []),
            "rebuild": list(alternative.get("rebuild") or []),
            "reason": "Następca wybrany jawnie przez użytkownika",
            "_proposed_package": package,
            "decision": {"package_id": str(package_id), "selected_by": "user"},
        })
    result["summary"] = summarize(list(entries.values()))
    return result


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Pieć liczb, ktorych wymaga narzedzie dry-run z sekcji P2.2."""
    by_class: dict[str, int] = {}
    changed_fields: dict[str, int] = {}
    rebuild: dict[str, int] = {}
    for entry in entries:
        by_class[entry["classification"]] = by_class.get(entry["classification"], 0) + 1
        for field in entry.get("changes") or []:
            changed_fields[field] = changed_fields.get(field, 0) + 1
        for target in entry.get("rebuild") or []:
            rebuild[target] = rebuild.get(target, 0) + 1
    return {
        "scenes": len(entries),
        "unchanged": by_class.get(CLASS_UNCHANGED, 0),
        "auto": by_class.get(CLASS_AUTO, 0),
        "needs_selection": by_class.get(CLASS_NEEDS_SELECTION, 0),
        "source_unavailable": by_class.get(CLASS_SOURCE_UNAVAILABLE, 0),
        "changed_fields": dict(sorted(changed_fields.items())),
        "rebuild": dict(sorted(rebuild.items())),
    }


def backup_path(project_id: str, created_at: str) -> Path:
    stamp = created_at.replace(":", "").replace("-", "").replace(".", "")[:15]
    target = project_dir(project_id) / ".migration_backups" / f"graph_v2_{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def apply(
    project_id: str,
    migration_plan: dict[str, Any],
    *,
    backup: bool = True,
    persist_auto: PersistAutomaticMigration | None = None,
) -> dict[str, Any]:
    """Zastosuj plan: automatyczne migracje, reszta dostaje `migration_required`.

    Derywaty NIE sa kasowane — plan wymienia je w `rebuild`, a ich odbudowa jest osobna
    decyzja. Kasowanie przed potwierdzeniem nowego odciska zamienia odwracalna zmiane
    w nieodwracalna.
    """
    entries = list(migration_plan.get("scenes") or [])
    if any(entry.get("classification") == CLASS_AUTO for entry in entries) and persist_auto is None:
        raise ValueError("persist_auto is required for automatic scene-package migration")

    touched = [entry for entry in entries if entry.get("classification") != CLASS_UNCHANGED]
    created_at = str(migration_plan.get("created_at") or datetime.now(timezone.utc).isoformat())
    target = backup_path(project_id, created_at) if backup and touched else None
    migrated: list[str] = []
    flagged: list[str] = []

    # Back up every document that apply may modify before the first write.  A failed
    # callback can therefore be recovered without combining pre- and post-migration files.
    if target is not None:
        for entry in touched:
            scene_id = str(entry.get("scene_id") or "")
            for document in ("scene_manifest", "scene"):
                payload = load_scene_json(project_id, scene_id, document, default={})
                if payload:
                    write_json_atomic(target / f"{scene_id}.{document}.json", payload)

    for entry in entries:
        scene_id = str(entry.get("scene_id") or "")
        classification = str(entry.get("classification") or "")
        if classification == CLASS_UNCHANGED:
            continue
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        if not manifest:
            continue
        if classification == CLASS_AUTO:
            proposed_package = entry.get("_proposed_package")
            if not isinstance(proposed_package, dict):
                raise ValueError(f"Migration plan for {scene_id} has no proposed package payload")
            selection = proposed_package.get("selection") or {}
            assert persist_auto is not None  # validated before the first backup/write
            persist_auto(
                project_id,
                scene_id,
                copy.deepcopy(proposed_package),
                copy.deepcopy(selection),
            )
            # Reload because the canonical import writer replaced source_package, scene
            # fields and possibly derivative lineage while preserving the scene directory.
            manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
            manifest.setdefault("migration", {})["graph_v2"] = {
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "from": entry.get("current"),
                "to": entry.get("proposed"),
                "changes": entry.get("changes") or [],
                "rebuild": entry.get("rebuild") or [],
            }
            save_scene_json(project_id, scene_id, "scene_manifest", manifest)
            migrated.append(scene_id)
            continue

        scene = load_scene_json(project_id, scene_id, "scene", default={})
        if scene:
            scene["preparation_status"] = STATUS_MIGRATION_REQUIRED
            save_scene_json(project_id, scene_id, "scene", scene)
        manifest.setdefault("working_view", {})["preparation_status"] = STATUS_MIGRATION_REQUIRED
        manifest.setdefault("migration", {})["graph_v2"] = {
            "flagged_at": datetime.now(timezone.utc).isoformat(),
            "classification": classification,
            "reason": entry.get("reason"),
        }
        save_scene_json(project_id, scene_id, "scene_manifest", manifest)
        flagged.append(scene_id)

    return {
        "project_id": project_id,
        "backup_path": str(target) if target else None,
        "migrated": migrated,
        "flagged": flagged,
        "summary": migration_plan.get("summary") or {},
    }
