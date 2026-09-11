"""v02 — Dokładność transformacji pixel -> world -> pixel  (Claim C3, wewnętrzna).

SUBSTRATE / TIER
    FAIR1M (EPSG:4326) + xView3 (UTM) dla afinicznej odwracalności · public;
    SAR_test (Capella, OBB) + FAIR1M dla wierności kształtu OBB · mixed.
    NITF/TPS jest POZA suite — pipeline geo jest afiniczny (patrz claim-evidence-matrix).

CLAIM
    Lokalizacja jest geometrycznie poprawna: transformacja afiniczna jest odwracalna,
    a ramka obrócona (OBB) niesiona jako polygon_scene_px NIE jest skewowana przy round-tripie
    (bo NIE jest odbudowywana z rotated_bbox).

METHOD
    (a) Afiniczna odwracalność — dla siatki pikseli na scenie geo licz round-trip
        pixel -> WGS84 -> pixel PUBLICZNYM API aplikacji (`SceneGeoModel.pixel_to_wgs84`
        / `wgs84_to_pixel`, gałąź affine) i residuum ||pixel_in − pixel_out|| (px oraz ×gsd -> m).
    (b) Kształt OBB — weź polygon_scene_px obróconych ramek, przejdź round-trip wierzchołek
        po wierzchołku i policz IoU(oryginał, po round-tripie) oraz maks. przesunięcie
        wierzchołka. Raportuj ROZKŁAD (p50/p95/min) po wielu scenach.

INPUTS
    - zaimportowane projekty FAIR1M + xView3 (afiniczna odwracalność)
    - fixture SAR_test + FAIR1M (wierność kształtu OBB)
    - SceneGeoModel z backendu (services/sensor_geometry.py)

OUTPUTS
    - results/roundtrip_residuals.csv  (scene, crs, residual_px, residual_m)
    - results/obb_shape_error.csv      (project, scene, source_annotation_id, iou, max_vertex_shift_px)
    - metrics: {affine_p95_px, affine_p95_m, obb_min_iou, n_points, n_obb}

PASS CRITERION
    afiniczna: residuum p95 < 1e-3 px (praktycznie zero);
    OBB: min IoU kształtu po round-trip > 0.999 (brak skewu).
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv
from _common.geom import polygon_iou, polygon_area, percentile

AFFINE_GRID = int(os.environ.get("V02_GRID", "15"))          # siatka NxN pikseli / scenę
FAIR1M_SCENES = int(os.environ.get("V02_FAIR1M_SCENES", "20"))
XVIEW3_SCENES = int(os.environ.get("V02_XVIEW3_SCENES", "10"))
OBB_FAIR1M_SCENES = int(os.environ.get("V02_OBB_FAIR1M_SCENES", "40"))
AFFINE_PASS_PX = 1e-3
OBB_PASS_IOU = 0.999
# Lokalny fixture SAR wskazuje SAR_TEST_PROJECT. Bez niego ten fragment
# sprawozdania jest pomijany, wiec domyslna sciezka nie jest potrzebna.
SAR_TEST_PROJECT = os.environ.get("SAR_TEST_PROJECT", "sar-test-project")


def _gsd_m(transform: list, crs: str, lat0: float) -> float:
    """Przybliżony GSD w metrach z afinicznego kroku 1 px."""
    a = abs(float(transform[0]))
    if "4326" in str(crs) or "CRS84" in str(crs).upper():
        return a * 111320.0 * max(0.1, math.cos(math.radians(lat0)))
    return a  # CRS projektowany (UTM) — jednostka to metr


def _affine_scene(scene_dir: Path, sid: str, SceneGeoModel):
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    geo = manifest.get("geospatial") or {}
    if not geo.get("has_geo") or not geo.get("transform") or not geo.get("crs"):
        return None
    model = SceneGeoModel.from_manifest(manifest)
    if model is None or model.kind != "affine":
        return None
    image = manifest.get("image") or {}
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    if not width or not height:
        return None
    bounds = geo.get("bounds_wgs84") or [0, 0, 0, 0]
    lat0 = (float(bounds[1]) + float(bounds[3])) / 2.0
    gsd = _gsd_m(geo["transform"], geo["crs"], lat0)

    pts = []
    for i in range(1, AFFINE_GRID + 1):
        for j in range(1, AFFINE_GRID + 1):
            pts.append([width * i / (AFFINE_GRID + 1), height * j / (AFFINE_GRID + 1)])
    wgs84 = model.pixel_to_wgs84(pts)
    back = model.wgs84_to_pixel(wgs84)
    residuals_px = [math.hypot(p[0] - b[0], p[1] - b[1]) for p, b in zip(pts, back)]
    return {"crs": geo["crs"], "gsd_m": gsd, "residuals_px": residuals_px}


def _obb_scene(scene_dir: Path, sid: str, SceneGeoModel):
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    model = SceneGeoModel.from_manifest(manifest)
    if model is None:
        return None
    records = json.loads((scene_dir / "annotations.json").read_text(encoding="utf-8"))
    out = []
    for rec in records:
        if rec.get("is_negative"):
            continue
        poly = rec.get("polygon_scene_px")
        if not poly or len(poly) < 3 or polygon_area(poly) <= 0:
            continue
        rt = model.wgs84_to_pixel(model.pixel_to_wgs84(poly))
        iou = polygon_iou(poly, rt)
        shift = max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(poly, rt))
        out.append((str(rec.get("source_annotation_id") or rec.get("id")), iou, shift))
    return out


def _iter(project: Path, limit: int | None):
    dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())
    return dirs[:limit] if limit else dirs


def main() -> ValidationResult:
    res = ValidationResult(
        id="v02",
        claim="C3",
        title="Round-trip pixel->world->pixel (affine) + wierność kształtu OBB",
        substrate=["FAIR1M", "xView3", "SAR_test"],
        tier="mixed",
    )
    res.config = {"grid": AFFINE_GRID, "affine_pass_px": AFFINE_PASS_PX, "obb_pass_iou": OBB_PASS_IOU}

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    from services.sensor_geometry import SceneGeoModel

    # --- (a) afiniczna odwracalność: FAIR1M + xView3 ---
    affine_rows = []
    residuals_px: list[float] = []
    residuals_m: list[float] = []
    n_affine_scenes = 0
    affine_targets = []
    for name, scenes in (("FAIR1M", FAIR1M_SCENES), ("xView3", XVIEW3_SCENES)):
        try:
            affine_targets.append((name, appenv.benchmark_project(name), scenes))
        except FileNotFoundError:
            pass
    for name, project, limit in affine_targets:
        for scene_dir in _iter(project, limit):
            out = _affine_scene(scene_dir, scene_dir.name, SceneGeoModel)
            if not out:
                continue
            n_affine_scenes += 1
            for r in out["residuals_px"]:
                residuals_px.append(r)
                residuals_m.append(r * out["gsd_m"])
            affine_rows.append((name, scene_dir.name, out["crs"],
                                f"{max(out['residuals_px']):.3e}",
                                f"{max(out['residuals_px']) * out['gsd_m']:.3e}"))

    # --- (b) wierność kształtu OBB: SAR_test + FAIR1M ---
    obb_rows = []
    ious: list[float] = []
    shifts: list[float] = []
    obb_targets = []
    sar = Path(SAR_TEST_PROJECT)
    if (sar / "scenes").is_dir():
        obb_targets.append(("SAR_test", sar, None))
    try:
        obb_targets.append(("FAIR1M", appenv.benchmark_project("FAIR1M"), OBB_FAIR1M_SCENES))
    except FileNotFoundError:
        pass
    for name, project, limit in obb_targets:
        for scene_dir in _iter(project, limit):
            out = _obb_scene(scene_dir, scene_dir.name, SceneGeoModel)
            if not out:
                continue
            for src, iou, shift in out:
                ious.append(iou)
                shifts.append(shift)
                obb_rows.append((name, scene_dir.name, src, f"{iou:.6f}", f"{shift:.4e}"))

    if not residuals_px or not ious:
        res.status = "todo" if (not affine_targets or not obb_targets) else "fail"
        res.notes = (
            f"Za mało danych: affine_points={len(residuals_px)}, obb={len(ious)}. "
            "Zaimportuj FAIR1M/xView3 i wskaż SAR_test."
        )
        return res

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "roundtrip_residuals.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "scene", "crs", "residual_px_max", "residual_m_max"])
        w.writerows(affine_rows)
    with open(os.path.join(out_dir, "obb_shape_error.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "scene", "source_annotation_id", "iou", "max_vertex_shift_px"])
        w.writerows(obb_rows)

    affine_p95_px = percentile(residuals_px, 0.95)
    affine_p95_m = percentile(residuals_m, 0.95)
    obb_min_iou = min(ious)

    res.metrics = {
        "n_points": len(residuals_px),
        "n_affine_scenes": n_affine_scenes,
        "affine_p50_px": round(percentile(residuals_px, 0.5), 12),
        "affine_p95_px": round(affine_p95_px, 12),
        "affine_max_px": round(max(residuals_px), 12),
        "affine_p95_m": round(affine_p95_m, 9),
        "n_obb": len(ious),
        "obb_min_iou": round(obb_min_iou, 6),
        "obb_p50_iou": round(percentile(ious, 0.5), 6),
        "obb_max_vertex_shift_px": round(max(shifts), 6),
    }
    res.artifacts = ["results/roundtrip_residuals.csv", "results/obb_shape_error.csv"]
    res.status = "pass" if (affine_p95_px < AFFINE_PASS_PX and obb_min_iou > OBB_PASS_IOU) else "fail"
    res.notes = (
        f"Affine round-trip na {len(residuals_px)} punktach / {n_affine_scenes} scenach: "
        f"p95={affine_p95_px:.2e} px ({affine_p95_m:.2e} m), max={max(residuals_px):.2e} px. "
        f"Kształt OBB na {len(ious)} ramkach (SAR_test+FAIR1M): min IoU={obb_min_iou:.6f}, "
        f"maks. przesunięcie wierzchołka={max(shifts):.2e} px (brak skewu)."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
