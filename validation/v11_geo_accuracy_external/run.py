"""v11 — Dokładność geo względem zewnętrznego GT  (Claim C3, falsyfikowalny).

SUBSTRATE / TIER
    xView3 — każda detekcja ma i `detect_lat/lon` (geo), i `detect_scene_row/col`
    (piksel): niezależne, publikowane odniesienie prawdy · public.

CLAIM
    Transformacja pixel->world aplikacji jest DOKŁADNA wobec zewnętrznego ground-truth,
    a nie tylko wewnętrznie odwracalna (v02). To jedyny w suite test falsyfikowalny z
    zewnątrz.

METHOD
    Dla każdej detekcji xView3 zaimportowanego projektu: weź `detect_scene_column/row`
    (piksel) i policz pixel->WGS84 przez PUBLICZNE API aplikacji
    ``SceneGeoModel.pixel_to_wgs84`` (gałąź affine, CRS sceny = UTM). Residuum liczymy
    w METRACH: rzutujemy WYNIK aplikacji i publikowany `detect_lat/lon` do CRS sceny
    (UTM, jednostka = metr) tą samą prymitywą co pipeline (``transform_points`` ->
    rasterio.warp) i bierzemy odległość euklidesową. Raportujemy rozkład po wszystkich
    scenach (p50/p95/max) oraz per-scena. Metrę zamiast geodezyjnej — bo CRS jest już
    metryczny i to eliminuje zależność od pyproj (backend liczy przez rasterio).

INPUTS
    - zaimportowany projekt xView3 (../importers/import_xview3.py): scene_manifest z
      afinicznym `geospatial.transform`+`crs`; annotations.json z `attributes`
      (`detect_lat/lon`, `detect_scene_column/row`)
    - SceneGeoModel + transform_points z backendu aplikacji

OUTPUTS
    - results/geo_accuracy.csv  (scene_id, detect_id, residual_m)
    - metrics: {n_detections, n_scenes, residual_m_p50, residual_m_p95, residual_m_max, crs, gsd_m}

PASS CRITERION
    residuum p95 na poziomie ~1 piksela GSD (10 m) — spójne z dokładnością etykiet
    xView3. Bramka sanity: p95 <= 1.5×GSD; sama wartość jest RAPORTOWANA.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv
from _common.geom import percentile

XVIEW3_GSD_M = 10.0  # Sentinel-1 xView3


def _num(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> ValidationResult:
    res = ValidationResult(
        id="v11",
        claim="C3",
        title="Dokładność pixel->world vs xView3 lat/lon (residuum w metrach)",
        substrate=["xView3"],
        tier="public",
    )
    res.config = {"gsd_m": XVIEW3_GSD_M, "pass_gate": "p95 <= 1.5*gsd"}

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    from services.sensor_geometry import SceneGeoModel, WGS84
    from services.attribute_engine.engine import transform_points

    try:
        project = appenv.benchmark_project("xView3")
    except FileNotFoundError as exc:
        res.status = "todo"
        res.notes = str(exc)
        return res

    scenes_dir = project / "scenes"
    rows: list[tuple[str, str, float]] = []
    residuals: list[float] = []
    per_scene: dict[str, list[float]] = {}
    crs_seen: set[str] = set()
    skipped_no_geo = 0

    for scene_dir in sorted(p for p in scenes_dir.iterdir() if p.is_dir()):
        sid = scene_dir.name
        manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
        model = SceneGeoModel.from_manifest(manifest)
        crs = ((manifest.get("geospatial") or {}).get("crs"))
        if model is None or model.kind != "affine" or not crs:
            skipped_no_geo += 1
            continue
        crs_seen.add(crs)

        ann_path = scene_dir / "annotations.json"
        if not ann_path.is_file():
            continue
        anns = json.loads(ann_path.read_text(encoding="utf-8"))

        pixels: list[list[float]] = []
        gts: list[list[float]] = []
        ids: list[str] = []
        for ann in anns:
            at = ann.get("attributes") or {}
            col = _num(at.get("detect_scene_column"))
            row = _num(at.get("detect_scene_row"))
            lat = _num(at.get("detect_lat"))
            lon = _num(at.get("detect_lon"))
            if None in (col, row, lat, lon):
                continue
            pixels.append([col, row])
            gts.append([lon, lat])
            ids.append(str(at.get("detect_id") or ann.get("source_annotation_id") or ann.get("id")))

        if not pixels:
            continue

        # Publiczne API aplikacji: pixel -> WGS84 (gałąź affine).
        computed_wgs84 = model.pixel_to_wgs84(pixels)
        # Residuum w metrach: oba punkty do CRS sceny (UTM) i odległość euklidesowa.
        computed_native = transform_points(computed_wgs84, WGS84, crs)
        gt_native = transform_points(gts, WGS84, crs)
        for det_id, (cx, cy), (gx, gy) in zip(ids, computed_native, gt_native):
            d = math.hypot(cx - gx, cy - gy)
            rows.append((sid, det_id, d))
            residuals.append(d)
            per_scene.setdefault(sid, []).append(d)

    if not residuals:
        res.status = "fail"
        res.notes = (
            "Brak detekcji z kompletem (detect_scene_column/row + detect_lat/lon). "
            f"Pominięto {skipped_no_geo} scen bez modelu afinicznego."
        )
        return res

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "geo_accuracy.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scene_id", "detect_id", "residual_m"])
        for sid, det_id, d in rows:
            writer.writerow([sid, det_id, f"{d:.4f}"])

    # Per-scena p95 do artefaktu (rozkład, nie jedna liczba).
    scene_csv = os.path.join(out_dir, "geo_accuracy_by_scene.csv")
    with open(scene_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scene_id", "n", "p50_m", "p95_m", "max_m"])
        for sid in sorted(per_scene):
            vals = per_scene[sid]
            writer.writerow([
                sid, len(vals), f"{percentile(vals, 0.5):.4f}",
                f"{percentile(vals, 0.95):.4f}", f"{max(vals):.4f}",
            ])

    p50 = percentile(residuals, 0.5)
    p95 = percentile(residuals, 0.95)
    rmax = max(residuals)
    mean = sum(residuals) / len(residuals)

    res.metrics = {
        "n_detections": len(residuals),
        "n_scenes": len(per_scene),
        "n_scenes_skipped_no_geo": skipped_no_geo,
        "residual_m_p50": round(p50, 4),
        "residual_m_p95": round(p95, 4),
        "residual_m_max": round(rmax, 4),
        "residual_m_mean": round(mean, 4),
        "crs": sorted(crs_seen),
        # Liczba RÓŻNYCH układów UTM w próbie. Wyodrębniona, bo tekst artykułu powołuje się
        # na nią wprost, a dotąd dało się ją dostać tylko ręcznym policzeniem listy.
        # Uwaga przy redakcji: to układy, nie strefy — EPSG:32632 i EPSG:32732 to ta sama
        # strefa 32 na dwóch półkulach.
        "n_crs": len(crs_seen),
        "gsd_m": XVIEW3_GSD_M,
    }
    res.artifacts = ["results/geo_accuracy.csv", "results/geo_accuracy_by_scene.csv"]
    res.status = "pass" if p95 <= 1.5 * XVIEW3_GSD_M else "fail"
    res.notes = (
        f"pixel->WGS84 (SceneGeoModel.affine) vs publikowany detect_lat/lon dla {len(residuals)} "
        f"detekcji z {len(per_scene)} scen: p50={p50:.2f} m, p95={p95:.2f} m, max={rmax:.2f} m "
        f"(GSD={XVIEW3_GSD_M:.0f} m). Residuum liczone w CRS sceny (UTM, metry)."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
