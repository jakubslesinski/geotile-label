from __future__ import annotations

from pathlib import Path

from routers import scenes
from routers.scene_import import DEFAULT_AUTO_FULLRES_COG_ENABLED
from services.scene_packages.fullres_cog_builder import (
    FULLRES_CANDIDATE_NAME,
    FULLRES_COG_NAME,
    fullres_dir,
    fullres_state_path,
)
from services.scene_packages.fullres_derivative import COG_PROFILE_VERSION
from services.scene_packages.fullres_publication import (
    STATE_ACTIVE,
    STATE_CANDIDATE_READY,
    PublicationRecord,
    advance,
    read_publication_record,
    write_publication_record,
)


def _candidate_record(payload: dict) -> PublicationRecord:
    record = PublicationRecord(payload={**payload, "validation": {"valid": True}})
    advance(record, "validating")
    advance(record, STATE_CANDIDATE_READY)
    return record


def test_recovery_finishes_activation_after_candidate_was_already_renamed(
    tmp_path: Path,
    monkeypatch,
):
    project_id = "project-1"
    scene_id = "scene-1"
    variant = "variant-1"
    payload = {
        "scene_id": scene_id,
        "source_fingerprint": "source-fp",
        "variant_id": variant,
        "cog_profile_version": COG_PROFILE_VERSION,
        "expected_source_revision": "overview-fp",
    }
    directory = fullres_dir(tmp_path, scene_id, variant)
    directory.mkdir(parents=True)
    active = directory / FULLRES_COG_NAME
    active.write_bytes(b"already-published-cog")
    state_path = fullres_state_path(tmp_path, scene_id, variant)
    write_publication_record(state_path, _candidate_record(payload))

    documents = {
        "scene_manifest": {"working_view": {}},
        "scene": {"id": scene_id},
    }
    monkeypatch.setattr(scenes, "project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        scenes,
        "load_scene_json",
        lambda _pid, _sid, name, default=None: documents.get(name, default),
    )
    monkeypatch.setattr(
        scenes,
        "save_scene_json",
        lambda _pid, _sid, name, value: documents.__setitem__(name, value),
    )
    monkeypatch.setattr(scenes, "invalidate_raster_sessions", lambda: None)
    monkeypatch.setattr(
        "services.scene_raster_resolver.invalidate_scene_render_caches",
        lambda _pid, _sid: None,
    )

    resumed = scenes._resume_candidate_publication(
        project_id,
        scene_id,
        {"variant": variant},
        payload,
    )

    assert resumed is True
    assert active.read_bytes() == b"already-published-cog"
    assert not (directory / FULLRES_CANDIDATE_NAME).exists()
    assert documents["scene_manifest"]["working_view"]["fullres_derivative"][
        "status"
    ] == "active"
    assert documents["scene"]["fullres_derivative_status"] == "ready"
    assert read_publication_record(state_path).state == STATE_ACTIVE


def test_recovery_does_not_activate_candidate_for_another_fingerprint(
    tmp_path: Path,
    monkeypatch,
):
    scene_id = "scene-1"
    variant = "variant-1"
    old_payload = {
        "scene_id": scene_id,
        "source_fingerprint": "old-source",
        "variant_id": variant,
        "cog_profile_version": COG_PROFILE_VERSION,
        "expected_source_revision": "overview-fp",
    }
    directory = fullres_dir(tmp_path, scene_id, variant)
    directory.mkdir(parents=True)
    (directory / FULLRES_CANDIDATE_NAME).write_bytes(b"stale-candidate")
    write_publication_record(
        fullres_state_path(tmp_path, scene_id, variant),
        _candidate_record(old_payload),
    )
    monkeypatch.setattr(scenes, "project_dir", lambda _project_id: tmp_path)

    assert scenes._resume_candidate_publication(
        "project-1",
        scene_id,
        {"variant": variant},
        {**old_payload, "source_fingerprint": "new-source"},
    ) is False
    assert not (directory / FULLRES_COG_NAME).exists()


def test_fullres_automatic_start_has_one_runtime_rollback_switch(monkeypatch):
    project_config: dict = {}
    monkeypatch.setattr(
        scenes,
        "load_json",
        lambda *_args, **_kwargs: project_config,
    )
    monkeypatch.delenv("GEOTILE_AUTO_FULLRES_COG_V2", raising=False)
    # Projekt bez zapisanego ustawienia: automat jest WYLACZONY. Tak opisuje to
    # dokumentacja uzytkownika (docs/input-data/produkty-pochodne.md: budowa COG kosztuje
    # dziesiatki minut), tak samo ustawia sie przelacznik w interfejsie.
    assert DEFAULT_AUTO_FULLRES_COG_ENABLED is False
    # Domyslna wartosc tutaj MUSI byc ta sama co w `scene_import` — gdyby sie rozjechaly,
    # przelacznik pokazywalby OFF, a backend i tak startowalby budowe.
    assert (
        scenes._fullres_auto_start_enabled("project-1")
        is DEFAULT_AUTO_FULLRES_COG_ENABLED
    )
    project_config["auto_fullres_cog_enabled"] = False
    assert scenes._fullres_auto_start_enabled("project-1") is False
    project_config["auto_fullres_cog_enabled"] = True
    assert scenes._fullres_auto_start_enabled("project-1") is True
    for disabled in ("0", "false", "NO", "off"):
        monkeypatch.setenv("GEOTILE_AUTO_FULLRES_COG_V2", disabled)
        assert scenes._fullres_auto_start_enabled("project-1") is False
