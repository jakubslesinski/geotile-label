"""E4 — budowa i walidacja pelnorozdzielczego COG z jednokaflowego JP2.

Realizuje etap E4 (DESIGN_DECISIONS.md, jp2-fullres). To NIE jest runner W0-W8:
pomiar runtime (E5) nalezy do `benchmark_jp2_fullres_strategies.py`, zeby ramiona B1
i C1 byly mierzone tym samym kodem (kontrakt porownania z sekcji 6.1). Tutaj mierzymy
wylacznie KONSTRUKCJE derywatu i jego poprawnosc.

Odstepstwo od sekcji 7 planu: plan przewiduje jeden artefakt
`benchmark_jp2_fullres_strategies.py`. E4 dostaje osobny modul, bo powstaje rownolegle
do E1-E3 i nie moze kolidowac z plikiem, ktory jest w tamtej pracy modyfikowany.
Wspolna logika porownania pikseli jest IMPORTOWANA stamtad, nie kopiowana — inaczej
dwie implementacje sha256 mogloby sie rozjechac i uniewaznic bramke E4-C.

Problem konstrukcyjny, ktory ten modul ma rozstrzygnac
-----------------------------------------------------
Zrodlo ma JEDEN kafel codestreamu na caly obraz. GDAL raportuje dla pelnej
rozdzielczosci bloki 1024x1024 i czyta je przez `opj_set_decode_area`, ktore przy
kazdym oknie parsuje naglowki pakietow od poczatku kafla. Zmierzone: ~2064 ms na blok,
niezaleznie od kolejnosci — czyli ~89 min na pelny przebieg dla sceny 60476x43476.
Dekod calosci jednym przebiegiem kosztuje 8,5 min. Ta asymetria, a nie kompresja,
decyduje o tym, czy bramka E4-R (<=10 min) jest osiagalna.

Stad strategie: nie roznia sie kompresja, tylko sposobem WYDOBYCIA pikseli 1x.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))

from benchmark_jp2_fullres_strategies import (  # noqa: E402
    _pixel_checksum,
    _pixel_probes,
    _raster_metadata,
    _write_json_atomic,
)

SCHEMA_NAME = "geotile_jp2_cog_construction"
SCHEMA_VERSION = 1

#: Ten sam limit, ktory ustawia backend aplikacji. Konstrukcja derywatu nie moze
#: dostawac wiekszego budzetu niz produkcja, bo wynik przestalby byc przenosny.
DEFAULT_GDAL_CACHE_MIB = 256

#: Bramka E4-R z planu.
GATE_PREFERRED_SECONDS = 10 * 60
GATE_CONDITIONAL_SECONDS = 20 * 60
GATE_RSS_ABSOLUTE_BYTES = 4 * 1024**3
GATE_RSS_HOST_FRACTION = 0.25
GATE_SIZE_RATIO = 2.0


# --- pomiar zasobow ---------------------------------------------------------


@dataclass
class ResourceTrace:
    """Szczyt RSS calego drzewa procesow, nie tylko naszego.

    Dekod idzie przez `opj_decompress` jako proces potomny, wiec probkowanie
    samego siebie pokazaloby prawie zero i bramka RSS bylaby fikcja.
    """

    peak_rss_bytes: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> "ResourceTrace":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            import psutil
        except Exception:
            return
        process = psutil.Process()
        started = time.perf_counter()
        while not self._stop.wait(1.0):
            total = 0
            try:
                total += process.memory_info().rss
                for child in process.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except Exception:
                        continue
            except Exception:
                continue
            self.peak_rss_bytes = max(self.peak_rss_bytes, total)
            self.samples.append(
                {"t": round(time.perf_counter() - started, 1), "rss_bytes": total}
            )

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)


def _directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


# --- wydobycie pikseli 1x ---------------------------------------------------


def _opj_decompress_binary() -> Path:
    """`opj_decompress` z tego samego runtime, ktory ma sterownik JP2OpenJPEG."""
    candidate = Path(sys.executable).parent / "Library" / "bin" / "opj_decompress.exe"
    if candidate.is_file():
        return candidate
    found = shutil.which("opj_decompress")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "Nie znaleziono opj_decompress; strategie oparte o pelny dekod sa niedostepne"
    )


def _gdal_geotransform(rasterio_transform: list[float]) -> list[float]:
    """Kolejnosc rasterio -> kolejnosc GDAL.

    rasterio zwraca Affine jako (a, b, c, d, e, f) = (xres, xrot, xmin, yrot, yres, ymax).
    Element `<GeoTransform>` w VRT oczekuje (xmin, xres, xrot, ymax, yrot, yres). Obie sa
    szescioelementowymi listami tych samych liczb, wiec pomylka nie wywala sie glosno —
    daje raster z bezbledna trescia i rozjechana georeferencja. Bramka E4-C to zlapala
    (100/100 okien pikselowo zgodnych przy niezgodnej transformacji) i dlatego
    konwersja jest tu jawna, nazwana funkcja, a nie recznym przestawieniem indeksow.
    """
    a, b, c, d, e, f = rasterio_transform[:6]
    return [c, a, b, f, d, e]


def _raw_vrt_xml(
    *,
    raw_path: Path,
    width: int,
    height: int,
    band_count: int,
    dtype: str,
    itemsize: int,
    crs_wkt: str | None,
    geotransform: list[float],
) -> str:
    """VRT nad surowym plikiem po dekodzie.

    `opj_decompress -OutFor RAWL` zapisuje komponenty SEKWENCYJNIE (BSQ), wiec pasmo
    n zaczyna sie na przesunieciu n * width * height * itemsize. Nie ufamy temu na
    slowo: bramka E4-C porownuje sha256 ze 100 okien z referencja A0, wiec bledne
    zalozenie o ukladzie ujawni sie jako niezgodnosc, a nie jako cichy smiec.

    Sciezka MUSI byc relatywna. GDAL od 3.9 odmawia otwarcia `VRTRawRasterBand`
    wskazujacego plik spoza katalogu VRT-a, jesli nie ustawiono
    `GDAL_VRT_RAWRASTERBAND_ALLOWED_SOURCE` — surowy VRT potrafi zaadresowac dowolny
    bajt na dysku, wiec to ograniczenie jest celowe. Trzymamy plik surowy obok VRT-a
    i adresujemy go nazwa, zamiast rozluzniac ustawienie globalne.
    """
    plane = width * height * itemsize
    bands = []
    for index in range(band_count):
        bands.append(
            f"""  <VRTRasterBand dataType="{dtype}" band="{index + 1}" subClass="VRTRawRasterBand">
    <SourceFilename relativeToVRT="1">{raw_path.name}</SourceFilename>
    <ImageOffset>{index * plane}</ImageOffset>
    <PixelOffset>{itemsize}</PixelOffset>
    <LineOffset>{width * itemsize}</LineOffset>
    <ByteOrder>LSB</ByteOrder>
  </VRTRasterBand>"""
        )
    srs = f"  <SRS>{crs_wkt}</SRS>\n" if crs_wkt else ""
    geo = "  <GeoTransform>" + ", ".join(f"{value!r}" for value in geotransform) + "</GeoTransform>\n"
    return (
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        + srs
        + geo
        + "\n".join(bands)
        + "\n</VRTDataset>\n"
    )


def _decode_whole_image(source: Path, raw_path: Path, log: Path) -> dict[str, Any]:
    """Jeden przebieg `opj_decompress` na calym obrazie."""
    binary = _opj_decompress_binary()
    command = [str(binary), "-i", str(source), "-o", str(raw_path), "-OutFor", "RAWL"]
    started = time.perf_counter()
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
    elapsed = time.perf_counter() - started
    return {
        "command": command,
        "returncode": completed.returncode,
        "elapsed_seconds": round(elapsed, 2),
        "raw_bytes": raw_path.stat().st_size if raw_path.exists() else 0,
    }


def _decode_regions(
    source: Path, raw_dir: Path, log: Path, *, width: int, height: int, regions: int
) -> dict[str, Any]:
    """Dekod w poziomych pasach przez `-d x0,y0,x1,y1`.

    Sens tego wariantu jest pamieciowy, nie czasowy: pelny dekod jednokaflowego
    obrazu 2629 Mpx wymaga buforow komponentow rzedu 10 GiB, co samo w sobie lamie
    bramke E4-R (<= 4 GiB). Pas zmniejsza szczyt proporcjonalnie, ale KAZDE
    wywolanie na nowo parsuje naglowki kafla — wiec czas moze wzrosnac. Ten
    kompromis jest wlasnie tym, co E4 ma zmierzyc, a nie zalozyc.
    """
    binary = _opj_decompress_binary()
    stripe = -(-height // regions)
    pieces: list[dict[str, Any]] = []
    started = time.perf_counter()
    with log.open("w", encoding="utf-8", errors="replace") as handle:
        for index in range(regions):
            y0 = index * stripe
            y1 = min(height, y0 + stripe)
            if y0 >= y1:
                break
            target = raw_dir / f"region_{index:02d}.raw"
            command = [
                str(binary),
                "-i", str(source),
                "-o", str(target),
                "-OutFor", "RAWL",
                "-d", f"0,{y0},{width},{y1}",
            ]
            handle.write(f"\n--- region {index}: rows {y0}..{y1} ---\n")
            handle.flush()
            piece_started = time.perf_counter()
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT)
            pieces.append(
                {
                    "index": index,
                    "y0": y0,
                    "y1": y1,
                    "returncode": completed.returncode,
                    "elapsed_seconds": round(time.perf_counter() - piece_started, 2),
                    "raw_bytes": target.stat().st_size if target.exists() else 0,
                }
            )
    return {
        "regions": regions,
        "pieces": pieces,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "failed": [item["index"] for item in pieces if item["returncode"] != 0],
    }


# --- zapis COG --------------------------------------------------------------


#: PREDICTOR ma sens tylko dla kodekow slownikowo-entropijnych. LERC ma wlasny model
#: predykcji, a JXL wlasna transformate — podanie im PREDICTOR jest bledem konfiguracji,
#: a nie drobna optymalizacja.
_PREDICTOR_CODECS = {"LZW", "DEFLATE", "ZSTD"}


def _cog_creation_options(
    compression: str,
    predictor: int | None,
    overviews: str,
    *,
    level: int | None = None,
    max_z_error: float | None = None,
    blocksize: int = 512,
) -> list[str]:
    options = [
        f"COMPRESS={compression}",
        f"BLOCKSIZE={blocksize}",
        "BIGTIFF=YES",
        "NUM_THREADS=ALL_CPUS",
        f"OVERVIEWS={overviews}",
        "RESAMPLING=AVERAGE",
    ]
    if predictor is not None and compression in _PREDICTOR_CODECS:
        options.append(f"PREDICTOR={predictor}")
    if level is not None:
        options.append(f"LEVEL={level}")
    if max_z_error is not None and compression.startswith("LERC"):
        # 0 znaczy bezstratnie. Nie ufamy temu na slowo — bramka E4-C porownuje
        # sha256 stu okien z referencja A0, wiec kodek stratny odpadnie tam, a nie
        # dopiero w produkcji.
        options.append(f"MAX_Z_ERROR={max_z_error}")
    return options


def _write_cog(
    source_dataset: str,
    target: Path,
    *,
    compression: str,
    predictor: int | None,
    overviews: str,
    level: int | None = None,
    max_z_error: float | None = None,
    blocksize: int = 512,
    log_progress: bool = True,
) -> dict[str, Any]:
    """Zapis do COG z publikacja atomowa z `.partial` (sekcja 2.4 planu)."""
    from osgeo import gdal

    gdal.UseExceptions()
    gdal.SetCacheMax(DEFAULT_GDAL_CACHE_MIB * 1024 * 1024)
    partial = target.with_name(f".{target.name}.partial")
    for stale in (partial, partial.with_name(partial.name + ".aux.xml")):
        stale.unlink(missing_ok=True)

    last = {"pct": -1.0}

    def callback(complete: float, _message: str, _data: Any) -> int:
        if log_progress and complete - last["pct"] >= 0.1:
            last["pct"] = complete
            print(f"      COG {complete * 100:5.1f}%", flush=True)
        return 1

    started = time.perf_counter()
    peak_partial = 0
    try:
        dataset = gdal.Translate(
            str(partial),
            source_dataset,
            format="COG",
            creationOptions=_cog_creation_options(
                compression, predictor, overviews,
                level=level, max_z_error=max_z_error, blocksize=blocksize,
            ),
            callback=callback if log_progress else None,
        )
        if dataset is None:
            raise RuntimeError("gdal.Translate nie zwrocil datasetu")
        dataset = None
        peak_partial = partial.stat().st_size
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        partial.with_name(partial.name + ".aux.xml").unlink(missing_ok=True)
        raise
    return {
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "creation_options": _cog_creation_options(
            compression, predictor, overviews,
            level=level, max_z_error=max_z_error, blocksize=blocksize,
        ),
        "peak_partial_bytes": peak_partial,
        "final_bytes": target.stat().st_size,
    }


# --- strategie --------------------------------------------------------------


def _codec_tag(args) -> str:
    """Nazwa wariantu kodeka — musi rozrozniac LEVEL i MAX_Z_ERROR, bo to osobne
    punkty pomiarowe, a nie powtorzenia tego samego."""
    tag = args.compression.lower()
    if getattr(args, "level", None) is not None:
        tag += f"-l{args.level}"
    if getattr(args, "max_z_error", None) is not None:
        tag += f"-z{args.max_z_error:g}"
    if getattr(args, "blocksize", 512) != 512:
        tag += f"-b{args.blocksize}"
    return tag


def _source_profile(source: Path) -> dict[str, Any]:
    import rasterio

    with rasterio.open(source) as dataset:
        meta = _raster_metadata(dataset)
    import numpy as np

    itemsize = np.dtype(meta["dtypes"][0]).itemsize
    meta["itemsize"] = int(itemsize)
    meta["gdal_dtype"] = {"uint16": "UInt16", "uint8": "Byte", "int16": "Int16",
                          "float32": "Float32"}.get(meta["dtypes"][0], "UInt16")
    return meta


def strategy_gdal_direct(
    source: Path, workdir: Path, profile: dict[str, Any], args
) -> dict[str, Any]:
    """A0/A1: `gdal.Translate` prosto z JP2.

    Sciezka blokowa. Mierzymy OGRANICZONE okno i ekstrapolujemy zamiast czekac
    ~89 min: bramka E4-R odrzuca wszystko powyzej 20 min, wiec do decyzji wystarczy
    obronny dolny szacunek, a nie dokladny pomiar czegos, co i tak odpada. Wynik jest
    jawnie oznaczony jako ekstrapolacja.
    """
    from osgeo import gdal

    gdal.UseExceptions()
    gdal.SetCacheMax(DEFAULT_GDAL_CACHE_MIB * 1024 * 1024)
    width, height = profile["width"], profile["height"]
    probe = min(args.probe_size, width, height)
    probe_target = workdir / "direct_probe.tif"
    probe_target.unlink(missing_ok=True)

    started = time.perf_counter()
    dataset = gdal.Translate(
        str(probe_target),
        str(source),
        format="GTiff",
        srcWin=[width // 2 & ~1023, height // 2 & ~1023, probe, probe],
        creationOptions=["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
                         "COMPRESS=ZSTD", "BIGTIFF=YES"],
    )
    dataset = None
    elapsed = time.perf_counter() - started
    probe_target.unlink(missing_ok=True)

    probe_blocks = max(1, (probe // 1024) ** 2)
    total_blocks = (-(-width // 1024)) * (-(-height // 1024))
    return {
        "strategy": "gdal_direct",
        "measured": "bounded_probe_only",
        "probe_window_px": probe,
        "probe_seconds": round(elapsed, 2),
        "probe_blocks": probe_blocks,
        "seconds_per_block": round(elapsed / probe_blocks, 3),
        "total_blocks": total_blocks,
        "extrapolated_seconds": round(elapsed / probe_blocks * total_blocks, 1),
        "extrapolated_minutes": round(elapsed / probe_blocks * total_blocks / 60, 1),
        "note": "ekstrapolacja z ograniczonego okna; pelnego przebiegu nie uruchamiano",
    }


def strategy_opj_whole(
    source: Path, workdir: Path, profile: dict[str, Any], args
) -> dict[str, Any]:
    """Pelny dekod jednym przebiegiem, potem zapis COG z surowego VRT."""
    # Artefakty sa prefiksowane source_id: dwie sceny w jednym katalogu wynikow
    # nadpisywalyby sobie plik surowy i COG.
    tag = args.source_id
    raw_path = workdir / f"{tag}_decoded_whole.raw"
    expected = profile["width"] * profile["height"] * profile["itemsize"] * profile["band_count"]

    # `--reuse-raw` istnieje po to, zeby dalszy etap potoku dalo sie poprawiac bez
    # ponawiania osmiominutowego dekodu. Wynik z ponownym uzyciem NIE jest wynikiem
    # E4-R: nie ma wtedy spojnej koperty wall-time ani szczytu RSS, wiec rekord
    # oznaczamy i bramka zasobow jest pomijana.
    if args.reuse_raw and raw_path.is_file() and raw_path.stat().st_size == expected:
        decode = {
            "reused_existing_raw": True,
            "raw_bytes": raw_path.stat().st_size,
            "returncode": 0,
            "elapsed_seconds": None,
        }
    else:
        raw_path.unlink(missing_ok=True)
        decode = _decode_whole_image(source, raw_path, workdir / f"{tag}_opj_whole.log")
        if decode["returncode"] != 0:
            return {"strategy": "opj_whole", "failed": True, "decode": decode}

    vrt_path = workdir / f"{tag}_decoded_whole.vrt"
    vrt_path.write_text(
        _raw_vrt_xml(
            raw_path=raw_path,
            width=profile["width"],
            height=profile["height"],
            band_count=profile["band_count"],
            dtype=profile["gdal_dtype"],
            itemsize=profile["itemsize"],
            crs_wkt=profile["crs_wkt"],
            geotransform=_gdal_geotransform(profile["transform"]),
        ),
        encoding="utf-8",
    )
    cog_path = workdir / f"{tag}_fullres_whole_{_codec_tag(args)}.tif"
    write = _write_cog(
        str(vrt_path),
        cog_path,
        compression=args.compression,
        predictor=args.predictor,
        overviews=args.overviews,
        level=args.level,
        max_z_error=args.max_z_error,
        blocksize=args.blocksize,
    )
    return {
        "strategy": "opj_whole",
        "decode": decode,
        "raw_bytes_expected": expected,
        "raw_bytes_actual": decode["raw_bytes"],
        "raw_layout_matches_bsq": decode["raw_bytes"] == expected,
        "cog": write,
        "cog_path": str(cog_path),
        "reused_raw": bool(decode.get("reused_existing_raw")),
        "total_seconds": (
            None
            if decode["elapsed_seconds"] is None
            else round(decode["elapsed_seconds"] + write["elapsed_seconds"], 2)
        ),
    }


def strategy_opj_regions(
    source: Path, workdir: Path, profile: dict[str, Any], args
) -> dict[str, Any]:
    """Dekod w pasach — ogranicza szczyt RAM kosztem powtorzonego parsowania."""
    tag = args.source_id
    raw_dir = workdir / f"{tag}_regions{args.regions}"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)
    decode = _decode_regions(
        source,
        raw_dir,
        workdir / f"{tag}_opj_regions{args.regions}.log",
        width=profile["width"],
        height=profile["height"],
        regions=args.regions,
    )
    if decode["failed"]:
        return {"strategy": "opj_regions", "failed": True, "decode": decode}

    from osgeo import gdal

    gdal.UseExceptions()
    base_geotransform = _gdal_geotransform(profile["transform"])
    piece_vrts: list[str] = []
    for piece in decode["pieces"]:
        rows = piece["y1"] - piece["y0"]
        # W kolejnosci GDAL indeks 3 to ymax, a 5 to yres (ujemne dla north-up),
        # wiec gorna krawedz pasa przesuwa sie o liczbe pominietych wierszy.
        piece_geotransform = list(base_geotransform)
        piece_geotransform[3] = base_geotransform[3] + piece["y0"] * base_geotransform[5]
        piece_vrt = raw_dir / f"region_{piece['index']:02d}.vrt"
        piece_vrt.write_text(
            _raw_vrt_xml(
                raw_path=raw_dir / f"region_{piece['index']:02d}.raw",
                width=profile["width"],
                height=rows,
                band_count=profile["band_count"],
                dtype=profile["gdal_dtype"],
                itemsize=profile["itemsize"],
                crs_wkt=profile["crs_wkt"],
                geotransform=piece_geotransform,
            ),
            encoding="utf-8",
        )
        piece_vrts.append(str(piece_vrt))

    mosaic = workdir / f"{tag}_decoded_regions{args.regions}.vrt"
    dataset = gdal.BuildVRT(str(mosaic), piece_vrts)
    if dataset is None:
        return {"strategy": "opj_regions", "failed": True, "decode": decode,
                "error": "BuildVRT nie zlozyl pasow"}
    dataset = None

    cog_path = workdir / f"{tag}_fullres_regions{args.regions}_{_codec_tag(args)}.tif"
    write = _write_cog(
        str(mosaic),
        cog_path,
        compression=args.compression,
        predictor=args.predictor,
        overviews=args.overviews,
        level=args.level,
        max_z_error=args.max_z_error,
        blocksize=args.blocksize,
    )
    return {
        "strategy": "opj_regions",
        "decode": decode,
        "cog": write,
        "cog_path": str(cog_path),
        "total_seconds": round(decode["elapsed_seconds"] + write["elapsed_seconds"], 2),
    }


STRATEGIES = {
    "gdal_direct": strategy_gdal_direct,
    "opj_whole": strategy_opj_whole,
    "opj_regions": strategy_opj_regions,
}


# --- bramka E4-C ------------------------------------------------------------


def validate_against_manifest(
    cog_path: Path, manifest_path: Path, source_id: str, source_path: Path
) -> dict[str, Any]:
    """Bramka poprawnosci E4-C: 100 okien 1x, geometria, overviewy, nienaruszone zrodlo."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from osgeo import gdal

    gdal.UseExceptions()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["sources"] if item["source_id"] == source_id)
    references = {item["window_id"]: item for item in entry["a0_references"]}

    mismatches: list[dict[str, Any]] = []
    checked = 0
    started = time.perf_counter()
    with rasterio.open(cog_path) as dataset:
        actual_meta = _raster_metadata(dataset)
        for window in entry["windows_1x"]:
            reference = references.get(window["id"])
            if reference is None:
                continue
            array = dataset.read(
                1,
                window=Window(window["x"], window["y"], window["width"], window["height"]),
                masked=False,
            )
            checked += 1
            digest = _pixel_checksum(array)
            if digest != reference["sha256"]:
                mismatches.append(
                    {
                        "window_id": window["id"],
                        "expected_sha256": reference["sha256"],
                        "actual_sha256": digest,
                        "expected_mean": reference["mean"],
                        "actual_mean": float(np.mean(array, dtype=np.float64)),
                        "actual_probes": _pixel_probes(array)[:4],
                    }
                )
    elapsed = time.perf_counter() - started

    # Referencja A0 z manifestu obejmuje WYLACZNIE pasmo 1 (`dataset.read(1, ...)`).
    # Dla zrodla wielopasmowego — np. Gray+Alpha — sama bramka manifestu przepuscilaby
    # COG z zamieniona albo przesunieta plaszczyzna alpha, bo pasmo 1 byloby zgodne.
    # Domykamy to porownaniem POZOSTALYCH pasm z bezposrednim odczytem zrodla. To nie
    # jest porownanie z zamrozona referencja i jest tak oznaczone.
    extra_bands: dict[str, Any] = {"checked": False}
    if actual_meta["band_count"] > 1:
        subset = entry["windows_1x"][: min(10, len(entry["windows_1x"]))]
        band_mismatches: list[dict[str, Any]] = []
        with rasterio.open(source_path) as origin, rasterio.open(cog_path) as produced:
            for band in range(2, actual_meta["band_count"] + 1):
                for window in subset:
                    region = Window(window["x"], window["y"], window["width"], window["height"])
                    expected_array = origin.read(band, window=region, masked=False)
                    actual_array = produced.read(band, window=region, masked=False)
                    if _pixel_checksum(expected_array) != _pixel_checksum(actual_array):
                        band_mismatches.append({"band": band, "window_id": window["id"]})
        extra_bands = {
            "checked": True,
            "method": "bezposredni odczyt zrodla, nie zamrozona referencja",
            "bands": list(range(2, actual_meta["band_count"] + 1)),
            "windows_per_band": len(subset),
            "mismatches": band_mismatches[:5],
            "mismatch_count": len(band_mismatches),
        }

    expected_meta = entry["raster"]
    geometry = {
        "width": actual_meta["width"] == expected_meta["width"],
        "height": actual_meta["height"] == expected_meta["height"],
        "band_count": actual_meta["band_count"] == expected_meta["band_count"],
        "dtypes": actual_meta["dtypes"] == expected_meta["dtypes"],
        "nodata": actual_meta["nodata"] == expected_meta["nodata"],
        "transform": [round(v, 12) for v in actual_meta["transform"]]
        == [round(v, 12) for v in expected_meta["transform"]],
        "crs_equivalent": _crs_equivalent(actual_meta["crs_wkt"], expected_meta["crs_wkt"]),
    }

    overviews = actual_meta["native_overviews"]
    validate = _run_cog_validator(cog_path)

    # Zrodlo musi byc nietkniete (sekcja 2.4). Porownujemy rozmiar i mtime; pelny
    # sha256 2,6 GB jest w bramce E0 i nie powtarzamy go przy kazdej strategii.
    source_stat = source_path.stat()
    source_untouched = {
        "size_matches": source_stat.st_size == entry["fingerprint"]["size_bytes"],
        "size_bytes": source_stat.st_size,
        "sidecars_present": sorted(
            item.name for item in source_path.parent.glob(source_path.name + ".*")
        ),
    }

    passed = (
        not mismatches
        and checked == len(entry["windows_1x"])
        and all(geometry.values())
        and bool(overviews)
        and source_untouched["size_matches"]
        and not source_untouched["sidecars_present"]
        and not extra_bands.get("mismatch_count", 0)
    )
    return {
        "gate": "E4-C",
        "passed": passed,
        "windows_checked": checked,
        "windows_expected": len(entry["windows_1x"]),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:5],
        "extra_bands": extra_bands,
        "geometry": geometry,
        "internal_overviews": overviews,
        "cog_validator": validate,
        "source_untouched": source_untouched,
        "elapsed_seconds": round(elapsed, 2),
        "actual_metadata": actual_meta,
    }


def _crs_equivalent(left: str | None, right: str | None) -> bool:
    if left == right:
        return True
    if not left or not right:
        return False
    try:
        from rasterio.crs import CRS

        return CRS.from_wkt(left) == CRS.from_wkt(right)
    except Exception:
        return False


def _run_cog_validator(cog_path: Path) -> dict[str, Any]:
    """Sprawdzenie zgodnosci ze specyfikacja COG, jesli `validate_cloud_optimized_geotiff` jest dostepny."""
    try:
        from osgeo_utils.samples import validate_cloud_optimized_geotiff as validator
    except Exception as error:
        return {"available": False, "reason": str(error)}
    try:
        warnings, errors, details = validator.validate(str(cog_path), full_check=True)
        return {
            "available": True,
            "errors": list(errors),
            "warnings": list(warnings),
            "is_cog": not errors,
            "details": {k: details[k] for k in list(details)[:8]},
        }
    except Exception as error:
        return {"available": True, "error": str(error), "is_cog": False}


def evaluate_resource_gate(
    total_seconds: float, peak_rss_bytes: int, cog_bytes: int, source_bytes: int
) -> dict[str, Any]:
    try:
        import psutil

        host_ram = psutil.virtual_memory().total
    except Exception:
        host_ram = 0
    rss_limit = min(GATE_RSS_ABSOLUTE_BYTES, int(host_ram * GATE_RSS_HOST_FRACTION)) if host_ram else GATE_RSS_ABSOLUTE_BYTES
    if total_seconds <= GATE_PREFERRED_SECONDS:
        time_verdict = "spelniona"
    elif total_seconds <= GATE_CONDITIONAL_SECONDS:
        time_verdict = "warunkowa"
    else:
        time_verdict = "niespelniona"
    return {
        "gate": "E4-R",
        "total_seconds": round(total_seconds, 1),
        "total_minutes": round(total_seconds / 60, 2),
        "time_verdict": time_verdict,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_gib": round(peak_rss_bytes / 1024**3, 2),
        "rss_limit_bytes": rss_limit,
        "rss_within_limit": peak_rss_bytes <= rss_limit if peak_rss_bytes else None,
        "cog_bytes": cog_bytes,
        "source_bytes": source_bytes,
        "size_ratio": round(cog_bytes / source_bytes, 3) if source_bytes else None,
        "size_within_limit": (cog_bytes / source_bytes) <= GATE_SIZE_RATIO if source_bytes else None,
    }


# --- CLI --------------------------------------------------------------------


def command_build(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    entry = next(item for item in manifest["sources"] if item["source_id"] == args.source_id)
    source = Path(entry["path"])
    if not source.is_file():
        print(f"BLAD: zrodlo nie istnieje: {source}", file=sys.stderr)
        return 1

    workdir = Path(args.output_dir)
    workdir.mkdir(parents=True, exist_ok=True)

    profile = _source_profile(source)
    free_before = shutil.disk_usage(workdir).free
    # Preflight: surowy dekod plus COG plus zapas. Bez tego brak miejsca ujawnilby
    # sie po 8 minutach dekodu, z polowa artefaktu na dysku.
    required = profile["width"] * profile["height"] * profile["itemsize"] * profile["band_count"] * 2
    if free_before < required:
        print(f"BLAD: za malo miejsca. Wymagane ~{required / 1024**3:.1f} GiB, "
              f"wolne {free_before / 1024**3:.1f} GiB", file=sys.stderr)
        return 1

    print(f"scena   : {source.name}")
    print(f"raster  : {profile['width']}x{profile['height']} x{profile['band_count']} {profile['dtypes'][0]}")
    print(f"strategia: {args.strategy}  kompresja={args.compression} predictor={args.predictor} overviews={args.overviews}")
    print(f"wolne   : {free_before / 1024**3:.1f} GiB")

    started = time.perf_counter()
    with ResourceTrace() as trace:
        result = STRATEGIES[args.strategy](source, workdir, profile, args)
    total_seconds = time.perf_counter() - started

    record: dict[str, Any] = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "stage": "E4",
        "manifest_id": manifest.get("manifest_id"),
        "source_id": args.source_id,
        "source_profile": profile,
        "result": result,
        "wall_seconds": round(total_seconds, 2),
        "peak_rss_bytes": trace.peak_rss_bytes,
        "resource_samples": trace.samples,
        "free_bytes_before": free_before,
        "free_bytes_after": shutil.disk_usage(workdir).free,
    }

    cog_path_value = result.get("cog_path")
    if cog_path_value and Path(cog_path_value).is_file():
        cog_path = Path(cog_path_value)
        if result.get("reused_raw"):
            record["resource_gate"] = {
                "gate": "E4-R",
                "skipped": "wynik z --reuse-raw: brak spojnej koperty czasu i RSS",
            }
        else:
            record["resource_gate"] = evaluate_resource_gate(
                total_seconds, trace.peak_rss_bytes, cog_path.stat().st_size,
                entry["fingerprint"]["size_bytes"],
            )
        if not args.skip_validate:
            print("\n=== bramka E4-C ===")
            record["correctness_gate"] = validate_against_manifest(
                cog_path, Path(args.manifest), args.source_id, source
            )
            gate = record["correctness_gate"]
            print(f"  okna zgodne : {gate['windows_checked'] - gate['mismatch_count']}"
                  f"/{gate['windows_expected']}")
            print(f"  geometria   : {gate['geometry']}")
            print(f"  overviewy   : {gate['internal_overviews']}")
            print(f"  walidator   : {gate['cog_validator'].get('is_cog')}")
            print(f"  WYNIK E4-C  : {'PASS' if gate['passed'] else 'FAIL'}")

    # Liczba pasow wchodzi w nazwe: strategia+kompresja nie jest kluczem unikalnym,
    # bo `opj_regions` z roznym `--regions` to rozne punkty pomiarowe, a nie powtorzenia.
    variant = f"{args.strategy}{args.regions}" if args.strategy == "opj_regions" else args.strategy
    codec = args.compression.lower()
    if args.level is not None:
        codec += f"-l{args.level}"
    if args.max_z_error is not None:
        codec += f"-z{args.max_z_error:g}"
    if args.blocksize != 512:
        codec += f"-b{args.blocksize}"
    out = workdir / f"e4_{args.source_id}_{variant}_{codec}.json"
    _write_json_atomic(out, record)
    print(f"\nzapisano: {out}")

    gate = record.get("resource_gate")
    if gate and "skipped" in gate:
        print(f"E4-R: pominieta ({gate['skipped']})")
    elif gate:
        print(f"E4-R: {gate['total_minutes']} min ({gate['time_verdict']}), "
              f"peak RSS {gate['peak_rss_gib']} GiB, rozmiar x{gate['size_ratio']}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    entry = next(item for item in manifest["sources"] if item["source_id"] == args.source_id)
    result = validate_against_manifest(
        Path(args.cog), Path(args.manifest), args.source_id, Path(entry["path"])
    )
    out = Path(args.cog).with_suffix(".correctness.json")
    _write_json_atomic(out, result)
    print(json.dumps({k: v for k, v in result.items() if k != "actual_metadata"},
                     indent=2, ensure_ascii=False)[:3000])
    print(f"\nzapisano: {out}")
    return 0 if result["passed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="zbuduj COG wybrana strategia i zmierz bramki")
    build.add_argument("--manifest", required=True)
    build.add_argument("--source-id", default="primary_arsenyev")
    build.add_argument("--output-dir", required=True)
    build.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    build.add_argument("--compression", default="ZSTD",
                       choices=["ZSTD", "DEFLATE", "LZW", "LZMA",
                                "LERC", "LERC_ZSTD", "LERC_DEFLATE", "JXL"])
    build.add_argument("--level", type=int, default=None,
                       help="LEVEL dla DEFLATE/ZSTD/LZMA/LERC_*; domyslnie ustawienie GDAL")
    build.add_argument("--max-z-error", type=float, default=None,
                       help="MAX_Z_ERROR dla LERC_*; 0 = bezstratnie")
    build.add_argument("--blocksize", type=int, default=512)
    build.add_argument("--predictor", type=int, default=2)
    build.add_argument("--overviews", default="AUTO",
                       choices=["AUTO", "IGNORE_EXISTING", "FORCE_USE_EXISTING", "NONE"])
    build.add_argument("--regions", type=int, default=4)
    build.add_argument("--probe-size", type=int, default=8192)
    build.add_argument("--skip-validate", action="store_true")
    build.add_argument("--reuse-raw", action="store_true",
                       help="uzyj istniejacego pliku surowego; wynik NIE liczy sie do E4-R")
    build.set_defaults(func=command_build)

    validate = sub.add_parser("validate", help="uruchom sama bramke E4-C na gotowym COG")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--source-id", default="primary_arsenyev")
    validate.add_argument("--cog", required=True)
    validate.set_defaults(func=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
