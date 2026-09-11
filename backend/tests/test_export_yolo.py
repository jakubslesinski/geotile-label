"""Regresja eksportu YOLO — chroni przed cichym psuciem treningu.

Historia bugów (sierpień 2026):
  1. `export_yolo_dataset` podawał JUŻ znormalizowane `tile_annotations` do `write_yolo_label`
     (oczekującej pikseli) → etykiety z UJEMNYM w/h → model uczył się na zerze (mAP 0).
  2. eksport in-place nadpisywał `data.yaml` runu formą przenośną `path: .` → ultralytics nie
     znajdował obrazów.
  3. eksport in-place mutował niezmienny dataset run (nadpisywał labels/).

Run: python backend/tests/test_export_yolo.py  (albo: pytest backend/tests/test_export_yolo.py)
"""

import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import yaml

from services.export_yolo import export_yolo_dataset, write_yolo_label_normalized


def _valid_yolo_line(line: str) -> bool:
    parts = line.split()
    if len(parts) != 5:
        return False
    cx, cy, w, h = (float(v) for v in parts[1:])
    return w > 0 and h > 0 and all(0.0 <= v <= 1.0 for v in (cx, cy, w, h))


def _make_run(tmp: pathlib.Path, with_build_outputs: bool = False):
    """Minimalny dataset run: 1 obraz/split + znormalizowane tile_annotations."""
    tile_annotations = {}
    for split in ("train", "val", "test"):
        (tmp / split / "images").mkdir(parents=True)
        name = f"{split}_0.png"
        (tmp / split / "images" / name).write_bytes(b"\x89PNG\r\n")
        tile_annotations[name] = [[1, 0.46923828125, 0.45947265625, 0.0068359375, 0.0341796875]]
        if with_build_outputs:
            (tmp / split / "labels").mkdir(parents=True)
            (tmp / split / "labels" / f"{split}_0.txt").write_text(
                "1 0.46923828125 0.45947265625 0.0068359375 0.0341796875\n", encoding="utf-8"
            )
    if with_build_outputs:
        (tmp / "data.yaml").write_text(
            f"path: {tmp.resolve().as_posix()}\ntrain: train/images\n", encoding="utf-8"
        )
    return tile_annotations


def test_write_yolo_label_normalized_verbatim():
    tmp = pathlib.Path(tempfile.mkdtemp())
    path = tmp / "t.txt"
    # wartości JUŻ znormalizowane (jak w tile_annotations / build_dataset)
    anns = [[1, 0.46923828125, 0.45947265625, 0.0068359375, 0.0341796875],
            [2, 0.3623046875, 0.68017578125, 0.0078125, 0.0224609375]]
    write_yolo_label_normalized(path, anns)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(_valid_yolo_line(ln) for ln in lines), "writer musi zachować dodatnie w/h w [0,1]"
    # zapis dosłowny (bez ponownej normalizacji)
    assert lines[0].split()[3] == "0.0068359375"


def test_export_inplace_labels_valid_and_absolute_path():
    tmp = pathlib.Path(tempfile.mkdtemp())
    tile_annotations = _make_run(tmp)
    yaml_path = export_yolo_dataset(tile_annotations, tmp, 640, {0: "a", 1: "b"}, tile_links=[])
    # etykiety AABB poprawne (nie zepsute na ujemne w/h)
    label = (tmp / "train" / "labels" / "train_0.txt").read_text(encoding="utf-8").strip()
    assert _valid_yolo_line(label), f"eksport in-place nie może psuć etykiet: {label!r}"
    # data.yaml runu ma ABSOLUTNĄ ścieżkę (trenowalny bez datasets_dir/CWD)
    cfg = yaml.safe_load(pathlib.Path(yaml_path).read_text(encoding="utf-8"))
    assert pathlib.Path(str(cfg["path"])).is_absolute(), f"in-place path musi być absolutny: {cfg['path']!r}"


def test_export_to_subdir_leaves_run_immutable():
    tmp = pathlib.Path(tempfile.mkdtemp())
    tile_annotations = _make_run(tmp, with_build_outputs=True)
    data_yaml_before = (tmp / "data.yaml").read_text(encoding="utf-8")
    label_before = (tmp / "train" / "labels" / "train_0.txt").read_text(encoding="utf-8")

    export_dir = tmp / "export" / "yolo"
    export_yolo_dataset(tile_annotations, tmp, 640, {0: "a", 1: "b"},
                        output_dir=export_dir, tile_links=[])

    # run NIEZMIENNY
    assert (tmp / "data.yaml").read_text(encoding="utf-8") == data_yaml_before
    assert (tmp / "train" / "labels" / "train_0.txt").read_text(encoding="utf-8") == label_before
    # paczka eksportu kompletna
    assert (export_dir / "data.yaml").is_file()
    assert (export_dir / "images" / "train").is_dir()
    assert (export_dir / "labels" / "train").is_dir()


def test_worker_forces_absolute_data_yaml():
    try:
        from training_worker import _ensure_absolute_data_yaml
    except Exception:  # noqa: BLE001 — worker ciągnie ultralytics; pomiń, gdy niedostępny
        import pytest
        pytest.skip("training_worker niedostępny w tym środowisku")

    tmp = pathlib.Path(tempfile.mkdtemp())
    dataset_dir = tmp / "dataset_run"
    dataset_dir.mkdir()
    portable = dataset_dir / "data.yaml"
    portable.write_text("path: .\ntrain: train/images\nval: val/images\n", encoding="utf-8")
    run_dir = tmp / "training_run"
    run_dir.mkdir()

    resolved = _ensure_absolute_data_yaml(str(portable), str(dataset_dir), run_dir)
    cfg = yaml.safe_load(pathlib.Path(resolved).read_text(encoding="utf-8"))
    assert pathlib.Path(str(cfg["path"])).is_absolute()
    # oryginał (dataset run) NIETKNIĘTY
    assert portable.read_text(encoding="utf-8").splitlines()[0] == "path: ."
    # poprawiona kopia w katalogu przebiegu treningu
    assert pathlib.Path(resolved).parent == run_dir


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("all export-yolo regression tests passed")
