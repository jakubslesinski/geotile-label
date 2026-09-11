"""Worker analizy embeddingów (Dataset Intelligence, DI2) — proces oddzielny.

Wzorzec `training_worker.py`: ciężka praca (wczytanie backbone'u DINO, wycięcie chipów,
embeddingi, podobieństwo, podejrzane etykiety) idzie do osobnego procesu, żeby nie blokować
UI. Postęp → ``state.json``, wyniki → ``result.json`` oraz wersjonowany, wznawialny indeks
(``embedding_index.json``, memmap ``embeddings.f32``, partycje Parquet i USearch).

Usage: ``python embedding_analysis_worker.py <run_dir>`` (``run_dir`` zawiera ``job.json``).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Puls zapisywany niezależnie od postępu. Musi być częstszy niż najdłuższy etap bez zmiany
# stanu — przy dużym projekcie samo liczenie embeddingów trwa minuty i nie woła `set_state`,
# więc puls oparty wyłącznie na przejściach etapów dawałby fałszywe „martwy".
HEARTBEAT_INTERVAL_S = 10.0

sys.path.insert(0, str(Path(__file__).resolve().parent))  # backend na ścieżce importów


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
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def main(run_dir: Path | None = None) -> int:
    if run_dir is None:
        if len(sys.argv) < 2:
            print("usage: embedding_analysis_worker.py <run_dir>", file=sys.stderr)
            return 2
        run_dir = Path(sys.argv[1])
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    state_path = run_dir / "state.json"
    state: dict[str, Any] = {
        "project_id": job.get("project_id"),
        "backbone": job.get("backbone"),
        "created_at": job.get("created_at"),
        "status": "running",
        "stage": "loading_backbone",
    }

    # Puls bije z wątku w tle, więc stan jest aktualizowany też w środku długiego etapu.
    # Lock: wątek pulsu i wątek główny piszą ten sam plik. `write_atomic` chroni przed
    # plikiem uciętym w pół, ale nie przed przeplotem zawartości — stąd serializacja.
    state_lock = threading.Lock()

    def set_state(**changes: Any) -> None:
        with state_lock:
            state.update(changes)
            state["heartbeat"] = utc_now()
            write_atomic(state_path, state)

    state["pid"] = os.getpid()
    set_state()

    heartbeat_stop = threading.Event()

    def _heartbeat_loop() -> None:
        while not heartbeat_stop.wait(HEARTBEAT_INTERVAL_S):
            try:
                set_state()  # bez zmian pól — odświeża wyłącznie `heartbeat`
            except Exception:  # noqa: BLE001 — puls nigdy nie może wywrócić analizy
                pass

    heartbeat = threading.Thread(target=_heartbeat_loop, name="analysis-heartbeat", daemon=True)
    heartbeat.start()
    try:
        from services.embedding_backbone import get_dino_embedder
        from services.predictor import resolve_device

        # Backbone MUSI dostać wykryte urządzenie. Bez tego `get_dino_embedder` bierze
        # swój domyślny `device="cpu"` i cała analiza liczy się na procesorze mimo obecnego
        # GPU — zmierzone na dinov2_vits14: 29,8 ms/chip (CPU, 1 wątek) vs 1,0 ms/chip (CUDA),
        # czyli ~29×. Ścieżka interaktywna (`routers/assist.py`) robiła to poprawnie od
        # początku; brakowało tego wyłącznie tutaj, w trybie batch.
        device = resolve_device()
        embedder = get_dino_embedder(preferred=job.get("backbone") or None, device=device)
        set_state(
            stage="extracting_and_embedding",
            backbone=embedder.checkpoint_name,
            device=device,
        )

        from services.embedding_analysis import (
            build_object_index,
            build_split_of,
            class_cohesion,
            class_example_exemplars,
            class_outliers,
            class_similarity,
            near_duplicate_pairs,
            render_class_example_thumbnails,
            suspected_mislabels,
        )

        index = build_object_index(
            job["project_id"],
            embedder,
            chip=int(job.get("chip", 64)),
            min_size_px=int(job.get("min_size_px", 6)),
            run_dir=run_dir,
            batch_size=int(job.get("embedding_batch_size", 256)),
            progress=lambda **values: set_state(
                stage="extracting_and_embedding", **values
            ),
        )
        n_objects = len(index["objects"])
        set_state(stage="building_ann", n_objects=n_objects)

        from services.embedding_index import build_ann_index

        exact_limit = int(job.get("exact_limit", 10_000))
        ann_info = build_ann_index(
            run_dir,
            index["embeddings"],
            exact_limit=exact_limit,
            batch_size=int(job.get("ann_batch_size", 8192)),
            progress=lambda **values: set_state(stage="building_ann", **values),
        )
        if ann_info.get("backend") == "usearch":
            index["ann_path"] = run_dir / str(ann_info["path"])
        set_state(stage="analyzing", n_objects=n_objects, search_backend=ann_info["backend"])

        margin = float(job.get("mislabel_margin", 0.02))
        set_state(stage="detecting_duplicates", n_objects=n_objects)

        # Przeciek train/val: split z opublikowanego runu, mapowany na obiekty źródłowe.
        # Bez cichej degradacji — jeśli run wskazany, ale split się nie zbuduje, zapisujemy
        # powód do wyniku (a analiza reszty i tak się kończy).
        dataset_run_id = job.get("dataset_run_id") or None
        split_of: dict[str, str] | None = None
        split_error: str | None = None
        n_split_tagged = 0
        if dataset_run_id:
            try:
                split_of = build_split_of(job["project_id"], dataset_run_id, index["objects"])
                n_split_tagged = len(split_of)
            except Exception as exc:  # noqa: BLE001 — powód trafia do wyniku, nie znika
                split_error = f"Nie udało się zmapować splitu runu {dataset_run_id}: {exc}"
                split_of = None

        near_dupes = near_duplicate_pairs(
            index,
            threshold=float(job.get("dup_threshold", 0.97)),
            split_of=split_of,
            exact_limit=exact_limit,
            candidate_k=int(job.get("ann_candidate_k", 64)),
        )
        n_leaks = sum(1 for pair in near_dupes if pair.get("cross_split"))

        # Galeria przykładów: prototypowe + graniczne obiekty per klasa, miniatury na dysk.
        set_state(stage="rendering_examples", n_objects=n_objects)
        examples = class_example_exemplars(index)
        examples = render_class_example_thumbnails(
            job["project_id"], examples, run_dir / "chips"
        )
        class_examples = {
            str(cid): kept
            for cid, exs in examples.items()
            if (kept := [ex for ex in exs if ex.get("thumb")])
        }

        names = index["class_names"]
        counts = Counter(obj["class_id"] for obj in index["objects"])
        result = {
            "schema_name": "geotile_embedding_analysis",
            "schema_version": 2,
            "project_id": job["project_id"],
            "backbone": index.get("backbone"),
            "n_objects": n_objects,
            "per_class": [
                {"class_id": cid, "name": names.get(cid, str(cid)), "count": count}
                for cid, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "class_similarity": class_similarity(index),
            "class_examples": class_examples,
            "class_cohesion": class_cohesion(index),
            "suspected_mislabels": suspected_mislabels(
                index, margin=margin, top_k=int(job.get("mislabel_top_k", 2000))
            ),
            "class_outliers": class_outliers(index, margin=margin),
            "near_duplicates": near_dupes,
            "n_near_duplicates": len(near_dupes),
            "embedding_index": index.get("artifact_manifest"),
            "neighbor_search": {
                **ann_info,
                "candidate_k": int(job.get("ann_candidate_k", 64)),
            },
            "duplicate_search": {
                "backend": "exact" if n_objects <= exact_limit else "cosine_lsh",
                "threshold": float(job.get("dup_threshold", 0.97)),
                "lsh_bands": 16 if n_objects > exact_limit else None,
                "lsh_bits_per_band": 16 if n_objects > exact_limit else None,
            },
            "split_dataset_run_id": dataset_run_id,
            "n_split_tagged": n_split_tagged,
            "n_leaks": n_leaks,
            "split_error": split_error,
            "generated_at": utc_now(),
        }
        write_atomic(run_dir / "result.json", result)
        set_state(status="completed", stage="done", n_objects=n_objects, ended_at=utc_now())
    except Exception as exc:  # noqa: BLE001 — worker zawsze kończy czytelnym stanem
        set_state(status="failed", error=str(exc), ended_at=utc_now())
        raise
    finally:
        # Zatrzymaj puls, żeby stan końcowy (`completed`/`failed`) nie dostał już świeżego
        # `heartbeat` — inaczej martwy worker wyglądałby przez chwilę na żywy.
        heartbeat_stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
