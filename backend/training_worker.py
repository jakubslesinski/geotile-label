"""Training worker — runs as its own process, one run per invocation.

Deliberately a separate process rather than a thread in the backend:

* cancelling means killing the process, which is the only thing that reliably stops
  ultralytics mid-epoch **and** releases the CUDA context;
* a crash cannot take the backend down with it;
* the backend keeps answering requests while training saturates the GPU;
* a process that disappears is detectable, so an abandoned run can be marked
  ``interrupted`` instead of hanging in ``running`` forever.

Usage: ``python training_worker.py <run_dir>`` where ``run_dir`` already contains
``job.json``. Progress goes to ``job_state.json``; the immutable
``training_manifest.json`` is written only on success.

``GEOTILE_TRAIN_MOCK=1`` simulates a run without ultralytics or a GPU, so the whole
lifecycle (progress, cancellation, interruption, manifest) is testable offline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HEARTBEAT_EVERY_SECONDS = 10
GPU_SAMPLE_EVERY_SECONDS = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows may briefly deny replacing a JSON file while the API is polling
        # job_state.json. Match the storage layer's bounded retry instead of turning
        # a harmless read/write overlap into an interrupted training run.
        for attempt in range(12):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


class JobState:
    def __init__(self, run_dir: Path, initial: dict[str, Any]):
        self.path = run_dir / "job_state.json"
        self.state = dict(initial)
        self._last_beat = 0.0

    def update(self, **changes: Any) -> None:
        self.state.update(changes)
        self.state["heartbeat"] = utc_now()
        write_atomic(self.path, self.state)
        _mirror_common_job_state(self.state)
        self._last_beat = time.monotonic()

    def beat(self, **changes: Any) -> None:
        """Refresh the heartbeat, but not more often than needed."""
        if changes or (time.monotonic() - self._last_beat) >= HEARTBEAT_EVERY_SECONDS:
            self.update(**changes)


def _configure_worker_threads(job: dict[str, Any]) -> dict[str, Any]:
    """Set a bounded native thread budget before NumPy/OpenCV/Ultralytics import."""

    preflight = job.get("resource_preflight") or {}
    system = preflight.get("system") or {}
    current = preflight.get("current_options") or {}
    advanced = job.get("advanced_options") or {}
    physical = max(1, int(system.get("physical_cpu_count") or (os.cpu_count() or 1) // 2 or 1))
    workers_raw = advanced.get("workers", current.get("workers", min(8, os.cpu_count() or 1)))
    try:
        workers = max(0, int(workers_raw))
    except (TypeError, ValueError):
        workers = min(8, os.cpu_count() or 1)
    threads = max(1, min(2, physical // max(1, workers + 1)))
    variables = {
        "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
        "OPENBLAS_NUM_THREADS": str(threads),
        "NUMEXPR_NUM_THREADS": str(threads),
    }
    for name, value in variables.items():
        os.environ[name] = value
    return {
        "data_loader_workers": workers,
        "physical_cpu_count": physical,
        "blas_threads_per_process": threads,
        # Ultralytics disables OpenCV's internal pool in its data module to avoid
        # multiplying native threads inside DataLoader processes. Preserve that safe
        # policy instead of forcing OpenCV's undocumented runtime configuration.
        "opencv_threads_per_process": 0,
        "environment": variables,
    }


def _batch_image_count(trainer: Any) -> int:
    batch = getattr(trainer, "batch", None)
    if isinstance(batch, dict):
        batch = batch.get("img")
    shape = getattr(batch, "shape", None)
    if shape is not None and len(shape):
        try:
            return int(shape[0])
        except (TypeError, ValueError):
            return 0
    try:
        return len(batch) if batch is not None else 0
    except TypeError:
        return 0


class TrainingTelemetry:
    """Low-overhead epoch telemetry written next to model-quality metrics."""

    def __init__(
        self,
        run_dir: Path,
        state: JobState,
        resource_config: dict[str, Any],
        *,
        clock=time.perf_counter,
    ):
        self.run_dir = Path(run_dir)
        self.state = state
        self.resource_config = resource_config
        self.clock = clock
        self.started_at = self.clock()
        self.epoch_started: float | None = None
        self.batch_started: float | None = None
        self.previous_batch_ended: float | None = None
        self.data_wait_seconds = 0.0
        self.train_batch_seconds = 0.0
        self.images = 0
        self.images_per_epoch = 0
        self.epochs: list[dict[str, Any]] = []
        self.gpu_utilization_samples: list[float] = []
        self.peak_rss_bytes = 0
        self.peak_process_tree_rss_bytes = 0
        self._last_resource_sample = 0.0

    def on_train_start(self, trainer: Any) -> None:
        self.started_at = self.clock()
        loader = getattr(trainer, "train_loader", None)
        dataset = getattr(loader, "dataset", None)
        try:
            effective_batch_size = int(getattr(loader, "batch_size", 0) or 0)
            if effective_batch_size > 0:
                self.resource_config["effective_batch_size"] = effective_batch_size
        except (TypeError, ValueError):
            pass
        try:
            effective_workers = int(getattr(loader, "num_workers", -1))
            if effective_workers >= 0:
                self.resource_config["effective_data_loader_workers"] = effective_workers
        except (TypeError, ValueError):
            pass
        try:
            self.images_per_epoch = max(0, int(len(dataset))) if dataset is not None else 0
        except TypeError:
            self.images_per_epoch = 0
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        self._sample_resources(force=True)

    def on_epoch_start(self, _trainer: Any) -> None:
        now = self.clock()
        self.epoch_started = now
        self.previous_batch_ended = now
        self.batch_started = None
        self.data_wait_seconds = 0.0
        self.train_batch_seconds = 0.0
        self.images = 0
        self.gpu_utilization_samples = []

    def on_batch_start(self, trainer: Any) -> None:
        now = self.clock()
        if self.epoch_started is None:
            self.on_epoch_start(trainer)
            now = self.clock()
        if self.previous_batch_ended is not None:
            self.data_wait_seconds += max(0.0, now - self.previous_batch_ended)
        self.batch_started = now
        self.images += _batch_image_count(trainer)

    def on_batch_end(self, _trainer: Any) -> None:
        now = self.clock()
        if self.batch_started is not None:
            self.train_batch_seconds += max(0.0, now - self.batch_started)
        self.previous_batch_ended = now
        self.batch_started = None
        self._sample_resources()

    def _sample_resources(self, *, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_resource_sample < GPU_SAMPLE_EVERY_SECONDS:
            return
        self._last_resource_sample = now
        try:
            import psutil

            process = psutil.Process()
            process_rss = int(process.memory_info().rss)
            tree_rss = process_rss
            try:
                children = process.children(recursive=True)
            except psutil.Error:
                children = []
            for child in children:
                try:
                    tree_rss += int(child.memory_info().rss)
                except psutil.Error:
                    pass
            self.peak_rss_bytes = max(self.peak_rss_bytes, process_rss)
            self.peak_process_tree_rss_bytes = max(
                self.peak_process_tree_rss_bytes,
                tree_rss,
            )
        except Exception:
            pass
        # Keep NVML outside the CUDA-owning process. Some torch/PyNVML combinations
        # terminate the process natively instead of raising, which would turn
        # optional telemetry into a failed training run. nvidia-smi is isolated,
        # bounded by a timeout and sampled infrequently to keep overhead negligible.
        try:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--id=0",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
                creationflags=creation_flags,
            )
            if result.returncode == 0:
                value = float(result.stdout.strip().splitlines()[0])
                if 0 <= value <= 100:
                    self.gpu_utilization_samples.append(value)
        except Exception:
            pass

    def _gpu_memory(self) -> dict[str, int | None]:
        result: dict[str, int | None] = {
            "vram_peak_allocated_bytes": None,
            "vram_peak_reserved_bytes": None,
        }
        try:
            import torch

            if torch.cuda.is_available():
                result["vram_peak_allocated_bytes"] = int(torch.cuda.max_memory_allocated())
                result["vram_peak_reserved_bytes"] = int(torch.cuda.max_memory_reserved())
        except Exception:
            pass
        return result

    def on_epoch_end(self, trainer: Any) -> dict[str, Any]:
        self._sample_resources(force=True)
        now = self.clock()
        epoch_started = self.epoch_started if self.epoch_started is not None else now
        epoch_seconds = max(0.0, now - epoch_started)
        wait_seconds = min(epoch_seconds, max(0.0, self.data_wait_seconds))
        batch_seconds = min(epoch_seconds, max(0.0, self.train_batch_seconds))
        images = self.images or self.images_per_epoch
        gpu_mean = (
            sum(self.gpu_utilization_samples) / len(self.gpu_utilization_samples)
            if self.gpu_utilization_samples
            else None
        )
        entry = {
            "epoch": int(getattr(trainer, "epoch", len(self.epochs))) + 1,
            "epoch_time_seconds": round(epoch_seconds, 6),
            "images": images,
            "images_per_second": round(images / epoch_seconds, 3) if epoch_seconds else None,
            # Proxy: callback time from the previous train-batch end to the next
            # train-batch start.  It includes DataLoader wait and small trainer
            # bookkeeping overhead, so the semantic is persisted with the value.
            "data_wait_seconds": round(wait_seconds, 6),
            "data_wait_fraction": round(wait_seconds / epoch_seconds, 6) if epoch_seconds else None,
            "data_wait_semantics": "inter_batch_callback_gap_proxy",
            "train_batch_seconds": round(batch_seconds, 6),
            "other_epoch_seconds": round(max(0.0, epoch_seconds - wait_seconds - batch_seconds), 6),
            "gpu_utilization_mean_percent": round(gpu_mean, 3) if gpu_mean is not None else None,
            "gpu_utilization_sample_count": len(self.gpu_utilization_samples),
            "peak_process_rss_bytes": self.peak_rss_bytes or None,
            "peak_process_tree_rss_bytes": self.peak_process_tree_rss_bytes or None,
            **self._gpu_memory(),
        }
        self.epochs.append(entry)
        self.epoch_started = None
        self._write()
        return entry

    def summary(self) -> dict[str, Any]:
        epoch_seconds = sum(float(item.get("epoch_time_seconds") or 0) for item in self.epochs)
        images = sum(int(item.get("images") or 0) for item in self.epochs)
        waits = sum(float(item.get("data_wait_seconds") or 0) for item in self.epochs)
        gpu_values = [
            float(item["gpu_utilization_mean_percent"])
            for item in self.epochs
            if item.get("gpu_utilization_mean_percent") is not None
        ]
        vram_values = [
            int(item["vram_peak_reserved_bytes"])
            for item in self.epochs
            if item.get("vram_peak_reserved_bytes") is not None
        ]
        return {
            "epochs_recorded": len(self.epochs),
            "epoch_time_seconds": round(epoch_seconds, 6),
            "images": images,
            "images_per_second": round(images / epoch_seconds, 3) if epoch_seconds else None,
            "data_wait_seconds": round(waits, 6),
            "data_wait_fraction": round(waits / epoch_seconds, 6) if epoch_seconds else None,
            "gpu_utilization_mean_percent": (
                round(sum(gpu_values) / len(gpu_values), 3) if gpu_values else None
            ),
            "vram_peak_reserved_bytes": max(vram_values) if vram_values else None,
            "peak_process_rss_bytes": self.peak_rss_bytes or None,
            "peak_process_tree_rss_bytes": self.peak_process_tree_rss_bytes or None,
        }

    def payload(self) -> dict[str, Any]:
        return {
            "schema_name": "geotile_training_performance",
            "schema_version": 1,
            "data_wait_semantics": "inter_batch_callback_gap_proxy",
            "resource_config": self.resource_config,
            "summary": self.summary(),
            "epochs": self.epochs,
        }

    def _write(self) -> None:
        write_atomic(self.run_dir / "training_performance.json", self.payload())

    def finalize(self) -> dict[str, Any]:
        self._sample_resources(force=True)
        self._write()
        return self.payload()


def mock_enabled() -> bool:
    return os.environ.get("GEOTILE_TRAIN_MOCK", "0").strip().lower() in ("1", "true", "yes")


def telemetry_enabled() -> bool:
    return os.environ.get("GEOTILE_TRAINING_TELEMETRY", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _mirror_common_job_state(state: dict[str, Any]) -> None:
    """Best-effort progress bridge into the durable P1.1 job contract."""

    project_id = os.environ.get("GEOTILE_COMMON_JOB_PROJECT_ID")
    job_id = os.environ.get("GEOTILE_COMMON_JOB_ID")
    if not project_id or not job_id:
        return
    try:
        from models.job import TERMINAL_JOB_STATUSES
        from services.jobs.store import mutate_state, utc_now as common_utc_now

        def apply(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") in TERMINAL_JOB_STATUSES or current.get("cancel_requested"):
                return current
            current["phase"] = str(state.get("stage") or "training")
            current["current"] = state.get("epochs_done", current.get("current"))
            current["total"] = state.get("epochs_total", current.get("total"))
            current["heartbeat"] = common_utc_now()
            return current

        mutate_state(project_id, job_id, apply)
    except Exception:
        # Legacy state remains authoritative for training-specific UI; failure of the
        # common progress mirror must never interrupt an epoch.
        pass


def _run_mock(job: dict[str, Any], run_dir: Path, state: JobState) -> dict[str, Any]:
    """Simulate a run: same state transitions and artefacts, no ultralytics.

    Dataset staging runs for real even in mock mode — it is ordinary file handling,
    and letting it run here is what makes the OBB label wiring testable without a GPU.
    """
    resolve_data_yaml(job, run_dir, state)
    epochs = int(job.get("epochs") or 3)
    delay = float(os.environ.get("GEOTILE_TRAIN_MOCK_EPOCH_SECONDS", "0.05"))
    rows = ["epoch,metrics/mAP50(B),metrics/mAP50-95(B),train/box_loss"]
    for epoch in range(1, epochs + 1):
        time.sleep(delay)
        m50 = round(0.40 + 0.30 * epoch / epochs, 4)
        m5095 = round(0.22 + 0.20 * epoch / epochs, 4)
        rows.append(f"{epoch},{m50},{m5095},{round(1.5 - 0.8 * epoch / epochs, 4)}")
        state.update(epochs_done=epoch)
    (run_dir / "results.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    weights = run_dir / "weights"
    weights.mkdir(parents=True, exist_ok=True)
    (weights / "best.pt").write_bytes(b"mock-checkpoint")
    return {
        "summary": {"mAP50": 0.70, "mAP50-95": 0.42, "precision": 0.75, "recall": 0.68},
        "per_class": {
            name: {
                "precision": round(0.72 + index * 0.01, 4),
                "recall": round(0.65 + index * 0.01, 4),
                "mAP50": round(0.68 + index * 0.01, 4),
                "mAP50-95": round(0.40 + index * 0.01, 4),
            }
            for index, name in enumerate(job.get("class_names") or [])
        },
        "source": "mock",
    }


def _ensure_absolute_data_yaml(data_yaml: str, dataset_dir: str, run_dir: Path) -> str:
    """Zwróć ścieżkę do data.yaml z ABSOLUTNYM ``path``.

    Jeśli plik ma już absolutny ``path`` — zwróć go bez zmian. Inaczej zapisz poprawioną
    kopię do ``run_dir`` (katalog przebiegu treningu, mutowalny) i zwróć jej ścieżkę.
    """
    import yaml

    source = Path(data_yaml)
    try:
        cfg = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return str(source)  # nie ryzykuj — oddaj oryginał
    path_val = str(cfg.get("path") or "").strip()
    if path_val and Path(path_val).is_absolute():
        return str(source)
    cfg["path"] = Path(dataset_dir).resolve().as_posix()
    target = run_dir / source.name
    target.write_text(
        yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return str(target)


def resolve_data_yaml(job: dict[str, Any], run_dir: Path, state: JobState) -> str:
    """Data path handed to ultralytics — staged first for oriented models.

    ultralytics reads labels from ``<images>/../labels``, so an OBB run must not be
    pointed at the dataset directly: it would silently train on the axis-aligned
    labels sitting in ``labels/``.
    """
    if job.get("task") != "obb":
        # Defense-in-depth: ultralytics rozwiązuje względny ``path`` z data.yaml przez swój
        # datasets_dir/CWD, nie względem pliku — więc portable ``path: .`` (np. po eksporcie)
        # zerwałby trening. Wymuszamy ABSOLUTNY path, zapisując poprawioną kopię do katalogu
        # PRZEBIEGU treningu (run datasetu pozostaje niezmienny).
        return _ensure_absolute_data_yaml(job["data_yaml"], job["dataset_dir"], run_dir)

    from services.training_dataset import resolve_obb_training_dataset

    state.update(stage="staging-obb-dataset")
    resolved = resolve_obb_training_dataset(
        project_id=str(job["project_id"]),
        dataset_run_id=str(job["dataset_run_id"]),
        dataset_dir=Path(job["dataset_dir"]),
        dataset_input_hash=job.get("dataset_input_hash"),
        preprocessing_hash=job.get("preprocessing_profile_hash"),
        fallback_target=run_dir / "dataset_obb",
    )
    cache_record = {
        key: resolved.get(key)
        for key in (
            "cache_enabled",
            "cache_hit",
            "cache_key",
            "cache_manifest",
            "logical_size_bytes",
            "allocated_size_estimate_bytes",
        )
    }
    state.update(
        stage="training",
        staged_data_yaml=str(resolved["data_yaml"]),
        training_dataset_cache=cache_record,
    )
    return str(resolved["data_yaml"])


def _silence_ultralytics_telemetry() -> None:
    """Stop ultralytics from posting anonymous usage events.

    ``SETTINGS["sync"]`` defaults to **true**, which makes training and validation fire
    analytics requests at a Google endpoint from a background thread. The application
    is offline by design and runs on isolated machines, so that outbound call must not
    happen — and it must not depend on how the operator's local settings file happens
    to be configured.

    The setting is persisted by ultralytics, so this is written once and then cheap.
    """
    try:
        from ultralytics import SETTINGS

        if SETTINGS.get("sync"):
            SETTINGS["sync"] = False
    except Exception:
        # Telemetry is not worth failing a training run over; a newer ultralytics may
        # rename the key. The run itself is unaffected either way.
        pass


def _metric_values(value: Any) -> list[float]:
    if value is None:
        return []
    try:
        raw = value.tolist()
    except AttributeError:
        raw = value
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    result: list[float] = []
    for item in raw:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            result.append(float("nan"))
    return result


def _persist_confusion_matrix(model: Any, run_dir: Path, class_names: list[str]) -> None:
    """Zapisz surowa macierz pomylek jako dane (nie tylko PNG) do analizy separowalnosci.

    Ultralytics rysuje confusion_matrix.png, ale nie zapisuje liczb. Bierzemy je z
    walidatora trenera. Best-effort: brak macierzy nie moze przewrocic zapisu przebiegu.
    Orientacja jak w ultralytics `ConfusionMatrix.matrix`: [predykcja][prawda], ostatni
    indeks = tlo/background.
    """
    try:
        cm = getattr(getattr(getattr(model, "trainer", None), "validator", None), "confusion_matrix", None)
        matrix = getattr(cm, "matrix", None)
        if matrix is None:
            return
        rows = matrix.tolist() if hasattr(matrix, "tolist") else matrix
        payload = {
            "schema_name": "geotile_confusion_matrix",
            "schema_version": 1,
            "orientation": "pred_by_true_last_is_background",
            "class_names": class_names,
            "matrix": [[float(v) for v in row] for row in rows],
        }
        write_atomic(run_dir / "confusion_matrix.json", payload)
    except Exception:
        pass


def _per_class_metrics(results: Any, class_names: list[str]) -> dict[str, dict[str, float | None]]:
    """Extract Ultralytics detection/OBB class metrics without relying on numpy."""
    box = getattr(results, "box", None)
    if box is None:
        return {}
    class_indices = [int(value) for value in _metric_values(getattr(box, "ap_class_index", []))]
    precision = _metric_values(getattr(box, "p", []))
    recall = _metric_values(getattr(box, "r", []))
    map50 = _metric_values(getattr(box, "ap50", []))
    map5095 = _metric_values(getattr(box, "ap", []))
    per_class: dict[str, dict[str, float | None]] = {}

    def at(values: list[float], index: int) -> float | None:
        if index >= len(values) or values[index] != values[index]:
            return None
        return values[index]

    for position, class_index in enumerate(class_indices):
        name = class_names[class_index] if class_index < len(class_names) else str(class_index)
        per_class[name] = {
            "precision": at(precision, position),
            "recall": at(recall, position),
            "mAP50": at(map50, position),
            "mAP50-95": at(map5095, position),
        }
    return per_class


def _run_ultralytics(
    job: dict[str, Any],
    run_dir: Path,
    state: JobState,
    resource_config: dict[str, Any],
) -> dict[str, Any]:
    from ultralytics import YOLO

    _silence_ultralytics_telemetry()
    data_yaml = resolve_data_yaml(job, run_dir, state)
    model = YOLO(job["base_model_path"])
    telemetry = TrainingTelemetry(run_dir, state, resource_config)
    collect_telemetry = telemetry_enabled()

    def on_epoch_end(trainer) -> None:
        # ultralytics counts epochs from zero.
        if collect_telemetry:
            epoch_metrics = telemetry.on_epoch_end(trainer)
            state.update(
                epochs_done=int(getattr(trainer, "epoch", 0)) + 1,
                performance={
                    "latest_epoch": epoch_metrics,
                    "summary": telemetry.summary(),
                },
            )
        else:
            state.update(epochs_done=int(getattr(trainer, "epoch", 0)) + 1)

    def on_batch_end(_trainer) -> None:
        # Odśwież heartbeat w obrębie epoki (dławione do HEARTBEAT_EVERY_SECONDS), żeby
        # długa epoka nie została uznana za porzuconą przez `resolve_job_state`.
        state.beat()

    if collect_telemetry:
        model.add_callback("on_train_start", telemetry.on_train_start)
        model.add_callback("on_train_epoch_start", telemetry.on_epoch_start)
        model.add_callback("on_train_batch_start", telemetry.on_batch_start)
        model.add_callback("on_train_batch_end", telemetry.on_batch_end)
    model.add_callback("on_train_epoch_end", on_epoch_end)
    model.add_callback("on_train_batch_end", on_batch_end)
    model.add_callback("on_val_batch_end", on_batch_end)

    train_options = {
        "data": data_yaml,
        "epochs": int(job["epochs"]),
        "imgsz": int(job["imgsz"]),
        "batch": job["batch"],
        "seed": int(job["seed"]),
        "patience": int(job["patience"]),
        "device": job["device"],
        "project": str(run_dir),
        "name": "ultralytics",
        "exist_ok": True,
        "verbose": False,
    }
    train_options.update(job.get("advanced_options") or {})
    results = model.train(**train_options)
    performance = (
        telemetry.finalize()
        if collect_telemetry
        else {
            "schema_name": "geotile_training_performance",
            "schema_version": 1,
            "enabled": False,
            "resource_config": resource_config,
            "summary": {},
            "epochs": [],
        }
    )

    output_dir = Path(getattr(results, "save_dir", run_dir / "ultralytics"))
    for name in ("results.csv", "confusion_matrix.png"):
        source = output_dir / name
        if source.is_file():
            (run_dir / name).write_bytes(source.read_bytes())
    _persist_confusion_matrix(model, run_dir, list(job.get("class_names") or []))
    best = output_dir / "weights" / "best.pt"
    if best.is_file():
        target = run_dir / "weights"
        target.mkdir(parents=True, exist_ok=True)
        (target / "best.pt").write_bytes(best.read_bytes())

    metrics = getattr(results, "results_dict", {}) or {}
    per_class = _per_class_metrics(results, list(job.get("class_names") or []))
    if not per_class:
        trainer = getattr(model, "trainer", None)
        validator = getattr(trainer, "validator", None)
        per_class = _per_class_metrics(
            getattr(validator, "metrics", None),
            list(job.get("class_names") or []),
        )
    return {
        "summary": {
            "mAP50": metrics.get("metrics/mAP50(B)"),
            "mAP50-95": metrics.get("metrics/mAP50-95(B)"),
            "precision": metrics.get("metrics/precision(B)"),
            "recall": metrics.get("metrics/recall(B)"),
        },
        "per_class": per_class,
        "performance": performance,
        "source": "ultralytics",
    }


def _evaluate_on_test(job: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """One-off evaluation of the finished checkpoint on the held-out test split."""
    if mock_enabled():
        return {"mAP50": 0.66, "mAP50-95": 0.39, "precision": 0.71, "recall": 0.64}

    from ultralytics import YOLO

    _silence_ultralytics_telemetry()
    state = JobState(run_dir, {"status": "running", "pid": os.getpid(), "stage": "test-eval"})
    data_yaml = resolve_data_yaml(job, run_dir, state)
    model = YOLO(str(run_dir / "weights" / "best.pt"))
    results = model.val(data=data_yaml, split="test", device=job.get("device", "cpu"), verbose=False)
    metrics = getattr(results, "results_dict", {}) or {}
    return {
        "mAP50": metrics.get("metrics/mAP50(B)"),
        "mAP50-95": metrics.get("metrics/mAP50-95(B)"),
        "precision": metrics.get("metrics/precision(B)"),
        "recall": metrics.get("metrics/recall(B)"),
    }


def run_test_evaluation(run_dir: Path) -> int:
    """Write the final test-set evaluation, exactly once.

    The test split answers "which model do I ship". Re-running it after seeing the
    number turns it into a second validation set and inflates the estimate, so the
    record is written once and never overwritten.
    """
    target = run_dir / "test_evaluation.json"
    if target.exists():
        print("test evaluation already exists; refusing to overwrite", file=sys.stderr)
        return 3

    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    if not (run_dir / "training_manifest.json").is_file():
        print("run has no manifest; it did not finish", file=sys.stderr)
        return 4

    try:
        metrics = _evaluate_on_test(job, run_dir)
    except Exception as exc:  # noqa: BLE001
        write_atomic(run_dir / "test_evaluation_error.json", {
            "error": f"{type(exc).__name__}: {exc}",
            "attempted_at": utc_now(),
        })
        return 1

    write_atomic(target, {
        "schema_name": "geotile_test_evaluation",
        "schema_version": 1,
        "training_run_id": job.get("training_run_id"),
        "dataset_run_id": job.get("dataset_run_id"),
        "split": "test",
        "final": True,
        "evaluated_at": utc_now(),
        "metrics": metrics,
    })
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: training_worker.py <run_dir> [eval]", file=sys.stderr)
        return 2

    run_dir = Path(argv[1])
    if len(argv) > 2 and argv[2] == "eval":
        return run_test_evaluation(run_dir)

    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    resource_config = _configure_worker_threads(job)

    state = JobState(run_dir, {
        **{key: job.get(key) for key in (
            "config_fingerprint", "attempt", "dataset_run_id", "base_model",
            "task", "device", "epochs_total",
        )},
        "status": "running",
        "pid": os.getpid(),
        "started_at": utc_now(),
        "epochs_done": 0,
        "resource_config": resource_config,
    })
    state.update()

    try:
        metrics = (
            _run_mock(job, run_dir, state)
            if mock_enabled()
            else _run_ultralytics(job, run_dir, state, resource_config)
        )
    except Exception as exc:  # noqa: BLE001 - the reason must reach the user verbatim
        state.update(status="failed", ended_at=utc_now(), error=f"{type(exc).__name__}: {exc}")
        _release_training_lock(run_dir, job)
        return 1

    write_atomic(run_dir / "metrics.json", metrics)

    # The manifest is the record of a *finished* run, so it is written last and only
    # once everything else is on disk. Its presence is what makes a run trustworthy.
    manifest = {
        "schema_name": "geotile_training_run",
        "schema_version": 1,
        **job,
        "training_dataset_cache": state.state.get("training_dataset_cache"),
        "training_performance": metrics.get("performance"),
        "resource_config": resource_config,
        "metrics": metrics.get("summary"),
        "completed_at": utc_now(),
    }
    manifest.pop("base_model_path", None)
    write_atomic(run_dir / "training_manifest.json", manifest)

    state.update(status="completed", ended_at=utc_now(), epochs_done=job.get("epochs"))
    _release_training_lock(run_dir, job)
    return 0


def _release_training_lock(run_dir: Path, job: dict[str, Any]) -> None:
    lock_path = run_dir.parent / ".active_training.json"
    try:
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if owner.get("training_run_id") != job.get("training_run_id"):
        return
    lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
