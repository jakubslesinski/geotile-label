"""v13 — Skutek przecieku: luka mAP random vs spatial  (Claim C1, skutek).

SUBSTRATE / TIER
    FAIR1M lub DOTA — dość klas/obrazów, by zmierzyć różnicę metryki · public.

CLAIM
    Przeciek przestrzenny nie jest tylko teoretyczny: split losowy ZAWYŻA raportowaną
    metrykę względem splitu blokowego. v04 pokazuje mechanizm; v13 pokazuje SKUTEK.

METHOD
    Dwa przebiegi treningu tego samego projektu (uruchomione w aplikacji), identyczne poza
    strategią splitu: `split=random_tile` vs `split=spatial_block_split`. Ten dowód KONSUMUJE
    ich artefakty (`metrics.json`) i zgłasza JEDNĄ liczbę: luka = mAP(random) − mAP(spatial).
    Sprawdza spójność konfiguracji (ten sam base_model / epoki / seed / task). Pełne ablacje
    (rozmiar bloku, ratio…) -> osobny artykuł metodologiczny.

INPUTS
    - dwa ukończone przebiegi OBB tego samego projektu (random_tile + spatial_block_split)
    - odczyt artefaktów: _common/appruns.py (APP_DATA_DIR)

OUTPUTS
    - results/leakage_effect.csv  (split, map50, map5095, precision, recall)
    - metrics: {project, map50_random, map50_spatial, map_gap_pp, map5095_gap_pp, base_model, seed}

PASS CRITERION
    Oba przebiegi ukończone; luka mAP policzona (oczekiwane: mAP(random) > mAP(spatial) —
    zawyżenie przez przeciek). Wynik opisowy, bez progu.
"""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appruns

CANDIDATES = [p for p in (os.environ.get("V13_PROJECT", "").split(",")) if p] or ["FAIR1M", "DOTA"]
SPATIAL_MODES = {"spatial_block_split", "class_balanced_spatial"}


def _find_pair(name: str):
    """Zwróć (random_run, spatial_run) — najnowsze ukończone OBB dla każdej strategii."""
    runs = appruns.list_completed_runs(name, task="obb") or appruns.list_completed_runs(name)
    rnd = next((r for r in runs if r["split_mode"] == "random_tile"), None)
    spa = next((r for r in runs if r["split_mode"] in SPATIAL_MODES), None)
    return rnd, spa


def _map(summary, key):
    v = summary.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def main() -> ValidationResult:
    res = ValidationResult(
        id="v13", claim="C1",
        title="Skutek przecieku: luka mAP random − spatial",
        substrate=["FAIR1M", "DOTA"], tier="public",
    )
    res.config = {"candidates": CANDIDATES}

    project = rnd = spa = None
    for name in CANDIDATES:
        r, s = _find_pair(name)
        if r and s:
            project, rnd, spa = name, r, s
            break

    if not (rnd and spa):
        res.status = "todo"
        res.notes = (
            f"Brak pary przebiegów random_tile + spatial_block w {CANDIDATES} "
            f"(szukano w {appruns.app_data_dir()}). Uruchom w aplikacji dwa treningi tego samego "
            "projektu: raz split=random_tile, raz split=spatial_block (reszta identyczna)."
        )
        return res

    map50_r, map50_s = _map(rnd["summary"], "mAP50"), _map(spa["summary"], "mAP50")
    map5095_r, map5095_s = _map(rnd["summary"], "mAP50-95"), _map(spa["summary"], "mAP50-95")
    gap50 = (map50_r - map50_s) if (map50_r is not None and map50_s is not None) else None
    gap5095 = (map5095_r - map5095_s) if (map5095_r is not None and map5095_s is not None) else None

    # Spójność konfiguracji — porównanie fair tylko przy tym samym modelu/epokach/seedzie/tasku.
    config_consistent = all([
        rnd["base_model"] == spa["base_model"],
        rnd["epochs"] == spa["epochs"],
        rnd["split_seed"] == spa["split_seed"],
        rnd["task"] == spa["task"],
    ])

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "leakage_effect.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["split", "run_id", "map50", "map5095", "precision", "recall"])
        for label, r in (("random_tile", rnd), ("spatial_block", spa)):
            s = r["summary"]
            w.writerow([label, r["run_id"], s.get("mAP50"), s.get("mAP50-95"),
                        s.get("precision"), s.get("recall")])

    res.metrics = {
        "project": project,
        "base_model": rnd["base_model"],
        "epochs": rnd["epochs"],
        "seed": rnd["split_seed"],
        "task": rnd["task"],
        "config_consistent": config_consistent,
        "random_run": rnd["run_id"],
        "spatial_run": spa["run_id"],
        "map50_random": round(map50_r, 6) if map50_r is not None else None,
        "map50_spatial": round(map50_s, 6) if map50_s is not None else None,
        "map_gap_pp": round(gap50, 6) if gap50 is not None else None,
        "map5095_random": round(map5095_r, 6) if map5095_r is not None else None,
        "map5095_spatial": round(map5095_s, 6) if map5095_s is not None else None,
        "map5095_gap_pp": round(gap5095, 6) if gap5095 is not None else None,
    }
    res.artifacts = ["results/leakage_effect.csv"]
    res.status = "pass" if (gap50 is not None and config_consistent) else "fail"
    direction = "random > spatial (zawyżenie przez przeciek)" if (gap50 or 0) > 0 else "random <= spatial"
    res.notes = (
        f"{project} OBB ({rnd['base_model']}, {rnd['epochs']} epok, seed {rnd['split_seed']}): "
        f"mAP50 random={map50_r:.4f} vs spatial={map50_s:.4f} -> luka {gap50:+.4f} "
        f"(mAP50-95 luka {gap5095:+.4f}); {direction}. Konfiguracja spójna={config_consistent}."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
