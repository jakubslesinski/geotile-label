"""Porownanie wariantow pipeline'u pansharpeningu (DESIGN_DECISIONS.md, scene-import P1.4b).

Aktualny pipeline zapisuje pelny, NIESKOMPRESOWANY TIFF posredni w rozdzielczosci
panchromatycznej, buduje jego overviews, a dopiero potem zapisuje COG. Dla dostawy WV2
MUL+PAN oznacza to 1,4 GiB scratchu przy 0,67 GiB wejscia. Roadmapa kaze porownac cztery
warianty i przyjac kandydata WYLACZNIE przy braku regresji jakosci i zasobow.

Mierzone jest wszystko, czego wymaga bramka: wall time per faza, read/write bytes, szczytowy
RSS calego drzewa procesow (pansharpening idzie w podprocesie), szczytowe zajecie scratchu,
rozmiar wyniku oraz zgodnosc geometrii i histogramu z wariantem odniesienia. Rownowaznosc
pikseli sprawdzana jest DOKLADNIE na losowanych oknach pelnej rozdzielczosci — statystyka
zdecymowana nie odroznilaby subtelnej zmiany jadra przetwarzania.

Uruchomienie:

    python backend/benchmarks/benchmark_pansharpen_pipeline.py \\
        --source-root <katalog WV2> --work-dir <scratch>

Sciezki zrodel sa konfiguracja lokalnego srodowiska i nie moga trafic do repozytorium
(sekcja 4.2 roadmapy).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.benchmark_scene_import import (  # noqa: E402
    ProcessTreeResourceSampler,
    _delta,
    _io_snapshot,
)
from benchmarks.common import add_common_arguments, configure_environment, output_path  # noqa: E402

OPERATION = "pansharpen_pipeline"

#: Wagi wariantu odniesienia — dokladnie te, ktorych uzywa `prepare_pansharpened_cog()`.
WEIGHTS = (1 / 3, 1 / 3, 1 / 3)
#: Poziomy piramidy budowane przez wariant biezacy.
OVERVIEW_FACTORS = (2, 4, 8, 16, 32)
#: Ile okien pelnej rozdzielczosci porownujemy piksel po pikselu.
EQUIVALENCE_WINDOWS = 8
EQUIVALENCE_WINDOW_SIZE = 512
#: Limit cache GDAL przy pojedynczym translate. Dobrany pomiarem: bez limitu szczytowy RSS
#: rosl do 2,4 GiB, z limitem spada do 0,57 GiB bez straty czasu.
COG_CACHE_BYTES = 256 * 1024 * 1024


class ScratchSampler:
    """Probkuje sumaryczny rozmiar plikow w katalogu roboczym.

    Szczytowy scratch jest osobnym budzetem od RAM: aktualny pipeline trzyma jednoczesnie
    nieskompresowany TIFF posredni i wynikowy COG, wiec chwilowe zajecie dysku jest wieksze
    niz jakikolwiek plik koncowy.
    """

    def __init__(self, directory: Path, interval_seconds: float = 0.5) -> None:
        self.directory = directory
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_bytes = 0
        self.baseline_bytes = 0

    def _measure(self) -> None:
        total = 0
        try:
            for path in self.directory.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            return
        self.peak_bytes = max(self.peak_bytes, total)

    @property
    def peak_delta_bytes(self) -> int:
        """Scratch DOLOZONY przez ten wariant.

        Katalog roboczy zawiera wyniki wczesniejszych wariantow (sa potrzebne do porownania
        pikseli), wiec bezwzgledny szczyt rosnie monotonicznie i nie mowi nic o koszcie
        pojedynczego wariantu.
        """
        return max(0, self.peak_bytes - self.baseline_bytes)

    def __enter__(self) -> "ScratchSampler":
        self._measure()
        self.baseline_bytes = self.peak_bytes
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._measure()

    def __exit__(self, *_exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._measure()
        return False


def _run_pansharpen(pan: Path, spectral: Path, target: Path, bands: int, extra: list[str]) -> None:
    command = [
        sys.executable, "-m", "osgeo_utils.gdal_pansharpen",
        *extra,
        str(pan), str(spectral), str(target),
    ]
    for weight in WEIGHTS:
        command.extend(["-w", str(weight)])
    for band in range(1, bands + 1):
        command.extend(["-b", str(band)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"gdal_pansharpen failed: {(result.stderr or result.stdout).strip()}")


def _translate_cog(source: str, target: Path, creation_options: list[str]) -> None:
    from osgeo import gdal

    gdal.UseExceptions()
    dataset = gdal.Translate(str(target), source, format="COG", creationOptions=creation_options)
    if dataset is None:
        raise RuntimeError("GDAL COG translation failed")
    dataset.FlushCache()


def _build_overviews(path: Path) -> None:
    from osgeo import gdal

    gdal.UseExceptions()
    handle = gdal.Open(str(path), gdal.GA_Update)
    if handle is None:
        raise RuntimeError("GDAL could not reopen the pansharpened intermediate")
    handle.BuildOverviews("AVERAGE", list(OVERVIEW_FACTORS))
    handle = None


def variant_current(context: dict[str, Any]) -> dict[str, Any]:
    """Wariant 1 — dokladnie to, co robi dzis `prepare_pansharpened_cog()`."""
    work, bands = context["work"], context["bands"]
    intermediate = work / f"current_intermediate{context['suffix']}.tif"
    target = work / f"current{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], intermediate, bands, [])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = intermediate.stat().st_size

    started = time.perf_counter()
    _build_overviews(intermediate)
    phases["overviews"] = time.perf_counter() - started

    started = time.perf_counter()
    _translate_cog(str(intermediate), target, [
        "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "OVERVIEWS=FORCE_USE_EXISTING",
    ])
    phases["cog"] = time.perf_counter() - started
    intermediate.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "subprocess + between phases"}


def variant_compressed_intermediate(context: dict[str, Any]) -> dict[str, Any]:
    """Wariant 2 — ten sam przebieg, ale posredni TIFF jest skompresowany od pierwszego etapu."""
    work, bands = context["work"], context["bands"]
    intermediate = work / f"compressed_intermediate{context['suffix']}.tif"
    target = work / f"compressed{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], intermediate, bands, [
        "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES", "-co", "BIGTIFF=IF_SAFER",
    ])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = intermediate.stat().st_size

    started = time.perf_counter()
    _build_overviews(intermediate)
    phases["overviews"] = time.perf_counter() - started

    started = time.perf_counter()
    _translate_cog(str(intermediate), target, [
        "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "OVERVIEWS=FORCE_USE_EXISTING",
    ])
    phases["cog"] = time.perf_counter() - started
    intermediate.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "subprocess + between phases"}


def variant_vrt_single_translate(context: dict[str, Any]) -> dict[str, Any]:
    """Wariant 3 — pansharpened VRT i JEDEN translate do COG; zaden raster posredni nie powstaje."""
    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened{context['suffix']}.vrt"
    target = work / f"vrt{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, ["-of", "VRT"])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    started = time.perf_counter()
    _translate_cog(str(vrt), target, ["BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
    phases["cog"] = time.perf_counter() - started
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


def variant_vrt_threaded(context: dict[str, Any]) -> dict[str, Any]:
    """Wariant 4 — jak 3, ale pansharpening i kompresja korzystaja z wielu watkow."""
    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened_threaded{context['suffix']}.vrt"
    target = work / f"vrt_threaded{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, [
        "-of", "VRT", "-threads", "ALL_CPUS",
    ])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    started = time.perf_counter()
    _translate_cog(str(vrt), target, [
        "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS",
    ])
    phases["cog"] = time.perf_counter() - started
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


def variant_vrt_threaded_pansharpen_only(context: dict[str, Any]) -> dict[str, Any]:
    """Rozbiór wariantu 4: watki TYLKO w pansharpeningu, zapis COG jednowatkowy.

    Wariant 4 daje inne piksele niz odniesienie. Te dwie proby izoluja przyczyne: kompresja
    DEFLATE jest bezstratna, wiec `NUM_THREADS` przy zapisie nie ma prawa zmienic wartosci —
    podejrzanym jest podzial na kawalki w wielowatkowym pansharpeningu.
    """
    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened_thr_ps{context['suffix']}.vrt"
    target = work / f"vrt_thr_ps{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, ["-of", "VRT", "-threads", "ALL_CPUS"])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    started = time.perf_counter()
    _translate_cog(str(vrt), target, ["BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
    phases["cog"] = time.perf_counter() - started
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


def variant_vrt_threaded_cog_only(context: dict[str, Any]) -> dict[str, Any]:
    """Rozbiór wariantu 4: pansharpening jednowatkowy, watki TYLKO przy zapisie COG."""
    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened_thr_cog{context['suffix']}.vrt"
    target = work / f"vrt_thr_cog{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, ["-of", "VRT"])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    started = time.perf_counter()
    _translate_cog(str(vrt), target, [
        "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS",
    ])
    phases["cog"] = time.perf_counter() - started
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


def variant_vrt_capped_cache(context: dict[str, Any]) -> dict[str, Any]:
    """Wariant 3 z OGRANICZONYM cache GDAL — sprawdza, czy da sie zdjac narzut RAM.

    Pojedynczy translate czyta pansharpened VRT, wiec bufory GDAL rosna razem z domyslnym
    `GDAL_CACHEMAX`. Bramka zabrania istotnego wzrostu RAM, wiec limit jest tu zmienna
    eksperymentu, a nie ustawieniem przyjetym z gory.
    """
    from osgeo import gdal

    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened_capped{context['suffix']}.vrt"
    target = work / f"vrt_capped{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, ["-of", "VRT", "-threads", "ALL_CPUS"])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    previous = gdal.GetCacheMax()
    gdal.SetCacheMax(256 * 1024 * 1024)
    try:
        started = time.perf_counter()
        _translate_cog(str(vrt), target, [
            "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS",
        ])
        phases["cog"] = time.perf_counter() - started
    finally:
        gdal.SetCacheMax(previous)
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


def variant_vrt_recommended(context: dict[str, Any]) -> dict[str, Any]:
    """Kandydat zlozony z tego, co wygral w rozbiorze wariantow 3 i 4.

    Pansharpening JEDNOWATKOWY (wielowatkowy zmienia piksele przy granicach kawalkow),
    zapis COG wielowatkowy (kompresja DEFLATE jest bezstratna, wiec watki nie zmieniaja
    wartosci) i ograniczony `GDAL_CACHEMAX`, zeby jeden translate nie podnosil szczytowego RAM.
    """
    from osgeo import gdal

    work, bands = context["work"], context["bands"]
    vrt = work / f"pansharpened_recommended{context['suffix']}.vrt"
    target = work / f"vrt_recommended{context['suffix']}.cog.tif"
    phases: dict[str, float] = {}

    started = time.perf_counter()
    _run_pansharpen(context["pan"], context["spectral"], vrt, bands, ["-of", "VRT"])
    phases["pansharpen"] = time.perf_counter() - started
    phases["intermediate_bytes"] = vrt.stat().st_size

    previous = gdal.GetCacheMax()
    gdal.SetCacheMax(COG_CACHE_BYTES)
    try:
        started = time.perf_counter()
        _translate_cog(str(vrt), target, [
            "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS",
        ])
        phases["cog"] = time.perf_counter() - started
    finally:
        gdal.SetCacheMax(previous)
    vrt.unlink(missing_ok=True)
    return {"target": target, "phases": phases, "cancellable": "GDAL progress callback"}


VARIANTS = {
    "vrt_recommended": variant_vrt_recommended,
    "current_intermediate_tif": variant_current,
    "compressed_intermediate": variant_compressed_intermediate,
    "vrt_single_translate": variant_vrt_single_translate,
    "vrt_threaded": variant_vrt_threaded,
    "vrt_threaded_pansharpen_only": variant_vrt_threaded_pansharpen_only,
    "vrt_threaded_cog_only": variant_vrt_threaded_cog_only,
    "vrt_capped_cache": variant_vrt_capped_cache,
}
#: Warianty uruchamiane domyslnie — cztery z zakresu P1.4b. Pozostale sa diagnostyczne.
DEFAULT_VARIANTS = (
    "current_intermediate_tif",
    "compressed_intermediate",
    "vrt_single_translate",
    "vrt_threaded",
)


def _describe_output(path: Path) -> dict[str, Any]:
    import rasterio

    with rasterio.open(path) as src:
        return {
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "crs": str(src.crs),
            "transform": [float(value) for value in src.transform[:6]],
            "bounds": [float(value) for value in src.bounds],
            "overview_levels": list(src.overviews(1)),
            "file_bytes": path.stat().st_size,
        }


def _decimated_statistics(path: Path) -> list[dict[str, float]]:
    import numpy as np
    import rasterio

    stats: list[dict[str, float]] = []
    with rasterio.open(path) as src:
        scale = max(1, max(src.width, src.height) // 1024)
        shape = (max(1, src.height // scale), max(1, src.width // scale))
        for band in range(1, src.count + 1):
            data = src.read(band, out_shape=shape).astype("float64")
            stats.append({
                "min": float(np.min(data)),
                "max": float(np.max(data)),
                "mean": round(float(np.mean(data)), 6),
                "std": round(float(np.std(data)), 6),
            })
    return stats


def _identical_pixels(reference: Path, candidate: Path, seed: int = 20260831) -> dict[str, Any]:
    """Porownaj DOKLADNIE losowane okna pelnej rozdzielczosci."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    generator = np.random.default_rng(seed)
    mismatched = 0
    max_abs_difference = 0.0
    with rasterio.open(reference) as left, rasterio.open(candidate) as right:
        if (left.width, left.height, left.count) != (right.width, right.height, right.count):
            return {"comparable": False, "reason": "different raster shape"}
        for _ in range(EQUIVALENCE_WINDOWS):
            col = int(generator.integers(0, max(1, left.width - EQUIVALENCE_WINDOW_SIZE)))
            row = int(generator.integers(0, max(1, left.height - EQUIVALENCE_WINDOW_SIZE)))
            window = Window(col, row, min(EQUIVALENCE_WINDOW_SIZE, left.width),
                            min(EQUIVALENCE_WINDOW_SIZE, left.height))
            first = left.read(window=window).astype("int64")
            second = right.read(window=window).astype("int64")
            difference = np.abs(first - second)
            if difference.any():
                mismatched += 1
                max_abs_difference = max(max_abs_difference, float(difference.max()))
    return {
        "comparable": True,
        "windows": EQUIVALENCE_WINDOWS,
        "windows_with_differences": mismatched,
        "max_abs_difference": max_abs_difference,
    }


def _prepare_inputs(source_root: Path, work: Path) -> dict[str, Any]:
    """Zbuduj VRT panchromatyczny i multispektralny dokladnie tak, jak robi to aplikacja."""
    import os

    os.environ.setdefault("GEOTILE_SCENE_PACKAGE_GRAPH_V2", "1")
    from services.scene_packages.resolvers import scan_source
    from services.scene_packages.working_view import create_vrt

    packages, _diagnostics = scan_source(source_root, "worldview")
    for package in packages:
        selection = package.get("selection") or {}
        if selection.get("product_type") != "MUL+PAN":
            continue
        by_id = {asset["asset_id"]: asset for asset in package["assets"]}
        rgb_bands = list(selection.get("rgb_bands") or [])
        if not rgb_bands:
            raise RuntimeError("MUL+PAN selection has no confirmed RGB band mapping")
        ms_paths = [
            source_root / by_id[asset_id]["relative_path"]
            for asset_id in selection["multispectral_asset_ids"] if asset_id in by_id
        ]
        pan_paths = [
            source_root / by_id[asset_id]["relative_path"]
            for asset_id in selection["panchromatic_asset_ids"] if asset_id in by_id
        ]
        project = "benchmark-pansharpen"
        spectral = create_vrt(project, "scene", "variant", ms_paths, "multispectral.vrt", band_list=rgb_bands)
        pan = create_vrt(project, "scene", "variant", pan_paths, "panchromatic.vrt")
        return {
            "pan": pan,
            "spectral": spectral,
            "bands": len(rgb_bands),
            "rgb_bands": rgb_bands,
            "package_root_relative": package["package_root_relative"],
        }
    raise RuntimeError("No MUL+PAN package found in the source root")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--source-root", required=True, help="Katalog zrodla WorldView z dostawa MUL+PAN")
    parser.add_argument("--work-dir", required=True, help="Katalog roboczy na wyniki wariantow")
    parser.add_argument(
        "--variants",
        default=",".join(DEFAULT_VARIANTS),
        help="Lista wariantow do uruchomienia, po przecinku",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Ile razy uruchomic kazdy wariant; powtorzenia sluza sprawdzeniu powtarzalnosci",
    )
    parser.add_argument(
        "--keep-outputs",
        action="store_true",
        help="Nie kasuj wynikowych COG-ow po pomiarze (do ogledzin wizualnych)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_environment(args)

    source_root = Path(args.source_root).expanduser()
    work = Path(args.work_dir).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    inputs = _prepare_inputs(source_root, work)
    context = {"work": work, **inputs}
    selected = [name.strip() for name in str(args.variants).split(",") if name.strip()]

    records: list[dict[str, Any]] = []
    outputs: dict[str, Path] = {}
    runs = [
        (name, run_index)
        for name in selected
        for run_index in range(1, max(1, int(args.repeat)) + 1)
    ]
    for name, run_index in runs:
        runner = VARIANTS.get(name)
        if runner is None:
            raise SystemExit(f"Unknown variant: {name}")
        label = name if run_index == 1 else f"{name}#{run_index}"
        context["suffix"] = "" if run_index == 1 else f"_run{run_index}"
        io_before = _io_snapshot()
        started = time.perf_counter()
        with ScratchSampler(work) as scratch, ProcessTreeResourceSampler() as resources:
            result = runner(context)
        wall = time.perf_counter() - started
        io_after = _io_snapshot()
        target = result["target"]
        outputs[label] = target
        resource_summary = resources.summary()
        records.append({
            "variant": label,
            "pipeline": name,
            "run_index": run_index,
            "wall_seconds": round(wall, 3),
            "phases": {
                key: (round(value, 3) if isinstance(value, float) else value)
                for key, value in result["phases"].items()
            },
            "read_bytes": _delta(io_after["read_bytes"], io_before["read_bytes"]),
            "write_bytes": _delta(io_after["write_bytes"], io_before["write_bytes"]),
            "peak_scratch_bytes": scratch.peak_delta_bytes,
            "peak_tree_rss_bytes": resource_summary.get("aggregate_rss_peak_bytes"),
            "resources": resource_summary,
            "cancellation": result["cancellable"],
            "output": _describe_output(target),
            "band_statistics": _decimated_statistics(target),
        })
        print(
            f"{label}: {wall:.1f}s | scratch {scratch.peak_delta_bytes / 2**30:.2f} GiB | "
            f"wynik {target.stat().st_size / 2**20:.0f} MiB"
        )

    reference_name = records[0]["variant"] if records else None
    first_run_of: dict[str, str] = {}
    for record in records:
        first_run_of.setdefault(record["pipeline"], record["variant"])
    for record in records[1:]:
        record["equivalence_to_reference"] = _identical_pixels(
            outputs[reference_name], outputs[record["variant"]]
        )
        # Powtorzenie porownujemy z PIERWSZYM przebiegiem tego samego wariantu: bramka wymaga
        # POWTARZALNEGO wyniku, a nie tylko zgodnego z odniesieniem.
        sibling = first_run_of[record["pipeline"]]
        if sibling != record["variant"]:
            record["repeatability"] = _identical_pixels(outputs[sibling], outputs[record["variant"]])

    report = {
        "operation": OPERATION,
        "reference_variant": reference_name,
        "inputs": {
            "package_root_relative": inputs["package_root_relative"],
            "rgb_bands": inputs["rgb_bands"],
            "pan": _describe_output(inputs["pan"]),
            "spectral": _describe_output(inputs["spectral"]),
        },
        "variants": records,
    }
    destination = output_path(args, OPERATION)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nraport: {destination}")

    if not args.keep_outputs:
        for target in outputs.values():
            target.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
