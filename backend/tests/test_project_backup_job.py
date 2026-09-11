"""P1.6 project-backup artifact regressions."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services import archive_io, project_backup


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    scene = root / "scenes" / "scene-1"
    scene.mkdir(parents=True)
    (scene / "scene.json").write_text("{}", encoding="utf-8")
    (scene / "annotations.json").write_text("[]", encoding="utf-8")
    (root / "classes.json").write_text("[]", encoding="utf-8")
    catalog = root / "tile_catalogs"
    catalog.mkdir()
    (catalog / "index.json").write_text("{}", encoding="utf-8")
    preview = catalog / "preview_cache"
    preview.mkdir()
    (preview / "large.png").write_bytes(b"excluded")
    monkeypatch.setattr(project_backup, "project_dir", lambda _project_id: root)
    monkeypatch.setattr(project_backup, "list_scene_ids", lambda _project_id: ["scene-1"])
    monkeypatch.setattr(
        project_backup,
        "load_scene_json",
        lambda *_args, **_kwargs: {"filename": "scene.tif"},
    )

    def load(_project_id, name, default=None):
        if name == "project":
            return {"name": "Backup test", "scene_folder": "E:/scenes"}
        return default

    monkeypatch.setattr(project_backup, "load_json", load)
    monkeypatch.setattr(archive_io, "ARCHIVE_RUNTIME_DIR", tmp_path / "runtime" / "archives")
    monkeypatch.setattr(
        archive_io,
        "PARTIAL_REGISTRY_PATH",
        tmp_path / "runtime" / "archive_partials.json",
    )
    return root


def test_backup_is_persistent_downloadable_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _configure(tmp_path, monkeypatch)
    result = project_backup.create_project_backup_artifact("project", artifact_id="job-1")

    assert result["downloadable"] is True
    assert result["archive_sha256"]
    assert Path(result["manifest_path"]).is_file()
    with zipfile.ZipFile(result["archive_path"]) as archive:
        names = set(archive.namelist())
    assert "backup_manifest.json" in names
    assert "scenes/scene-1/annotations.json" in names
    assert "tile_catalogs/index.json" in names
    assert not any("preview_cache" in name for name in names)
    assert not list((root / "artifacts").rglob("*.partial"))


def test_cancelled_backup_is_not_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _configure(tmp_path, monkeypatch)
    cancelled = False

    def progress(phase: str, current: int, _total: int) -> None:
        nonlocal cancelled
        if phase == "writing_backup" and current == 1:
            cancelled = True

    with pytest.raises(project_backup.ProjectBackupCancelled):
        project_backup.create_project_backup_artifact(
            "project",
            artifact_id="job-cancelled",
            should_cancel=lambda: cancelled,
            progress=progress,
        )

    backup_root = root / "artifacts" / "project_backups"
    assert not list(backup_root.glob("*.zip"))
    assert not list(backup_root.glob("*.partial"))
