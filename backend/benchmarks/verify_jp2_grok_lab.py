"""Verify the isolated native Windows JP2Grok laboratory runtime (stage E1).

The verifier is intentionally independent from GeoTile Label's backend.  It
loads only the explicitly supplied laboratory runtime, opens one immutable JP2
through JP2OpenJPEG and JP2Grok, and writes the evidence required by the E1
gate described in ``DESIGN_DECISIONS.md`` (jp2-fullres).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import rasterio
from osgeo import gdal


SCHEMA_NAME = "geotile_jp2_grok_lab_e1"
SCHEMA_VERSION = 1
DRIVERS = ("JP2OpenJPEG", "JP2Grok")
gdal.UseExceptions()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _write_json_atomic(path: Path, value: Any) -> None:
    _write_text_atomic(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
    )


def _sha256_file(path: Path, *, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    try:
        label = str(resolved.relative_to(root.resolve())) if root else str(resolved)
    except ValueError:
        label = str(resolved)
    return {
        "path": label.replace("\\", "/"),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(resolved),
    }


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git_identity(path: Path) -> dict[str, str | None]:
    if not (path / ".git").exists():
        return {"path": str(path), "tag": None, "commit": None}
    tag = _run(["git", "-C", str(path), "describe", "--tags", "--exact-match"], check=False)
    commit = _run(["git", "-C", str(path), "rev-parse", "HEAD"])
    return {
        "path": str(path),
        "tag": tag.stdout.strip() if tag.returncode == 0 else None,
        "commit": commit.stdout.strip(),
    }


def _installed_driver_names() -> list[str]:
    return [gdal.GetDriver(index).ShortName for index in range(gdal.GetDriverCount())]


def _projection_record(dataset: gdal.Dataset) -> dict[str, Any]:
    spatial_reference = dataset.GetSpatialRef()
    return {
        "wkt": spatial_reference.ExportToWkt() if spatial_reference else dataset.GetProjectionRef(),
        "authority_name": spatial_reference.GetAuthorityName(None) if spatial_reference else None,
        "authority_code": spatial_reference.GetAuthorityCode(None) if spatial_reference else None,
    }


def _open_explicitly(source: Path, driver_name: str) -> tuple[dict[str, Any], str]:
    started = time.perf_counter()
    dataset = gdal.OpenEx(
        str(source),
        gdal.OF_RASTER | gdal.OF_READONLY,
        allowed_drivers=[driver_name],
    )
    if dataset is None:
        raise RuntimeError(f"{driver_name} failed to open {source}")
    elapsed_open = time.perf_counter() - started
    actual_driver = dataset.GetDriver().ShortName
    if actual_driver != driver_name:
        raise RuntimeError(
            f"Requested {driver_name}, but GDAL opened the source through {actual_driver}"
        )

    window_size = min(256, dataset.RasterXSize, dataset.RasterYSize)
    x_offset = max(0, (dataset.RasterXSize - window_size) // 2)
    y_offset = max(0, (dataset.RasterYSize - window_size) // 2)
    read_started = time.perf_counter()
    array = dataset.ReadAsArray(x_offset, y_offset, window_size, window_size)
    read_elapsed = time.perf_counter() - read_started
    if array is None:
        raise RuntimeError(f"{driver_name} failed to read the 1x verification window")
    contiguous = np.ascontiguousarray(array)

    bands: list[dict[str, Any]] = []
    for index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(index)
        bands.append(
            {
                "index": index,
                "data_type": gdal.GetDataTypeName(band.DataType),
                "color_interpretation": gdal.GetColorInterpretationName(
                    band.GetColorInterpretation()
                ),
                "block_size": list(band.GetBlockSize()),
                "overview_sizes": [
                    [
                        band.GetOverview(overview).XSize,
                        band.GetOverview(overview).YSize,
                    ]
                    for overview in range(band.GetOverviewCount())
                ],
            }
        )

    result = {
        "requested_driver": driver_name,
        "actual_driver": actual_driver,
        "open_seconds": round(elapsed_open, 6),
        "width": dataset.RasterXSize,
        "height": dataset.RasterYSize,
        "band_count": dataset.RasterCount,
        "geo_transform": list(dataset.GetGeoTransform()),
        "projection": _projection_record(dataset),
        "bands": bands,
        "verification_window": {
            "offset": [x_offset, y_offset],
            "size": [window_size, window_size],
            "source_factor": 1,
            "dtype": str(contiguous.dtype),
            "shape": list(contiguous.shape),
            "sha256": hashlib.sha256(contiguous.tobytes(order="C")).hexdigest(),
            "minimum": float(np.min(contiguous)),
            "maximum": float(np.max(contiguous)),
            "mean": float(np.mean(contiguous)),
            "read_seconds": round(read_elapsed, 6),
        },
    }
    projection_wkt = result["projection"]["wkt"]
    dataset = None
    return result, projection_wkt


def _compare_driver_results(
    first: dict[str, Any],
    second: dict[str, Any],
    first_wkt: str,
    second_wkt: str,
) -> dict[str, Any]:
    # Import from osgeo.osr lazily because some packaged bindings do not expose
    # it as an attribute of the gdal module.
    from osgeo import osr

    srs_a = osr.SpatialReference()
    srs_b = osr.SpatialReference()
    projection_same = False
    if first_wkt and second_wkt:
        srs_a.ImportFromWkt(first_wkt)
        srs_b.ImportFromWkt(second_wkt)
        projection_same = bool(srs_a.IsSame(srs_b))
    elif not first_wkt and not second_wkt:
        projection_same = True

    checks = {
        "dimensions_equal": (first["width"], first["height"])
        == (second["width"], second["height"]),
        "band_count_equal": first["band_count"] == second["band_count"],
        "band_types_equal": [band["data_type"] for band in first["bands"]]
        == [band["data_type"] for band in second["bands"]],
        "geo_transform_equal": bool(
            np.allclose(
                first["geo_transform"],
                second["geo_transform"],
                rtol=0.0,
                atol=1e-12,
            )
        ),
        "projection_equal": projection_same,
        "verification_pixels_equal": first["verification_window"]["sha256"]
        == second["verification_window"]["sha256"],
    }
    return {"checks": checks, "passed": all(checks.values())}


def _parse_dumpbin_dependencies(output: str) -> list[str]:
    collecting = False
    dependencies: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line == "Image has the following dependencies:":
            collecting = True
            continue
        if not collecting:
            continue
        if not line:
            if dependencies:
                break
            continue
        if re.fullmatch(r"[^\\/:*?\"<>|]+\.dll", line, flags=re.IGNORECASE):
            dependencies.append(line)
    return dependencies


def _dependency_graph(
    roots: Iterable[Path],
    *,
    lab_root: Path,
    environment_root: Path,
    dumpbin: Path,
) -> dict[str, Any]:
    dll_lookup: dict[str, list[Path]] = {}
    for directory in (
        environment_root / "bin",
        environment_root / "Library" / "bin",
        environment_root / "Library" / "lib" / "gdalplugins",
    ):
        if directory.exists():
            for dll in directory.glob("*.dll"):
                dll_lookup.setdefault(dll.name.lower(), []).append(dll.resolve())

    queue = [path.resolve() for path in roots]
    visited: set[Path] = set()
    nodes: list[dict[str, Any]] = []
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        result = _run([str(dumpbin), "/dependents", str(path)])
        imports: list[dict[str, Any]] = []
        for name in _parse_dumpbin_dependencies(result.stdout):
            candidates = dll_lookup.get(name.lower(), [])
            resolved = candidates[0] if candidates else None
            imports.append(
                {
                    "name": name,
                    "resolved_lab_path": (
                        str(resolved.relative_to(lab_root)).replace("\\", "/")
                        if resolved and resolved.is_relative_to(lab_root)
                        else None
                    ),
                    "scope": "lab" if resolved else "windows_system_or_unresolved",
                }
            )
            if resolved and resolved not in visited:
                queue.append(resolved)
        nodes.append(
            {
                **_file_record(path, root=lab_root),
                "imports": imports,
            }
        )
    return {"nodes": nodes, "node_count": len(nodes)}


def _manifest_dlls(manifests: Iterable[Path], lab_root: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        if not manifest.exists():
            continue
        for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
            candidate = Path(line.strip())
            if candidate.suffix.lower() != ".dll" or not candidate.exists():
                continue
            record = _file_record(candidate, root=lab_root)
            records[record["path"]] = record
    return [records[key] for key in sorted(records)]


def _directory_size(path: Path) -> dict[str, int]:
    total = 0
    files = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            try:
                total += candidate.stat().st_size
                files += 1
            except OSError:
                continue
    return {"file_count": files, "size_bytes": total}


def _production_snapshot(runtime: Path) -> dict[str, Any]:
    candidates = [
        runtime / "python.exe",
        runtime / "Library" / "bin" / "gdal.dll",
        runtime / "Library" / "lib" / "gdalplugins" / "gdal_JP2OpenJPEG.dll",
        runtime / "Library" / "lib" / "gdalplugins" / "gdal_JP2Grok.dll",
        runtime / "conda-meta" / "history",
    ]
    files = {
        str(path.relative_to(runtime)).replace("\\", "/"): (
            _file_record(path, root=runtime) if path.exists() else None
        )
        for path in candidates
    }
    python = runtime / "python.exe"
    runtime_info: dict[str, Any] | None = None
    if python.exists():
        library_bin = runtime / "Library" / "bin"
        process_env = os.environ.copy()
        laboratory_prefix = Path(sys.prefix).resolve()
        inherited_path: list[str] = []
        for entry in process_env.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            try:
                if Path(entry).resolve().is_relative_to(laboratory_prefix):
                    continue
            except OSError:
                pass
            inherited_path.append(entry)
        process_env["PATH"] = os.pathsep.join(
            [str(library_bin), str(runtime), str(runtime / "DLLs"), *inherited_path]
        )
        process_env["GDAL_DATA"] = str(runtime / "Library" / "share" / "gdal")
        process_env["GDAL_DRIVER_PATH"] = str(
            runtime / "Library" / "lib" / "gdalplugins"
        )
        process_env["PYTHONNOUSERSITE"] = "1"
        code = (
            "import importlib.metadata, json, sys; "
            "print(json.dumps({'python': sys.version, "
            "'rasterio': importlib.metadata.version('rasterio')}))"
        )
        result = _run([str(python), "-c", code], env=process_env)
        runtime_info = json.loads(result.stdout.strip().splitlines()[-1])
        gdalinfo = library_bin / "gdalinfo.exe"
        if gdalinfo.exists():
            version_result = _run([str(gdalinfo), "--version"], env=process_env)
            formats_result = _run([str(gdalinfo), "--formats"], env=process_env)
            runtime_info["gdal"] = version_result.stdout.strip()
            runtime_info["jp2_drivers"] = [
                driver
                for driver in DRIVERS
                if re.search(
                    rf"^\s*{re.escape(driver)}\s+-",
                    formats_result.stdout,
                    flags=re.MULTILINE,
                )
            ]
    return {"root": str(runtime), "files": files, "runtime": runtime_info}


def _lock_environment(
    output_directory: Path,
    *,
    conda: Path | None,
    environment_root: Path,
) -> dict[str, Any]:
    commands: dict[str, list[str]] = {
        "pip-freeze.txt": [str(environment_root / "python.exe"), "-m", "pip", "freeze"],
    }
    if conda:
        commands["conda-explicit.txt"] = [
            str(conda),
            "list",
            "--explicit",
            "--prefix",
            str(environment_root),
        ]
        commands["conda-environment.yml"] = [
            str(conda),
            "env",
            "export",
            "--prefix",
            str(environment_root),
        ]
        commands["conda-list.json"] = [
            str(conda),
            "list",
            "--json",
            "--prefix",
            str(environment_root),
        ]
    records: dict[str, Any] = {}
    for filename, command in commands.items():
        result = _run(command)
        path = output_directory / filename
        _write_text_atomic(path, result.stdout)
        records[filename] = _file_record(path, root=output_directory)
    return records


def verify(args: argparse.Namespace) -> int:
    source = args.source.resolve(strict=True)
    output_directory = args.output_dir.resolve()
    lab_root = args.lab_root.resolve(strict=True)
    environment_root = args.environment_root.resolve(strict=True)
    output_directory.mkdir(parents=True, exist_ok=True)

    production_before = (
        _production_snapshot(args.production_runtime.resolve(strict=True))
        if args.production_runtime
        else None
    )

    formats = _run([str(args.gdalinfo), "--formats"])
    _write_text_atomic(output_directory / "gdal-formats.txt", formats.stdout)
    formats_present = {
        driver: bool(re.search(rf"^\s*{re.escape(driver)}\s+-", formats.stdout, re.MULTILINE))
        for driver in DRIVERS
    }
    installed = _installed_driver_names()

    driver_results: dict[str, Any] = {}
    projections: dict[str, str] = {}
    for driver in DRIVERS:
        result, projection = _open_explicitly(source, driver)
        driver_results[driver] = result
        projections[driver] = projection

    parity = _compare_driver_results(
        driver_results[DRIVERS[0]],
        driver_results[DRIVERS[1]],
        projections[DRIVERS[0]],
        projections[DRIVERS[1]],
    )

    with rasterio.open(source) as dataset:
        rasterio_smoke = {
            "version": rasterio.__version__,
            "gdal_version": rasterio.__gdal_version__,
            "actual_default_driver": dataset.driver,
            "width": dataset.width,
            "height": dataset.height,
            "band_count": dataset.count,
            "crs": dataset.crs.to_string() if dataset.crs else None,
        }

    grok_plugin = environment_root / "Library" / "lib" / "gdalplugins" / "gdal_JP2Grok.dll"
    openjpeg_plugin = (
        environment_root / "Library" / "lib" / "gdalplugins" / "gdal_JP2OpenJPEG.dll"
    )
    grok_library = environment_root / "bin" / "grokj2k.dll"
    required_dlls = [grok_plugin, openjpeg_plugin, grok_library]
    for dll in required_dlls:
        if not dll.exists():
            raise FileNotFoundError(f"Required E1 DLL is missing: {dll}")

    install_manifests = [
        lab_root / "build" / "grok" / "install_manifest.txt",
        lab_root / "build" / "gdal-jp2grok" / "install_manifest.txt",
    ]
    additional_dlls = _manifest_dlls(install_manifests, lab_root)
    dependency_graph = _dependency_graph(
        [grok_plugin, openjpeg_plugin, grok_library],
        lab_root=lab_root,
        environment_root=environment_root,
        dumpbin=args.dumpbin,
    )
    dll_manifest = {
        "captured_at": _utc_now(),
        "required_components": [_file_record(path, root=lab_root) for path in required_dlls],
        "additional_dlls_from_build_manifests": additional_dlls,
        "additional_dll_count": len(additional_dlls),
        "additional_dll_size_bytes": sum(item["size_bytes"] for item in additional_dlls),
        "dependency_graph": dependency_graph,
    }
    _write_json_atomic(output_directory / "dll-manifest.json", dll_manifest)

    source_gdal = lab_root / "src" / "gdal-clean"
    source_grok = lab_root / "src" / "grok"
    rasterio_archive = lab_root / "src" / f"rasterio-{rasterio.__version__}.tar.gz"
    rasterio_setup = lab_root / "src" / f"rasterio-{rasterio.__version__}" / "setup.cfg"
    environment = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "captured_at": _utc_now(),
        "host": platform.node(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "gdal_version": gdal.VersionInfo("--version"),
        "rasterio_version": rasterio.__version__,
        "rasterio_gdal_version": rasterio.__gdal_version__,
        "numpy_version": np.__version__,
        "gdal_data": os.environ.get("GDAL_DATA"),
        "gdal_driver_path": os.environ.get("GDAL_DRIVER_PATH"),
        "lab_root": str(lab_root),
        "environment_root": str(environment_root),
        "environment_footprint": _directory_size(environment_root),
        "sources": {
            "gdal": _git_identity(source_gdal),
            "grok": _git_identity(source_grok),
            "rasterio": {
                "version": rasterio.__version__,
                "built_from_source": True,
                "source_archive": (
                    _file_record(rasterio_archive, root=lab_root)
                    if rasterio_archive.exists()
                    else None
                ),
                "build_configuration": (
                    _file_record(rasterio_setup, root=lab_root)
                    if rasterio_setup.exists()
                    else None
                ),
            },
        },
    }
    environment["locks"] = _lock_environment(
        output_directory,
        conda=args.conda,
        environment_root=environment_root,
    )
    conda_list_path = output_directory / "conda-list.json"
    if conda_list_path.exists():
        relevant_names = {
            "gdal",
            "libgdal-core",
            "libgdal-jp2openjpeg",
            "openjpeg",
            "python",
            "numpy",
            "lcms2",
        }
        environment["selected_conda_packages"] = [
            package
            for package in json.loads(conda_list_path.read_text(encoding="utf-8"))
            if package.get("name") in relevant_names
        ]
    _write_json_atomic(output_directory / "environment.json", environment)

    production_after = (
        _production_snapshot(args.production_runtime.resolve(strict=True))
        if args.production_runtime
        else None
    )
    production_check = {
        "before": production_before,
        "after": production_after,
        "unchanged": production_before == production_after,
    }
    _write_json_atomic(output_directory / "production-runtime-check.json", production_check)

    checks = {
        "native_windows": sys.platform == "win32",
        "gdal_at_least_3_13": int(gdal.VersionInfo("VERSION_NUM")) >= 3_130_000,
        "rasterio_uses_same_gdal": rasterio.__gdal_version__
        == gdal.VersionInfo("RELEASE_NAME"),
        "both_formats_listed": all(formats_present.values()),
        "both_drivers_registered": all(driver in installed for driver in DRIVERS),
        "explicit_driver_selection": all(
            driver_results[driver]["actual_driver"] == driver for driver in DRIVERS
        ),
        "metadata_and_pixels_match": parity["passed"],
        "production_runtime_unchanged": production_check["unchanged"],
        "production_has_no_jp2grok": not bool(
            production_after
            and production_after["files"].get(
                "Library/lib/gdalplugins/gdal_JP2Grok.dll"
            )
        ),
        "dll_dependencies_captured": dependency_graph["node_count"] >= 3,
        "environment_locked": bool(environment["locks"]),
    }
    passed = all(checks.values())
    smoke = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "captured_at": _utc_now(),
        "status": "passed" if passed else "failed",
        "source": _file_record(source),
        "formats_present": formats_present,
        "registered_jp2_drivers": [driver for driver in installed if "JP2" in driver],
        "drivers": driver_results,
        "parity": parity,
        "rasterio_smoke": rasterio_smoke,
        "checks": checks,
    }
    _write_json_atomic(output_directory / "smoke.json", smoke)

    status = "DONE" if passed else "FAILED"
    summary = f"""# E1 — isolated GDAL 3.13 + Grok environment

- Status: **{status}**
- Captured: `{smoke['captured_at']}`
- Source: `{source}`
- GDAL: `{environment['gdal_version']}`
- Rasterio: `{rasterio.__version__}` against GDAL `{rasterio.__gdal_version__}`
- Drivers: `{DRIVERS[0]}`, `{DRIVERS[1]}` (both selected explicitly)
- Pixel parity (central 256 × 256 at source factor 1): `{parity['passed']}`
- Production runtime unchanged during verification: `{production_check['unchanged']}`
- Lab environment: `{environment['environment_footprint']['size_bytes']}` bytes
- Additional build-manifest DLLs: `{dll_manifest['additional_dll_count']}` files, `{dll_manifest['additional_dll_size_bytes']}` bytes

## Gate checks

"""
    summary += "\n".join(
        f"- [{'x' if value else ' '}] `{name}`" for name, value in checks.items()
    )
    summary += "\n"
    _write_text_atomic(output_directory / "summary.md", summary)

    print(summary)
    print(f"Evidence: {output_directory}")
    return 0 if passed else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lab-root", type=Path, required=True)
    parser.add_argument("--environment-root", type=Path, required=True)
    parser.add_argument("--gdalinfo", type=Path, required=True)
    parser.add_argument("--dumpbin", type=Path, required=True)
    parser.add_argument("--production-runtime", type=Path)
    parser.add_argument("--conda", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(verify(_parse_args()))
