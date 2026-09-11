"""Odczyt ukończonych przebiegów treningu z DANYCH APLIKACJI (nie z benchmark_projects).

Trening odbywa się w GeoTile Label; jego artefakty (`metrics.json`, `results.csv`,
`confusion_matrix.json`, `training_manifest.json`) lądują w
``%APPDATA%\\GeoTileLabel\\data\\projects\\<id>\\training_runs\\<run>``. Dowody v06/v13/v09/v12
KONSUMUJĄ te artefakty (dowód „zamkniętej pętli w aplikacji"), zamiast uruchamiać trening.

Nadpisania: ``APP_DATA_DIR`` (domyślnie ``%APPDATA%\\GeoTileLabel\\data``).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any


def app_data_dir() -> Path:
    override = os.environ.get("APP_DATA_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "GeoTileLabel" / "data"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def project_id_by_name(name: str) -> str | None:
    index = _load(app_data_dir() / "projects_index.json", {}) or {}
    for project in index.get("projects", []):
        if project.get("name") == name:
            return project.get("project_id")
    return None


def training_runs_dir(name: str) -> Path | None:
    pid = project_id_by_name(name)
    if not pid:
        return None
    return app_data_dir() / "projects" / pid / "training_runs"


def _results_curve(run_dir: Path) -> list[dict[str, float]]:
    path = run_dir / "results.csv"
    if not path.is_file():
        return []
    lines = [ln.strip() for ln in io.open(path, encoding="utf-8") if ln.strip()]
    if len(lines) < 2:
        return []
    header = [h.strip() for h in lines[0].split(",")]

    def col(*needles: str, exclude: str | None = None) -> str | None:
        for h in header:
            low = h.lower()
            if all(n.lower() in low for n in needles) and (exclude is None or exclude.lower() not in low):
                return h
        return None

    keymap = {
        "epoch": col("epoch"),
        "train_box_loss": col("train", "box_loss"),
        "train_cls_loss": col("train", "cls_loss"),
        "val_box_loss": col("val", "box_loss"),
        "map50": col("map50", exclude="95"),
        "map5095": col("map50-95"),
        "precision": col("precision"),
        "recall": col("recall"),
    }
    rows: list[dict[str, float]] = []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        d = dict(zip(header, parts))
        row: dict[str, float] = {}
        for out_key, src in keymap.items():
            if src and src in d:
                try:
                    row[out_key] = float(d[src])
                except ValueError:
                    pass
        rows.append(row)
    return rows


def read_run(run_dir: Path) -> dict[str, Any]:
    """Odczytaj jeden przebieg treningu. ``completed`` = metrics.json z podsumowaniem."""
    job = _load(run_dir / "job.json", {}) or {}
    manifest = _load(run_dir / "training_manifest.json", {}) or {}
    metrics = _load(run_dir / "metrics.json", {}) or {}
    state = _load(run_dir / "job_state.json", {}) or {}
    summary = metrics.get("summary") or {}
    curve = _results_curve(run_dir)
    completed = bool(summary) and (
        bool(manifest.get("completed_at")) or state.get("status") == "complete" or len(curve) > 0
    )
    return {
        "run_id": run_dir.name,
        "dir": run_dir,
        "task": job.get("task") or manifest.get("task"),
        "base_model": job.get("base_model") or manifest.get("base_model"),
        "epochs": job.get("epochs") or manifest.get("epochs"),
        "split_mode": job.get("dataset_split_mode") or manifest.get("dataset_split_mode"),
        "split_seed": job.get("dataset_split_seed") or manifest.get("dataset_split_seed"),
        "dataset_run_id": job.get("dataset_run_id") or manifest.get("dataset_run_id"),
        "status": state.get("status"),
        "completed": completed,
        "completed_at": manifest.get("completed_at"),
        "summary": summary,
        "per_class": metrics.get("per_class") or {},
        "curve": curve,
        "n_epochs": len(curve),
        "has_confusion": (run_dir / "confusion_matrix.json").is_file(),
        "confusion": _load(run_dir / "confusion_matrix.json", {}) or {},
    }


def list_completed_runs(name: str, task: str | None = None) -> list[dict[str, Any]]:
    """Ukończone przebiegi projektu (po nazwie), najnowsze pierwsze; opcjonalny filtr zadania."""
    trd = training_runs_dir(name)
    if not trd or not trd.is_dir():
        return []
    runs = []
    for run_dir in trd.iterdir():
        if not run_dir.is_dir():
            continue
        info = read_run(run_dir)
        if not info["completed"]:
            continue
        if task and info["task"] != task:
            continue
        runs.append(info)
    runs.sort(key=lambda r: r["run_id"], reverse=True)
    return runs
