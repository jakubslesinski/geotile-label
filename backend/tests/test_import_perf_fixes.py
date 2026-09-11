"""Regresja poprawek wydajności/porządku importu (2026-08-26):

B: `list_project_ids`/`_dedupe_projects_index` — dwa wpisy indeksu na ten sam katalog
   (folder nazwany nazwą projektu, nie hex-id) dawały dwa kafle. Dedup po ŚCIEŻCE, nie nazwie.
D: `content_signature` — tani odcisk (size + head/middle/tail ~24 MB) zamiast pełnego sha256
   całego pliku (który czytał 100% wolumenu i dominował czas importu).

Run: python backend/tests/test_import_perf_fixes.py
"""

import json
import os
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="geotile_perf_"))

from db import storage  # noqa: E402
from services.scene_packages.identity import sampled_content_signature  # noqa: E402


def test_content_signature_format_and_size_sensitivity():
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "a.bin"
    sample = 1 * 1024 * 1024
    f.write_bytes(b"\x01" * (5 * sample))
    sig = sampled_content_signature(f, sample_bytes=sample)
    assert sig.startswith("sig1:"), sig
    assert sig == sampled_content_signature(f, sample_bytes=sample)  # deterministyczny
    # zmiana rozmiaru → inny odcisk (rozmiar jest częścią odcisku)
    f.write_bytes(b"\x01" * (6 * sample))
    sig_bigger = sampled_content_signature(f, sample_bytes=sample)
    assert sig_bigger != sig
    # zmiana w GŁOWIE pliku → inny odcisk
    data = bytearray(b"\x01" * (6 * sample))
    data[0:10] = b"\x09" * 10
    f.write_bytes(bytes(data))
    assert sampled_content_signature(f, sample_bytes=sample) != sig_bigger


def test_content_signature_reads_limited_bytes(monkeypatch=None):
    # plik dużo większy niż 3*sample; odcisk nie może zależeć od środka poza próbkami
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "big.bin"
    sample = 1 * 1024 * 1024
    f.write_bytes(b"\x00" * (50 * sample))
    sig_a = sampled_content_signature(f, sample_bytes=sample)
    # zmiana bajtu w obszarze NIE próbkowanym (ćwiartka pliku) — odcisk się nie zmienia
    raw = bytearray(f.read_bytes())
    raw[10 * sample] = 0xFF  # ~1/5 pliku: poza head(0..1), middle(~25), tail(~49)
    f.write_bytes(bytes(raw))
    sig_b = sampled_content_signature(f, sample_bytes=sample)
    assert sig_a == sig_b, "odcisk czyta tylko head/middle/tail — zmiana poza próbkami niewidoczna"


def test_dedupe_projects_index_by_root():
    # katalog projektu z project.json.id = prawdziwe hex id
    proj_dir = pathlib.Path(tempfile.mkdtemp()) / "MY_NAME"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({"id": "abc123def456", "name": "MY_NAME"}), encoding="utf-8")

    index = {"projects": [
        {"project_id": "abc123def456", "project_root": str(proj_dir), "name": "MY_NAME"},
        {"project_id": "MY_NAME", "project_root": str(proj_dir), "name": "MY_NAME"},  # zły duplikat (nazwa jako id)
        {"project_id": "other", "project_root": str(proj_dir.parent / "other"), "name": "other"},
    ]}
    changed = storage._dedupe_projects_index(index)
    assert changed is True
    roots = [e["project_id"] for e in index["projects"]]
    assert "MY_NAME" not in roots, roots  # zły wpis usunięty
    assert "abc123def456" in roots and "other" in roots, roots
    assert len(index["projects"]) == 2


def test_resolve_overview_status():
    from routers import scenes

    # zapisany status wygrywa
    assert scenes._resolve_overview_status("p", "s", {"overview_status": "ready"}) == "ready"
    assert scenes._resolve_overview_status("p", "s", {"overview_status": "error"}) == "error"
    # scena nie-direct → native (piramida po stronie projektu nie dotyczy)
    assert scenes._resolve_overview_status("p", "s", {"raster_kind": "virtual_mosaic"}) == "native"
    # scena direct bez zbudowanego VRT → pending
    assert scenes._resolve_overview_status("p", "s", {"raster_kind": "direct"}) == "pending"
    assert scenes._resolve_overview_status(
        "p",
        "large-jp2",
        {
            "raster_kind": "direct",
            "overview_status": "native",
            "scene_info": {
                "width": 63_856,
                "height": 42_336,
                "source_overviews": {
                    "type": "native_multiresolution",
                    "usable": True,
                },
            },
        },
    ) == "pending"


def test_set_scene_overview_status_persists():
    from db.storage import project_dir, save_scene_json, load_scene_json
    from routers import scene_import as si

    pid = "ovrstatusproj"
    sid = "scene0001"
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    save_scene_json(pid, sid, "scene", {"id": sid, "raster_kind": "direct"})
    si._set_scene_overview_status(pid, sid, "ready")
    assert load_scene_json(pid, sid, "scene", default={}).get("overview_status") == "ready"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all import perf/fix regression tests passed")
