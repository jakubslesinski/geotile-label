"""Warstwa QA modułu Dataset Intelligence (DESIGN_DECISIONS.md, dataset-intelligence DI1).

Deterministyczna analiza metadanych — **nie bramkowana przez DI0**. Odpowiada na
pytanie, którego sama liczność klasy nie rozstrzyga: czy klasa ma **pokrycie wariancji**,
czy tylko dużo redundantnych przykładów z jednego miejsca/akwizycji. Klasa z 500 chipami
z jednego lotniska albo jednej polaryzacji jest słabiej reprezentowana niż 120 chipów z
dziesięciu scen i wielu warunków akwizycji.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from db.storage import list_scene_ids, load_json, load_scene_json
from services.dataset_audit import _acquisition_key

# scipy/sklearn nie sa w srodowisku bazowym; leaf-order liczymy sami (average linkage),
# zeby nie dokladac zaleznosci do instalatora analityka. Dla <= kilkuset klas koszt
# O(N^3) jest nieistotny.

# Próg liczności, poniżej którego klasa jest oznaczana jako niedostatecznie reprezentowana.
# Wartość robocza — do dostrojenia przy realnej taksonomii; nie jest twardym prawem.
DEFAULT_MIN_SAMPLES = 50


def _scene_gsd(scene: dict, manifest: dict) -> float | None:
    for source in (manifest, scene.get("scene_info") or {}, scene):
        value = source.get("gsd_m")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def class_coverage_report(project_id: str, min_samples: int = DEFAULT_MIN_SAMPLES) -> dict[str, Any]:
    """Pokrycie wariancji per klasa: liczności, niezależne sceny/akwizycje/autorzy, rozmiary."""
    classes = {c["id"]: c["name"] for c in load_json(project_id, "classes", default=[])}
    modality = (load_json(project_id, "project", default={}).get("profile") or {}).get("modality")

    agg: dict[Any, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "scenes": set(),
            "acquisitions": set(),
            "authors": set(),
            "sizes_px": [],
            "sizes_m": [],
        }
    )

    for scene_id in list_scene_ids(project_id):
        annotations = load_scene_json(project_id, scene_id, "annotations", default=[])
        if not annotations:
            continue
        scene = load_scene_json(project_id, scene_id, "scene", default={})
        manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
        acq = _acquisition_key(manifest)
        gsd = _scene_gsd(scene, manifest)

        for ann in annotations:
            if ann.get("is_negative") or not ann.get("bbox"):
                continue
            entry = agg[ann.get("class_id")]
            entry["count"] += 1
            entry["scenes"].add(scene_id)
            if acq:
                entry["acquisitions"].add(acq)
            entry["authors"].add(ann.get("annotator_email"))
            x0, y0, x1, y1 = ann["bbox"]
            size_px = max(abs(x1 - x0), abs(y1 - y0))
            entry["sizes_px"].append(size_px)
            if gsd:
                entry["sizes_m"].append(size_px * gsd)

    report_classes: list[dict[str, Any]] = []
    for class_id, entry in agg.items():
        count = entry["count"]
        scenes = len(entry["scenes"])
        acquisitions = len(entry["acquisitions"])
        flags: list[str] = []
        if count < min_samples:
            flags.append("under_min")
        if scenes <= 1:
            flags.append("single_scene")
        if acquisitions <= 1 and modality == "SAR":
            # Dla SAR jedna konfiguracja akwizycji = brak pokrycia geometrii obserwacji.
            flags.append("single_acquisition")
        report_classes.append({
            "class_id": class_id,
            "name": classes.get(class_id, str(class_id)),
            "count": count,
            "scenes": scenes,
            "acquisitions": acquisitions,
            "authors": len([a for a in entry["authors"] if a]),
            "median_size_px": round(statistics.median(entry["sizes_px"]), 1) if entry["sizes_px"] else None,
            "median_size_m": round(statistics.median(entry["sizes_m"]), 2) if entry["sizes_m"] else None,
            "flags": flags,
        })

    report_classes.sort(key=lambda item: item["count"], reverse=True)

    # Klasy zdefiniowane, ale bez adnotacji — osobno, żeby nie ginęły.
    annotated = {item["class_id"] for item in report_classes}
    unused = [
        {"class_id": cid, "name": name}
        for cid, name in classes.items()
        if cid not in annotated
    ]

    return {
        "schema_name": "geotile_class_coverage",
        "schema_version": 1,
        "project_id": project_id,
        "modality": modality,
        "min_samples": min_samples,
        "classes": report_classes,
        "unused_classes": unused,
        "summary": {
            "annotated_classes": len(report_classes),
            "under_min": sum(1 for c in report_classes if "under_min" in c["flags"]),
            "single_scene": sum(1 for c in report_classes if "single_scene" in c["flags"]),
            "single_acquisition": sum(1 for c in report_classes if "single_acquisition" in c["flags"]),
            "unused": len(unused),
        },
    }


# --- Analiza macierzy pomylek (DI1, warstwa confusion) ---------------------------
#
# Zamienia surowa macierz z treningu w symetryczne podobienstwo klas i kolejnosc
# leaf-order klasteryzacji — to ona sprawia, ze macierz jest czytelna przy 200-300
# klasach (mylace sie pary ukladaja sie w bloki przy przekatnej). WARSTWA DANYCH; osady
# "ktore pary faktycznie scalac" sa bramkowane przez DI0.


def confusion_to_similarity(matrix, class_names):
    """Surowa macierz [predykcja][prawda] -> symetryczne podobienstwo klas N x N (0..1).

    Odrzuca wiersz/kolumne tla (ostatni indeks ultralytics), normalizuje po klasie
    prawdziwej i symetryzuje: S[A,B] = (P(pred=B|true=A) + P(pred=A|true=B)) / 2.
    """
    import numpy as np

    m = np.asarray(matrix, dtype=float)
    n = len(class_names)
    m = m[:n, :n]  # odetnij ewentualny wiersz/kolumne tla (ultralytics: ostatni indeks)
    col = m.sum(axis=0, keepdims=True)  # suma po predykcjach dla danej prawdy
    norm = np.divide(m, col, out=np.zeros_like(m), where=col > 0)  # norm[pred][true]
    sim = 0.5 * (norm + norm.T)
    return sim


def confusion_leaf_order(similarity):
    """Kolejnosc klas z klasteryzacji aglomeracyjnej (average linkage) po podobienstwie.

    Mylace sie pary trafiaja obok siebie. Czysty numpy — bez scipy/sklearn.
    """
    import numpy as np

    sim = np.asarray(similarity, dtype=float)
    n = sim.shape[0]
    if n <= 2:
        return list(range(n))

    INF = 1e18
    dist = (1.0 - sim).astype(float)
    np.fill_diagonal(dist, INF)
    order = {i: [i] for i in range(n)}
    size = np.ones(n)
    active = list(range(n))

    while len(active) > 1:
        sub = dist[np.ix_(active, active)]
        flat = int(np.argmin(sub))
        ai, bi = divmod(flat, len(active))
        a, b = active[ai], active[bi]
        order[a] = order[a] + order[b]  # scalone leaves pozostaja spojne
        for c in active:
            if c in (a, b):
                continue
            merged = (size[a] * dist[a, c] + size[b] * dist[b, c]) / (size[a] + size[b])
            dist[a, c] = dist[c, a] = merged  # Lance-Williams (average)
        size[a] += size[b]
        active.remove(b)
        dist[a, a] = INF

    return order[active[0]]


def confused_pairs(similarity, class_names, top_k=20, min_similarity=0.02):
    """Ranking najbardziej mylonych par (off-diagonal), najsilniejsze pierwsze."""
    import numpy as np

    sim = np.asarray(similarity, dtype=float)
    n = sim.shape[0]
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= min_similarity:
                pairs.append({
                    "class_a": class_names[i] if i < len(class_names) else str(i),
                    "class_b": class_names[j] if j < len(class_names) else str(j),
                    "similarity": round(float(sim[i, j]), 4),
                })
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs[:top_k]
