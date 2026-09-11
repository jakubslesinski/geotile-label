"""v09 — Podobieństwo klas: wykrywanie klas trudnych do odróżnienia  (Claim S2, support).

SUBSTRATE / TIER
    FAIR1M — 37 celowo mylących podklas (typy samolotów/statków) · public. Idealny do metody
    similarity + jej WALIDACJI wobec confusion z realnego treningu (v06/v13, FAIR1M OBB).

CLAIM (WSPIERAJĄCY)
    Analiza podobieństwa embeddingów wykrywa pary klas trudne do odróżnienia i wskazuje
    potrzebę scalenia/dozbierania. Metoda jest ZWALIDOWANA: pary o wysokim podobieństwie
    pokrywają się z parami mylonymi w confusion matrix wytrenowanego modelu.

METHOD
    Tą samą metodą co „Znajdź podobne" w aplikacji: embedding DINO chipów obiektów
    (`get_dino_embedder` + `embed_chips`), prototyp per klasa i macierz podobieństwa
    międzyklasowego (`class_similarity`). Dla tractowności próbkujemy sceny/obiekty. Confusion
    bierzemy z ukończonego przebiegu FAIR1M OBB (macierz 37 klas). Liczymy KORELACJĘ Spearmana
    między podobieństwem par a symetryczną liczbą pomyłek tych par — walidacja metryki, nie
    tylko jej uruchomienie.

INPUTS
    - projekt FAIR1M (sceny + adnotacje) w danych aplikacji
    - embedder DINO (MODELS_ROOT/dino, offline) + confusion z przebiegu FAIR1M OBB

OUTPUTS
    - results/class_similarity.csv  (class_a, class_b, similarity, confusion_count)
    - metrics: {n_classes, n_pairs, top_pairs[], similarity_confusion_spearman}

PASS CRITERION
    Macierz i ranking par wygenerowane; wysoko-podobne pary zidentyfikowane; korelacja
    podobieństwa z pomyłkami raportowana (wynik opisowy — rekomendacja scal/dozbierz).
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv, appruns

PROJECT = os.environ.get("V09_PROJECT", "FAIR1M")
N_SCENES = int(os.environ.get("V09_SCENES", "200"))
MAX_PER_CLASS = int(os.environ.get("V09_MAX_PER_CLASS", "300"))
CHIP = int(os.environ.get("V09_CHIP", "64"))
MIN_SIZE_PX = 6
SAMPLE_SEED = 20260825


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Korelacja Spearmana (rank + Pearson na rangach), bez scipy."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _collect_sample(project_id: str, list_scene_ids, load_scene_json, resolve_scene_raster,
                    read_window, rng):
    """Chipy obiektów z PRÓBKI scen (ten sam odczyt okienkowy co aplikacja), cap per klasa."""
    scene_ids = list(list_scene_ids(project_id))
    rng.shuffle(scene_ids)
    per_class: dict[int, int] = defaultdict(int)
    objects: list[dict] = []
    chips: list = []
    used_scenes = 0
    for scene_id in scene_ids:
        if used_scenes >= N_SCENES:
            break
        anns = load_scene_json(project_id, scene_id, "annotations", default=[])
        if not anns:
            continue
        scene_info = (load_scene_json(project_id, scene_id, "scene", default={}).get("scene_info") or {})
        width, height = scene_info.get("width", 0), scene_info.get("height", 0)
        if not width:
            continue
        try:
            raster = resolve_scene_raster(project_id, scene_id)
        except Exception:
            continue
        used_scenes += 1
        for ann in anns:
            if ann.get("is_negative") or not ann.get("bbox"):
                continue
            cid = ann.get("class_id")
            if per_class[cid] >= MAX_PER_CLASS:
                continue
            x0, y0, x1, y1 = (int(round(v)) for v in ann["bbox"])
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(width, x1), min(height, y1)
            if x1 - x0 < MIN_SIZE_PX or y1 - y0 < MIN_SIZE_PX:
                continue
            try:
                arr = read_window(raster, scene_info, x0, y0, x1, y1, CHIP, CHIP, CHIP)
            except Exception:
                arr = None
            if arr is None:
                continue
            objects.append({"scene_id": scene_id, "annotation_id": ann.get("id"), "class_id": cid})
            chips.append(arr)
            per_class[cid] += 1
    return objects, chips, used_scenes


def main() -> ValidationResult:
    res = ValidationResult(
        id="v09", claim="S2",
        title="Podobieństwo klas (embeddingi) — zwalidowane confusion z v06",
        substrate=["FAIR1M"], tier="public",
    )
    res.config = {"project": PROJECT, "n_scenes": N_SCENES, "max_per_class": MAX_PER_CLASS, "chip": CHIP}

    # DATA_DIR (rejestr projektów) i MODELS_ROOT (wagi DINO) muszą wskazywać dane aplikacji
    # PRZED importem backendu — predictor.py/embedding_backbone.py czytają MODELS_ROOT przy imporcie.
    os.environ["DATA_DIR"] = str(appruns.app_data_dir())
    os.environ["MODELS_ROOT"] = str(appruns.app_data_dir() / "models")
    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"; res.notes = blocked; return res

    import numpy as np
    from db.storage import list_scene_ids, load_scene_json, load_json
    from services.scene_raster_resolver import resolve_scene_raster
    from routers.scenes import _read_geotiff_window
    from services.embedding_backbone import get_dino_embedder
    from services.embedding_analysis import class_similarity
    from services.dataset_intelligence import confused_pairs  # noqa: F401 (reużyty w class_similarity)

    project_id = appruns.project_id_by_name(PROJECT)
    if not project_id:
        res.status = "todo"; res.notes = f"Projekt {PROJECT} nie znaleziony w {appruns.app_data_dir()}"; return res

    # Confusion z ukończonego przebiegu OBB (macierz 37 klas)
    obb_runs = appruns.list_completed_runs(PROJECT, task="obb")
    conf_run = next((r for r in obb_runs if r.get("has_confusion")), None)
    if conf_run is None:
        res.status = "todo"
        res.notes = f"Brak ukończonego przebiegu OBB z confusion w {PROJECT}. Uruchom trening OBB w aplikacji."
        return res

    # KOLEJNOŚĆ WAŻNA (Windows): najpierw otwórz rastry przez rasterio/GDAL, DOPIERO POTEM
    # zbuduj model torch (DINO). Zbudowanie torcha przed otwarciem GDAL powoduje twardy crash
    # (konflikt DLL). Dlatego kolekcja chipów jest PRZED get_dino_embedder.
    rng = random.Random(SAMPLE_SEED)
    objects, chips, used_scenes = _collect_sample(
        project_id, list_scene_ids, load_scene_json, resolve_scene_raster, _read_geotiff_window, rng
    )
    if len(objects) < 10:
        res.status = "fail"; res.notes = f"Za mało chipów obiektów ({len(objects)})."; return res

    # Domyślnie CPU: walidacja nie powinna konkurować o GPU z treningami uruchamianymi w
    # aplikacji (kolizja CUDA potrafi twardo ubić proces). V09_DEVICE=cuda by wymusić GPU.
    device = os.environ.get("V09_DEVICE", "cpu")
    try:
        embedder = get_dino_embedder(device=device)
    except Exception as exc:  # noqa: BLE001
        res.status = "todo"; res.notes = f"Brak backbone DINO: {exc}"; return res

    embeddings = embedder.embed_chips(chips)
    class_names_map = {c["id"]: c["name"] for c in load_json(project_id, "classes", default=[])}
    index = {"embeddings": np.asarray(embeddings, dtype=np.float32), "objects": objects,
             "class_names": class_names_map}
    sim = class_similarity(index, top_k_pairs=40)
    sim_names = sim["class_names"]
    sim_matrix = sim["similarity"]

    # Symetryczna liczba pomyłek per para z confusion (matrix[pred][true], last=background)
    conf = conf_run["confusion"]
    conf_names = conf.get("class_names", [])
    matrix = conf.get("matrix", [])
    conf_idx = {name: i for i, name in enumerate(conf_names)}

    def conf_count(a: str, b: str) -> float | None:
        ia, ib = conf_idx.get(a), conf_idx.get(b)
        if ia is None or ib is None:
            return None
        return float(matrix[ia][ib]) + float(matrix[ib][ia])

    # Pary obecne w OBU (similarity + confusion), po nazwie klasy
    sims: list[float] = []
    confs: list[float] = []
    rows = []
    n = len(sim_names)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = sim_names[i], sim_names[j]
            cc = conf_count(a, b)
            if cc is None:
                continue
            s = float(sim_matrix[i][j])
            sims.append(s); confs.append(cc)
            rows.append((a, b, round(s, 4), int(cc)))

    spearman = _spearman(sims, confs)
    rows.sort(key=lambda r: r[2], reverse=True)

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "class_similarity.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["class_a", "class_b", "similarity", "confusion_count"])
        w.writerows(rows)

    top_pairs = [
        {"class_a": a, "class_b": b, "similarity": s, "confusion_count": c}
        for (a, b, s, c) in rows[:10]
    ]
    res.metrics = {
        "project": PROJECT,
        "confusion_run": conf_run["run_id"],
        "device": device,
        "backbone": getattr(embedder, "checkpoint_name", "dino"),
        "n_scenes_sampled": used_scenes,
        "n_objects": len(objects),
        "n_classes_with_objects": len(sim_names),
        "n_pairs": len(sims),
        "similarity_confusion_spearman": round(spearman, 4) if spearman is not None else None,
        "top_pairs": top_pairs,
    }
    res.artifacts = ["results/class_similarity.csv"]
    res.status = "pass" if (len(sim_names) >= 2 and rows and spearman is not None) else "fail"
    res.notes = (
        f"{PROJECT}: DINO similarity ({len(sim_names)} klas / {len(objects)} obiektów z "
        f"{used_scenes} scen, {device}) vs confusion z {conf_run['run_id']}. "
        f"Korelacja Spearmana podobieństwo↔pomyłki = {res.metrics['similarity_confusion_spearman']} "
        f"na {len(sims)} parach. Najpodobniejsza para: "
        f"{top_pairs[0]['class_a']}↔{top_pairs[0]['class_b']} "
        f"(sim={top_pairs[0]['similarity']}, pomyłek={top_pairs[0]['confusion_count']})."
        if top_pairs else "Brak par do korelacji."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
