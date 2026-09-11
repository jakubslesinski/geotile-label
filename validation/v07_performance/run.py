"""v07 — Wydajność dla dużej sceny  (Claim S3).

SUBSTRATE / TIER
    Ramię publiczne: xView3 — realna scena XXL, Sentinel-1 SAR (~30k×27k px,
    Float16->Float32 VRT) · public.
    Ramię JP2/COG: realna scena JP2 60476×43476 uint16 (2,63 Gpx) z opublikowanym
    derywatem 1× · reported-only.  (mixed, gdy drugie ramię jest dostępne)
    Etykieta dostawcy pochodzi z manifestu wykrytej sceny, nie z założenia — scena użyta
    na tej stacji raportuje `generic`, bo została zaimportowana jako raster generyczny.

CLAIM
    Workflow skaluje się do dużej sceny satelitarnej w praktycznym czasie/pamięci.

    Drugie ramię pokazuje, CZYM ta praktyczność stoi na scenie JP2: nie samym odczytem
    okienkowym, lecz istnieniem opublikowanego derywatu. Ten sam viewport czytany wprost
    ze źródłowego JP2 jest o dwa rzędy wielkości wolniejszy. Bez tego ramienia S3 opierało
    się na jednym substracie, którego najdroższy przypadek nie był reprezentatywny dla
    najtrudniejszego przypadku aplikacji.

METHOD
    Dla największej sceny xView3 mierzę czas etapów oraz szczytową pamięć procesu (psutil RSS):
      - ingest/manifest: `get_scene_info` na rastrze roboczym Float32 (VRT sprowadza
        Float16 do Float32, omijając `KeyError:15`),
      - kaflowanie: `compute_grid` na pełnych wymiarach -> liczba kafli,
      - odczyt+render kafla: próbka N kafli czytana OKNOWO (rasterio Window, ten sam
        mechanizm co `read_tile_from_source`) + `apply_preprocessing_profile` (render 8-bit);
        z mediany na kafel ekstrapoluję czas pełnego buildu,
      - eksport: zapis etykiet jest zdominowany przez I/O kafli, raportowany jako ~0.
    Powtarzam K razy, raportuję medianę i rozrzut. Odczyt jest OKNOWY, więc przy tej ścieżce
    pamięć nie rośnie z rozmiarem sceny.

    Ramię JP2/COG mierzy ODCZYT 1× tej samej sceny dwiema ścieżkami — ze źródłowego JP2
    i z opublikowanego COG-a — oknem 512 px i ze ŚWIEŻYM UCHWYTEM na kafel, bo tak zachowuje
    się serwer kafli (każdy kafel to osobne żądanie HTTP, bez współdzielonego uchwytu GDAL).
    Derywat jest KONSUMOWANY, nie budowany: przygotowanie jednej sceny to 19,3–19,6 min
    (JP2_FULL_RESOLUTION_DECISION_PLAN §16.3), więc budowa w środku dowodu zamieniłaby
    pomiar odczytu w pomiar budowy.

    Uwaga o powtarzalności: wartości bezwzględne tego ramienia zależą od stanu cache'u
    systemu plików — w kolejnych przebiegach obserwowano od ~60× do ~700× różnicy. Wnioskiem
    jest RZĄD WIELKOŚCI, nie konkretna liczba; dlatego ramię nie ma progu i jest opisowe.

INPUTS
    - największa scena xView3 (raster roboczy Float32) z zaimportowanego projektu
    - opcjonalnie: projekt z opublikowanym derywatem 1× w katalogu danych aplikacji
      (`V07_JP2_DATA_DIR`, domyślnie `%APPDATA%/GeoTileLabel/data`) — dane pod licencją,
      więc ramię jest `reported-only` i nie jest wymagane do przejścia dowodu
    - etapy z backendu (scene_loader / tiler / preprocessing_profiles / scene_packages)

OUTPUTS
    - results/perf.csv  (stage, median_s, p95_s)
    - metrics: {scene_px, n_tiles, ingest_s, tiling_s, per_tile_ms, est_build_s,
                peak_rss_mb, hardware, jp2_cog_arm{source_jp2, published_cog, speedup_x,
                derived_to_source_ratio}}

PASS CRITERION
    Wszystkie etapy kończą się poprawnie; czasy i pamięć raportowane (opisowe, bez progu).
    Przy ODCZYCIE OKIENKOWYM GeoTIFF pamięć szczytowa pozostaje ograniczona i nie skaluje
    się z rozmiarem sceny.

    Zawężenie „odczyt okienkowy GeoTIFF" jest świadome: poprzednia wersja formułowała tezę
    bez tego warunku, czyli szerzej niż potwierdzał ją jedyny substrat. ZMIERZONE zostało
    przy tym, że ograniczenie pamięci utrzymuje się także na ścieżce JP2 przy oknie
    viewportu (Δ RSS rzędu pojedynczych MB) — liczby 2,7–3,6 GiB z planu decyzyjnego JP2
    dotyczą innej operacji i NIE zostały tutaj odtworzone. Ramię JP2/COG nie ma progu:
    jest raportem różnicy między ścieżkami odczytu, nie bramką.
"""

from __future__ import annotations

import csv
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv
from _common.geom import percentile

K_REPS = int(os.environ.get("V07_REPS", "3"))
SAMPLE_TILES = int(os.environ.get("V07_SAMPLE_TILES", "60"))
TILE_SIZE = int(os.environ.get("V07_TILE_SIZE", "1024"))
BUFFER = int(os.environ.get("V07_BUFFER", "200"))

#: Ramię JP2/COG. Domyślnie katalog danych ZAINSTALOWANEJ aplikacji — tam leżą realne
#: projekty z opublikowanymi derywatami. Ramię jest opcjonalne: bez dostępnego derywatu
#: dowód raportuje `na` z powodem i mierzy dalej samo ramię publiczne.
JP2_DATA_DIR = os.environ.get("V07_JP2_DATA_DIR", "")
JP2_TILES = int(os.environ.get("V07_JP2_TILES", "3"))
#: Okno odczytu 1× w pikselach źródła — rzędem wielkości okno jednego kafla na najwyższym
#: poziomie.
JP2_WINDOW = 512


def _find_published_derivative(data_dir: Path):
    """Największy opublikowany derywat 1× w katalogu danych, albo `None`.

    Dowód KONSUMUJE gotowy derywat — nie buduje go. Przygotowanie jednej sceny to
    19,3–19,6 min (JP2_FULL_RESOLUTION_DECISION_PLAN §16.3), więc budowa w środku dowodu
    zamieniłaby pomiar odczytu w pomiar budowy. Ten sam wzorzec, którym `v06` konsumuje
    ukończony przebieg treningu.
    """
    projects = data_dir / "projects"
    if not projects.is_dir():
        return None
    best = None
    for cog in projects.glob("*/derived_scenes/*/*/fullres/fullres.tif"):
        try:
            size = cog.stat().st_size
        except OSError:
            continue
        if best is None or size > best[0]:
            best = (size, cog)
    return best[1] if best else None


def _source_raster(project: Path, manifest: dict) -> Path | None:
    """Ścieżka rastra źródłowego z manifestu (storage `project` albo `source`)."""
    ref = (manifest.get("working_view") or {}).get("raster_ref") or {}
    relative = ref.get("relative_path")
    if not relative:
        return None
    if ref.get("storage") == "project":
        return project / relative
    if ref.get("storage") == "source":
        sources_path = project / "scene_sources.json"
        if not sources_path.is_file():
            return None
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
        for source in sources.get("sources", []):
            if source.get("source_id") == ref.get("source_id"):
                return Path(source.get("root_path", "")) / relative
    return None


def _jp2_arm():
    """Zmierz ODCZYT 1× z dwóch ścieżek tej samej sceny: źródłowy JP2 i opublikowany COG.

    Zwraca `(metryki, "")` albo `(None, powód)`. Wszystko wyłącznie do odczytu.

    Świadomie NIE wołamy `SceneRasterResolver.resolve()`, choć byłaby to droga produktu:
    resolver po drodze synchronizuje stan piramid i potrafi ZAPISAĆ `scene.json`. To są
    realne dane użytkownika, a dowód wydajnościowy nie ma prawa ich zmienić. Ścieżki
    składamy więc z manifestu, a fakt publikacji weryfikuje `published_fullres_cog()`,
    która jest czysto odczytowa.
    """
    import psutil
    import rasterio
    from rasterio.windows import Window

    data_dir = Path(JP2_DATA_DIR) if JP2_DATA_DIR else (
        Path(os.environ.get("APPDATA", "")) / "GeoTileLabel" / "data"
    )
    cog = _find_published_derivative(data_dir)
    if cog is None:
        return None, (
            "brak opublikowanego derywatu 1x w katalogu danych — ramię JP2/COG konsumuje "
            "gotowy derywat, nie buduje go"
        )

    variant_id = cog.parent.parent.name
    scene_id = cog.parent.parent.parent.name
    project = cog.parents[4]
    manifest_path = project / "scenes" / scene_id / "scene_manifest.json"
    if not manifest_path.is_file():
        return None, "derywat bez manifestu sceny"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest.get("source_identity") or {}

    from services.scene_packages.fullres_cog_builder import published_fullres_cog

    published = published_fullres_cog(
        project, scene_id, variant_id,
        source_fingerprint=identity.get("source_scene_fingerprint")
        or identity.get("source_package_fingerprint"),
    )
    if published is None:
        return None, "derywat istnieje, ale nie jest aktywny wg zapisu publikacji"

    source = _source_raster(project, manifest)
    if source is None or not source.is_file():
        return None, "raster źródłowy niedostępny z tej stacji"

    image = manifest.get("image") or {}
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    origin_x, origin_y = max(0, width // 3), max(0, height // 3)
    proc = psutil.Process()

    def measure(path: Path) -> dict:
        """Odczyt ze ŚWIEŻYM UCHWYTEM na kafel — tak zachowuje się serwer kafli, w którym
        każdy kafel to osobne żądanie HTTP bez współdzielonego uchwytu GDAL."""
        base = proc.memory_info().rss
        peak = base
        started = time.perf_counter()
        for index in range(JP2_TILES):
            with rasterio.open(path) as dataset:
                dataset.read(
                    indexes=[1],
                    window=Window(origin_x + index * JP2_WINDOW * 8, origin_y,
                                  JP2_WINDOW, JP2_WINDOW),
                    boundless=True, fill_value=0,
                )
                peak = max(peak, proc.memory_info().rss)
        elapsed = time.perf_counter() - started
        return {
            "ms_per_tile": round(1000.0 * elapsed / JP2_TILES, 1),
            "peak_rss_mb": round(peak / (1024 * 1024), 1),
            "rss_delta_mb": round((peak - base) / (1024 * 1024), 1),
            "bytes": path.stat().st_size,
        }

    source_stats = measure(source)
    cog_stats = measure(published)
    return {
        "provider": str(manifest.get("provider") or "unknown"),
        "scene_wh": [width, height],
        "scene_px": width * height,
        "scene_dtype": str(image.get("dtype") or "-"),
        "window_px": JP2_WINDOW,
        "tiles_sampled": JP2_TILES,
        "source_jp2": source_stats,
        "published_cog": cog_stats,
        "speedup_x": round(source_stats["ms_per_tile"] / max(cog_stats["ms_per_tile"], 1e-6), 1),
        "derived_to_source_ratio": round(cog_stats["bytes"] / max(source_stats["bytes"], 1), 3),
    }, ""


def _largest_scene(project: Path):
    best = None
    for sd in (project / "scenes").iterdir():
        if not sd.is_dir():
            continue
        m = json.loads((sd / "scene_manifest.json").read_text(encoding="utf-8"))
        im = m.get("image") or {}
        px = int(im.get("width") or 0) * int(im.get("height") or 0)
        if best is None or px > best[0]:
            best = (px, sd, m)
    return best


def _working_vrt(project: Path, manifest: dict) -> Path | None:
    ref = (manifest.get("working_view") or {}).get("raster_ref") or {}
    rel = ref.get("relative_path")
    if rel and ref.get("storage") == "project":
        return project / rel
    return None


def main() -> ValidationResult:
    res = ValidationResult(
        id="v07", claim="S3",
        title="Wydajność dla dużej sceny: xView3 XXL oraz odczyt 1x JP2 vs COG",
        substrate=["xView3"], tier="public",
    )
    res.config = {"reps": K_REPS, "sample_tiles": SAMPLE_TILES, "tile_size": TILE_SIZE, "buffer": BUFFER}

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"; res.notes = blocked; return res

    import numpy as np
    import psutil
    import rasterio
    from rasterio.windows import Window
    from services.scene_loader import get_scene_info
    from models.tiling_config import TilingConfig
    from models.preprocessing import PreprocessingProfile
    from services.tiler import compute_grid
    from services.preprocessing_profiles import apply_preprocessing_profile

    try:
        project = appenv.benchmark_project("xView3")
    except FileNotFoundError as exc:
        res.status = "todo"; res.notes = str(exc); return res

    px, scene_dir, manifest = _largest_scene(project)
    image = manifest.get("image") or {}
    width, height = int(image["width"]), int(image["height"])
    vrt = _working_vrt(project, manifest)
    if vrt is None or not vrt.is_file():
        res.status = "todo"; res.notes = "Brak rastra roboczego (VRT) dla sceny xView3."; return res

    profs = json.loads((project / "preprocessing_profiles.json").read_text(encoding="utf-8"))
    plist = profs.get("profiles") if isinstance(profs, dict) else profs
    profile = PreprocessingProfile(**plist[0])
    tiling = TilingConfig(tile_size=TILE_SIZE, buffer=BUFFER)

    proc = psutil.Process()
    baseline_rss = proc.memory_info().rss
    peak_rss = baseline_rss

    def bump_peak():
        nonlocal peak_rss
        peak_rss = max(peak_rss, proc.memory_info().rss)

    ingest_times, tiling_times, per_tile_ms_list = [], [], []
    n_tiles = 0
    render_ok = True
    render_note = ""

    for _ in range(K_REPS):
        t0 = time.perf_counter()
        info = get_scene_info(str(vrt))
        ingest_times.append(time.perf_counter() - t0)
        bump_peak()

        t0 = time.perf_counter()
        preview = compute_grid(width, height, tiling)
        tiling_times.append(time.perf_counter() - t0)
        n_tiles = preview.total_tiles

        # próbka kafli rozłożona po scenie
        rects = preview.tile_rects
        step = max(1, len(rects) // SAMPLE_TILES)
        sample = rects[::step][:SAMPLE_TILES]
        t0 = time.perf_counter()
        with rasterio.open(vrt) as ds:
            band_count = 1 if profile.rgb_conversion == "grayscale_rgb" else min(3, ds.count)
            for (x0, y0, x1, y1) in sample:
                data = ds.read(
                    indexes=list(range(1, band_count + 1)),
                    window=Window(x0, y0, TILE_SIZE, TILE_SIZE),
                    boundless=True, masked=True, fill_value=0,
                )
                mask = np.ma.getmaskarray(data)
                valid = ~np.all(mask, axis=0)
                arr = np.transpose(data.filled(0), (1, 2, 0))
                try:
                    apply_preprocessing_profile(arr, profile, valid)
                except Exception as exc:  # noqa: BLE001
                    render_ok = False
                    render_note = f"{type(exc).__name__}: {str(exc)[:60]}"
                bump_peak()
        elapsed = time.perf_counter() - t0
        per_tile_ms_list.append(1000.0 * elapsed / len(sample))

    ingest_s = statistics.median(ingest_times)
    tiling_s = statistics.median(tiling_times)
    per_tile_ms = statistics.median(per_tile_ms_list)
    est_build_s = per_tile_ms / 1000.0 * n_tiles
    peak_rss_mb = round(peak_rss / (1024 * 1024), 1)
    baseline_mb = round(baseline_rss / (1024 * 1024), 1)

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "perf.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["stage", "median_s", "p95_s"])
        w.writerow(["ingest", f"{ingest_s:.4f}", f"{percentile(ingest_times, 0.95):.4f}"])
        w.writerow(["tiling", f"{tiling_s:.4f}", f"{percentile(tiling_times, 0.95):.4f}"])
        w.writerow(["per_tile_read_render_ms", f"{per_tile_ms:.3f}", f"{percentile(per_tile_ms_list, 0.95):.3f}"])
        w.writerow(["est_full_build", f"{est_build_s:.2f}", ""])

    vm = psutil.virtual_memory()
    hardware = {
        "platform": platform.platform(),
        "cpu_logical": psutil.cpu_count(),
        "ram_total_gb": round(vm.total / (1024 ** 3), 1),
    }
    jp2, jp2_reason = _jp2_arm()

    res.metrics = {
        "scene_px": px,
        "scene_wh": [width, height],
        "scene_dtype": info.dtype,
        "n_tiles": n_tiles,
        "ingest_s": round(ingest_s, 4),
        "tiling_s": round(tiling_s, 4),
        "per_tile_ms": round(per_tile_ms, 3),
        "est_build_s": round(est_build_s, 1),
        "sample_tiles": SAMPLE_TILES,
        "peak_rss_mb": peak_rss_mb,
        "rss_delta_mb": round(peak_rss_mb - baseline_mb, 1),
        "render_ok": render_ok,
        "hardware": hardware,
        "jp2_cog_arm": jp2 or {"status": "na", "reason": jp2_reason},
    }
    res.artifacts = ["results/perf.csv"]
    if jp2:
        # Etykieta z manifestu, nie wpisana na sztywno — auto-wykrycie może trafić na scenę
        # innego dostawcy. Do wyniku idzie sam dostawca, nigdy nazwa projektu ani ścieżka.
        res.substrate = list(res.substrate) + [
            f"provider:{jp2.get('provider') or 'unknown'} (JP2 1x + COG)"
        ]
        res.tier = "mixed"
    # Opisowy: pass gdy etapy przeszły i pamięć pozostała ograniczona (delta ≪ rozmiar sceny w RAM).
    scene_ram_mb = px * 4 / (1024 * 1024)  # gdyby wczytać całość jako float32
    memory_bounded = res.metrics["rss_delta_mb"] < 0.25 * scene_ram_mb
    res.status = "pass" if (render_ok and memory_bounded) else "fail"

    if jp2:
        source_ms = jp2["source_jp2"]["ms_per_tile"]
        cog_ms = jp2["published_cog"]["ms_per_tile"]
        jp2_desc = (
            f"Ramię JP2/COG (reported-only): scena {jp2['scene_wh'][0]}×{jp2['scene_wh'][1]} "
            f"({jp2['scene_px']/1e9:.2f} Gpx, {jp2['scene_dtype']}), okno {jp2['window_px']} px "
            f"ze świeżym uchwytem na kafel. Źródłowy JP2 {source_ms:.0f} ms/kafel wobec "
            f"{cog_ms:.0f} ms/kafel z opublikowanego COG — **{jp2['speedup_x']:.0f}×** różnicy. "
            f"Pamięć w OBU ścieżkach pozostaje ograniczona (Δ RSS "
            f"{jp2['source_jp2']['rss_delta_mb']:.0f} / {jp2['published_cog']['rss_delta_mb']:.0f} MB). "
            f"Derywat zajmuje {jp2['derived_to_source_ratio']:.2f}× rozmiaru źródła."
        )
    else:
        jp2_desc = f"Ramię JP2/COG: na — {jp2_reason}."

    res.notes = (
        f"Ramię publiczne (xView3): scena {width}×{height}px ({px/1e6:.0f} Mpx, {info.dtype}) "
        f"-> {n_tiles} kafli. Ingest {ingest_s*1000:.0f} ms, kaflowanie {tiling_s*1000:.0f} ms, "
        f"odczyt+render {per_tile_ms:.1f} ms/kafel (próbka {SAMPLE_TILES}), "
        f"ekstrapolowany build ≈ {est_build_s:.0f} s. Szczyt RSS {peak_rss_mb:.0f} MB "
        f"(Δ {res.metrics['rss_delta_mb']:.0f} MB ≪ {scene_ram_mb:.0f} MB całej sceny w RAM) — "
        f"przy ODCZYCIE OKIENKOWYM GeoTIFF pamięć nie skaluje się z rozmiarem sceny. "
        f"{jp2_desc} "
        f"Sprzęt: {hardware['cpu_logical']} vCPU, {hardware['ram_total_gb']} GB RAM."
        + (f" [render: {render_note}]" if not render_ok else "")
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
