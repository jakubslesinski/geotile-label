"""Warstwa analizy embeddingów (Dataset Intelligence, DI2 — tryb A).

Na backbonie `embedding_backbone` (DINO/YOLO) buduje **indeks obiektów** (embedding +
pochodzenie: scena, adnotacja, klasa) i liczy na nim, **bez trenowania**:

- **prototypy i macierz podobieństwa klas** (reużywa widget/porządkowanie z warstwy confusion),
- **„20 najbardziej podobnych obiektów"** (kNN po cosine, czysty numpy — sklearn nie jest
  w środowisku bazowym),
- **podejrzane etykiety** (obiekt bliżej prototypu innej klasy niż własnej) → kolejka
  przeglądu z deep-linkiem do edytora (scena + adnotacja).

Każdy obiekt niesie `scene_id`/`annotation_id`, więc każda lista wraca do edytora — to jest
domknięcie pętli „analiza → popraw adnotację".
"""

from __future__ import annotations

from collections import Counter, defaultdict
import heapq
from pathlib import Path
from typing import Any

import numpy as np

from db.storage import load_json, load_scene_json
from services.dataset_intelligence import confused_pairs, confusion_leaf_order


def _collect_object_chips(
    project_id: str, chip: int = 64, min_size_px: int = 6
) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    """Compatibility helper for small diagnostics that explicitly need all chips.

    The production worker calls :func:`build_object_index` with ``run_dir`` and never
    materializes the complete chip collection in memory.
    """
    from services.embedding_index import iter_object_chip_batches

    objects: list[dict[str, Any]] = []
    chips: list[np.ndarray] = []
    for batch in iter_object_chip_batches(
        project_id, chip=chip, min_size_px=min_size_px
    ):
        objects.extend(batch.objects)
        chips.extend(batch.chips)
    return objects, chips


def build_object_index(
    project_id: str,
    embedder: Any,
    *,
    chip: int = 64,
    min_size_px: int = 6,
    run_dir: Path | None = None,
    batch_size: int = 256,
    progress: Any = None,
) -> dict[str, Any]:
    """Build an L2-normalized object index.

    ``run_dir`` selects the P1.4 production path: bounded chip batches, one raster
    handle per scene, memmapped embeddings and resumable Parquet metadata. The branch
    without a directory remains an exact compatibility mode for small fixtures.
    """
    if run_dir is not None:
        from services.embedding_index import build_streaming_object_index

        return build_streaming_object_index(
            project_id,
            embedder,
            run_dir,
            chip=chip,
            min_size_px=min_size_px,
            batch_size=batch_size,
            progress=progress,
        )

    from services.embedding_index import iter_object_chip_batches

    objects: list[dict[str, Any]] = []
    vector_batches: list[np.ndarray] = []
    for batch in iter_object_chip_batches(
        project_id,
        chip=chip,
        min_size_px=min_size_px,
        batch_size=batch_size,
    ):
        if not batch.chips:
            continue
        objects.extend(batch.objects)
        vector_batches.append(np.asarray(embedder.embed_chips(batch.chips), dtype=np.float32))
    class_names = {c["id"]: c["name"] for c in load_json(project_id, "classes", default=[])}
    if not objects:
        return {"embeddings": np.zeros((0, 0), dtype=np.float32), "objects": [], "class_names": class_names}
    embeddings = np.concatenate(vector_batches, axis=0)
    return {
        "embeddings": np.asarray(embeddings, dtype=np.float32),
        "objects": objects,
        "class_names": class_names,
        "backbone": getattr(embedder, "checkpoint_name", "unknown"),
    }


def class_prototypes(index: dict[str, Any]) -> tuple[list[int], np.ndarray]:
    """Prototyp klasy = uśredniony, znormalizowany embedding jej obiektów. Zwraca (ids, P)."""
    buckets: dict[int, list[int]] = defaultdict(list)
    for i, obj in enumerate(index["objects"]):
        buckets[obj["class_id"]].append(i)
    embeddings = index["embeddings"]
    ids = sorted(buckets)
    protos = []
    for cid in ids:
        vec = embeddings[buckets[cid]].mean(axis=0)
        protos.append(vec / (np.linalg.norm(vec) + 1e-9))
    return ids, (np.stack(protos) if protos else np.zeros((0, embeddings.shape[1] if embeddings.size else 0)))


def class_similarity(index: dict[str, Any], *, top_k_pairs: int = 30) -> dict[str, Any]:
    """Macierz podobieństwa klas (cosine prototypów) + leaf-order + najpodobniejsze pary.

    Ta sama forma co macierz confusion (reużywalny widget); tu sygnał jest z embeddingów,
    nie z wytrenowanego modelu — dostępny **przed** treningiem.
    """
    ids, protos = class_prototypes(index)
    names = [index["class_names"].get(cid, str(cid)) for cid in ids]
    if len(ids) < 2:
        return {"class_ids": ids, "class_names": names, "similarity": [], "leaf_order": list(range(len(ids))), "similar_pairs": []}
    sim = protos @ protos.T  # symetryczne, przekątna ≈ 1
    sim = np.clip(sim, -1.0, 1.0)
    return {
        "class_ids": ids,
        "class_names": names,
        "similarity": sim.tolist(),
        "leaf_order": confusion_leaf_order(sim),
        "similar_pairs": confused_pairs(sim, names, top_k=top_k_pairs, min_similarity=0.0),
    }


def class_example_exemplars(
    index: dict[str, Any], *, proto_k: int = 6, boundary_k: int = 4
) -> dict[int, list[dict[str, Any]]]:
    """Wybierz reprezentatywne obiekty per klasa do galerii przykładów.

    Dwa rodzaje: **prototypowe** (najbliżej centroidu własnej klasy — „jak typowo wygląda
    ta klasa") oraz **graniczne** (najbliżej centroidu najpodobniejszej innej klasy — to
    obiekty, które napędzają mylenie tych dwóch klas). Zwraca ``{class_id: [exemplar,...]}``;
    każdy exemplar niesie ``annotation_id``/``scene_id``/``bbox``/``kind`` i podobieństwa.
    """
    objects = index["objects"]
    embeddings = index["embeddings"]
    if not objects or embeddings.size == 0:
        return {}
    ids, protos = class_prototypes(index)
    if not ids or protos.size == 0:
        return {}
    id_to_row = {cid: r for r, cid in enumerate(ids)}
    buckets: dict[int, list[int]] = defaultdict(list)
    for i, obj in enumerate(objects):
        buckets[obj["class_id"]].append(i)

    # Najpodobniejsza inna klasa per klasa (cosine prototypów, przekątna wykluczona).
    nearest_other: dict[int, int] = {}
    if len(ids) > 1:
        proto_sim = protos @ protos.T
        np.fill_diagonal(proto_sim, -np.inf)
        for r, cid in enumerate(ids):
            nearest_other[cid] = ids[int(np.argmax(proto_sim[r]))]

    def _exemplar(oi: int, kind: str, own: float, other_cid: int | None, other: float | None) -> dict[str, Any]:
        obj = objects[oi]
        return {
            "annotation_id": obj.get("annotation_id"),
            "scene_id": obj.get("scene_id"),
            "bbox": obj.get("bbox"),
            "kind": kind,
            "own_similarity": round(float(own), 4),
            "other_class_id": other_cid,
            "other_similarity": None if other is None else round(float(other), 4),
        }

    result: dict[int, list[dict[str, Any]]] = {}
    for cid in ids:
        rows = buckets.get(cid, [])
        if not rows:
            continue
        own_sims = embeddings[rows] @ protos[id_to_row[cid]]
        proto_order = np.argsort(-own_sims)
        seen: set[int] = set()
        exemplars: list[dict[str, Any]] = []
        for k in proto_order[:proto_k]:
            oi = rows[int(k)]
            if oi in seen:
                continue
            seen.add(oi)
            exemplars.append(_exemplar(oi, "proto", own_sims[int(k)], None, None))
        other_cid = nearest_other.get(cid)
        if other_cid is not None and other_cid in id_to_row and boundary_k > 0:
            other_sims = embeddings[rows] @ protos[id_to_row[other_cid]]
            for k in np.argsort(-other_sims)[:boundary_k]:
                oi = rows[int(k)]
                if oi in seen:
                    continue
                seen.add(oi)
                exemplars.append(
                    _exemplar(oi, "boundary", own_sims[int(k)], other_cid, other_sims[int(k)])
                )
        result[cid] = exemplars
    return result


def render_class_example_thumbnails(
    project_id: str,
    examples_by_class: dict[int, list[dict[str, Any]]],
    out_dir: "Path",
    *,
    size: int = 112,
    margin: float = 0.3,
) -> dict[int, list[dict[str, Any]]]:
    """Zapisz miniatury JPEG dla wybranych przykładów (wycinek = bbox + margines kontekstu).

    Miniatury renderujemy raz, razem z analizą — potem galeria czyta je z dysku, bez
    ponownego (wolnego) odczytu wielkich scen. Ustawia ``thumb`` na exemplarze (nazwa pliku)
    i pomija te, których sceny/rastra nie da się wczytać.
    """
    from pathlib import Path as _Path
    from routers.scenes import _read_geotiff_window
    from services.scene_raster_resolver import resolve_scene_raster

    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 — bez PIL po prostu nie renderujemy miniatur
        return examples_by_class

    out_dir = _Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_info_cache: dict[str, dict] = {}
    raster_cache: dict[str, Any] = {}
    saved: set[str] = set()

    def _resolve(scene_id: str) -> tuple[dict, Any]:
        if scene_id not in scene_info_cache:
            scene = load_scene_json(project_id, scene_id, "scene", default={})
            scene_info_cache[scene_id] = scene.get("scene_info") or {}
            try:
                raster_cache[scene_id] = resolve_scene_raster(project_id, scene_id)
            except Exception:  # noqa: BLE001
                raster_cache[scene_id] = None
        return scene_info_cache[scene_id], raster_cache[scene_id]

    for exemplars in examples_by_class.values():
        for ex in exemplars:
            aid = ex.get("annotation_id")
            sid = ex.get("scene_id")
            bbox = ex.get("bbox")
            if not aid or not sid or not bbox:
                continue
            fname = _chip_filename(str(aid))
            if aid in saved:
                ex["thumb"] = fname
                continue
            scene_info, raster = _resolve(str(sid))
            width, height = scene_info.get("width", 0), scene_info.get("height", 0)
            if raster is None or not width or not height:
                continue
            x0, y0, x1, y1 = (float(v) for v in bbox)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            half = max(max(x1 - x0, y1 - y0) * (1 + 2 * margin) / 2, 4.0)
            X0, Y0 = int(max(0, cx - half)), int(max(0, cy - half))
            X1, Y1 = int(min(width, cx + half)), int(min(height, cy + half))
            if X1 - X0 < 2 or Y1 - Y0 < 2:
                continue
            try:
                arr = _read_geotiff_window(raster, scene_info, X0, Y0, X1, Y1, size, size, size)
                Image.fromarray(arr).save(out_dir / fname, format="JPEG", quality=82)
            except Exception:  # noqa: BLE001 — brak jednej miniatury nie psuje analizy
                continue
            saved.add(str(aid))
            ex["thumb"] = fname
    return examples_by_class


def _chip_filename(annotation_id: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_-]", "_", annotation_id)[:120] or "chip"
    return f"{safe}.jpg"


def nearest_objects(index: dict[str, Any], query_index: int, k: int = 20) -> list[dict[str, Any]]:
    """k najbardziej podobnych obiektów do wskazanego (cosine; obiekty znormalizowane)."""
    embeddings = index["embeddings"]
    if embeddings.shape[0] <= 1 or not (0 <= query_index < embeddings.shape[0]):
        return []
    sims = embeddings @ embeddings[query_index]
    sims[query_index] = -np.inf  # bez samego siebie
    order = np.argsort(sims)[::-1][:k]
    names = index["class_names"]
    result = []
    for j in order:
        obj = index["objects"][int(j)]
        result.append({
            **obj,
            "class_name": names.get(obj["class_id"], str(obj["class_id"])),
            "similarity": round(float(sims[int(j)]), 4),
        })
    return result


def suspected_mislabels(index: dict[str, Any], *, margin: float = 0.02, top_k: int | None = None) -> list[dict[str, Any]]:
    """Obiekty bliżej prototypu INNEJ klasy niż własnej (podejrzenie błędnej etykiety).

    Bez trenowania: metryka to różnica cosine (prototyp obcej klasy − prototyp własnej).
    Zwraca kolejkę przeglądu posortowaną malejąco po tej różnicy, z pochodzeniem do edytora.
    """
    ids, protos = class_prototypes(index)
    if len(ids) < 2:
        return []
    id_to_row = {cid: r for r, cid in enumerate(ids)}
    embeddings = index["embeddings"]
    names = index["class_names"]
    flagged: list[dict[str, Any]] = []
    bounded: list[tuple[float, int, dict[str, Any]]] = []
    serial = 0
    objects = index["objects"]
    # N×C can itself become material for large class taxonomies. Compute bounded
    # row blocks and, when requested, retain only the review queue's top-k.
    for start in range(0, len(objects), 4096):
        stop = min(start + 4096, len(objects))
        similarities = np.asarray(embeddings[start:stop]) @ protos.T
        for local_i, obj in enumerate(objects[start:stop]):
            own_row = id_to_row.get(obj["class_id"])
            if own_row is None:
                continue
            own = similarities[local_i, own_row]
            others = similarities[local_i].copy()
            others[own_row] = -np.inf
            best_other_row = int(np.argmax(others))
            gap = float(others[best_other_row] - own)
            if gap <= margin:
                continue
            item = {
                **obj,
                "class_name": names.get(obj["class_id"], str(obj["class_id"])),
                "suggested_class_id": ids[best_other_row],
                "suggested_class_name": names.get(ids[best_other_row], str(ids[best_other_row])),
                "own_similarity": round(float(own), 4),
                "suggested_similarity": round(float(others[best_other_row]), 4),
                "gap": round(gap, 4),
            }
            if top_k:
                serial += 1
                heap_item = (gap, serial, item)
                if len(bounded) < int(top_k):
                    heapq.heappush(bounded, heap_item)
                elif gap > bounded[0][0]:
                    heapq.heapreplace(bounded, heap_item)
            else:
                flagged.append(item)
    if top_k:
        flagged = [entry[2] for entry in bounded]
    flagged.sort(key=lambda item: item["gap"], reverse=True)
    return flagged


def class_cohesion(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Spójność (cohesion) każdej klasy = średni cosine jej obiektów do prototypu WŁASNEJ klasy.

    Niska spójność / duży rozrzut = klasa wizualnie niejednorodna (kandydat do rozbicia albo
    rewizji definicji). Rekomendacja, nie decyzja — zgodnie z DI0. Posortowane rosnąco, więc
    najmniej spójne klasy są na górze.
    """
    ids, protos = class_prototypes(index)
    if not ids:
        return []
    id_to_row = {cid: r for r, cid in enumerate(ids)}
    embeddings = index["embeddings"]
    per_class: dict[int, list[float]] = defaultdict(list)
    for i, obj in enumerate(index["objects"]):
        row = id_to_row.get(obj["class_id"])
        if row is None:
            continue
        per_class[obj["class_id"]].append(float(embeddings[i] @ protos[row]))
    names = index["class_names"]
    out = []
    for cid in ids:
        vals = np.asarray(per_class.get(cid, []), dtype=np.float32)
        if vals.size == 0:
            continue
        out.append({
            "class_id": cid,
            "name": names.get(cid, str(cid)),
            "count": int(vals.size),
            "cohesion": round(float(vals.mean()), 4),      # 1.0 = idealnie spójna
            "spread": round(float(vals.std()), 4),
            "min_cohesion": round(float(vals.min()), 4),
        })
    out.sort(key=lambda d: d["cohesion"])
    return out


def class_outliers(
    index: dict[str, Any], *, margin: float = 0.02, max_per_class: int = 5, min_class_size: int = 8
) -> list[dict[str, Any]]:
    """Obiekty najdalej od prototypu WŁASNEJ klasy (niska cosine), które NIE są jednocześnie
    bliżej klasy obcej — te drugie trafiają do `suspected_mislabels`.

    Kandydaci na błędne wycięcia / trudne przykłady. Pomijamy klasy zbyt małe (prototyp
    niestabilny). Zwraca do `max_per_class` najdalszych na klasę, z pochodzeniem do edytora.
    """
    if max_per_class <= 0:
        return []
    ids, protos = class_prototypes(index)
    if not ids:
        return []
    id_to_row = {cid: r for r, cid in enumerate(ids)}
    embeddings = index["embeddings"]
    names = index["class_names"]
    class_sizes = Counter(obj["class_id"] for obj in index["objects"])
    per_class: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    objects = index["objects"]
    serial = 0
    for start in range(0, len(objects), 4096):
        stop = min(start + 4096, len(objects))
        similarities = np.asarray(embeddings[start:stop]) @ protos.T
        for local_i, obj in enumerate(objects[start:stop]):
            own_row = id_to_row.get(obj["class_id"])
            if own_row is None or class_sizes[obj["class_id"]] < min_class_size:
                continue
            own = float(similarities[local_i, own_row])
            others = similarities[local_i].copy()
            others[own_row] = -np.inf
            if float(np.max(others)) - own > margin:  # to już mislabel, nie outlier
                continue
            serial += 1
            candidates = per_class[obj["class_id"]]
            item = (-own, serial, start + local_i)
            if len(candidates) < max_per_class:
                heapq.heappush(candidates, item)
            elif own < -candidates[0][0]:
                heapq.heapreplace(candidates, item)
    result = []
    for cid, candidates in per_class.items():
        items = sorted(
            [(-negative_own, index_value) for negative_own, _serial, index_value in candidates]
        )
        for own, i in items:
            obj = index["objects"][i]
            result.append({
                **obj,
                "class_name": names.get(cid, str(cid)),
                "own_similarity": round(own, 4),
            })
    result.sort(key=lambda d: d["own_similarity"])
    return result


def build_split_of(project_id: str, run_id: str, objects: list[dict[str, Any]]) -> dict[str, str]:
    """Mapuj `annotation_id → split` obiektów źródłowych na podstawie opublikowanego runu.

    Split przypisujemy obiektowi, gdy **środek jego boxa** wpada w okno kafla tego splitu.
    Obiekty spoza runu (odfiltrowane przy budowie, scena nieujęta) nie dostają splitu i nie
    biorą udziału w wykrywaniu przecieku. Podawane do `near_duplicate_pairs(split_of=...)`.
    """
    from services.dataset_content import tile_split_map

    info = tile_split_map(project_id, run_id)
    tile_size = int(info.get("tile_size") or 0)
    if tile_size <= 0:
        return {}
    by_scene: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for tile in info["tiles"]:
        by_scene[tile["scene_id"]].append(tile)
    # deterministyczne rozstrzyganie, gdy kafle się nakładają (środek w >1 kaflu)
    for tiles in by_scene.values():
        tiles.sort(key=lambda t: (t["y0"], t["x0"], t["split"]))
    result: dict[str, str] = {}
    for obj in objects:
        aid = obj.get("annotation_id")
        if aid is None:
            continue
        bx0, by0, bx1, by1 = obj["bbox"]
        cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
        for tile in by_scene.get(obj["scene_id"], ()):
            if tile["x0"] <= cx < tile["x0"] + tile_size and tile["y0"] <= cy < tile["y0"] + tile_size:
                result[aid] = tile["split"]
                break
    return result


def _lsh_candidate_batches(
    embeddings: np.ndarray,
    *,
    bands: int = 16,
    bits_per_band: int = 16,
    pair_batch: int = 8192,
    seed: int = 0x47544C,
):
    """Yield bounded candidate-pair arrays from random-hyperplane cosine LSH.

    At cosine 0.97, 16 independent 16-bit bands have a theoretical candidate recall
    of about 0.995. Random unrelated vectors produce only about ``bands / 2**bits`` of
    all possible pairs, replacing quadratic work with a bounded approximate scan.
    """

    count = int(embeddings.shape[0])
    dimension = int(embeddings.shape[1]) if embeddings.ndim == 2 else 0
    if count < 2 or dimension == 0:
        return
    rng = np.random.default_rng(seed)
    powers = np.left_shift(np.uint32(1), np.arange(bits_per_band, dtype=np.uint32))
    pending_i: list[np.ndarray] = []
    pending_j: list[np.ndarray] = []
    pending_count = 0

    def flush():
        nonlocal pending_i, pending_j, pending_count
        if not pending_i:
            return None
        left = np.concatenate(pending_i)
        right = np.concatenate(pending_j)
        pending_i, pending_j, pending_count = [], [], 0
        return left, right

    matrix = np.asarray(embeddings)
    for _band in range(max(1, int(bands))):
        planes = rng.standard_normal((bits_per_band, dimension)).astype(np.float32)
        projections = matrix @ planes.T
        codes = ((projections >= 0).astype(np.uint32) * powers).sum(axis=1, dtype=np.uint32)
        order = np.argsort(codes, kind="stable")
        sorted_codes = codes[order]
        boundaries = np.flatnonzero(np.diff(sorted_codes)) + 1
        starts = np.concatenate((np.asarray([0]), boundaries))
        stops = np.concatenate((boundaries, np.asarray([count])))
        for start, stop in zip(starts, stops):
            rows = order[int(start) : int(stop)]
            size = len(rows)
            if size < 2:
                continue
            # In a pathologically collapsed bucket, enough deterministic pairs are
            # retained to fill the bounded result queue; subsequent bands provide
            # independent permutations/collisions for the remaining rows.
            if size > 2048:
                rows = rng.choice(rows, size=2048, replace=False)
                size = len(rows)
            tri_i, tri_j = np.triu_indices(size, k=1)
            for offset in range(0, len(tri_i), pair_batch):
                left = rows[tri_i[offset : offset + pair_batch]]
                right = rows[tri_j[offset : offset + pair_batch]]
                pending_i.append(np.asarray(left, dtype=np.int64))
                pending_j.append(np.asarray(right, dtype=np.int64))
                pending_count += len(left)
                if pending_count >= pair_batch:
                    emitted = flush()
                    if emitted is not None:
                        yield emitted
        emitted = flush()
        if emitted is not None:
            yield emitted


def near_duplicate_pairs(
    index: dict[str, Any],
    *,
    threshold: float = 0.97,
    max_pairs: int = 200,
    split_of: dict[str, str] | None = None,
    ann_index: Any | None = None,
    exact_limit: int = 10_000,
    candidate_k: int = 64,
    large_mode: str = "lsh",
) -> list[dict[str, Any]]:
    """Return a bounded top-k list of near duplicates without quadratic large-N work.

    Small inputs use the exact reference implementation. Larger inputs default to
    random-hyperplane cosine LSH (bounded candidate batches); ``large_mode='ann'`` keeps
    the persisted USearch kNN path available for diagnostics. Before vector search,
    source identity and same-scene geometry are checked in O(N), giving deterministic
    leakage candidates even if an approximate visual search misses a neighbor.
    """
    embeddings = index["embeddings"]
    n = int(embeddings.shape[0])
    max_pairs = max(0, int(max_pairs))
    if n < 2 or max_pairs == 0:
        return []
    objects = index["objects"]
    names = index["class_names"]
    heap: list[tuple[int, float, int, dict[str, Any]]] = []
    heap_keys: set[tuple[int, int]] = set()
    serial = 0

    def make_pair(i: int, j: int, similarity: float, candidate_source: str) -> dict[str, Any]:
        a, b = objects[i], objects[j]
        pair: dict[str, Any] = {
            "similarity": round(float(similarity), 4),
            "a": {**a, "class_name": names.get(a["class_id"], str(a["class_id"]))},
            "b": {**b, "class_name": names.get(b["class_id"], str(b["class_id"]))},
            "same_class": a["class_id"] == b["class_id"],
            "candidate_source": candidate_source,
        }
        if split_of is not None:
            sa = split_of.get(a.get("annotation_id"))
            sb = split_of.get(b.get("annotation_id"))
            pair["split_a"], pair["split_b"] = sa, sb
            pair["cross_split"] = sa is not None and sb is not None and sa != sb
        return pair

    def keep(pair: dict[str, Any], pair_key: tuple[int, int]) -> None:
        nonlocal serial
        if pair_key in heap_keys:
            return
        serial += 1
        item = (
            int(bool(pair.get("cross_split", False))),
            float(pair["similarity"]),
            serial,
            pair,
        )
        if len(heap) < max_pairs:
            heapq.heappush(heap, item)
            heap_keys.add(pair_key)
        elif item[:2] > heap[0][:2]:
            removed = heapq.heapreplace(heap, item)
            # Annotation IDs need not be unique in legacy data, so derive the old row
            # key saved privately on the pair and remove it before API serialization.
            heap_keys.discard(removed[3].pop("_row_pair"))
            pair["_row_pair"] = pair_key
            heap_keys.add(pair_key)

    def keep_rows(i: int, j: int, similarity: float, source: str) -> None:
        pair_key = (min(i, j), max(i, j))
        pair = make_pair(pair_key[0], pair_key[1], similarity, source)
        pair["_row_pair"] = pair_key
        keep(pair, pair_key)

    # Source/geometry identity is cheaper and deterministic. Limit candidate expansion
    # to the result budget so a malformed pile of identical boxes cannot become O(N²).
    identity_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for row, obj in enumerate(objects):
        source_id = obj.get("source_annotation_id")
        if source_id:
            identity_groups[("source", str(source_id))].append(row)
        bbox = obj.get("bbox") or []
        if len(bbox) == 4:
            identity_groups[
                (
                    "geometry",
                    obj.get("scene_id"),
                    *(round(float(value), 2) for value in bbox),
                )
            ].append(row)
    identity_pairs: set[tuple[int, int]] = set()
    for key, rows in identity_groups.items():
        if len(rows) < 2:
            continue
        source_name = "source_id" if key[0] == "source" else "source_geometry"
        for offset, i in enumerate(rows[:-1]):
            for j in rows[offset + 1 :]:
                pair_key = (min(i, j), max(i, j))
                if pair_key in identity_pairs:
                    continue
                identity_pairs.add(pair_key)
                similarity = float(embeddings[i] @ embeddings[j])
                if similarity >= threshold:
                    keep_rows(i, j, similarity, source_name)
                if len(identity_pairs) >= max_pairs:
                    break
            if len(identity_pairs) >= max_pairs:
                break
        if len(identity_pairs) >= max_pairs:
            break

    if n <= int(exact_limit):
        block = min(512, n)
        for start in range(0, n, block):
            stop = min(start + block, n)
            similarities = np.asarray(embeddings[start:stop]) @ np.asarray(embeddings).T
            for local_i in range(stop - start):
                i = start + local_i
                row = similarities[local_i]
                for raw_j in np.flatnonzero(row >= threshold):
                    j = int(raw_j)
                    if j <= i or (i, j) in identity_pairs:
                        continue
                    keep_rows(i, j, float(row[j]), "exact")
    elif large_mode == "lsh":
        for left, right in _lsh_candidate_batches(embeddings):
            left_vectors = np.asarray(embeddings[left], dtype=np.float32)
            right_vectors = np.asarray(embeddings[right], dtype=np.float32)
            similarities = np.einsum("ij,ij->i", left_vectors, right_vectors)
            for offset in np.flatnonzero(similarities >= threshold):
                i, j = int(left[offset]), int(right[offset])
                pair_key = (min(i, j), max(i, j))
                if pair_key in identity_pairs or pair_key in heap_keys:
                    continue
                keep_rows(i, j, float(similarities[offset]), "lsh")
    elif large_mode == "ann":
        if ann_index is None:
            ann_path = index.get("ann_path")
            if ann_path:
                from usearch.index import Index

                ann_index = Index.restore(str(ann_path), view=True)
        if ann_index is None:
            raise RuntimeError(
                f"ANN index required for {n} objects (exact limit: {exact_limit})"
            )
        search_k = min(n, max(2, int(candidate_k) + 1))
        query_batch = 2048
        for start in range(0, n, query_batch):
            stop = min(start + query_batch, n)
            matches = ann_index.search(
                np.asarray(embeddings[start:stop], dtype=np.float32), search_k
            )
            keys = np.asarray(matches.keys)
            distances = np.asarray(matches.distances)
            if keys.ndim == 1:
                keys = keys.reshape(1, -1)
                distances = distances.reshape(1, -1)
            for local_i in range(stop - start):
                i = start + local_i
                for raw_j, distance in zip(keys[local_i], distances[local_i]):
                    j = int(raw_j)
                    if j <= i or j >= n or (i, j) in identity_pairs:
                        continue
                    similarity = 1.0 - float(distance)
                    if similarity >= threshold:
                        keep_rows(i, j, similarity, "ann")
    else:
        raise ValueError(f"Unsupported large near-duplicate mode: {large_mode}")

    pairs = [item[3] for item in heap]
    for pair in pairs:
        pair.pop("_row_pair", None)
    pairs.sort(
        key=lambda pair: (pair.get("cross_split", False), pair["similarity"]),
        reverse=True,
    )
    return pairs
