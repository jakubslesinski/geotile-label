"""P1.6 gates for selectable, durable and cacheable export artifacts."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services import archive_io, artifact_exports, dataset_package, training_dataset


@pytest.fixture()
def artifact_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path / "project"
    dataset = project_root / "dataset_runs" / "run-1"
    images = dataset / "train" / "images"
    images.mkdir(parents=True)
    (images / "tile.png").write_bytes(b"image" * 1024)
    runtime = tmp_path / "runtime" / "archives"
    registry = tmp_path / "runtime" / "archive_partials.json"
    monkeypatch.setattr(artifact_exports, "project_dir", lambda _project_id: project_root)
    monkeypatch.setattr(training_dataset, "project_dir", lambda _project_id: project_root)
    monkeypatch.setattr(artifact_exports, "load_json", lambda *_args, **_kwargs: {"name": "Test"})
    monkeypatch.setattr(archive_io, "ARCHIVE_RUNTIME_DIR", runtime)
    monkeypatch.setattr(archive_io, "PARTIAL_REGISTRY_PATH", registry)
    return project_root, dataset


def test_selected_formats_and_identical_retry_hit_cache(
    artifact_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    project_root, dataset = artifact_workspace
    builds: list[tuple[str, ...]] = []

    def fake_build(_project_id, _dataset_dir, *, formats, package_dir, **_kwargs):
        selected = tuple(formats)
        builds.append(selected)
        target = Path(package_dir)
        for value in selected:
            output = target / value
            output.mkdir(parents=True, exist_ok=True)
            (output / "payload.txt").write_text(value, encoding="utf-8")
        return target

    monkeypatch.setattr(artifact_exports, "build_dataset_package", fake_build)
    first = artifact_exports.create_dataset_export_artifact(
        "project-1", dataset, dataset_run_id="run-1", formats=["coco"]
    )
    second = artifact_exports.create_dataset_export_artifact(
        "project-1", dataset, dataset_run_id="run-1", formats=["coco"]
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert builds == [("coco",)]
    assert first["cache_key"] == second["cache_key"]
    with zipfile.ZipFile(first["archive_path"]) as archive:
        assert archive.namelist() == ["coco/payload.txt"]
    assert not list((project_root / "artifacts").rglob("*.partial"))


def test_cancelled_export_removes_zip_partial_and_staging(
    artifact_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
):
    project_root, dataset = artifact_workspace
    cancelled = False

    def fake_build(_project_id, _dataset_dir, *, package_dir, **_kwargs):
        target = Path(package_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "first.bin").write_bytes(b"1" * 1024)
        (target / "second.bin").write_bytes(b"2" * 1024)
        return target

    def progress(phase: str, current: int, _total: int) -> None:
        nonlocal cancelled
        if phase == "writing_zip" and current == 1:
            cancelled = True

    monkeypatch.setattr(artifact_exports, "build_dataset_package", fake_build)
    with pytest.raises(artifact_exports.ArtifactExportCancelled):
        artifact_exports.create_dataset_export_artifact(
            "project-1",
            dataset,
            dataset_run_id="run-1",
            formats=["yolo"],
            should_cancel=lambda: cancelled,
            progress=progress,
        )

    export_root = project_root / "artifacts" / "dataset_exports"
    assert not list(export_root.glob("*.zip"))
    assert not list(export_root.glob("*.partial"))
    assert not list(export_root.glob(".*.partial"))


def test_cache_key_is_format_order_independent():
    left = artifact_exports.dataset_export_cache_key("run", ["voc", "yolo", "coco"])
    right = artifact_exports.dataset_export_cache_key("run", ["coco", "voc", "yolo"])
    assert left == right


def test_package_manifest_lists_only_selected_formats():
    manifest = dataset_package.build_package_manifest(
        "project",
        "run",
        {"name": "Project"},
        {"run_id": "run"},
        ["coco"],
    )
    assert manifest["selected_formats"] == ["coco"]
    assert manifest["formats"] == {"coco": "coco/annotations"}
    assert "yolo_aabb" not in manifest["artifacts"]
    assert "pascal_voc" not in manifest["artifacts"]
    with pytest.raises(ValueError, match="at least one"):
        dataset_package.normalize_package_formats([])


def test_package_stages_sidecars_without_mutating_published_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    run = tmp_path / "run"
    run.mkdir()
    (run / "dataset_run_manifest.json").write_text(
        '{"run_id":"run-1","classes":[{"id":0,"name":"object"}],"tiling_config":{"tile_size":64}}',
        encoding="utf-8",
    )
    (run / "tile_annotations.json").write_text("{}", encoding="utf-8")
    (run / "tile_annotation_links.json").write_text('{"annotations":[]}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()}

    monkeypatch.setattr(dataset_package, "require_dataset_exact_identities", lambda _manifest: None)
    monkeypatch.setattr(dataset_package, "load_json", lambda *_args, **_kwargs: {"name": "Project"})

    def fake_sidecars(_project_id, _dataset_dir, _format, _run_id=None, **kwargs):
        output = Path(kwargs["output_dir"])
        (output / "metadata").mkdir(parents=True, exist_ok=True)
        (output / "geotile_export_manifest.json").write_text("{}", encoding="utf-8")
        return {}

    def fake_coco(*_args, output_dir, **_kwargs):
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "annotations.json").write_text("{}", encoding="utf-8")
        return target

    monkeypatch.setattr(dataset_package, "generate_export_sidecars", fake_sidecars)
    monkeypatch.setattr(dataset_package, "export_coco_dataset", fake_coco)
    target = tmp_path / "package.partial"
    dataset_package.build_dataset_package(
        "project",
        run,
        run_id="run-1",
        formats=["coco"],
        package_dir=target,
    )

    after = {path.name: path.read_bytes() for path in run.iterdir() if path.is_file()}
    assert after == before
    assert (target / "coco" / "annotations.json").is_file()
    assert (target / "metadata" / "geotile_export_manifest.json").is_file()


def test_output_copy_is_atomic_and_bounded(
    artifact_workspace: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _project_root, dataset = artifact_workspace

    def fake_build(_project_id, _dataset_dir, *, package_dir, **_kwargs):
        target = Path(package_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "payload.bin").write_bytes(b"payload" * 1024)
        return target

    monkeypatch.setattr(artifact_exports, "build_dataset_package", fake_build)
    output = tmp_path / "chosen.zip"
    result = artifact_exports.create_dataset_export_artifact(
        "project-1",
        dataset,
        dataset_run_id="run-1",
        formats=["yolo"],
        output_path=output,
    )
    assert output.is_file()
    assert not output.with_name("chosen.zip.partial").exists()
    assert output.read_bytes() == Path(result["archive_path"]).read_bytes()
