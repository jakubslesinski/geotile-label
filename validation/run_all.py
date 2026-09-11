"""Uruchom wszystkie dowody i zagreguj results/*/result.json do jednej tabeli.

Wynik: figures/evidence_summary.csv + wypis do konsoli (claim, status, kluczowe metryki).
Ta tabela jest źródłem zwartej tabeli claim↔wynik wstawianej do artykułu.
"""

from __future__ import annotations

import csv
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER_ROOT = os.path.dirname(HERE)

# kolejność zgodna z claim-evidence-matrix.md
ORDER = [
    "v01_compat_matrix", "v02_geometry_roundtrip", "v03_run_reproducibility",
    "v04_split_leakage", "v05_id_provenance_chain", "v06_training_example",
    "v07_performance", "v08_offline_egress", "v09_class_similarity",
    "v10_import_fidelity", "v11_geo_accuracy_external",
    "v12_metadata_stratified_eval", "v13_split_leakage_effect",
    "v14_display_asset_invariance",
]


def _run_one(folder: str) -> None:
    """Uruchom dowód jako IZOLOWANY subprocess.

    Każdy dowód musi startować we własnym procesie: (1) `db.storage.DATA_DIR`/`MODELS_ROOT`
    są cache'owane przy imporcie, więc współdzielony proces powodowałby wyciek konfiguracji
    między dowodami; (2) kolejność ładowania GDAL↔torch i runtime MKL muszą być czyste
    (patrz appenv). Dowód sam zapisuje `results/result.json` przez swój blok __main__.
    """
    import subprocess

    run_py = os.path.join(HERE, folder, "run.py")
    if not os.path.isfile(run_py):
        return
    # Bez jawnego `encoding` Python czyta wyjscie podprocesu kodowaniem konsoli
    # (cp1250 na polskim Windows) i wywala UnicodeDecodeError w watku czytajacym,
    # gdy dowod wypisze cokolwiek spoza tej strony kodowej.
    completed = subprocess.run(
        [sys.executable, run_py], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-1:] or ["(brak stderr)"]
        print(f"[warn] {folder}: exit {completed.returncode}: {tail[0][:120]}")


def main() -> int:
    # konsola Windows (cp1250) nie ma -> / × — wypisuj bezpiecznie
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.path.insert(0, HERE)
    for folder in ORDER:
        try:
            _run_one(folder)
        except Exception as exc:  # nie przerywaj agregacji przez jeden dowód
            print(f"[warn] {folder}: {exc}")

    rows = []
    for path in sorted(glob.glob(os.path.join(HERE, "*", "results", "result.json"))):
        with open(path, encoding="utf-8") as fh:
            r = json.load(fh)
        rows.append(r)
        substrate = ",".join(r.get("substrate", [])) or "-"
        tier = r.get("tier", "-")
        print(f"{r['id']:>4}  {r['claim']:<6}  {r['status']:<5}  "
              f"{tier:<13}  {substrate:<24}  {r['title']}")

    # Domyslnie obok dowodow. W repozytorium artykulu ta sama tabela jest zrodlem
    # zwartej tabeli claim<->wynik, wiec sciezke mozna przekierowac zmienna.
    fig_dir = os.environ.get("EVIDENCE_SUMMARY_DIR", os.path.join(HERE, "results"))
    os.makedirs(fig_dir, exist_ok=True)
    out = os.path.join(fig_dir, "evidence_summary.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "claim", "status", "tier", "substrate", "title", "metrics"])
        for r in rows:
            w.writerow([r["id"], r["claim"], r["status"], r.get("tier", ""),
                        ";".join(r.get("substrate", [])), r["title"],
                        json.dumps(r.get("metrics", {}), ensure_ascii=False)])
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
