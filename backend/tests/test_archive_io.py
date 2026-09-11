"""P0.3 regressions for disk-backed atomic archives."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import archive_io


@contextmanager
def _redirect_runtime():
    old_runtime = archive_io.ARCHIVE_RUNTIME_DIR
    old_registry = archive_io.PARTIAL_REGISTRY_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="geotile-archive-test-") as temp_name:
            tmp_path = Path(temp_name)
            runtime = tmp_path / "runtime" / "archives"
            registry = tmp_path / "runtime" / "archive_partials.json"
            archive_io.ARCHIVE_RUNTIME_DIR = runtime
            archive_io.PARTIAL_REGISTRY_PATH = registry
            yield tmp_path, runtime, registry
    finally:
        archive_io.ARCHIVE_RUNTIME_DIR = old_runtime
        archive_io.PARTIAL_REGISTRY_PATH = old_registry


def test_zip_is_published_atomically_without_partial_leftovers():
    with _redirect_runtime() as (tmp_path, _runtime, registry):
        destination = tmp_path / "result.zip"

        archive_io.write_zip_atomic(
            destination,
            lambda archive: archive.writestr("payload.txt", b"ok"),
        )

        assert destination.is_file()
        assert not (tmp_path / "result.zip.partial").exists()
        with zipfile.ZipFile(destination) as archive:
            assert archive.read("payload.txt") == b"ok"
        assert json.loads(registry.read_text(encoding="utf-8"))["paths"] == []


def test_failed_zip_removes_partial_and_does_not_publish():
    with _redirect_runtime() as (tmp_path, _runtime, _registry):
        destination = tmp_path / "failed.zip"

        def fail(archive):
            archive.writestr("partial.txt", b"partial")
            raise RuntimeError("boom")

        try:
            archive_io.write_zip_atomic(destination, fail)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("Expected ZIP writer failure")

        assert not destination.exists()
        assert not (tmp_path / "failed.zip.partial").exists()


def test_startup_cleanup_removes_tracked_and_app_owned_archives():
    with _redirect_runtime() as (tmp_path, runtime, registry):
        runtime.mkdir(parents=True)
        leaked_download = runtime / "download.zip"
        leaked_download.write_bytes(b"old")
        leaked_runtime_partial = runtime / "runtime.zip.partial"
        leaked_runtime_partial.write_bytes(b"partial")
        leaked_external_partial = tmp_path / "chosen.zip.partial"
        leaked_external_partial.write_bytes(b"partial")
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"paths": [str(leaked_external_partial)]}),
            encoding="utf-8",
        )

        result = archive_io.cleanup_partial_archives()

        assert result["partial"] == 2
        assert result["temporary"] == 1
        assert not leaked_download.exists()
        assert not leaked_runtime_partial.exists()
        assert not leaked_external_partial.exists()


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"OK {name}")
    print("all archive I/O tests passed")
