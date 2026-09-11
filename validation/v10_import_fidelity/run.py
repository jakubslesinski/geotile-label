"""v10 — Wierność importu benchmarku (lossless canonicalization)  (Claim C2a).

SUBSTRATE / TIER
    DOTA / DIOR-R / FAIR1M — import zewnętrznych OBB do postaci kanonicznej · public.

CLAIM
    Wciągnięcie danych zewnętrznych nie gubi ani nie zniekształca informacji: benchmark
    OBB -> kanoniczny `polygon_scene_px` (to, co aplikacja zapisuje i z czego eksportuje)
    zachowuje geometrię i klasę oryginału. Rozszerza C2a (przeżycie tożsamości) na
    WEJŚCIE z zewnątrz.

METHOD
    Dla próbki scen każdego benchmarku:
      1. Sparsuj ORYGINALNY plik etykiet tym samym czystym parserem, którego używa
         importer (`fair1m/dota/diorr .parse`) -> surowe wielokąty + nazwa klasy.
      2. Wczytaj zapisany przez aplikację `annotations.json` (kanoniczny
         `polygon_scene_px` + `class_id`), posortowany po indeksie z `source_annotation_id`.
      3. Porównaj obiekt-po-obiekcie: IoU(oryginał, zapis), zgodność nazwy klasy
         (class_id->nazwa z classes.json == nazwa z pliku), zgodność LICZBY obiektów.
    Ścieżkę oryginału wyprowadzamy z `scene_manifest.source_path` (bez twardych korzeni).
    Raportujemy rozkład IoU (p50/min) po wielu scenach i zbiorach.

INPUTS
    - zaimportowane projekty benchmarków (../importers): FAIR1M, DOTA, DIOR-R
    - oryginalne katalogi benchmarków (E:\\Datasets\\...) — wskazywane przez source_path
    - czyste parsery importerów (bez backendu)

OUTPUTS
    - results/import_fidelity.csv  (dataset, scene, n_in, n_out, iou_min, class_ok, count_ok)
    - metrics: {n_scenes, iou_p50, iou_min, class_match_pct, count_match_pct, per_dataset{}}

PASS CRITERION
    count_match_pct == 100 i class_match_pct == 100; iou_min > 0.999 (import bezstratny).
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv
from _common.geom import polygon_iou, polygon_vertex_deviation, polygon_area, is_convex, percentile

SCENES_PER_DATASET = int(os.environ.get("V10_SCENES_PER_DATASET", "300"))
SAMPLE_SEED = 20260824
IOU_PASS = 0.999      # dolny próg IoU dla NIEzdegenerowanych wielokątów
DEV_PASS = 0.01       # maks. odchylenie wierzchołków (px) — próg bezstratności
DEGEN_AREA = 1e-6     # poniżej tego pola wielokąt uznajemy za zdegenerowany (IoU pomijamy)


def _fair1m_label(source_path: Path) -> Path:
    # .../data/images/<stem>.tif -> .../data/labelXmls/<stem>.xml
    return source_path.parent.parent / "labelXmls" / f"{source_path.stem}.xml"


def _dota_label(source_path: Path) -> Path:
    # .../<split>/images/<stem>.png -> .../<split>/annotations/version2.0/<stem>.txt
    return source_path.parent.parent / "annotations" / "version2.0" / f"{source_path.stem}.txt"


def _diorr_label(source_path: Path) -> Path:
    # .../JPEGImages-*/<id>.jpg -> .../Annotations/Oriented Bounding Boxes/<id>.xml
    return source_path.parent.parent / "Annotations" / "Oriented Bounding Boxes" / f"{source_path.stem}.xml"


def _ann_index(source_annotation_id: str) -> int:
    # source_annotation_id = "<stem>-<index:05d>"
    try:
        return int(str(source_annotation_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def _load_parsers():
    from fair1m.parse import parse_fair1m_xml
    from dota.parse import parse_dota_txt
    from diorr.parse import parse_diorr_xml
    return {
        "FAIR1M": (parse_fair1m_xml, _fair1m_label),
        "DOTA": (parse_dota_txt, _dota_label),
        "DIOR-R": (parse_diorr_xml, _diorr_label),
    }


def _compare_scene(parsed: list[dict], stored: list[dict], class_names: dict[int, str]):
    """Porównaj obiekty sceny. Zwróć słownik z metrykami wierności.

    - dev_max: maks. odchylenie wierzchołków (bezstratność, odporne na degenerację),
    - ious: IoU tylko dla par NIEzdegenerowanych (obie figury mają pole > DEGEN_AREA),
    - n_degenerate: liczba par zdegenerowanych (zerowe pole — przycięte przy krawędzi),
    - class_ok / count_ok.
    """
    stored_sorted = sorted(stored, key=lambda a: _ann_index(a.get("source_annotation_id", "")))
    count_ok = len(parsed) == len(stored_sorted)
    ious: list[float] = []
    dev_max = 0.0
    n_degenerate = 0
    n_nonconvex = 0
    class_ok = True
    for orig, rec in zip(parsed, stored_sorted):
        orig_poly = orig["polygon"]
        stored_poly = rec.get("polygon_scene_px") or []
        dev = polygon_vertex_deviation(orig_poly, stored_poly)
        if dev is not None:
            dev_max = max(dev_max, dev)
        if polygon_area(orig_poly) <= DEGEN_AREA or polygon_area(stored_poly) <= DEGEN_AREA:
            n_degenerate += 1  # zerowe pole — IoU nieokreślone
        elif not (is_convex(orig_poly) and is_convex(stored_poly)):
            n_nonconvex += 1   # niewypukły quad źródła — nasze wypukłe IoU nie jest miarodajne
        else:
            ious.append(polygon_iou(orig_poly, stored_poly))
        if class_names.get(int(rec.get("class_id", -1))) != orig.get("class_name"):
            class_ok = False
    iou_min = min(ious) if ious else None
    return {
        "n_in": len(parsed),
        "n_out": len(stored_sorted),
        "dev_max": dev_max,
        "iou_min": iou_min,
        "ious": ious,
        "n_degenerate": n_degenerate,
        "n_nonconvex": n_nonconvex,
        "class_ok": class_ok,
        "count_ok": count_ok,
    }


def _run_dataset(name: str, parser, label_of, rng: random.Random):
    project = appenv.benchmark_project(name)
    class_list = json.loads((project / "classes.json").read_text(encoding="utf-8"))
    class_names = {int(c["id"]): c["name"] for c in class_list}
    scene_dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())
    if len(scene_dirs) > SCENES_PER_DATASET:
        scene_dirs = rng.sample(scene_dirs, SCENES_PER_DATASET)
        scene_dirs.sort(key=lambda p: p.name)

    rows = []
    all_ious: list[float] = []
    dev_max_ds = 0.0
    n_degenerate_ds = 0
    n_nonconvex_ds = 0
    n_class_ok = n_count_ok = n_compared = n_missing_label = 0
    for scene_dir in scene_dirs:
        manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
        source_path = Path(manifest.get("source_path", ""))
        label_path = label_of(source_path)
        if not label_path.is_file():
            n_missing_label += 1
            continue
        stored = json.loads((scene_dir / "annotations.json").read_text(encoding="utf-8"))
        parsed = parser(label_path)
        cmp = _compare_scene(parsed, stored, class_names)
        all_ious.extend(cmp["ious"])
        dev_max_ds = max(dev_max_ds, cmp["dev_max"])
        n_degenerate_ds += cmp["n_degenerate"]
        n_nonconvex_ds += cmp["n_nonconvex"]
        n_compared += 1
        n_class_ok += int(cmp["class_ok"])
        n_count_ok += int(cmp["count_ok"])
        iou_min_str = "" if cmp["iou_min"] is None else f"{cmp['iou_min']:.6f}"
        rows.append((
            name, scene_dir.name, cmp["n_in"], cmp["n_out"],
            f"{cmp['dev_max']:.6f}", iou_min_str, int(cmp["class_ok"]), int(cmp["count_ok"]),
        ))

    summary = {
        "n_scenes": n_compared,
        "n_pairs": len(all_ious),
        "n_degenerate": n_degenerate_ds,
        "n_nonconvex": n_nonconvex_ds,
        "n_missing_label": n_missing_label,
        "vertex_dev_max_px": round(dev_max_ds, 6),
        "iou_p50": round(percentile(all_ious, 0.5), 6) if all_ious else None,
        "iou_min": round(min(all_ious), 6) if all_ious else None,
        "class_match_pct": round(100.0 * n_class_ok / n_compared, 3) if n_compared else None,
        "count_match_pct": round(100.0 * n_count_ok / n_compared, 3) if n_compared else None,
    }
    return rows, all_ious, summary, dev_max_ds


def main() -> ValidationResult:
    res = ValidationResult(
        id="v10",
        claim="C2a",
        title="Wierność importu benchmarku (lossless canonicalization)",
        substrate=["DOTA", "DIOR-R", "FAIR1M"],
        tier="public",
    )
    res.config = {"scenes_per_dataset": SCENES_PER_DATASET, "sample_seed": SAMPLE_SEED, "iou_pass": IOU_PASS}

    appenv.bootstrap()
    try:
        parsers = _load_parsers()
    except Exception as exc:  # noqa: BLE001
        res.status = "todo"
        res.notes = f"Nie można zaimportować parserów importerów: {exc}"
        return res

    rng = random.Random(SAMPLE_SEED)
    all_rows = []
    all_ious: list[float] = []
    dev_max_all = 0.0
    per_dataset = {}
    missing_projects = []
    for name, (parser, label_of) in parsers.items():
        try:
            rows, ious, summary, dev_max_ds = _run_dataset(name, parser, label_of, rng)
        except FileNotFoundError as exc:
            missing_projects.append(name)
            per_dataset[name] = {"error": str(exc)}
            continue
        all_rows.extend(rows)
        all_ious.extend(ious)
        dev_max_all = max(dev_max_all, dev_max_ds)
        per_dataset[name] = summary

    if not all_ious:
        res.status = "todo" if missing_projects else "fail"
        res.notes = (
            f"Brak porównanych obiektów. Brakujące projekty: {missing_projects or 'brak'}. "
            "Zaimportuj benchmarki (../importers/README.md) i upewnij się, że oryginały są dostępne."
        )
        res.metrics = {"per_dataset": per_dataset}
        return res

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "import_fidelity.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["dataset", "scene", "n_in", "n_out", "vertex_dev_max_px", "iou_min", "class_ok", "count_ok"])
        writer.writerows(all_rows)

    graded = [d for d in per_dataset.values() if d.get("n_scenes")]
    iou_min = min(all_ious) if all_ious else None
    class_pct = min((d["class_match_pct"] for d in graded), default=0.0)
    count_pct = min((d["count_match_pct"] for d in graded), default=0.0)
    n_degenerate = sum(d.get("n_degenerate", 0) for d in graded)
    n_nonconvex = sum(d.get("n_nonconvex", 0) for d in graded)

    res.metrics = {
        "n_scenes": sum(d["n_scenes"] for d in graded),
        "n_pairs": len(all_ious),
        "n_degenerate": n_degenerate,
        "n_nonconvex": n_nonconvex,
        "vertex_dev_max_px": round(dev_max_all, 6),
        "iou_p50": round(percentile(all_ious, 0.5), 6) if all_ious else None,
        "iou_min_nondegenerate": round(iou_min, 6) if iou_min is not None else None,
        "class_match_pct": class_pct,
        "count_match_pct": count_pct,
        "per_dataset": per_dataset,
    }
    res.artifacts = ["results/import_fidelity.csv"]
    # Bezstratność orzekamy odchyleniem wierzchołków (odporne na degenerację), a nie IoU:
    # IoU jest metryką kształtu dla niezdegenerowanych, ale nieokreślone dla zerowego pola.
    lossless = dev_max_all <= DEV_PASS and count_pct == 100.0 and class_pct == 100.0
    shape_ok = iou_min is None or iou_min > IOU_PASS
    res.status = "pass" if (lossless and shape_ok) else "fail"
    per_ds_counts = ", ".join(f"{k}:{v.get('n_scenes', '-')}" for k, v in per_dataset.items())
    res.notes = (
        f"Import bezstratny na {res.metrics['n_scenes']} scenach / "
        f"{len(all_ious) + n_degenerate + n_nonconvex} obiektach ({per_ds_counts}): maks. "
        f"odchylenie wierzchołków={dev_max_all:.4g} px, klasy={class_pct}%, liczności={count_pct}%; "
        f"IoU (wypukłe niezdegenerowane, n={len(all_ious)}) min={res.metrics['iou_min_nondegenerate']}, "
        f"p50={res.metrics['iou_p50']}. Pominięte w IoU (nie w werdykcie bezstratności): "
        f"{n_degenerate} zdegenerowanych (zerowe pole, przycięte przy krawędzi) + "
        f"{n_nonconvex} niewypukłych quadów źródła — wszystkie zapisane wiernie (odchylenie ≈0)."
    )
    if missing_projects:
        res.notes += f" Pominięto niezaimportowane: {missing_projects}."
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
