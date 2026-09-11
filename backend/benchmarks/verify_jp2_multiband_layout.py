#!/usr/bin/env python3
"""Domkniecie luki wielopasmowej z E4 (DESIGN_DECISIONS.md, jp2-fullres).

Caly potok konstrukcji COG stoi na zalozeniu, ze `opj_decompress -OutFor RAWL` zapisuje
komponenty SEKWENCYJNIE (BSQ), czyli pasmo n zaczyna sie na przesunieciu
`n * width * height * itemsize`. Obie sceny zamrozone w E0 sa jednopasmowe, wiec to
zalozenie jest tam **niefalsyfikowalne** — przy jednym pasmie zgadza sie z definicji.
Bledne zalozenie na pliku wielopasmowym da ciche przestawienie pasm, a nie awarie.

Test jest wykonywany na JEDNYM PASIE, nie na calej scenie: uklad danych jest jednorodny,
wiec pasmo 1 i 2 zgodne na pasie rozstrzygaja pytanie tak samo jak pelny przebieg, a
kosztuja ~2 minuty zamiast ~20.

Kontrola przeciwna jest czescia testu: jesli pasmo alpha okazaloby sie identyczne z
pasmem obrazu albo stale, zgodnosc nie dowodzilaby niczego i test jest raportowany jako
niekonkluzywny.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))

from benchmark_jp2_cog_construction import (  # noqa: E402
    _cog_creation_options,
    _gdal_geotransform,
    _opj_decompress_binary,
    _raw_vrt_xml,
    _source_profile,
)
from benchmark_jp2_fullres_strategies import _pixel_checksum, _write_json_atomic  # noqa: E402


def verify(source: Path, workdir: Path, *, y0: int, rows: int, windows: int) -> dict[str, Any]:
    import numpy as np
    import rasterio
    from osgeo import gdal
    from rasterio.windows import Window

    gdal.UseExceptions()
    workdir.mkdir(parents=True, exist_ok=True)
    profile = _source_profile(source)
    width = profile["width"]
    band_count = profile["band_count"]
    if band_count < 2:
        return {
            "conclusive": False,
            "reason": f"zrodlo ma {band_count} pasmo/pasma — test wymaga wielopasmowego",
            "source": str(source),
        }

    y1 = min(profile["height"], y0 + rows)
    strip_rows = y1 - y0

    raw_path = workdir / "multiband_strip.raw"
    raw_path.unlink(missing_ok=True)
    command = [
        str(_opj_decompress_binary()), "-i", str(source), "-o", str(raw_path),
        "-OutFor", "RAWL", "-d", f"0,{y0},{width},{y1}",
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    decode_seconds = time.perf_counter() - started
    if completed.returncode != 0:
        return {"conclusive": False, "reason": "opj_decompress zwrocil blad",
                "returncode": completed.returncode, "stderr": completed.stderr[-2000:]}

    expected_bytes = width * strip_rows * profile["itemsize"] * band_count
    actual_bytes = raw_path.stat().st_size
    size_matches = actual_bytes == expected_bytes

    # Geotransform pasa: gorna krawedz przesuwa sie o liczbe pominietych wierszy.
    base = _gdal_geotransform(profile["transform"])
    strip_geotransform = list(base)
    strip_geotransform[3] = base[3] + y0 * base[5]

    vrt_path = workdir / "multiband_strip.vrt"
    vrt_path.write_text(
        _raw_vrt_xml(
            raw_path=raw_path, width=width, height=strip_rows, band_count=band_count,
            dtype=profile["gdal_dtype"], itemsize=profile["itemsize"],
            crs_wkt=profile["crs_wkt"], geotransform=strip_geotransform,
        ),
        encoding="utf-8",
    )

    cog_path = workdir / "multiband_strip_cog.tif"
    cog_path.unlink(missing_ok=True)
    started = time.perf_counter()
    dataset = gdal.Translate(
        str(cog_path), str(vrt_path), format="COG",
        creationOptions=_cog_creation_options("ZSTD", 2, "AUTO", blocksize=256),
    )
    dataset = None
    cog_seconds = time.perf_counter() - started

    # Okna rozlozone po szerokosci pasa, z marginesem od krawedzi.
    tile = 512
    step = max(tile, (width - tile) // max(1, windows))
    offsets = [(min(index * step, width - tile), 0) for index in range(windows)]

    per_band: list[dict[str, Any]] = []
    band_distinct = False
    with rasterio.open(source) as origin, rasterio.open(cog_path) as produced:
        for band in range(1, band_count + 1):
            mismatches = []
            for x, _ in offsets:
                expected = origin.read(band, window=Window(x, y0, tile, min(tile, strip_rows)),
                                       masked=False)
                actual = produced.read(band, window=Window(x, 0, tile, min(tile, strip_rows)),
                                       masked=False)
                if _pixel_checksum(expected) != _pixel_checksum(actual):
                    mismatches.append({"band": band, "x": x,
                                       "expected_mean": float(np.mean(expected, dtype=np.float64)),
                                       "actual_mean": float(np.mean(actual, dtype=np.float64))})
            per_band.append({"band": band, "windows": len(offsets),
                             "mismatches": len(mismatches), "detail": mismatches[:3]})
        # Kontrola przeciwna: pasma musza sie od siebie roznic, inaczej zgodnosc
        # nie odroznia poprawnego mapowania od zamienionego.
        probe = Window(offsets[0][0], y0, tile, min(tile, strip_rows))
        first = origin.read(1, window=probe, masked=False)
        second = origin.read(2, window=Window(offsets[0][0], y0, tile, min(tile, strip_rows)),
                             masked=False)
        band_distinct = _pixel_checksum(first) != _pixel_checksum(second)

    all_match = all(item["mismatches"] == 0 for item in per_band)
    return {
        "schema_name": "geotile_jp2_multiband_layout_check",
        "schema_version": 1,
        "source": str(source),
        "band_count": band_count,
        "strip": {"y0": y0, "rows": strip_rows, "width": width},
        "decode_seconds": round(decode_seconds, 2),
        "cog_seconds": round(cog_seconds, 2),
        "raw_bytes_expected": expected_bytes,
        "raw_bytes_actual": actual_bytes,
        "bsq_size_matches": size_matches,
        "per_band": per_band,
        "bands_are_distinct": band_distinct,
        "conclusive": bool(band_distinct and size_matches),
        "passed": bool(all_match and size_matches and band_distinct),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="wielopasmowy plik .jp2")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--y0", type=int, default=8000)
    parser.add_argument("--rows", type=int, default=1024)
    parser.add_argument("--windows", type=int, default=6)
    args = parser.parse_args(argv)

    result = verify(Path(args.source), Path(args.output_dir),
                    y0=args.y0, rows=args.rows, windows=args.windows)
    out = Path(args.output_dir) / "multiband_layout_check.json"
    _write_json_atomic(out, result)

    print(f"zrodlo      : {Path(args.source).name}")
    print(f"pasm        : {result.get('band_count')}")
    if not result.get("conclusive"):
        print(f"NIEKONKLUZYWNY: {result.get('reason') or 'pasma nierozroznialne albo zly rozmiar'}")
        print(f"zapisano: {out}")
        return 2
    print(f"pas         : wiersze {result['strip']['y0']}..{result['strip']['y0'] + result['strip']['rows']}")
    print(f"dekod       : {result['decode_seconds']} s | COG {result['cog_seconds']} s")
    print(f"rozmiar BSQ : {'zgodny' if result['bsq_size_matches'] else 'NIEZGODNY'} "
          f"({result['raw_bytes_actual']} / {result['raw_bytes_expected']})")
    print(f"pasma rozne : {result['bands_are_distinct']}  (kontrola przeciwna)")
    for item in result["per_band"]:
        print(f"  pasmo {item['band']}: {item['windows'] - item['mismatches']}/{item['windows']} okien zgodnych")
    print(f"WYNIK: {'PASS' if result['passed'] else 'FAIL'}")
    print(f"zapisano: {out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
