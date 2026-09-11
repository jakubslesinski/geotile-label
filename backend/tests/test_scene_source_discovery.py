"""Regresja wykrywania scen w źródłach — provider Generic musi schodzić w PODFOLDERY.

Historia (sierpień 2026): `GenericResolver.discover_packages` skanował tylko pliki bezpośrednie
(`root.iterdir()`), więc wskazanie folderu-rodzica z samymi podfolderami lokalizacji
(np. `<folder dostawy>/<lokalizacja>/*.tif|*.jp2`) dawało 0 scen. Teraz jest rekurencyjny (`rglob`),
z pominięciem ukrytych/derived katalogów (nazwa od kropki).

Run: python backend/tests/test_scene_source_discovery.py  (albo: pytest backend/tests/test_scene_source_discovery.py)
"""

import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.scene_packages.resolvers import get_resolver, scan_source


def _make_tree(root: pathlib.Path) -> None:
    (root / "BARANOVICHI").mkdir()
    (root / "BARANOVICHI" / "BARANOVICHI_BY_202106281222_WV1_PAN.tif").write_bytes(b"x")
    (root / "BARANOVICHI" / "BARANOVICHI_BY_202109260913_WV2_PANSHARP.tif").write_bytes(b"x")
    (root / "BOGUCHAR").mkdir()
    (root / "BOGUCHAR" / "BOGUCHAR_RU_202103280811_GE1_PANSHARP.tif").write_bytes(b"x")
    # jp2 też jest rastrem — musi zostać znaleziony
    (root / "BOGUCHAR" / "CHABAROWSK_RU_202303180211_PHRNEO_PAN.jp2").write_bytes(b"x")
    # plik bezpośrednio w rootcie — nadal ma być sceną
    (root / "direct_WV3.tif").write_bytes(b"x")
    # ukryty/derived katalog — jego rastry mają być POMINIĘTE
    (root / ".thumbnails").mkdir()
    (root / ".thumbnails" / "preview.jpg").write_bytes(b"x")


def test_generic_discovers_rasters_in_subfolders():
    root = pathlib.Path(tempfile.mkdtemp())
    _make_tree(root)
    candidates = get_resolver("generic").discover_packages(root)
    rels = sorted(c.relative_root for c in candidates)
    # 2 (BARANOVICHI) + 2 (BOGUCHAR, w tym jp2) + 1 (bezpośredni) = 5; ukryty pominięty
    assert len(candidates) == 5, rels
    assert "BARANOVICHI/BARANOVICHI_BY_202106281222_WV1_PAN.tif" in rels
    assert any(r.endswith(".jp2") for r in rels), "jp2 musi być wykryty"
    assert any(r == "direct_WV3.tif" for r in rels), "plik bezpośredni nadal działa"
    assert not any(".thumbnails" in r for r in rels), "ukryte/derived katalogi pomijane"


def test_scan_source_generic_counts_all_locations():
    root = pathlib.Path(tempfile.mkdtemp())
    _make_tree(root)
    packages, diagnostics = scan_source(root, "generic")
    assert len(packages) == 5, [p.get("package_root_relative") for p in packages]
    assert not any(d.get("level") == "error" for d in diagnostics)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all scene-source discovery regression tests passed")
