"""Creation and locking of project-local raster working views."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from db.storage import list_project_ids, load_scene_json, project_dir, save_scene_json
from services.scene_packages.base import canonical_hash
from services.scene_overviews import inspect_source_overviews, source_overviews_are_display_ready
from services.scene_packages import mosaics
from services.scene_sources import resolve_source_asset


def build_working_variant_definition(selection: dict[str, Any]) -> dict[str, Any]:
    """Canonical algorithm/options declaration used by IDs and lineage."""

    asset_ids = list(selection.get("asset_ids") or [])
    raster_kind = selection.get("raster_kind") or (
        "derived"
        if selection.get("product_type") in {"MUL+PAN", "MS-FS_RGB+PAN"}
        else "direct"
        if len(asset_ids) == 1
        else "virtual_mosaic"
    )
    if raster_kind == "derived":
        method = "weighted_brovey"
        inputs: Any = {
            "multispectral_asset_ids": list(selection.get("multispectral_asset_ids") or []),
            "panchromatic_asset_ids": list(selection.get("panchromatic_asset_ids") or []),
        }
        options = {
            "weights": [1 / 3, 1 / 3, 1 / 3],
            "rgb_bands": list(selection.get("rgb_bands") or []),
        }
    elif raster_kind == "virtual_mosaic":
        method = "gdal_build_vrt"
        inputs = {"asset_ids": asset_ids}
        options = {"mosaic_parts_order": list(selection.get("mosaic_parts_order") or [])}
    else:
        method = "direct_reference"
        inputs = {"asset_ids": asset_ids}
        options = {}
    return {
        "schema_version": 1,
        "raster_kind": raster_kind,
        "method": method,
        "product_type": selection.get("product_type"),
        "inputs": inputs,
        "options": options,
    }


def working_variant_id(definition: dict[str, Any]) -> str:
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "variant_" + hashlib.sha256(encoded).hexdigest()[:16]


def working_grid_uid(info: dict[str, Any], variant_id: str | None, parameters: dict[str, Any] | None = None) -> str:
    payload = {
        "width": info.get("width"),
        "height": info.get("height"),
        "crs": info.get("crs"),
        "transform": info.get("transform"),
        "channels": info.get("channels"),
        "variant_id": variant_id,
        "parameters": parameters or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"grid-sha256:{hashlib.sha256(encoded).hexdigest()}"


def lock_working_view(project_id: str, scene_id: str, reason: str) -> None:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    if not manifest:
        return
    working = manifest.get("working_view") or {}
    manifest["working_view"] = working
    if working.get("locked"):
        return
    working.update({
        "locked": True,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "lock_reason": reason,
    })
    save_scene_json(project_id, scene_id, "scene_manifest", manifest)
    scene = load_scene_json(project_id, scene_id, "scene", default={})
    scene["working_asset_locked"] = True
    save_scene_json(project_id, scene_id, "scene", scene)


def cleanup_partial_scene_products() -> int:
    removed = 0
    for project_id in list_project_ids():
        derived = project_dir(project_id) / "derived_scenes"
        if not derived.is_dir():
            continue
        for path in derived.rglob("*"):
            if path.is_file() and ".partial" in path.name:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def create_vrt(
    project_id: str,
    scene_id: str,
    variant_id: str,
    asset_paths: list[Path],
    name: str = "scene.vrt",
    band_list: list[int] | None = None,
    report: "mosaics.MosaicReport | None" = None,
) -> Path:
    if not asset_paths:
        raise ValueError("At least one raster part is required")
    # Wolajacy, ktory juz zbadal czesci (patrz `_configure_working_view`), przekazuje raport,
    # zeby nie otwierac tych samych rastrow po raz drugi przez udzial sieciowy.
    if report is not None:
        if report.is_blocking:
            raise ValueError(report.first_error)
    else:
        _validate_mosaic_parts(asset_paths)
    target_dir = project_dir(project_id) / "derived_scenes" / scene_id / variant_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    partial = target.with_name(f".{target.stem}.partial.vrt")
    partial.unlink(missing_ok=True)
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        if band_list:
            options = gdal.BuildVRTOptions(bandList=band_list)
            dataset = gdal.BuildVRT(str(partial), [str(path) for path in asset_paths], options=options)
        else:
            dataset = gdal.BuildVRT(str(partial), [str(path) for path in asset_paths])
        if dataset is None:
            raise RuntimeError("GDAL BuildVRT failed")
        dataset.FlushCache()
        dataset = None
    except ImportError:
        executable = shutil.which("gdalbuildvrt")
        if not executable:
            raise RuntimeError("GDAL VRT support is unavailable")
        command = [executable]
        for band in band_list or []:
            command.extend(["-b", str(band)])
        command.extend([str(partial), *map(str, asset_paths)])
        subprocess.run(command, check=True, capture_output=True, text=True)
    os.replace(partial, target)
    return target


#: Nazwa pansharpened VRT — plik posredni, ktory NIE zawiera pikseli, tylko przepis.
PANSHARPENED_VRT_NAME = "rgb_pansharpened.vrt"
#: Identyfikator pipeline'u zapisywany w `processing_manifest.json` (P1.4b).
PANSHARPEN_PIPELINE = "pansharpened_vrt_single_translate_v2"
#: Wersja algorytmu. Podnosic, gdy zmienia sie WARTOSCI wyniku, a nie tylko sposob zapisu.
PANSHARPEN_ALGORITHM_VERSION = 2
#: Wersja schematu `processing_manifest.json`; v2 dodaje tozsamosc wejsc, srodowisko,
#: parametry algorytmu i semantyke wyjscia (P1.5).
PROCESSING_MANIFEST_VERSION = 2
#: Limit cache GDAL na czas jednego translate. Dobrany pomiarem na WV2 MUL+PAN: bez limitu
#: szczytowy RSS drzewa procesow rosl do 2,4 GiB, z limitem spada do 0,57 GiB — przy krotszym
#: czasie. Poprzedni pipeline (TIFF posredni) mial 1,83 GiB, wiec limit jest tym, co pozwala
#: przyjac kandydata bez regresji RAM.
COG_CACHE_BYTES = 256 * 1024 * 1024


def prepare_pansharpened_cog(
    project_id: str,
    scene_id: str,
    rgb_bands: list[int],
    *,
    cancel_check=lambda: False,
) -> dict[str, Any]:
    manifest = load_scene_json(project_id, scene_id, "scene_manifest", default={})
    package = manifest.get("source_package") or {}
    working = manifest.get("working_view") or {}
    assets = {asset.get("asset_id"): asset for asset in package.get("assets") or []}
    selection = package.get("selection") or {}
    ms_ids = selection.get("multispectral_asset_ids") or []
    pan_ids = selection.get("panchromatic_asset_ids") or []
    ms_paths = _asset_paths(project_id, package.get("source_id"), assets, ms_ids)
    pan_paths = _asset_paths(project_id, package.get("source_id"), assets, pan_ids)
    if not ms_paths or not pan_paths:
        raise RuntimeError("Multispectral or panchromatic assets are missing")
    selection = {**selection, "rgb_bands": list(rgb_bands), "raster_kind": "derived"}
    variant_definition = build_working_variant_definition(selection)
    variant_id = working_variant_id(variant_definition)
    if working.get("locked") and working.get("variant_id") not in {None, variant_id}:
        raise PermissionError("Working view is locked; create a separate scene for another variant")

    ms_vrt = create_vrt(
        project_id,
        scene_id,
        variant_id,
        ms_paths,
        "multispectral.vrt",
        band_list=rgb_bands,
    )
    pan_vrt = create_vrt(project_id, scene_id, variant_id, pan_paths, "panchromatic.vrt")
    target_dir = project_dir(project_id) / "derived_scenes" / scene_id / variant_id
    pansharpened_vrt = target_dir / PANSHARPENED_VRT_NAME
    target = target_dir / "rgb_pansharpened.cog.tif"
    pansharpened_vrt.unlink(missing_ok=True)
    # Preflight zasobow (P1.4). Po przejsciu na pansharpened VRT na dysku powstaje TYLKO jeden
    # pelny obraz — wynikowy COG. Szacunek zostaje nieskompresowany, wiec jest zachowawczy:
    # pomiar na WV2 MUL+PAN dal 1,19 GiB szczytowego scratchu przy szacunku 1,32 GiB.
    ensure_derivative_space(
        target_dir,
        _raster_payload_bytes(pan_vrt, bands=len(rgb_bands)),
        "pansharpened product",
    )
    if cancel_check():
        raise InterruptedError("Preparation cancelled")

    # Etap 1: pansharpening jako VRT — nie powstaje zaden raster posredni. Podproces jest
    # tani (setne sekundy) i nadal anulowalny tak jak dotad.
    command = [
        sys.executable, "-m", "osgeo_utils.gdal_pansharpen",
        "-of", "VRT",
        str(pan_vrt), str(ms_vrt), str(pansharpened_vrt),
        "-w", str(1 / 3), "-w", str(1 / 3), "-w", str(1 / 3),
    ]
    for band in range(1, len(rgb_bands) + 1):
        command.extend(["-b", str(band)])
    _run_cancellable(command, cancel_check)
    if cancel_check():
        pansharpened_vrt.unlink(missing_ok=True)
        raise InterruptedError("Preparation cancelled")

    cog_partial = target.with_name(f".{target.stem}.partial.tif")
    cog_partial.unlink(missing_ok=True)
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        previous_cache = gdal.GetCacheMax()
        # Bez limitu pojedynczy translate podnosil szczytowy RSS drzewa do 2,4 GiB; z limitem
        # 256 MiB spada do 0,57 GiB i jest przy tym SZYBSZY (mniej pracy alokatora).
        gdal.SetCacheMax(COG_CACHE_BYTES)
        try:
            # Etap 2: jeden translate liczy pansharpening, piramidy i kompresje. Anulowanie
            # idzie przez callback postepu GDAL — zwrocenie zera przerywa zapis w miejscu.
            def progress(_complete: float, _message: str, _data: Any) -> int:
                return 0 if cancel_check() else 1

            translated = gdal.Translate(
                str(cog_partial),
                str(pansharpened_vrt),
                format="COG",
                creationOptions=[
                    "BLOCKSIZE=512",
                    "COMPRESS=DEFLATE",
                    "BIGTIFF=IF_SAFER",
                    "NUM_THREADS=ALL_CPUS",
                ],
                callback=progress,
            )
        finally:
            gdal.SetCacheMax(previous_cache)
        if translated is None:
            raise RuntimeError("GDAL COG translation failed")
        translated.FlushCache()
        translated = None
        if cancel_check():
            raise InterruptedError("Preparation cancelled")
        os.replace(cog_partial, target)
    except RuntimeError as exc:
        # Przerwanie przez callback wraca z GDAL jako zwykly blad; rozrozniamy je po tym,
        # ze anulowanie jest nadal aktywne, zeby wolajacy dostal `InterruptedError`.
        if cancel_check():
            raise InterruptedError("Preparation cancelled") from exc
        raise
    finally:
        pansharpened_vrt.unlink(missing_ok=True)
        _remove_with_sidecars(cog_partial)

    # P1.5: manifest przetwarzania musi wystarczyc do ODTWORZENIA wariantu i do stwierdzenia,
    # czym sa jego piksele. Zapisujemy wiec tozsamosc kazdego wejscia, pelne parametry
    # algorytmu, srodowisko i semantyke wyjscia — a nie same nazwy plikow.
    algorithm = {
        "name": "weighted_brovey",
        "version": PANSHARPEN_ALGORITHM_VERSION,
        "pipeline": PANSHARPEN_PIPELINE,
        "weights": [1 / 3, 1 / 3, 1 / 3],
        "resampling": "cubic",
        "rgb_bands": list(rgb_bands),
        "rgb_bands_source": "selection",
        "pansharpen_threads": 1,
        "cog_num_threads": "ALL_CPUS",
        "gdal_cache_bytes": COG_CACHE_BYTES,
        "creation_options": [
            "BLOCKSIZE=512", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS",
        ],
    }
    inputs = [
        _input_identity(assets.get(asset_id), role)
        for role, ids in (("multispectral", ms_ids), ("panchromatic", pan_ids))
        for asset_id in ids
        if assets.get(asset_id)
    ]
    processing = {
        "schema_name": "geotile_scene_processing_manifest",
        "schema_version": PROCESSING_MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "variant_id": variant_id,
        "variant_definition": variant_definition,
        "kind": "labeling_visual_product",
        "method": algorithm["name"],
        "weights": algorithm["weights"],
        "rgb_bands": list(rgb_bands),
        # Pipeline jest zapisany wprost, bo wynik JEST od niego zalezny: liczba poziomow
        # piramidy rozni sie miedzy stara a nowa sciezka (5 wobec 6), mimo identycznych
        # pikseli pelnej rozdzielczosci.
        "pipeline": PANSHARPEN_PIPELINE,
        "pansharpen_threads": 1,
        "cog_num_threads": "ALL_CPUS",
        "gdal_cache_bytes": COG_CACHE_BYTES,
        "rgb_bands_source": "selection",
        "algorithm": algorithm,
        "environment": _processing_environment(),
        "inputs": inputs,
        # Odcisk liczony WYLACZNIE z tozsamosci wejsc i parametrow — nie z wyniku. Dwa
        # przebiegi na tych samych plikach musza dac ten sam odcisk (bramka P1.5).
        "inputs_fingerprint": canonical_hash({"inputs": inputs, "algorithm": algorithm}),
        "output": target.name,
        "output_semantics": _output_semantics(target, rgb_bands),
    }
    processing_path = target_dir / "processing_manifest.json"
    processing_path.write_text(json.dumps(processing, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "variant_id": variant_id,
        "variant_definition": variant_definition,
        "path": target,
        "processing_manifest": processing_path,
    }


def _input_identity(asset: dict[str, Any] | None, role: str) -> dict[str, Any]:
    """Tozsamosc jednego wejscia produktu pochodnego.

    Sama sciezka wzgledna nie wystarcza: po relinku albo podmianie pliku o tej samej nazwie
    manifest twierdzilby, ze wynik pochodzi z tych samych danych.
    """
    asset = asset or {}
    return {
        "role": role,
        "asset_id": asset.get("asset_id"),
        "relative_path": asset.get("relative_path"),
        "size": asset.get("size"),
        "mtime_ns": asset.get("mtime_ns"),
        "sha256": asset.get("sha256"),
        "content_signature": asset.get("content_signature"),
        "content_signature_method": asset.get("content_signature_method"),
    }


def _processing_environment() -> dict[str, Any]:
    """Wersje, ktore realnie wplywaja na wynik przetwarzania."""
    try:
        from osgeo import gdal

        gdal_version = gdal.__version__
    except Exception:
        gdal_version = None
    return {
        "gdal_version": gdal_version,
        "python_version": platform.python_version(),
        "platform": platform.system(),
    }


def _output_semantics(path: Path, rgb_bands: list[int]) -> dict[str, Any]:
    """Czym sa piksele WYNIKU — czytane z gotowego pliku, a nie zakladane."""
    try:
        import rasterio

        with rasterio.open(path) as src:
            overviews = list(src.overviews(1))
            return {
                "width": src.width,
                "height": src.height,
                "band_count": src.count,
                "dtype": src.dtypes[0],
                "nodata": src.nodata,
                "crs": str(src.crs),
                "transform": [float(value) for value in src.transform[:6]],
                "overview_levels": overviews,
                # Produkt roboczy jest wizualnym zlozeniem MUL i PAN, a nie wielkoscia
                # fizyczna: pansharpening miesza pasma, wiec kalibracja zrodla przestaje
                # obowiazywac. Zapisujemy to wprost, zeby nikt nie wzial go za radiancje.
                "quantity": "pansharpened_visual",
                "calibration_state": "uncalibrated",
                "source_band_indexes": list(rgb_bands),
            }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _remove_with_sidecars(path: Path) -> None:
    """Skasuj plik razem z tym, co GDAL dopisal obok niego.

    Przerwany zapis zostawia sidecar PAM (`.aux.xml`), a przy innych sciezkach takze `.ovr`.
    Sam `unlink()` pliku glownego zostawialby te resztki w katalogu wariantu, czyli dokladnie
    to, czego zabrania bramka: „anulowanie usuwa partial i nie publikuje niepelnego wariantu".
    """
    path.unlink(missing_ok=True)
    try:
        for sibling in path.parent.iterdir():
            if sibling.is_file() and sibling.name.startswith(path.name):
                sibling.unlink(missing_ok=True)
    except OSError:
        return


def _raster_payload_bytes(path: Path, *, bands: int | None = None) -> int:
    """Rozmiar nieskompresowanych pikseli rastra; 0, gdy nie da sie go otworzyc.

    Preflight nie moze wywrocic przygotowania tylko dlatego, ze nie potrafil oszacowac
    kosztu — brak oszacowania oznacza brak blokady, a nie blokade.
    """
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        dataset = gdal.Open(str(path))
        if dataset is None:
            return 0
        count = bands if bands is not None else dataset.RasterCount
        sample_bytes = gdal.GetDataTypeSize(dataset.GetRasterBand(1).DataType) // 8
        total = dataset.RasterXSize * dataset.RasterYSize * max(count, 1) * max(sample_bytes, 1)
        dataset = None
        return int(total)
    except Exception:
        return 0


def _asset_paths(project_id: str, source_id: str, assets: dict[str, dict[str, Any]], ids: list[str]) -> list[Path]:
    paths: list[Path] = []
    for asset_id in ids:
        asset = assets.get(asset_id)
        if not asset:
            continue
        path = resolve_source_asset(project_id, str(source_id or ""), asset.get("relative_path", ""))
        if path:
            paths.append(path)
    return paths


def _run_cancellable(command: list[str], cancel_check) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        while process.poll() is None:
            if cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise InterruptedError("Preparation cancelled")
            time.sleep(0.2)
        stdout, stderr = process.communicate()
        if process.returncode:
            details = (stderr or stdout or "unknown GDAL error").strip()
            raise RuntimeError(f"GDAL pansharpening failed: {details}")
    finally:
        if process.poll() is None:
            process.kill()


def _validate_mosaic_parts(paths: list[Path]) -> None:
    """Zablokuj budowe VRT, gdy czesci nie tworza jednej siatki.

    Cala kontrola geometrii mieszka w `mosaics.inspect_parts()` (P1.4). Tutaj zostaje wylacznie
    tlumaczenie raportu na wyjatek, zeby zachowac dotychczasowy kontrakt `create_vrt`. Nakladki,
    duplikaty i luki NIE blokuja — sa stanem pokrycia i trafiaja do diagnostyki selekcji.
    """
    report = mosaics.inspect_parts(paths)
    if report.is_blocking:
        raise ValueError(report.first_error)


# --- Piramidy dla produktow direct bez overviews (DESIGN_DECISIONS.md, tile-serving P1) ---
#
# Produkty `direct` dostarczone jako paskowany GeoTIFF bez wewnetrznych overviews sa
# wolne przy szerokim kadrze: kafelek niskiego zoomu decymuje pelna rozdzielczosc
# (P0: ~372 ms vs ~7 ms z piramidami). Dokladamy piramide po stronie projektu jako
# sidecar `.ovr` obok lekkiego VRT wskazujacego zrodlo. Zrodlo pozostaje read-only
# (piramida liczona z odczytu). Wariant wybrany pomiarem: VRT+.ovr z DEFLATE — ~24%
# narzutu zamiast duplikatu calego produktu (patrz plan, decyzja P1).

DIRECT_OVERVIEW_VRT_NAME = "overview.vrt"
DIRECT_OVERVIEW_PROFILE_NAME = "overview.profile.json"
DIRECT_OVERVIEW_PROFILE_VERSION = 3
JP2_NATIVE_OVERVIEW_STRATEGY = "jp2_native_level_copy_v1"
GDAL_BUILD_OVERVIEW_STRATEGY = "gdal_build_overviews_v1"

#: Rodzaje widoku roboczego, dla ktorych budujemy piramide wyswietlania.
#: `virtual_mosaic` dolaczyl w P1.4: bez piramidy kafel niskiego zoomu decymuje WSZYSTKIE
#: czesci mozaiki naraz (WV2 PAN to szesc czesci, 26958×42040 px). Mechanizm jest ten sam —
#: VRT z sidecarem `.ovr` — tyle ze opakowywany VRT jest juz mozaika, a nie pojedynczym plikiem.
DISPLAY_OVERVIEW_RASTER_KINDS = frozenset({"direct", "virtual_mosaic"})

#: Zapas wolnego miejsca zostawiany ponad szacowany rozmiar derywatu.
DISPLAY_OVERVIEW_RESERVE_BYTES = 128 * 1024 * 1024
#: Suma poziomow 1/4 + 1/16 + ... zbiega do 1/3 rozmiaru pelnego obrazu; kompresja zbija to
#: ponizej, ale preflight ma byc zachowawczy, wiec nie zakladamy zadnego zysku z kompresji.
OVERVIEW_SIZE_FRACTION = 1 / 3


class InsufficientDerivativeSpace(RuntimeError):
    """Za malo miejsca na derywat (piramida albo produkt pansharpened)."""

    def __init__(self, path: Path, required: int, free: int, kind: str):
        super().__init__(
            f"Not enough free space for the {kind}: {required} byte(s) required, "
            f"{free} available at {path}"
        )
        self.path, self.required, self.free, self.kind = path, required, free, kind


def estimate_overview_bytes(width: int, height: int, bands: int, bytes_per_sample: int) -> int:
    return int(width * height * max(bands, 1) * max(bytes_per_sample, 1) * OVERVIEW_SIZE_FRACTION)


def ensure_derivative_space(path: Path, required_bytes: int, kind: str) -> int:
    """Sprawdz wolne miejsce PRZED rozpoczeciem ciezkiego zapisu.

    Bez tego brak miejsca ujawnia sie dopiero w polowie budowy piramidy albo produktu
    pansharpened, czyli po przeczytaniu calego zrodla i z czesciowym plikiem na dysku.
    """
    path.mkdir(parents=True, exist_ok=True)
    free = int(shutil.disk_usage(path).free)
    required = int(required_bytes) + DISPLAY_OVERVIEW_RESERVE_BYTES
    if free < required:
        raise InsufficientDerivativeSpace(path=path, required=required, free=free, kind=kind)
    return free


def _direct_overview_dir(project_id: str, scene_id: str, variant_id: str | None) -> Path:
    return project_dir(project_id) / "derived_scenes" / scene_id / (variant_id or "_direct_overview")


@lru_cache(maxsize=1024)
def _overview_profile_is_compatible_cached(
    profile_path_value: str,
    mtime_ns: int,
    size: int,
) -> bool:
    """Validate a completed overview profile without rereading it for every map tile.

    Profile v2 JP2 derivatives were produced by ``VRT.BuildOverviews``.  That path
    performs a full-resolution JPEG2000 pass and is the source of the multi-minute
    preparation regression.  Generic GeoTIFF/mosaic derivatives are still valid;
    only native-JP2 artifacts need the v3 strategy marker.

    ``mtime_ns`` and ``size`` are deliberate cache-key fields.  Atomic profile
    replacement therefore invalidates this cache without a global lock or manual
    cache clearing.
    """

    del mtime_ns, size
    try:
        profile = json.loads(Path(profile_path_value).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        # A missing/corrupt best-effort profile must not hide a complete, usable
        # derivative.  Source/cache synchronization can rebuild it separately.
        return True
    if str(profile.get("source_overview_type") or "") != "native_multiresolution":
        return True
    return (
        int(profile.get("schema_version") or 0) >= DIRECT_OVERVIEW_PROFILE_VERSION
        and str(profile.get("strategy") or "") == JP2_NATIVE_OVERVIEW_STRATEGY
    )


def _overview_profile_is_compatible(vrt: Path) -> bool:
    profile_path = vrt.parent / DIRECT_OVERVIEW_PROFILE_NAME
    try:
        stat = profile_path.stat()
    except OSError:
        return True
    return _overview_profile_is_compatible_cached(
        str(profile_path),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


def direct_overview_vrt(project_id: str, scene_id: str, variant_id: str | None) -> Path | None:
    """Sciezka gotowego VRT z piramida, jesli istnieje. Tania — sam `is_file`.

    Nie weryfikuje, czy zrodlo sie nie zmienilo: to obsluguje uniewaznianie przy
    `source_changed`/relink, a serwowanie sceny w tym stanie jest i tak zablokowane.
    """
    vrt = _direct_overview_dir(project_id, scene_id, variant_id) / DIRECT_OVERVIEW_VRT_NAME
    ovr = vrt.with_name(vrt.name + ".ovr")
    return (
        vrt
        if vrt.is_file() and ovr.is_file() and _overview_profile_is_compatible(vrt)
        else None
    )


@dataclass(frozen=True)
class DirectPreviewAsset:
    """Standalone reduced GeoTIFF used without opening the backing JP2."""

    path: Path
    vrt_path: Path
    base_factor: int
    profile_version: int
    fingerprint: str


@lru_cache(maxsize=1024)
def _direct_preview_asset_cached(
    vrt_value: str,
    ovr_mtime_ns: int,
    ovr_size: int,
    profile_mtime_ns: int,
    profile_size: int,
) -> DirectPreviewAsset | None:
    del profile_mtime_ns, profile_size
    vrt = Path(vrt_value)
    ovr = vrt.with_name(vrt.name + ".ovr")
    profile_path = vrt.parent / DIRECT_OVERVIEW_PROFILE_NAME
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (
        int(profile.get("schema_version") or 0) < DIRECT_OVERVIEW_PROFILE_VERSION
        or str(profile.get("strategy") or "") != JP2_NATIVE_OVERVIEW_STRATEGY
    ):
        return None
    base_factor = int(profile.get("jp2_base_factor") or 0)
    if base_factor <= 1:
        factors = [int(value) for value in (profile.get("factors") or []) if int(value) > 1]
        base_factor = min(factors) if factors else 0
    if base_factor <= 1:
        return None
    fingerprint = hashlib.sha256(
        repr(
            (
                str(ovr.resolve(strict=False)),
                int(ovr_mtime_ns),
                int(ovr_size),
                profile,
            )
        ).encode("utf-8")
    ).hexdigest()[:20]
    return DirectPreviewAsset(
        path=ovr,
        vrt_path=vrt,
        base_factor=base_factor,
        profile_version=int(profile.get("schema_version") or 0),
        fingerprint=fingerprint,
    )


def direct_preview_asset(
    project_id: str,
    scene_id: str,
    variant_id: str | None,
) -> DirectPreviewAsset | None:
    """Return a self-contained JP2 preview descriptor or ``None`` when inconsistent."""

    vrt = direct_overview_vrt(project_id, scene_id, variant_id)
    if vrt is None:
        return None
    ovr = vrt.with_name(vrt.name + ".ovr")
    profile = vrt.parent / DIRECT_OVERVIEW_PROFILE_NAME
    try:
        ovr_stat = ovr.stat()
        profile_stat = profile.stat()
    except OSError:
        return None
    return _direct_preview_asset_cached(
        str(vrt),
        int(ovr_stat.st_mtime_ns),
        int(ovr_stat.st_size),
        int(profile_stat.st_mtime_ns),
        int(profile_stat.st_size),
    )


def source_has_internal_overviews(source_path: Path) -> bool:
    """True, gdy raster ma juz wlasne piramidy — wtedy P1 go pomija."""
    try:
        import rasterio

        with rasterio.open(source_path) as src:
            return bool(src.overviews(1))
    except Exception:
        # Nie potrafimy sprawdzic — zachowawczo uznajemy, ze ma (nie ruszamy).
        return True


def source_has_overviews(source_path: Path) -> bool:
    """Detect overviews that are fast enough for interactive display.

    Large native JP2 resolution levels deliberately return ``False`` so the
    display-preparation job builds a project-local VRT/GTiff OVR.
    """

    state = inspect_source_overviews(source_path)
    # Preserve the conservative legacy behavior for unreadable sources.
    # An unreadable raster is not evidence of native overviews.  Treating it as such
    # hides a catalogue failure and incorrectly skips the recovery path.
    return source_overviews_are_display_ready(state)


def _overview_factors(width: int, height: int) -> list[int]:
    """Poziomy decymacji az najwyzszy zmiesci sie ponizej ~512 px."""
    factors: list[int] = []
    factor = 2
    while max(width, height) / factor > 512:
        factors.append(factor)
        factor *= 2
    return factors or [2]


def _coarser_overview_factors(width: int, height: int) -> list[int]:
    """Relative levels below an already materialized overview base.

    Unlike :func:`_overview_factors`, an empty result is valid here: if the copied
    native JP2 level already fits below roughly 512 px, it is itself the coarsest
    display level and no extra internal overview is necessary.
    """

    factors: list[int] = []
    factor = 2
    while max(width, height) / factor > 512:
        factors.append(factor)
        factor *= 2
    return factors


def _cancel_callback(cancel_check):
    state = {"cancelled": False}

    def callback(_complete, _message, _callback_data):
        if cancel_check and cancel_check():
            state["cancelled"] = True
            return 0
        return 1

    return callback, state


def _select_jp2_base_factor(
    native_factors: list[int],
    *,
    uncompressed_source_bytes: int,
    requested: dict[str, Any],
) -> tuple[int, str, int, int | None]:
    """Choose the finest native level that fits a conservative RAM budget.

    OpenJPEG's peak is dominated by decoded component buffers, not GDAL's block
    cache or thread count. Measurements on the ARSENYEV Gray+Alpha JP2 were about
    6.9 GiB/3.4 GiB/2.5 GiB for base factors 2/4/8. The conservative estimate
    below intentionally overstates the latter two. At most 40% of memory available
    per concurrent overview worker may be assigned to one JP2 preparation.
    """

    available_factors = sorted({value for value in native_factors if value > 1})
    if not available_factors:
        available_factors = [2]
    explicit_value = requested.get("jp2_base_factor") or os.environ.get(
        "GEOTILE_JP2_OVERVIEW_BASE_FACTOR"
    )
    if explicit_value not in {None, "", "auto", "AUTO"}:
        try:
            explicit = max(2, int(explicit_value))
        except (TypeError, ValueError):
            explicit = available_factors[0]
        chosen = min(available_factors, key=lambda value: (abs(value - explicit), value))
        estimated = int(uncompressed_source_bytes * (2 / chosen) + 1.75 * 1024**3)
        return chosen, "explicit", estimated, None

    available_memory = requested.get("available_memory_bytes")
    try:
        available_memory = int(available_memory) if available_memory else 0
    except (TypeError, ValueError):
        available_memory = 0
    if available_memory <= 0:
        try:
            import psutil  # type: ignore

            available_memory = int(psutil.virtual_memory().available)
        except Exception:
            available_memory = 0
    workers = max(1, int(requested.get("workers") or 1))
    budget = int(available_memory / workers * 0.40) if available_memory else 0
    candidates = [value for value in available_factors if value <= 8] or available_factors[:1]
    chosen = candidates[-1]
    estimated = int(uncompressed_source_bytes * (2 / chosen) + 1.75 * 1024**3)
    if budget:
        for candidate in candidates:
            candidate_estimate = int(
                uncompressed_source_bytes * (2 / candidate) + 1.75 * 1024**3
            )
            if candidate_estimate <= budget:
                chosen = candidate
                estimated = candidate_estimate
                break
    return chosen, "adaptive_memory_budget", estimated, available_memory or None


def clear_direct_overviews(project_id: str, scene_id: str, variant_id: str | None) -> None:
    """Uniewaznienie derywatu piramid dla konkretnego wariantu."""
    target_dir = _direct_overview_dir(project_id, scene_id, variant_id)
    for name in (
        DIRECT_OVERVIEW_VRT_NAME,
        DIRECT_OVERVIEW_VRT_NAME + ".ovr",
        DIRECT_OVERVIEW_PROFILE_NAME,
        f".{DIRECT_OVERVIEW_VRT_NAME}.partial.vrt",
        f".{DIRECT_OVERVIEW_VRT_NAME}.partial.vrt.ovr",
        f".{DIRECT_OVERVIEW_VRT_NAME}.partial.vrt.ovr.ovr",
        Path(DIRECT_OVERVIEW_PROFILE_NAME).with_suffix(".tmp").name,
    ):
        (target_dir / name).unlink(missing_ok=True)


def clear_all_scene_display_overviews(project_id: str, scene_id: str) -> None:
    """Skasuj piramidy wyswietlania sceny niezaleznie od wariantu.

    Uzywane przy relinku: VRT wskazuje zrodlo sciezka, wiec po przeniesieniu zrodla
    staly VRT jest martwy. Kasujemy synchronicznie, zanim ktos zdazy go odczytac;
    odbudowa idzie w tle, a do tego czasu wyswietlanie wraca do zrodla.
    """
    scene_derived = project_dir(project_id) / "derived_scenes" / scene_id
    if not scene_derived.is_dir():
        return
    for variant_dir in scene_derived.iterdir():
        if variant_dir.is_dir():
            clear_direct_overviews(project_id, scene_id, variant_dir.name)


def build_direct_overviews(
    project_id: str,
    scene_id: str,
    source_path: Path,
    variant_id: str | None,
    cancel_check=None,
    profile: dict[str, Any] | None = None,
) -> Path | None:
    """Zbuduj piramide dla produktu direct bez overviews. Idempotentne.

    Zwraca sciezke VRT z piramida albo None, gdy zrodlo juz ma overviews (pomijamy)
    lub GDAL jest niedostepny. Nie modyfikuje zrodla.
    """
    existing = direct_overview_vrt(project_id, scene_id, variant_id)
    if existing is not None:
        return existing
    source_overview_state = inspect_source_overviews(source_path)
    if (
        source_overviews_are_display_ready(source_overview_state)
        or source_overview_state.get("read_error")
    ):
        return None
    native_jp2 = str(source_overview_state.get("type") or "") == "native_multiresolution"

    try:
        from osgeo import gdal
    except ImportError:
        return None

    gdal.UseExceptions()
    target_dir = _direct_overview_dir(project_id, scene_id, variant_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    vrt = target_dir / DIRECT_OVERVIEW_VRT_NAME
    partial = target_dir / f".{DIRECT_OVERVIEW_VRT_NAME}.partial.vrt"
    partial.unlink(missing_ok=True)
    partial.with_name(partial.name + ".ovr").unlink(missing_ok=True)

    try:
        dataset = gdal.BuildVRT(str(partial), [str(source_path)])
        if dataset is None:
            raise RuntimeError("GDAL BuildVRT failed")
        width, height = dataset.RasterXSize, dataset.RasterYSize
        bands = dataset.RasterCount
        dtype_name = (
            gdal.GetDataTypeName(dataset.GetRasterBand(1).DataType) if bands else ""
        )
        sample_bytes = (
            gdal.GetDataTypeSize(dataset.GetRasterBand(1).DataType) // 8 if bands else 1
        )
        dataset = None
        ensure_derivative_space(
            target_dir,
            estimate_overview_bytes(width, height, bands, sample_bytes),
            "display overview",
        )
        if cancel_check and cancel_check():
            raise InterruptedError("Overview build cancelled")

        requested = dict(profile or {})
        compression = str(requested.get("compression") or "DEFLATE").upper()
        if compression not in {"DEFLATE", "ZSTD", "LZW"}:
            compression = "DEFLATE"
        if native_jp2:
            default_jp2_threads = max(2, min(8, max(1, (os.cpu_count() or 2) // 2)))
            gdal_threads = str(
                requested.get("jp2_gdal_threads")
                or os.environ.get("GEOTILE_JP2_OVERVIEW_GDAL_THREADS")
                or default_jp2_threads
            )
        else:
            gdal_threads = str(requested.get("gdal_threads") or "1")
        resampling = str(requested.get("resampling") or "AVERAGE").upper()
        predictor = requested.get("predictor")
        if predictor == "auto":
            predictor = 3 if str(dtype_name).startswith("Float") else 2
        used_predictor = int(predictor) if predictor is not None else None
        strategy = (
            JP2_NATIVE_OVERVIEW_STRATEGY
            if native_jp2
            else GDAL_BUILD_OVERVIEW_STRATEGY
        )
        jp2_base_factor: int | None = None
        jp2_base_factor_selection: str | None = None
        jp2_estimated_peak_bytes: int | None = None
        jp2_available_memory_bytes: int | None = None
        # Keep the configured cache limit in the outer scope.  It is both used by
        # each codec attempt and persisted in the completed overview profile.
        # Defining it inside ``build_with_codec`` made the successful build path
        # fail afterwards with NameError while writing that profile.
        cache_bytes = int(requested.get("gdal_cache_bytes") or COG_CACHE_BYTES)

        def build_standard_with_codec(codec: str) -> tuple[str, int | None, list[int]]:
            factors = _overview_factors(width, height)
            options = {
                "COMPRESS_OVERVIEW": codec,
                "BIGTIFF_OVERVIEW": "YES",
                "GDAL_NUM_THREADS": gdal_threads,
            }
            handle = gdal.Open(str(partial), gdal.GA_Update)
            if handle is None:
                raise RuntimeError("GDAL could not reopen display overview VRT")
            if used_predictor is not None:
                options["PREDICTOR_OVERVIEW"] = str(used_predictor)
            for key, value in options.items():
                gdal.SetConfigOption(key, value)
            # Bez ograniczenia cache budowa piramidy MOZAIKI szla do 2,88 GiB szczytowego RSS
            # (pomiar na WV2 PAN, szesc czesci, 26958x42040). To zadanie tla, wiec taki szczyt
            # konkurowalby z interaktywnym odczytem kafli. Limit jest ten sam co przy zapisie
            # COG w P1.4b i tam nie kosztowal czasu.
            # Profil moze podniesc limit dla zrodel, ktore na tym traca — pomiar obejmowal
            # mozaike GeoTIFF; natywne JP2 z wlasnymi poziomami rozdzielczosci bylo strojone
            # osobno (wiecej watkow GDAL) i nie zostalo tu przemierzone.
            previous_cache = gdal.GetCacheMax()
            gdal.SetCacheMax(cache_bytes)
            try:
                result = handle.BuildOverviews(resampling, factors)
                handle = None
                if result not in {None, 0}:
                    raise RuntimeError(f"GDAL BuildOverviews failed with code {result}")
            finally:
                handle = None
                gdal.SetCacheMax(previous_cache)
                for key in options:
                    gdal.SetConfigOption(key, None)
            return codec, used_predictor, factors

        def build_native_jp2_with_codec(codec: str) -> tuple[str, int | None, list[int]]:
            """Materialize the first native JP2 level instead of resampling full resolution.

            ``BuildOverviews`` on a VRT starts with a full source pass even if the
            JPEG2000 codestream already exposes 2x/4x/... resolution levels.  A
            direct ``Translate(..., overviewLevel='AUTO')`` selects that native
            codestream level and writes it as the first GTiff overview.  Further
            levels are then cheap internal overviews of that much smaller file.
            """

            nonlocal jp2_available_memory_bytes, jp2_base_factor
            nonlocal jp2_base_factor_selection, jp2_estimated_peak_bytes
            native_factors: list[int] = []
            for value in source_overview_state.get("factors") or []:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 1:
                    native_factors.append(parsed)
            (
                jp2_base_factor,
                jp2_base_factor_selection,
                jp2_estimated_peak_bytes,
                jp2_available_memory_bytes,
            ) = _select_jp2_base_factor(
                native_factors,
                uncompressed_source_bytes=(
                    width * height * max(bands, 1) * max(sample_bytes, 1)
                ),
                requested=requested,
            )
            proxy_width = max(1, (width + jp2_base_factor - 1) // jp2_base_factor)
            proxy_height = max(1, (height + jp2_base_factor - 1) // jp2_base_factor)
            relative_factors = _coarser_overview_factors(proxy_width, proxy_height)
            partial_ovr = partial.with_name(partial.name + ".ovr")
            _remove_with_sidecars(partial_ovr)

            creation_options = [
                "TILED=YES",
                "BLOCKXSIZE=256",
                "BLOCKYSIZE=256",
                f"COMPRESS={codec}",
                "BIGTIFF=YES",
                f"NUM_THREADS={gdal_threads}",
            ]
            if used_predictor is not None:
                creation_options.append(f"PREDICTOR={used_predictor}")
            config_options = {
                "COMPRESS_OVERVIEW": codec,
                "BIGTIFF_OVERVIEW": "YES",
                "GDAL_NUM_THREADS": gdal_threads,
            }
            if used_predictor is not None:
                config_options["PREDICTOR_OVERVIEW"] = str(used_predictor)
            for key, value in config_options.items():
                gdal.SetConfigOption(key, value)

            previous_cache = gdal.GetCacheMax()
            gdal.SetCacheMax(cache_bytes)
            output = None
            handle = None
            try:
                translate_callback, translate_state = _cancel_callback(cancel_check)
                translate_options = gdal.TranslateOptions(
                    format="GTiff",
                    width=proxy_width,
                    height=proxy_height,
                    resampleAlg=resampling.lower(),
                    overviewLevel="AUTO",
                    creationOptions=creation_options,
                    callback=translate_callback,
                )
                try:
                    output = gdal.Translate(
                        str(partial_ovr),
                        str(source_path),
                        options=translate_options,
                    )
                except Exception as exc:
                    if translate_state["cancelled"]:
                        raise InterruptedError("Overview build cancelled") from exc
                    raise
                if output is None:
                    if translate_state["cancelled"]:
                        raise InterruptedError("Overview build cancelled")
                    raise RuntimeError("GDAL Translate failed to copy a native JP2 level")
                output = None
                if cancel_check and cancel_check():
                    raise InterruptedError("Overview build cancelled")

                if relative_factors:
                    handle = gdal.Open(str(partial_ovr), gdal.GA_Update)
                    if handle is None:
                        raise RuntimeError("GDAL could not reopen the JP2 display overview")
                    overview_callback, overview_state = _cancel_callback(cancel_check)
                    try:
                        result = handle.BuildOverviews(
                            resampling,
                            relative_factors,
                            callback=overview_callback,
                        )
                    except Exception as exc:
                        if overview_state["cancelled"]:
                            raise InterruptedError("Overview build cancelled") from exc
                        raise
                    handle = None
                    if overview_state["cancelled"]:
                        raise InterruptedError("Overview build cancelled")
                    if result not in {None, 0}:
                        raise RuntimeError(f"GDAL BuildOverviews failed with code {result}")
            finally:
                output = None
                handle = None
                gdal.SetCacheMax(previous_cache)
                for key in config_options:
                    gdal.SetConfigOption(key, None)

            probe = gdal.Open(str(partial))
            if probe is None or not probe.RasterCount:
                raise RuntimeError("GDAL could not validate the JP2 display overview")
            actual_factors: list[int] = []
            first_band = probe.GetRasterBand(1)
            for index in range(first_band.GetOverviewCount()):
                overview = first_band.GetOverview(index)
                if overview is not None and overview.XSize:
                    actual_factors.append(max(2, int(round(width / overview.XSize))))
            band_overview_counts = [
                probe.GetRasterBand(index).GetOverviewCount()
                for index in range(1, probe.RasterCount + 1)
            ]
            probe = None
            if not actual_factors or actual_factors[0] != jp2_base_factor:
                raise RuntimeError(
                    "Published JP2 display overview does not expose the copied native level"
                )
            if any(count != len(actual_factors) for count in band_overview_counts):
                raise RuntimeError("JP2 display overview levels differ between bands")
            return codec, used_predictor, actual_factors

        try:
            if native_jp2:
                used_compression, used_predictor, factors = build_native_jp2_with_codec(
                    compression
                )
            else:
                used_compression, used_predictor, factors = build_standard_with_codec(
                    compression
                )
        except InterruptedError:
            raise
        except Exception:
            _remove_with_sidecars(partial.with_name(partial.name + ".ovr"))
            if compression == "DEFLATE":
                raise
            if native_jp2:
                used_compression, used_predictor, factors = build_native_jp2_with_codec(
                    "DEFLATE"
                )
            else:
                used_compression, used_predictor, factors = build_standard_with_codec(
                    "DEFLATE"
                )
    except BaseException:
        _remove_with_sidecars(partial)
        raise

    os.replace(partial.with_name(partial.name + ".ovr"), vrt.with_name(vrt.name + ".ovr"))
    os.replace(partial, vrt)
    profile_path = target_dir / DIRECT_OVERVIEW_PROFILE_NAME
    temp_profile = profile_path.with_suffix(".tmp")
    try:
        temp_profile.write_text(
            json.dumps(
                {
                    "schema_name": "geotile_display_overview_profile",
                    "schema_version": DIRECT_OVERVIEW_PROFILE_VERSION,
                    "strategy": strategy,
                    "compression": used_compression,
                    "predictor": used_predictor,
                    "gdal_threads": gdal_threads,
                    "source_overview_type": source_overview_state.get("type"),
                    "source_native_factors": list(source_overview_state.get("factors") or []),
                    "jp2_base_factor": jp2_base_factor,
                    "source_width": width,
                    "source_height": height,
                    "preview_width": (
                        max(1, (width + jp2_base_factor - 1) // jp2_base_factor)
                        if jp2_base_factor else None
                    ),
                    "preview_height": (
                        max(1, (height + jp2_base_factor - 1) // jp2_base_factor)
                        if jp2_base_factor else None
                    ),
                    "jp2_base_factor_selection": jp2_base_factor_selection,
                    "jp2_estimated_peak_bytes": jp2_estimated_peak_bytes,
                    "jp2_available_memory_bytes": jp2_available_memory_bytes,
                    "resampling": resampling,
                    "factors": factors,
                    "gdal_cache_bytes": cache_bytes,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp_profile, profile_path)
    except OSError:
        temp_profile.unlink(missing_ok=True)
    return vrt
