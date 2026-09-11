"""Przygotowanie paczki adnotacji: plan kosztu, postep i przerwanie.

Eksport paczki wymaga DOKLADNEJ tozsamosci zrodla (pelne sha256 zasobu pomiarowego),
a import liczy tylko sygnature probkowana. Te testy pilnuja przycisku, ktory te luke
zasypuje: ile obiecuje przeczytac, czy naprawde odblokowuje eksport i czy da sie go
przerwac w srodku wielogigabajtowego pliku.

Run: python backend/tests/test_annotation_package_prepare.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TEST_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="geotile-package-prepare-"))
os.environ["DATA_DIR"] = str(TEST_ROOT / "data")

from db.storage import load_scene_json, save_json, save_scene_json  # noqa: E402
from services.annotation_package import (  # noqa: E402
    AnnotationPackagePrepareCancelled,
    plan_annotation_package_preparation,
    prepare_annotation_package,
)
from services.scene_sources import save_scene_sources  # noqa: E402


PAYLOAD_SIZE = 3 * 1024 * 1024  # ponad jeden 8 MB chunk to i tak jeden odczyt; wystarczy


def _project(project_id: str, scenes: dict[str, int]) -> dict[str, pathlib.Path]:
    """Projekt etykietujacy z podanymi scenami: nazwa -> rozmiar rastra w bajtach."""

    source_root = TEST_ROOT / project_id / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    save_json(project_id, "project", {
        "id": project_id,
        "name": project_id,
        "profile": {
            "project_role": "labeling",
            "labeling_author_email": "analyst@example.invalid",
        },
    })
    save_json(project_id, "classes", [{"id": 0, "name": "obiekt"}])
    save_scene_sources(project_id, {"sources": [{
        "source_id": "src",
        "provider": "generic",
        "root_path": str(source_root),
        "enabled": True,
    }]})

    paths: dict[str, pathlib.Path] = {}
    for scene_id, size in scenes.items():
        path = source_root / f"{scene_id}.bin"
        path.write_bytes(bytes((index * 7 + 11) % 256 for index in range(size)))
        stat = path.stat()
        paths[scene_id] = path
        save_scene_json(project_id, scene_id, "scene", {
            "id": scene_id,
            "filename": path.name,
            "path": str(path),
        })
        save_scene_json(project_id, scene_id, "scene_manifest", {
            "scene_id": scene_id,
            "source_package": {
                "source_id": "src",
                "provider_scene_id": scene_id,
                "identity_asset_ids": ["raster"],
                "assets": [{
                    "asset_id": "raster",
                    "role": "primary_raster",
                    "part_id": None,
                    "relative_path": path.name,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": None,
                }],
            },
            "source_identity": {"status": "pending"},
            "working_view": {"working_grid_uid": "grid"},
        })
        save_scene_json(project_id, scene_id, "annotations", [{
            "id": f"{scene_id}-a1",
            "class_id": 0,
            "bbox": [0, 0, 10, 10],
            "annotator_email": "analyst@example.invalid",
        }])
    return paths


def test_plan_counts_only_what_it_will_read():
    project_id = "plan-cost"
    _project(project_id, {"scene_a": PAYLOAD_SIZE, "scene_b": PAYLOAD_SIZE // 2})

    plan = plan_annotation_package_preparation(project_id)
    assert plan["scene_count"] == 2
    assert plan["total_bytes"] == PAYLOAD_SIZE + PAYLOAD_SIZE // 2

    # Po przeliczeniu plan schodzi do zera: hash jest zapamietany przy niezmienionym
    # snapshocie, wiec druga paczka nie czyta ani bajtu i nie moze obiecywac inaczej.
    prepare_annotation_package(project_id)
    after = plan_annotation_package_preparation(project_id)
    assert after["scene_count"] == 0
    assert after["total_bytes"] == 0


def test_prepare_unblocks_export_and_reports_progress():
    project_id = "prepare-unblocks"
    _project(project_id, {"scene_a": PAYLOAD_SIZE})

    updates: list[dict] = []
    result = prepare_annotation_package(project_id, progress=updates.append)

    assert result["exact"] == 1
    assert result["failed"] == 0
    assert result["can_export"] is True
    assert not result["errors"]

    manifest = load_scene_json(project_id, "scene_a", "scene_manifest", default={})
    assert str(manifest["source_scene_uid"]).startswith("scene-sha256:")
    assert manifest["source_identity_strength"] == "exact"

    assert updates, "brak raportow postepu"
    assert all(item["total_bytes"] == PAYLOAD_SIZE for item in updates)
    # Postep musi ROSNAC i dojsc do konca — licznik stojacy na zerze przez caly
    # wielogigabajtowy odczyt jest gorszy niz jego brak.
    progress_bytes = [item["done_bytes"] for item in updates]
    assert progress_bytes == sorted(progress_bytes)
    assert progress_bytes[-1] == PAYLOAD_SIZE


def test_cancel_stops_inside_the_file_and_leaves_no_false_identity():
    project_id = "prepare-cancel"
    _project(project_id, {"scene_a": PAYLOAD_SIZE})

    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # pierwsze sprawdzenie przepuszcza, kolejne przerywa

    try:
        prepare_annotation_package(project_id, should_cancel=should_cancel)
    except AnnotationPackagePrepareCancelled:
        pass
    else:
        raise AssertionError("przerwanie nie zostalo zgloszone")

    manifest = load_scene_json(project_id, "scene_a", "scene_manifest", default={})
    assert not manifest.get("source_scene_uid")
    assert plan_annotation_package_preparation(project_id)["scene_count"] == 1


def test_failed_scene_does_not_stop_the_rest():
    project_id = "prepare-partial"
    paths = _project(project_id, {"scene_a": PAYLOAD_SIZE, "scene_b": PAYLOAD_SIZE // 4})
    paths["scene_a"].unlink()  # zrodlo zniknelo z dysku po imporcie

    result = prepare_annotation_package(project_id)
    by_scene = {item["scene_id"]: item for item in result["scenes"]}
    assert by_scene["scene_b"]["exact"] is True
    assert by_scene["scene_a"]["exact"] is False
    assert result["can_export"] is False  # jedna scena nadal blokuje — i tak ma byc


def test_every_job_type_has_a_worker_handler():
    """Nowy typ zadania bez handlera przechodzi walidacje i umiera dopiero u uzytkownika.

    `submit_job` sprawdza tylko, czy typ jest w enumie — brakujacy handler wychodzi
    dopiero w procesie workera, po zakolejkowaniu. Tu kosztuje to jedna asercje.
    """
    from job_worker import HANDLERS
    from models.job import JobType

    missing = sorted({item.value for item in JobType} - set(HANDLERS))
    assert not missing, f"typy zadan bez handlera: {missing}"


def _run() -> int:
    tests = [value for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"[OK  ] {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - raport testowy
            failed += 1
            print(f"[BLAD] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} przeszlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
