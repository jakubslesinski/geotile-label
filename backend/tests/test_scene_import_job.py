"""Regresja: import scen jako job w tle z plikiem postępu.

Historia (sierpień 2026): `POST /projects/from-sources` katalogował sceny SYNCHRONICZNIE
(per-scena `get_scene_info`), więc setki wielkich rastrów blokowały żądanie na godziny,
a jeden patologiczny plik (JP2 z alpha) wieszał cały import bez śladu postępu. Teraz
katalogowanie biegnie w wątku roboczym `_run_import_job`, a `_catalogue_scenes` po każdej
scenie flushuje `import_jobs/<job_id>.json` (done/total, current, recent, errors) — UI odpytuje
to paskiem. Ten test pilnuje mechaniki postępu: liczniki, listy pending i zapis pliku.

DATA_DIR jest czytany przy imporcie db.storage, więc ustawiamy go PRZED importem.

Run: python backend/tests/test_scene_import_job.py  (albo: pytest backend/tests/test_scene_import_job.py)
"""

import json
import os
import pathlib
import sys
import tempfile
from types import SimpleNamespace

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "tests"))

# DATA_DIR musi być ustawiony przed importem storage (cache'owany przy imporcie).
_TMP_DATA = tempfile.mkdtemp(prefix="geotile_import_job_")
os.environ["DATA_DIR"] = _TMP_DATA

from db.storage import project_dir  # noqa: E402
from routers import scene_import as si  # noqa: E402
from services.scene_loader import SCENE_INFO_VERSION  # noqa: E402
from services.scene_packages.contracts import FLAG_GRAPH_V2  # noqa: E402
from services.scene_packages.resolvers import scan_source  # noqa: E402
from fixtures.scene_packages import build_worldview_incomplete_extract  # noqa: E402


def _pkg(i: int, *, ready: bool = True, direct: bool = True) -> dict:
    return {
        "source_id": "src1",
        "package_id": f"pkg{i}",
        "provider": "generic",
        "package_root_relative": f"LOC/scene_{i}.tif",
        "assets": [{"asset_id": f"a{i}", "relative_path": f"LOC/scene_{i}.tif", "role": "raster_candidate"}],
        "selection": {
            "asset_ids": [f"a{i}"],
            "identity_asset_ids": [f"a{i}"],
            "status": "ready" if ready else "decision_required",
            "raster_kind": "direct" if direct else "virtual_mosaic",
        },
    }


class _SkipDecision:
    def __init__(self, package_id: str):
        self.package_id = package_id
        self.action = "skip"
        self.asset_ids: list[str] = []
        self.rgb_bands = None


def _preview(*packages: dict) -> dict:
    return {"sources": [{"source_id": "src1", "root_path": "X"}], "packages": list(packages)}


def test_catalogue_writes_progress_and_collects_pending(monkeypatch=None):
    pid = "testprojjob01"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    # Podmieniamy ciężką część — testujemy wyłącznie mechanikę postępu, nie I/O rastrów.
    saved: list[str] = []
    si._save_package_scene = lambda project_id, scene_id, package, selection: saved.append(scene_id)

    preview = _preview(_pkg(1), _pkg(2), _pkg(3))
    job = si._new_import_job(pid, "deadbeef01", si._count_importable(preview, []))
    report, pending_identity, pending_overviews = si._catalogue_scenes(pid, preview, [], job)

    disk = json.loads((si._import_jobs_dir(pid) / "deadbeef01.json").read_text(encoding="utf-8"))
    assert disk["total"] == 3 and disk["done"] == 3 and disk["added"] == 3, disk
    assert disk["current"] is None
    assert len(disk["recent"]) == 3 and all(r["status"] == "ok" for r in disk["recent"])
    assert len(pending_identity) == 3, pending_identity
    assert len(pending_overviews) == 3, pending_overviews  # ready + direct
    assert report["added"] == 3
    assert len(saved) == 3


def test_virtual_mosaic_is_queued_for_overviews():
    """P1.4 odwraca wcześniejszy kontrakt: mozaika też dostaje piramidę wyświetlania.

    Poprzednia wersja tego testu utrwalała zakres pierwszego etapu piramid, który celowo
    obejmował wyłącznie produkty `direct`. Dla mozaiki skutek był taki, że kafel niskiego
    zoomu decymował wszystkie części naraz — dla WV2 PAN sześć części o łącznym rozmiarze
    26958×42040 px. Bramka P1.4 wymaga wprost, żeby niski zoom czytał overview.
    """
    pid = "testprojjob02"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    si._save_package_scene = lambda project_id, scene_id, package, selection: None
    preview = _preview(_pkg(1, direct=False))
    job = si._new_import_job(pid, "deadbeef02", 1)
    _report, pending_identity, pending_overviews = si._catalogue_scenes(pid, preview, [], job)
    assert len(pending_identity) == 1
    assert len(pending_overviews) == 1, "virtual_mosaic ma trafiać do fazy piramid"


def test_count_importable_respects_skip():
    preview = _preview(_pkg(1), _pkg(2), _pkg(3))
    assert si._count_importable(preview, []) == 3
    assert si._count_importable(preview, [_SkipDecision("pkg2")]) == 2


def test_catalogue_decision_refines_worldview_and_persists_partial_provenance(
    tmp_path,
    monkeypatch,
):
    """Decyzja w kreatorze ma ten sam kontrakt co pozniejszy endpoint `select-asset`.

    To zabezpiecza jawny import niepelnej dostawy P0.6: uzytkownik wybiera jedyna
    alternatywe `partial`, a katalog zapisuje `ready`, ale nie gubi `completeness=partial`.
    """
    monkeypatch.setenv(FLAG_GRAPH_V2, "1")
    fixture = build_worldview_incomplete_extract(tmp_path / "worldview_partial")
    packages, _ = scan_source(fixture.root, "worldview")
    package = packages[0]
    package["source_id"] = "src-worldview"
    original = package["selection"]
    decision = SimpleNamespace(
        package_id=package["package_id"],
        action="import",
        asset_ids=list(original["alternatives"][0]["asset_ids"]),
        rgb_bands=None,
    )
    captured: list[dict] = []
    monkeypatch.setattr(
        si,
        "_save_package_scene",
        lambda _project_id, _scene_id, _package, selection: captured.append(dict(selection)),
    )
    pid = "testprojjob-worldview-partial"
    project_dir(pid).mkdir(parents=True, exist_ok=True)

    si._catalogue_scenes(pid, _preview(package), [decision])

    assert len(captured) == 1
    persisted = captured[0]
    assert persisted["status"] == "ready"
    assert persisted["selected_by"] == "user"
    assert persisted["product_type"] == "PAN"
    assert persisted["completeness"] == "partial"
    assert len(persisted["missing_parts"]) == 3


def test_worker_error_is_captured_not_raised():
    pid = "testprojjob03"
    project_dir(pid).mkdir(parents=True, exist_ok=True)

    def _boom(project_id, scene_id, package, selection):
        raise RuntimeError("simulated read failure")

    si._save_package_scene = _boom
    preview = _preview(_pkg(1), _pkg(2))
    job = si._new_import_job(pid, "deadbeef03", 2)
    # W trybie job wyjątek per-scena jest łapany (import się nie wywraca), a scena liczona jako failed.
    _report, _pi, _po = si._catalogue_scenes(pid, preview, [], job)
    disk = json.loads((si._import_jobs_dir(pid) / "deadbeef03.json").read_text(encoding="utf-8"))
    assert disk["failed"] == 2, disk
    assert len(disk["errors"]) == 2
    assert all(r["status"] == "error" for r in disk["recent"])


def test_cancel_stops_loop_and_flags_report():
    pid = "testprojjob04"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    seen: list[str] = []

    def _record(project_id, scene_id, package, selection):
        seen.append(scene_id)

    si._save_package_scene = _record
    preview = _preview(_pkg(1), _pkg(2), _pkg(3), _pkg(4))
    job = si._new_import_job(pid, "deadbeef04", 4)
    # cancel_check zwraca True dopiero po przetworzeniu 2 scen (sprawdzany na starcie iteracji).
    state = {"n": 0}

    def _cancel():
        cancel = state["n"] >= 2
        state["n"] += 1
        return cancel

    report, _pi, _po = si._catalogue_scenes(pid, preview, [], job, cancel_check=_cancel)
    assert report["cancelled"] is True, report
    assert len(seen) == 2, f"powinny przejść tylko 2 sceny, było {len(seen)}"
    disk = json.loads((si._import_jobs_dir(pid) / "deadbeef04.json").read_text(encoding="utf-8"))
    assert disk["done"] == 2


def test_resume_skips_complete_scene():
    from db.storage import save_scene_json

    pid = "testprojjob05"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    # pkg1 → scena już gotowa (ma scene_info, tożsamość complete); pkg2 → jeszcze nie istnieje.
    sid1 = si._logical_scene_id("src1", "pkg1")
    save_scene_json(pid, sid1, "scene", {
        "id": sid1, "source_id": "src1", "package_id": "pkg1",
        "scene_info": {"width": 10, "height": 10},
        "scene_info_version": SCENE_INFO_VERSION,
    })
    save_scene_json(pid, sid1, "scene_manifest", {"source_identity": {"status": "complete"}})

    calls: list[str] = []
    si._save_package_scene = lambda project_id, scene_id, package, selection: calls.append(scene_id)

    preview = _preview(_pkg(1), _pkg(2))
    job = si._new_import_job(pid, "deadbeef05", 2)
    _report, pending_identity, _po = si._catalogue_scenes(pid, preview, [], job, skip_complete=True)

    sid2 = si._logical_scene_id("src1", "pkg2")
    assert calls == [sid2], f"tylko niekompletna scena ma być przetworzona: {calls}"
    # gotowa scena z complete identity nie wraca do kolejki tożsamości; niekompletna owszem
    assert pending_identity == [(sid2, "scene_2.tif")], pending_identity
    disk = json.loads((si._import_jobs_dir(pid) / "deadbeef05.json").read_text(encoding="utf-8"))
    assert disk["done"] == 2
    assert any(r["status"] == "skipped" for r in disk["recent"])


def test_resume_requeues_incomplete_identity():
    from db.storage import save_scene_json

    pid = "testprojjob06"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    sid = si._logical_scene_id("src1", "pkg1")
    # scena skatalogowana (scene_info), ale tożsamość utknęła na pending → resume ma ją dokończyć
    save_scene_json(pid, sid, "scene", {
        "id": sid, "source_id": "src1", "package_id": "pkg1",
        "scene_info": {"width": 10, "height": 10},
        "scene_info_version": SCENE_INFO_VERSION,
    })
    save_scene_json(pid, sid, "scene_manifest", {"source_identity": {"status": "pending"}})
    si._save_package_scene = lambda *a: (_ for _ in ()).throw(AssertionError("nie powinno wołać save dla gotowej sceny"))

    preview = _preview(_pkg(1))
    job = si._new_import_job(pid, "deadbeef06", 1)
    _report, pending_identity, _po = si._catalogue_scenes(pid, preview, [], job, skip_complete=True)
    assert pending_identity == [(sid, "scene_1.tif")], pending_identity


def test_resume_recharacterizes_source_when_size_or_mtime_changed(monkeypatch):
    from db.storage import save_scene_json

    pid = "testprojjob09"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    sid = si._logical_scene_id("src1", "pkg1")
    save_scene_json(pid, sid, "scene", {
        "id": sid,
        "source_id": "src1",
        "package_id": "pkg1",
        "scene_info": {
            "width": 10,
            "height": 10,
            "characterization_source_size": 100,
            "characterization_source_mtime_ns": 10,
        },
        "scene_info_version": SCENE_INFO_VERSION,
    })
    save_scene_json(pid, sid, "scene_manifest", {"source_identity": {"status": "complete"}})
    package = _pkg(1)
    package["assets"][0].update({"size": 101, "mtime_ns": 11})
    calls: list[str] = []
    monkeypatch.setattr(
        si,
        "_save_package_scene",
        lambda project_id, scene_id, package, selection: calls.append(scene_id),
    )

    job = si._new_import_job(pid, "deadbeef09", 1)
    _report, pending_identity, _overviews = si._catalogue_scenes(
        pid,
        _preview(package),
        [],
        job,
        skip_complete=True,
    )

    assert calls == [sid]
    assert pending_identity == [(sid, "scene_1.tif")]


def test_characterization_cache_accepts_matching_source_signature():
    package = _pkg(1)
    package["assets"][0].update({"size": 100, "mtime_ns": 10})
    scene = {
        "scene_info_version": SCENE_INFO_VERSION,
        "scene_info": {
            "width": 10,
            "height": 10,
            "characterization_source_size": 100,
            "characterization_source_mtime_ns": 10,
        },
    }

    assert si._scene_characterization_matches_selection(
        scene,
        package,
        package["selection"],
    )


def test_source_derivative_invalidation_removes_only_display_cache():
    pid = "testprojjob10"
    sid = "scene-cache-invalidation"
    scene_cache = si.scene_dir(pid, sid)
    scene_cache.mkdir(parents=True, exist_ok=True)
    (scene_cache / "histogram.json").write_text("{}", encoding="utf-8")
    (scene_cache / "scene_thumbnail_v1.png").write_bytes(b"thumbnail")
    tile = scene_cache / "geo_tile_cache" / "v1" / "0" / "0" / "0.png"
    tile.parent.mkdir(parents=True, exist_ok=True)
    tile.write_bytes(b"tile")
    unrelated = scene_cache / "annotations.json"
    unrelated.write_text("[]", encoding="utf-8")
    overview_dir = project_dir(pid) / "derived_scenes" / sid / "variant-a"
    overview_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "overview.vrt",
        "overview.vrt.ovr",
        "overview.profile.json",
        ".overview.vrt.partial.vrt",
        ".overview.vrt.partial.vrt.ovr",
        "overview.profile.tmp",
    ):
        (overview_dir / name).write_bytes(b"derived")

    si._invalidate_scene_source_derivatives(pid, sid)

    assert not (scene_cache / "histogram.json").exists()
    assert not (scene_cache / "scene_thumbnail_v1.png").exists()
    assert not (scene_cache / "geo_tile_cache").exists()
    assert unrelated.is_file()
    assert not any(overview_dir.iterdir())


def test_import_done_before_overviews_and_index_once():
    """#1: job osiąga state=done po tożsamości; piramidy dobudowują się po done.
    #6: kompletacja bez wołania rebuild_scenes_index po każdej scenie tożsamości."""
    import threading
    import time as _time

    pid = "testprojjob07"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    si._save_package_scene = lambda project_id, scene_id, package, selection: None
    id_calls: list[bool] = []
    si.compute_scene_package_identity = lambda project_id, scene_id, rebuild_index=True: id_calls.append(rebuild_index)
    states_during_overviews: list[str] = []
    ovr_calls: list[str] = []

    def _fake_overview(project_id, scene_id):
        # w chwili budowy piramid job MUSI już być "done"
        disk = json.loads((si._import_jobs_dir(pid) / "cafe0007.json").read_text(encoding="utf-8"))
        states_during_overviews.append(disk["state"])
        ovr_calls.append(scene_id)
        _time.sleep(0.02)

    si._build_scene_display_overviews = _fake_overview

    preview = _preview(_pkg(1), _pkg(2), _pkg(3))
    th = threading.Thread(target=si._run_import_job, args=(pid, "cafe0007", preview, []), daemon=True)
    th.start()
    th.join(timeout=15)

    disk = json.loads((si._import_jobs_dir(pid) / "cafe0007.json").read_text(encoding="utf-8"))
    assert disk["state"] == "done", disk
    assert disk["phase"] == "complete"
    assert disk["overviews_total"] == 3 and disk["overviews_done"] == 3, disk
    assert disk["identity_done"] == 3
    # #1: każde budowanie piramidy widziało state=="done"
    assert states_during_overviews == ["done", "done", "done"], states_during_overviews
    # #6: import wołał tożsamość z rebuild_index=False (indeks przebudowany raz w workerze)
    assert id_calls == [False, False, False], id_calls


def test_write_import_job_retries_on_permission_error():
    """Windows: równoczesny odczyt pliku joba (polling UI) powoduje PermissionError w os.replace.
    Zapis MUSI to przeżyć (retry), nie wywracając importu."""
    pid = "testprojjob08"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    job = si._new_import_job(pid, "deadbeef08", 1)
    real_replace = si.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    si.os.replace = flaky_replace
    try:
        si._write_import_job(pid, job)  # nie może rzucić
    finally:
        si.os.replace = real_replace
    assert calls["n"] >= 3, calls
    disk = json.loads((si._import_jobs_dir(pid) / "deadbeef08.json").read_text(encoding="utf-8"))
    assert disk["job_id"] == "deadbeef08"


# --- wznowienie dla mozaiki i produktu pochodnego (bramka M2) ---------------------------
#
# `_scene_characterization_matches_selection()` deklaruje, ze produkt mozaikowy i pochodny
# NIE moga uzywac klucza „rozmiar + mtime jednego rastra". Testy nizej sprawdzaja to wprost:
# zmiana JEDNEJ czesci mozaiki i zmiana JEDNEGO ze skladnikow produktu pochodnego musza
# unieważnić charakterystykę. Bez nich wznowienie importu potwierdzalo tylko sciezke `direct`.


def _mosaic_pkg(parts: int = 3, *, kind: str = "virtual_mosaic") -> dict:
    assets = [
        {
            "asset_id": f"part{index}",
            "relative_path": f"LOC/R1C{index}.tif",
            "role": "raster_candidate",
            "size": 100 + index,
            "mtime_ns": 10 + index,
        }
        for index in range(1, parts + 1)
    ]
    asset_ids = [asset["asset_id"] for asset in assets]
    return {
        "source_id": "src1",
        "package_id": "pkg-mosaic",
        "provider": "worldview",
        "package_root_relative": "LOC",
        "assets": assets,
        "selection": {
            "asset_ids": asset_ids,
            "identity_asset_ids": asset_ids,
            "status": "ready",
            "raster_kind": kind,
            "product_type": "PAN",
        },
    }


def _catalogued_scene(pid: str, package: dict) -> str:
    """Zapisz scene tak, jak wygladalaby po udanym imporcie tego pakietu."""
    from db.storage import save_scene_json

    scene_id = si._logical_scene_id("src1", package["package_id"])
    save_scene_json(pid, scene_id, "scene", {
        "id": scene_id,
        "source_id": "src1",
        "package_id": package["package_id"],
        "scene_info": {"width": 10, "height": 10},
        "scene_info_version": SCENE_INFO_VERSION,
    })
    save_scene_json(pid, scene_id, "scene_manifest", {
        "source_identity": {"status": "complete"},
        "source_package": {
            **package,
            "identity_asset_ids": package["selection"]["identity_asset_ids"],
            "defining_metadata_asset_ids": [],
        },
    })
    return scene_id


def test_resume_skips_an_unchanged_virtual_mosaic(monkeypatch):
    pid = "testprojjob11"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    package = _mosaic_pkg()
    _catalogued_scene(pid, package)
    calls: list[str] = []
    monkeypatch.setattr(si, "_save_package_scene", lambda _p, sid, _pkg, _sel: calls.append(sid))

    si._catalogue_scenes(pid, _preview(package), [], skip_complete=True)

    assert calls == [], "niezmieniona mozaika nie moze byc katalogowana ponownie"


def test_resume_recharacterizes_a_mosaic_when_one_part_changed(monkeypatch):
    """Zmiana TRZECIEJ czesci — klucz jednego rastra by jej nie zauwazyl."""
    pid = "testprojjob12"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    package = _mosaic_pkg()
    scene_id = _catalogued_scene(pid, package)

    changed = _mosaic_pkg()
    changed["assets"][2]["mtime_ns"] += 1
    calls: list[str] = []
    monkeypatch.setattr(si, "_save_package_scene", lambda _p, sid, _pkg, _sel: calls.append(sid))

    si._catalogue_scenes(pid, _preview(changed), [], skip_complete=True)

    assert calls == [scene_id], "zmiana czesci mozaiki musi uniewazniac charakterystyke"


def test_resume_recharacterizes_a_derived_product_when_a_component_changed(monkeypatch):
    pid = "testprojjob13"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    package = _mosaic_pkg(parts=2, kind="derived")
    package["selection"]["product_type"] = "MUL+PAN"
    package["selection"]["multispectral_asset_ids"] = ["part1"]
    package["selection"]["panchromatic_asset_ids"] = ["part2"]
    scene_id = _catalogued_scene(pid, package)

    changed = _mosaic_pkg(parts=2, kind="derived")
    changed["selection"].update({
        "product_type": "MUL+PAN",
        "multispectral_asset_ids": ["part1"],
        "panchromatic_asset_ids": ["part2"],
    })
    changed["assets"][0]["size"] += 1
    calls: list[str] = []
    monkeypatch.setattr(si, "_save_package_scene", lambda _p, sid, _pkg, _sel: calls.append(sid))

    si._catalogue_scenes(pid, _preview(changed), [], skip_complete=True)

    assert calls == [scene_id], "zmiana skladnika produktu pochodnego musi uniewazniac charakterystyke"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all scene-import job regression tests passed")
