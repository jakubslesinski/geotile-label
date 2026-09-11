from __future__ import annotations

from routers import scene_import


def test_scene_import_config_fields_are_updated_independently(monkeypatch):
    stored: dict = {}

    monkeypatch.setattr(
        scene_import,
        "load_json",
        lambda *_args, **_kwargs: dict(stored),
    )
    monkeypatch.setattr(
        scene_import,
        "save_json",
        lambda _project_id, _name, payload: stored.update(payload),
    )

    initial = scene_import._save_project_import_config(
        "project-1",
        import_mode="prepare_all",
    )
    assert initial["schema_version"] == 2
    assert initial["import_mode"] == "prepare_all"
    # Automat COG pelnej rozdzielczosci jest domyslnie WYLACZONY
    # (docs/input-data/produkty-pochodne.md).
    assert initial["auto_fullres_cog_enabled"] is False
    assert (
        initial["auto_fullres_cog_enabled"]
        is scene_import.DEFAULT_AUTO_FULLRES_COG_ENABLED
    )

    # Wlaczenie automatu nie rusza zapisanego trybu importu...
    updated = scene_import._save_project_import_config(
        "project-1",
        auto_fullres_cog_enabled=True,
    )
    assert updated["import_mode"] == "prepare_all"
    assert updated["auto_fullres_cog_enabled"] is True

    # ...a zmiana trybu importu nie gasi wlaczonego automatu. To jest wlasciwy dowod
    # niezaleznosci pol: gdyby zapis odtwarzal wartosc domyslna, zgasloby tu na False.
    final = scene_import._save_project_import_config(
        "project-1",
        import_mode="on_demand",
    )
    assert final["import_mode"] == "on_demand"
    assert final["auto_fullres_cog_enabled"] is True
