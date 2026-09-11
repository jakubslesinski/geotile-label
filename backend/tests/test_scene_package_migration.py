"""Migracja projektow na kontrakt grafu v2 (DESIGN_DECISIONS.md, scene-import P2.2).

Pomiar na rzeczywistych zrodlach pokazal, ze wlaczenie flagi daje CZTERY rozne skutki dla
tozsamosci scen — od zera zmian (Airbus, generic) po calkowita wymiane identyfikatorow
(ICEYE: 1 pakiet → 84, zaden `package_id` nie przezywa). Testy odwzorowuja kazdy z nich
na fixture, bo to od nich zalezy, czy migracja moze byc automatyczna.

Zasada, ktorej pilnuja: **migracja nie zgaduje**. Scena bez jednoznacznego nastepcy dostaje
`migration_required`, a nie arbitralnie wybrany produkt.

Uruchomienie: pytest backend/tests/test_scene_package_migration.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db import storage  # noqa: E402
from routers import scene_import as scene_import_router  # noqa: E402
from services.scene_packages import migration  # noqa: E402

_CANONICAL_SAVE_PACKAGE_SCENE = scene_import_router._save_package_scene


def _asset(asset_id: str, relative: str) -> dict:
    return {
        "asset_id": asset_id,
        "relative_path": relative,
        "package_relative_path": relative.split("/")[-1],
        "role": "raster_candidate",
        "asset_role": "measurement",
        "size": 100,
        "mtime_ns": 1,
    }


def _stored_scene(
    project: str,
    scene_id: str,
    *,
    package_id: str,
    asset_ids: list[str],
    assets: list[dict],
    product_type: str = "UNRESOLVED",
    status: str = "decision_required",
    raster_kind: str = "direct",
    with_working_view: bool = True,
) -> None:
    storage.save_scene_json(project, scene_id, "scene", {
        "id": scene_id,
        "source_id": "src1",
        "package_id": package_id,
        "filename": f"{scene_id}.tif",
    })
    storage.save_scene_json(project, scene_id, "scene_manifest", {
        "schema_version": 6,
        "source_package": {
            "package_id": package_id,
            "source_id": "src1",
            "provider": "capella",
            "package_root_relative": "DOSTAWA",
            "assets": assets,
            "selection": {
                "asset_ids": asset_ids,
                "identity_asset_ids": asset_ids,
                "product_type": product_type,
                "status": status,
                "raster_kind": raster_kind,
            },
        },
        "working_view": (
            {"variant_id": "variant_x", "raster_ref": {"storage": "source"}, "raster_kind": raster_kind}
            if with_working_view
            else {}
        ),
    })


def _new_package(package_id: str, asset_ids: list[str], assets: list[dict], *,
                 product_type: str = "GEO", status: str = "ready") -> dict:
    return {
        "package_id": package_id,
        "provider": "capella",
        "package_root_relative": "DOSTAWA",
        "assets": assets,
        "selection": {
            "asset_ids": asset_ids,
            "identity_asset_ids": asset_ids,
            "product_type": product_type,
            "status": status,
            "raster_kind": "direct",
        },
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path / "appdata")
    project_id = "p22"
    source_root = tmp_path / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    storage.project_dir(project_id).mkdir(parents=True, exist_ok=True)
    (storage.project_dir(project_id) / "scene_sources.json").write_text(
        json.dumps({
            "schema_name": "geotile_scene_sources",
            "schema_version": 3,
            "sources": [{
                "source_id": "src1",
                "provider": "capella",
                "root_path": str(source_root),
                "enabled": True,
            }],
        }),
        encoding="utf-8",
    )
    return project_id


def _scan_returning(packages: list[dict]):
    return lambda _root, _provider: list(packages)


def _persist_auto(project_id: str, scene_id: str, package: dict, selection: dict) -> None:
    """Small persistence adapter for service-level apply tests.

    Production injects the canonical scene-import writer.  These tests keep the adapter
    intentionally minimal so they exercise migration orchestration without GDAL.
    """
    manifest = storage.load_scene_json(project_id, scene_id, "scene_manifest", default={})
    manifest["source_package"] = {**package, "selection": selection}
    storage.save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    scene = storage.load_scene_json(project_id, scene_id, "scene", default={})
    scene.update({
        "id": scene_id,
        "source_id": package["source_id"],
        "package_id": package["package_id"],
    })
    storage.save_scene_json(project_id, scene_id, "scene", scene)


# --- klasyfikacja -----------------------------------------------------------------------


def test_identical_package_is_unchanged(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets,
                  product_type="GEO", status="ready")
    result = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    assert result["summary"]["unchanged"] == 1
    assert result["scenes"][0]["changes"] == []


def test_better_product_on_the_same_package_migrates_automatically(project):
    """Przypadek Capelli: ten sam `package_id`, `UNRESOLVED/decision_required` → `GEO/ready`."""
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    result = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    entry = result["scenes"][0]
    assert entry["classification"] == migration.CLASS_AUTO
    assert set(entry["changes"]) >= {"product_type", "status"}
    assert entry["proposed"]["product_type"] == "GEO"


def test_package_split_into_two_acquisitions_needs_a_decision(project):
    """Przypadek `Woronez (Pogonovo)`: jedna dostawa, dwie akwizycje po migracji."""
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1", "a2"], assets=assets)
    packages = [
        _new_package("pkg-a", ["a1"], [assets[0]]),
        _new_package("pkg-b", ["a2"], [assets[1]]),
    ]
    result = migration.plan(project, _scan_returning(packages))
    entry = result["scenes"][0]
    assert entry["classification"] == migration.CLASS_NEEDS_SELECTION
    assert entry["proposed"] is None
    assert {item["package_id"] for item in entry["alternatives"]} == {"pkg-a", "pkg-b"}


def test_merged_source_without_a_successor_needs_a_decision(project):
    """Przypadek ICEYE: stara scena BYLA calym zrodlem, wiec nie ma jednego nastepcy."""
    assets = [_asset(f"a{index}", f"DOSTAWA/scene_{index}.tif") for index in range(1, 4)]
    _stored_scene(project, "s1", package_id="pkg-root", asset_ids=[], assets=assets)
    packages = [_new_package(f"pkg{index}", [f"a{index}"], [assets[index - 1]]) for index in range(1, 4)]
    result = migration.plan(project, _scan_returning(packages))
    entry = result["scenes"][0]
    assert entry["classification"] == migration.CLASS_NEEDS_SELECTION
    assert "decyzję" in entry["reason"] or "nie da się" in entry["reason"]


def test_renamed_package_is_followed_by_content(project):
    """Zmiana `package_id` przy tych samych plikach nie moze wygladac jak utrata sceny."""
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="stary", asset_ids=["a1"], assets=assets,
                  product_type="GEO", status="ready")
    result = migration.plan(project, _scan_returning([_new_package("nowy", ["a1"], assets)]))
    entry = result["scenes"][0]
    assert entry["classification"] == migration.CLASS_AUTO
    assert entry["changes"] == ["package_id"]
    assert entry["proposed"]["package_id"] == "nowy"


def test_unavailable_source_is_reported_not_guessed(project, monkeypatch):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets)
    from services import scene_sources

    monkeypatch.setattr(scene_sources, "resolve_source_root", lambda _source: None)
    result = migration.plan(project, _scan_returning([]))
    assert result["summary"]["source_unavailable"] == 1
    assert result["scenes"][0]["proposed"] is None


def test_scan_failure_does_not_break_the_plan(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets)

    def failing(_root, _provider):
        raise OSError("share unreachable")

    result = migration.plan(project, failing)
    assert result["summary"]["source_unavailable"] == 1
    assert "OSError" in result["scenes"][0]["scan_error"]


# --- co plan raportuje ------------------------------------------------------------------


def test_plan_reports_the_five_categories_the_tool_promises(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets)
    summary = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))["summary"]
    assert set(summary) >= {
        "scenes", "unchanged", "auto", "needs_selection", "source_unavailable",
        "changed_fields", "rebuild",
    }


def test_changed_asset_set_marks_the_working_view_for_rebuild(project):
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets,
                  product_type="GEO", status="ready")
    result = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1", "a2"], assets)]))
    assert result["scenes"][0]["rebuild"] == ["working_view"]


def test_same_asset_count_but_different_identity_is_detected(project):
    old_asset = _asset("a1", "DOSTAWA/a.tif")
    new_asset = _asset("a2", "DOSTAWA/b.tif")
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=[old_asset],
                  product_type="GEO", status="ready")

    result = migration.plan(
        project,
        _scan_returning([_new_package("pkg1", ["a2"], [new_asset])]),
    )
    entry = result["scenes"][0]
    assert entry["classification"] == migration.CLASS_AUTO
    assert entry["current"]["asset_count"] == entry["proposed"]["asset_count"] == 1
    assert "asset_count" not in entry["changes"]
    assert "asset_identity_fingerprint" in entry["changes"]
    assert entry["rebuild"] == ["working_view"]


def test_same_asset_path_with_changed_file_snapshot_is_detected(project):
    old_asset = _asset("a1", "DOSTAWA/a.tif")
    new_asset = {**old_asset, "size": 101, "mtime_ns": 2}
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=[old_asset],
                  product_type="GEO", status="ready")

    result = migration.plan(
        project,
        _scan_returning([_new_package("pkg1", ["a1"], [new_asset])]),
    )
    assert result["scenes"][0]["changes"] == ["asset_identity_fingerprint"]


def test_product_type_change_alone_does_not_force_a_rebuild(project):
    """Zmiana etykiety produktu nie rusza pikseli, wiec derywaty pozostaja wazne."""
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets,
                  product_type="UNRESOLVED", status="ready")
    result = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    assert result["scenes"][0]["changes"] == ["product_type"]
    assert result["scenes"][0]["rebuild"] == []


def test_plan_writes_nothing(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets)
    before = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    assert storage.load_scene_json(project, "s1", "scene_manifest", default={}) == before


def test_public_plan_hides_apply_only_package_inventory(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    internal = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    public = migration.public_plan(internal)
    assert "_proposed_package" in internal["scenes"][0]
    assert "_proposed_package" not in public["scenes"][0]


def test_public_plan_exposes_successor_summaries_but_not_candidate_payloads(project):
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="old", asset_ids=["a1", "a2"], assets=assets)
    internal = migration.plan(project, _scan_returning([
        _new_package("pkg-a", ["a1"], [assets[0]]),
        _new_package("pkg-b", ["a2"], [assets[1]]),
    ]))

    public = migration.public_plan(internal)

    assert "_candidate_packages" in internal["scenes"][0]
    assert "_candidate_packages" not in public["scenes"][0]
    assert {item["package_id"] for item in public["scenes"][0]["alternatives"]} == {
        "pkg-a",
        "pkg-b",
    }


# --- zapis ------------------------------------------------------------------------------


def test_apply_backs_up_every_touched_manifest(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    applied = migration.apply(project, plan, persist_auto=_persist_auto)
    backup = pathlib.Path(applied["backup_path"])
    assert (backup / "s1.scene_manifest.json").is_file()
    assert (backup / "s1.scene.json").is_file()


def test_apply_flags_ambiguous_scenes_instead_of_choosing_for_the_user(project):
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1", "a2"], assets=assets)
    plan = migration.plan(project, _scan_returning([
        _new_package("pkg-a", ["a1"], [assets[0]]),
        _new_package("pkg-b", ["a2"], [assets[1]]),
    ]))
    applied = migration.apply(project, plan, persist_auto=_persist_auto)
    assert applied["flagged"] == ["s1"] and applied["migrated"] == []
    scene = storage.load_scene_json(project, "s1", "scene", default={})
    assert scene["preparation_status"] == migration.STATUS_MIGRATION_REQUIRED


def test_apply_records_provenance_of_the_automatic_migration(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    migration.apply(project, plan, persist_auto=_persist_auto)
    manifest = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    record = manifest["migration"]["graph_v2"]
    assert record["from"]["product_type"] == "UNRESOLVED"
    assert record["to"]["product_type"] == "GEO"
    assert record["applied_at"]


def test_apply_does_not_bump_the_manifest_schema_version(project):
    """Podbicie wersji unieważniłoby atrybuty WSZYSTKICH adnotacji (sekcja 17.3).

    Migracja zapisuje swoją proweniencję w osobnym bloku, więc kaskada obejmuje wyłącznie
    sceny, które faktycznie się zmieniły, a nie cały magazyn.
    """
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    migration.apply(project, plan, persist_auto=_persist_auto)
    manifest = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    assert manifest["schema_version"] == 6


def test_apply_leaves_derived_artifacts_in_place(project):
    """Kasowanie derywatow przed potwierdzeniem nowego odcisku jest nieodwracalne."""
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets,
                  product_type="GEO", status="ready")
    derived = storage.project_dir(project) / "derived_scenes" / "s1" / "variant_x"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "scene.vrt").write_text("<VRTDataset/>", encoding="utf-8")

    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1", "a2"], assets)]))
    migration.apply(project, plan, persist_auto=_persist_auto)

    assert (derived / "scene.vrt").is_file()
    assert plan["scenes"][0]["rebuild"] == ["working_view"]


def test_unchanged_scene_is_not_touched_by_apply(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets,
                  product_type="GEO", status="ready")
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    before = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    applied = migration.apply(project, plan, persist_auto=_persist_auto)
    assert applied["migrated"] == [] and applied["flagged"] == []
    assert storage.load_scene_json(project, "s1", "scene_manifest", default={}) == before


def test_backup_can_be_skipped_only_explicitly(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    applied = migration.apply(project, plan, backup=False, persist_auto=_persist_auto)
    assert applied["backup_path"] is None
    assert not (storage.project_dir(project) / ".migration_backups").exists()


def test_apply_requires_a_real_persistence_adapter_before_any_write(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=[], assets=assets)
    plan = migration.plan(project, _scan_returning([_new_package("pkg1", ["a1"], assets)]))
    before = storage.load_scene_json(project, "s1", "scene_manifest", default={})

    with pytest.raises(ValueError, match="persist_auto"):
        migration.apply(project, plan)

    assert storage.load_scene_json(project, "s1", "scene_manifest", default={}) == before
    assert not (storage.project_dir(project) / ".migration_backups").exists()


def test_explicit_successor_decision_migrates_the_existing_scene(project):
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="old", asset_ids=["a1", "a2"], assets=assets)
    plan = migration.plan(project, _scan_returning([
        _new_package("pkg-a", ["a1"], [assets[0]]),
        _new_package("pkg-b", ["a2"], [assets[1]]),
    ]))

    decided = migration.apply_decisions(plan, {"s1": "pkg-b"})
    applied = migration.apply(project, decided, persist_auto=_persist_auto)

    scene = storage.load_scene_json(project, "s1", "scene", default={})
    manifest = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    assert applied["migrated"] == ["s1"] and applied["flagged"] == []
    assert scene["id"] == "s1" and scene["package_id"] == "pkg-b"
    assert manifest["source_package"]["selection"]["selected_by"] == "user"


def test_unoffered_successor_decision_is_rejected_before_apply(project):
    assets = [_asset("a1", "DOSTAWA/a.tif"), _asset("a2", "DOSTAWA/b.tif")]
    _stored_scene(project, "s1", package_id="old", asset_ids=["a1", "a2"], assets=assets)
    plan = migration.plan(project, _scan_returning([
        _new_package("pkg-a", ["a1"], [assets[0]]),
        _new_package("pkg-b", ["a2"], [assets[1]]),
    ]))

    with pytest.raises(ValueError, match="not an offered successor"):
        migration.apply_decisions(plan, {"s1": "not-present"})


def test_apply_endpoint_forwards_reviewed_successor_decisions(project, monkeypatch):
    raw_plan = {"project_id": project, "scenes": [], "summary": {}}
    decided_plan = {**raw_plan, "reviewed": True}
    captured: dict[str, object] = {}
    monkeypatch.setattr(scene_import_router, "_require_project", lambda _project_id: None)
    monkeypatch.setattr(scene_import_router, "migration_plan", lambda _project_id, _scan: raw_plan)

    def decide(plan, decisions):
        captured["decisions"] = decisions
        assert plan is raw_plan
        return decided_plan

    def apply(_project_id, plan, **kwargs):
        captured["plan"] = plan
        captured["backup"] = kwargs["backup"]
        return {"migrated": ["s1"], "flagged": []}

    monkeypatch.setattr(scene_import_router, "apply_migration_decisions", decide)
    monkeypatch.setattr(scene_import_router, "apply_migration", apply)
    body = scene_import_router.SceneMigrationApplyRequest(
        backup=True,
        decisions={"s1": "pkg-b"},
    )

    result = scene_import_router.scene_import_migration_apply(project, body)

    assert result == {"migrated": ["s1"], "flagged": []}
    assert captured == {
        "decisions": {"s1": "pkg-b"},
        "plan": decided_plan,
        "backup": True,
    }


def test_canonical_apply_preserves_scene_id_manifest_assets_and_annotations(project):
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(
        project,
        "s1",
        package_id="old-package",
        asset_ids=["a1"],
        assets=assets,
        product_type="GEO",
        status="ready",
    )
    annotations = [{"id": "ann-1", "class_id": "vessel", "geometry": {"type": "Point"}}]
    storage.save_scene_json(project, "s1", "annotations", annotations)
    stored_annotations = storage.load_scene_json(project, "s1", "annotations", default=[])
    proposed = _new_package(
        "new-package",
        ["a1"],
        assets,
        product_type="GEO",
        status="prepare_required",
    )
    plan = migration.plan(project, _scan_returning([proposed]))

    migration.apply(
        project,
        plan,
        persist_auto=lambda pid, sid, package, selection: _CANONICAL_SAVE_PACKAGE_SCENE(
            pid,
            sid,
            package,
            selection,
            accept_source_change=True,
        ),
    )

    assert storage.list_scene_ids(project) == ["s1"]
    scene = storage.load_scene_json(project, "s1", "scene", default={})
    manifest = storage.load_scene_json(project, "s1", "scene_manifest", default={})
    assert scene["id"] == "s1"
    assert scene["package_id"] == "new-package"
    assert manifest["source_package"]["package_id"] == "new-package"
    assert [item["asset_id"] for item in manifest["source_package"]["assets"]] == ["a1"]
    assert manifest["migration"]["graph_v2"]["to"]["package_id"] == "new-package"
    assert storage.load_scene_json(project, "s1", "annotations", default=[]) == stored_annotations


def test_scan_runs_under_the_v2_contract_regardless_of_the_current_flag(project, monkeypatch):
    """Plan pokazuje, co się STANIE po włączeniu flagi, więc skanuje tak, jakby była włączona."""
    from services.scene_packages.contracts import FLAG_GRAPH_V2, graph_v2_enabled

    monkeypatch.delenv(FLAG_GRAPH_V2, raising=False)
    seen: list[bool] = []
    assets = [_asset("a1", "DOSTAWA/scene.tif")]
    _stored_scene(project, "s1", package_id="pkg1", asset_ids=["a1"], assets=assets)

    def scan(_root, _provider):
        seen.append(graph_v2_enabled())
        return [_new_package("pkg1", ["a1"], assets)]

    migration.plan(project, scan)
    assert seen == [True]
    # Flaga wraca do stanu sprzed planu.
    assert graph_v2_enabled() is False
