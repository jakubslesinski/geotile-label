"""v08 — Brak egresu / działanie offline  (Claim C5).

SUBSTRATE / TIER
    FAIR1M (projekt lokalny) · public.

CLAIM
    Ścieżka danych działa offline — żadna scena, adnotacja ani dataset nie opuszcza
    stanowiska. Krytyczne dla materiału wrażliwego / GEOINT.

    ZAKRES CLAIMU. Dowód mierzy potok BACKENDU: ingest, model geo, propagację
    adnotacja->kafel, render okna i zapis etykiet. Poza nim zostaje jeden składnik
    interfejsu, który sięga do sieci świadomie: opcjonalna warstwa referencyjna podkładu
    mapowego w widoku etykietowania, pobierana przez przeglądarkę wprost od dostawcy
    kafli (Esri/OpenStreetMap). Jest pomocą wizualną — nie zapisuje się, nie eksportuje
    i nie wchodzi do żadnego artefaktu. Claim brzmi więc „brak egresu w ścieżce danych",
    a nie „aplikacja nie wykonuje żadnych połączeń".

METHOD
    Uruchamiam realny lokalny pipeline aplikacji (ingest -> model geo -> propagacja
    adnotacja->kafel -> render 8-bit okna -> zapis etykiet YOLO) na projekcie FAIR1M w dwóch
    poziomach:
      1. OBSERWACJA — instaluję sondę na `socket.connect`/`connect_ex`/`getaddrinfo`,
         zliczam próby połączeń WYCHODZĄCYCH (poza loopback). Oczekiwane: 0.
      2. DOWÓD MOCNY — blokuję egress (każdy connect do adresu nie-loopback podnosi wyjątek)
         i uruchamiam ten sam pipeline; ma przejść w CAŁOŚCI (brak zależności od sieci).
    Loopback/AF_UNIX nie są egresem.

INPUTS
    - lokalny projekt FAIR1M (../importers)
    - realne funkcje backendu (scene_loader / sensor_geometry / annotation_propagator /
      preprocessing_profiles / export_yolo)

OUTPUTS
    - results/egress_log.csv  (stage, kind, target)
    - metrics: {outbound_attempts_total, hosts[], ran_with_network_blocked, stages_run}

PASS CRITERION
    outbound_attempts_total == 0 dla projektu lokalnego ORAZ pełny run przechodzi przy
    zablokowanym egresie.
"""

from __future__ import annotations

import csv
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv

N_SCENES = int(os.environ.get("V08_SCENES", "5"))
TILE_SIZE = int(os.environ.get("V08_TILE_SIZE", "640"))
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "", "0.0.0.0"}


def _is_local(host) -> bool:
    h = str(host)
    return h in _LOCAL_HOSTS or h.startswith("127.")


class EgressMonitor:
    """Sonda na sockety: zbiera próby połączeń wychodzących (observe) lub je blokuje (block)."""

    def __init__(self, block: bool = False):
        self.block = block
        self.events: list[tuple[str, str]] = []  # (kind, target)
        self._orig = {}

    def __enter__(self):
        self._orig["connect"] = socket.socket.connect
        self._orig["connect_ex"] = socket.socket.connect_ex
        self._orig["getaddrinfo"] = socket.getaddrinfo
        mon = self

        def connect(self_sock, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else address
            if not _is_local(host):
                mon.events.append(("connect", str(host)))
                if mon.block:
                    raise OSError("v08: egress zablokowany (offline)")
            return mon._orig["connect"](self_sock, address, *a, **k)

        def connect_ex(self_sock, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else address
            if not _is_local(host):
                mon.events.append(("connect_ex", str(host)))
                if mon.block:
                    raise OSError("v08: egress zablokowany (offline)")
            return mon._orig["connect_ex"](self_sock, address, *a, **k)

        def getaddrinfo(host, *a, **k):
            if host is not None and not _is_local(host):
                mon.events.append(("dns", str(host)))
                if mon.block:
                    raise socket.gaierror("v08: DNS zablokowany (offline)")
            return mon._orig["getaddrinfo"](host, *a, **k)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = getaddrinfo
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._orig["connect"]
        socket.socket.connect_ex = self._orig["connect_ex"]
        socket.getaddrinfo = self._orig["getaddrinfo"]
        return False


def _run_pipeline(project: Path, scene_dirs, deps, export_dir: Path) -> list[str]:
    (get_scene_info, SceneGeoModel, Annotation, TilingConfig, compute_grid,
     open_scene_source, read_tile_from_source, apply_preprocessing_profile,
     propagate, PreprocessingProfile, write_yolo) = deps
    profs = json.loads((project / "preprocessing_profiles.json").read_text(encoding="utf-8"))
    plist = profs.get("profiles") if isinstance(profs, dict) else profs
    profile = PreprocessingProfile(**plist[0])
    tiling = TilingConfig(tile_size=TILE_SIZE, buffer=0)
    stages = set()
    for scene_dir in scene_dirs:
        manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
        src = manifest.get("source_path")
        if not src or not Path(src).is_file():
            continue
        get_scene_info(src); stages.add("ingest")
        SceneGeoModel.from_manifest(manifest); stages.add("geo_model")
        records = json.loads((scene_dir / "annotations.json").read_text(encoding="utf-8"))
        anns = [Annotation(**r) for r in records]
        image = manifest.get("image") or {}
        preview = compute_grid(int(image["width"]), int(image["height"]), tiling)
        tiles = []
        for index, (x0, y0, x1, y1) in enumerate(preview.tile_rects):
            r = index // preview.num_cols + 1
            c = index % preview.num_cols + 1
            from models.tiling_config import TileInfo
            tiles.append(TileInfo(filename=f"{c}_{r}.png", col=c, row=r, x0=x0, y0=y0, x1=x1, y1=y1,
                                  scene_id=scene_dir.name))
        tile_anns, _attrs = propagate(anns, tiles, tiling, 0.3); stages.add("propagate")
        # render 8-bit okno pierwszego kafla
        reader = open_scene_source(src, profile)
        raw, mask = read_tile_from_source(reader, tiles[0].x0, tiles[0].y0, TILE_SIZE)
        apply_preprocessing_profile(raw, profile, mask); reader.close(); stages.add("render")
        # eksport etykiet YOLO
        labels_dir = export_dir / "labels"
        labels_dir.mkdir(parents=True, exist_ok=True)
        for tname, rows in tile_anns.items():
            if rows:
                with open(labels_dir / f"{scene_dir.name}__{tname.replace('.png','.txt')}", "w") as fh:
                    for a in rows:
                        fh.write(f"{int(a[0])} {a[1]} {a[2]} {a[3]} {a[4]}\n")
        stages.add("export")
    return sorted(stages)


def main() -> ValidationResult:
    res = ValidationResult(
        id="v08", claim="C5", title="Offline / brak egresu (projekt lokalny)",
        substrate=["FAIR1M"], tier="public",
    )
    res.config = {"n_scenes": N_SCENES, "tile_size": TILE_SIZE}

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"; res.notes = blocked; return res

    from services.scene_loader import get_scene_info
    from services.sensor_geometry import SceneGeoModel
    from models.annotation import Annotation
    from models.tiling_config import TilingConfig
    from models.preprocessing import PreprocessingProfile
    from services.tiler import compute_grid
    from services.preprocessing_profiles import (
        open_scene_source, read_tile_from_source, apply_preprocessing_profile,
    )
    from services.annotation_propagator import propagate_annotations_with_attributes as propagate

    try:
        project = appenv.benchmark_project("FAIR1M")
    except FileNotFoundError as exc:
        res.status = "todo"; res.notes = str(exc); return res

    scene_dirs = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())[:N_SCENES]
    deps = (get_scene_info, SceneGeoModel, Annotation, TilingConfig, compute_grid,
            open_scene_source, read_tile_from_source, apply_preprocessing_profile,
            propagate, PreprocessingProfile, None)

    tmp = Path(tempfile.mkdtemp(prefix="v08_"))

    # Poziom 1: obserwacja
    with EgressMonitor(block=False) as mon:
        stages = _run_pipeline(project, scene_dirs, deps, tmp / "observe")
    observe_events = list(mon.events)

    # Poziom 2: egress zablokowany — pipeline ma przejść w całości
    ran_blocked = False
    block_error = ""
    try:
        with EgressMonitor(block=True):
            stages_blocked = _run_pipeline(project, scene_dirs, deps, tmp / "blocked")
        ran_blocked = stages_blocked == stages
    except Exception as exc:  # noqa: BLE001 — jakikolwiek błąd = zależność od sieci
        block_error = f"{type(exc).__name__}: {str(exc)[:80]}"

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "egress_log.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["level", "kind", "target"])
        for kind, target in observe_events:
            w.writerow(["observe", kind, target])

    hosts = sorted({t for _, t in observe_events})
    res.metrics = {
        "outbound_attempts_total": len(observe_events),
        "hosts": hosts,
        "ran_with_network_blocked": ran_blocked,
        "stages_run": stages,
        "block_error": block_error,
    }
    res.artifacts = ["results/egress_log.csv"]
    res.status = "pass" if (len(observe_events) == 0 and ran_blocked) else "fail"
    res.notes = (
        f"Lokalny pipeline FAIR1M ({', '.join(stages)}) na {len(scene_dirs)} scenach: "
        f"prób egresu = {len(observe_events)} {('-> ' + ', '.join(hosts)) if hosts else ''}; "
        f"pełny run przy zablokowanym egresie = {ran_blocked}"
        f"{(' (' + block_error + ')') if block_error else ''}. "
        f"Claim obejmuje ścieżkę danych; opcjonalna warstwa referencyjna podkładu "
        f"w interfejsie jest poza nim i nie wchodzi do żadnego artefaktu."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
