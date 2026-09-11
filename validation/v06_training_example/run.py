"""v06 — Przykład treningu OBB: loss/mAP + confusion matrix  (Claim C4).

SUBSTRATE / TIER
    DOTA lub FAIR1M — realny benchmark OBB (wiele klas) · public.
    Confusion matrix z tego runu zasila v09 (walidacja podobieństwa) i v13 (luka mAP).

CLAIM
    Zbudowany dataset jest wprost używalny — zamknięta pętla treningu W APLIKACJI
    (dataset -> trening -> metryki) działa bez wychodzenia poza narzędzie.

METHOD
    Trening uruchamiany jest w GeoTile Label (wbudowany worker YOLO). Ten dowód KONSUMUJE
    artefakty ukończonego przebiegu z danych aplikacji (``training_runs/<run>``):
    ``metrics.json`` (summary + per_class), ``results.csv`` (krzywe loss/mAP po epokach),
    ``confusion_matrix.json``. To demonstracja używalności na realnym benchmarku (nie pogoń
    za SOTA). Preferujemy przebieg OBB (task=obb); jeśli brak — bierzemy dowolny ukończony
    i zaznaczamy odstępstwo.

INPUTS
    - ukończony przebieg treningu OBB projektu DOTA/FAIR1M (uruchomiony w aplikacji)
    - odczyt artefaktów: _common/appruns.py (APP_DATA_DIR)

OUTPUTS
    - results/metrics.csv          (epoch, box_loss, cls_loss, map50, map5095)
    - results/confusion_matrix.json (kopia — feed dla v09)
    - metrics: {project, run_id, task, base_model, epochs, best_map50, best_map5095, n_classes}

PASS CRITERION
    Znaleziono UKOŃCZONY przebieg z krzywymi (≥1 epoka) i confusion matrix; metryki
    zaraportowane (opisowo, bez progu jakościowego).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appruns

CANDIDATES = [p for p in (os.environ.get("V06_PROJECT", "").split(",")) if p] or ["DOTA", "FAIR1M"]


def _pick_run():
    """Zwróć (project, run) — preferuj OBB; w ostateczności dowolny ukończony."""
    for name in CANDIDATES:
        obb = appruns.list_completed_runs(name, task="obb")
        if obb:
            return name, obb[0]
    for name in CANDIDATES:
        any_run = appruns.list_completed_runs(name)
        if any_run:
            return name, any_run[0]
    return None, None


def main() -> ValidationResult:
    res = ValidationResult(
        id="v06", claim="C4",
        title="Przykład treningu OBB (loss/mAP + confusion matrix)",
        substrate=["DOTA", "FAIR1M"], tier="public",
    )
    res.config = {"candidates": CANDIDATES, "app_data_dir": str(appruns.app_data_dir())}

    project, run = _pick_run()
    if run is None:
        res.status = "todo"
        res.notes = (
            f"Brak ukończonego przebiegu treningu w projektach {CANDIDATES} "
            f"(szukano w {appruns.app_data_dir()}). Uruchom trening OBB (DOTA/FAIR1M) w aplikacji, "
            "potem ten dowód skonsumuje jego artefakty."
        )
        return res

    curve = run["curve"]
    summary = run["summary"]
    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)

    # krzywe loss/mAP
    with open(os.path.join(out_dir, "metrics.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["epoch", "train_box_loss", "train_cls_loss", "val_box_loss", "map50", "map5095"])
        for row in curve:
            w.writerow([
                row.get("epoch", ""), row.get("train_box_loss", ""), row.get("train_cls_loss", ""),
                row.get("val_box_loss", ""), row.get("map50", ""), row.get("map5095", ""),
            ])

    # confusion matrix — kopia jako feed dla v09
    artifacts = ["results/metrics.csv"]
    if run["has_confusion"]:
        shutil.copyfile(run["dir"] / "confusion_matrix.json", os.path.join(out_dir, "confusion_matrix.json"))
        artifacts.append("results/confusion_matrix.json")
        png = run["dir"] / "confusion_matrix.png"
        if png.is_file():
            shutil.copyfile(png, os.path.join(out_dir, "confusion_matrix.png"))
            artifacts.append("results/confusion_matrix.png")

    map50_curve = [r["map50"] for r in curve if "map50" in r]
    best_map50 = max(map50_curve) if map50_curve else summary.get("mAP50")
    map5095_curve = [r["map5095"] for r in curve if "map5095" in r]
    best_map5095 = max(map5095_curve) if map5095_curve else summary.get("mAP50-95")
    n_classes = len(run["per_class"]) or len((run["confusion"] or {}).get("class_names", []))

    res.metrics = {
        "project": project,
        "run_id": run["run_id"],
        "task": run["task"],
        "base_model": run["base_model"],
        "epochs": run["epochs"],
        "n_epochs_logged": run["n_epochs"],
        "n_classes": n_classes,
        "best_map50": round(best_map50, 6) if isinstance(best_map50, (int, float)) else None,
        "best_map5095": round(best_map5095, 6) if isinstance(best_map5095, (int, float)) else None,
        "final_precision": summary.get("precision"),
        "final_recall": summary.get("recall"),
        "has_confusion": run["has_confusion"],
    }
    res.artifacts = artifacts
    obb_note = "" if run["task"] == "obb" else f" UWAGA: przebieg task={run['task']} (nie OBB) — brak przebiegu OBB."
    res.status = "pass" if (curve and run["has_confusion"] and summary) else "fail"
    res.notes = (
        f"Zamknięta pętla treningu w aplikacji: {project} / {run['run_id']} "
        f"(task={run['task']}, base={run['base_model']}, {run['n_epochs']} epok). "
        f"best mAP50={res.metrics['best_map50']}, mAP50-95={res.metrics['best_map5095']}, "
        f"klas={n_classes}; confusion matrix obecna={run['has_confusion']}." + obb_note
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
