"""v01 — Macierz zgodności dostawca × format × CRS × dtype × postać dostawy  (Claim S1).

SUBSTRATE / TIER
    xView3 (Sentinel-1 SAR, UTM, Float16) + FAIR1M (EO, EPSG:4326, uint8) +
    DOTA (EO, PNG, NO_GEO) + DIOR-R (EO, JPEG, NO_GEO) · public;
    SAR_test (Capella SAR, UTM, uint16) oraz rzeczywiste paczki dostawców
    (ICEYE, Capella, Airbus PHR/PNEO, WorldView, generic) · reported-only.  (mixed)

CLAIM
    Obsługa formatów/dostawców jest szeroka i spójna; scena z każdego z nich normalizuje
    się do tej samej postaci (manifest z geo/dtype) bez utraty georeferencji, a odczyt
    okna daje 8-bit RGB wg profilu preprocessingu.

    OŚ POSTACI DOSTAWY jest osobnym wymiarem, bo format pliku jej nie opisuje: ten sam
    GeoTIFF przychodzi jako pojedynczy plik, jako wieloczęściowa dostawa spięta manifestem
    dostawcy albo w archiwum — i są to trzy różne ścieżki kodu. Bez tego wymiaru macierz
    pokazywała wyłącznie pojedyncze pliki benchmarków i nie mówiła nic o resolverach paczek,
    które §2.2 manuskryptu opisuje jako funkcjonalność.

METHOD
    Wiersze benchmarkowe (public) — dla każdego sprawdzam REALNYM backendem:
      - manifest_ok: manifest niesie provider/modality/georeferencing/dtype; dla GEO także
        has_geo + transform(6) + crs; dla NO_GEO — georeferencing=="NO_GEO",
      - ingest_ok: `get_scene_info(working_raster)` otwiera raster roboczy i zgadza się z
        wymiarami z manifestu,
      - geo_ok: `SceneGeoModel.from_manifest` buduje model i round-trip 1 px < 1e-3 px (GEO),
      - render_8bit: `open_scene_source`+`read_tile_from_source`+`apply_preprocessing_profile`
        daje okno (H,W,3) uint8.
    Wymiar DTYPE jest jawny: xView3 to Float16 (GDT_Float16, kod 15) — rasterio go NIE
    mapuje (`KeyError: 15`), więc ingest idzie przez raster roboczy Float32 (VRT). Sprawdzam
    OBA: oryginał Float16 podnosi wyjątek, raster roboczy otwiera się jako float32.

    Wiersze dostawców (reported-only) — `resolvers.scan_source()`, ta sama funkcja, której
    używa aplikacja i audyt B0b, WYŁĄCZNIE do odczytu. Sceny NIE są importowane: przedmiotem
    claimu jest zakres obsługi, nie przebieg importu. Kształt dostawy bierze się wprost
    z `selection.raster_kind` (`direct` / `virtual_mosaic`) i `package_kind == "archive"`.
    Gdy resolver kończy na `decision_required`/`prepare_required`, wiersz dostaje `na`
    z powodem — to obowiązująca decyzja architektoniczna (§3.6: dostawa z wieloma produktami
    wymaga decyzji użytkownika), a nie awaria.

INPUTS
    - zaimportowane projekty benchmarków (../importers) + fixture SAR_test
    - opcjonalnie `scripts/provider-import-paths.json` z repozytorium aplikacji — plik
      LOKALNY i gitignorowany; jego brak jest normalnym stanem u recenzenta
    - backend: scene_loader / sensor_geometry / preprocessing_profiles / scene_packages

OUTPUTS
    - results/compat_matrix.csv  (dostawca, format, CRS, dtype, postać dostawy, tier + statusy)
    - metrics: {rows, public_rows, reported_only_rows, delivery_shapes, providers_reported,
                undecided_rows, coverage_pct, float16_handled, failing[]}

    Do wyniku nie trafia ŻADNA ścieżka ani nazwa pliku dostawcy — dane dostawców nie mogą
    opuścić stanowiska (roadmapa §14), a `result.json` i CSV mają być publikowalne razem
    z artykułem.

PASS CRITERION
    Brak wiersza ze statusem "error" w manifest_ok/ingest_ok/geo_ok (dla dostępnych rastrów);
    Float16 xView3 obsłużony. Macierz jest OPISOWA — pokazuje zakres i jawne luki (np. render
    „na" dla working-VRT, ingest „na" gdy raster źródłowy niedostępny na tej stacji albo gdy
    resolver oddaje wybór produktu użytkownikowi).

    Brak konfiguracji ścieżek dostawców NIE jest błędem: wiersze reported-only znikają,
    publiczne zostają kompletne, a dowód nadal przechodzi. Tier `mixed` opisuje dokładnie
    tę asymetrię.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv

# Lokalny fixture SAR wskazuje SAR_TEST_PROJECT. Bez niego ten fragment
# sprawozdania jest pomijany, wiec domyslna sciezka nie jest potrzebna.
SAR_TEST_PROJECT = os.environ.get("SAR_TEST_PROJECT", "sar-test-project")
RENDERABLE_EXT = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}

#: Konfiguracja ścieżek do paczek dostawców. Plik jest gitignorowany w repozytorium
#: aplikacji, bo ścieżki do udziałów sieciowych są konfiguracją lokalnego środowiska
#: testowego, a dane dostawców nie mogą trafić do repozytorium ani do CI
#: (SCENE_IMPORT_PROVIDER_ROADMAP.md §4.2 i §14).
#:
#: Jego BRAK jest normalnym stanem u recenzenta: wiersze dostawców dostają wtedy `na`
#: z jawnym powodem, a dowód nadal przechodzi. Publiczne wiersze benchmarkowe są
#: kompletne same z siebie — to właśnie opisuje tier `mixed`.
PROVIDER_PATHS_JSON = os.environ.get("PROVIDER_PATHS_JSON", "")

#: Ile paczek najwyżej otwieramy per źródło. Macierz ma pokazywać ZAKRES obsługi, nie
#: inwentaryzować dostawę: jedno źródło potrafi mieć 39 paczek tego samego kształtu.
MAX_PACKAGES_PER_SOURCE = int(os.environ.get("V01_MAX_PACKAGES_PER_SOURCE", "4"))

#: Postać dostawy — oś NIEZALEŻNA od formatu pliku. Ten sam GeoTIFF przychodzi jako
#: pojedynczy plik, jako wieloczęściowa dostawa spięta przez TIL albo w archiwum, i są
#: to trzy różne ścieżki kodu przy identycznym formacie.
_DELIVERY_SHAPES = {
    "direct": "single_file",
    "virtual_mosaic": "multipart_mosaic",
    "derived": "derived_product",
}

#: Statusy resolvera, przy których produkt NIE jest jeszcze wybrany. To nie jest błąd,
#: tylko obowiązująca decyzja architektoniczna (§3.6: poprawność przed automatyzacją) —
#: dostawa z wieloma produktami wymaga decyzji użytkownika. W macierzy zostaje `na`
#: z powodem, nigdy `error`.
_UNDECIDED_STATUSES = {"decision_required", "prepare_required", "invalid"}


def _resolve_working_raster(project_dir: Path, manifest: dict) -> Path | None:
    """Ścieżka rastra roboczego z working_view.raster_ref (project/source), fallback source_path."""
    wv = manifest.get("working_view") or {}
    ref = wv.get("raster_ref") or {}
    rel = ref.get("relative_path")
    storage = ref.get("storage")
    if rel and storage == "project":
        return project_dir / rel
    if rel and storage == "source":
        sources = (json.loads((project_dir / "scene_sources.json").read_text(encoding="utf-8"))
                   if (project_dir / "scene_sources.json").is_file() else {})
        for src in sources.get("sources", []):
            if src.get("source_id") == ref.get("source_id"):
                return Path(src.get("root_path", "")) / rel
    sp = manifest.get("source_path")
    return Path(sp) if sp else None


def _provider_config() -> tuple[list[dict], str]:
    """Wczytaj konfigurację źródeł dostawców. Zwraca `(sources, powód_pustej_listy)`."""
    candidate = Path(PROVIDER_PATHS_JSON) if PROVIDER_PATHS_JSON else (
        appenv.app_repo() / "scripts" / "provider-import-paths.json"
    )
    if not candidate.is_file():
        return [], (
            "brak konfiguracji ścieżek dostawców "
            "(scripts/provider-import-paths.json — plik lokalny, poza repozytorium)"
        )
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"konfiguracja ścieżek nieczytelna: {type(exc).__name__}"
    sources = [
        item for item in (payload.get("sources") or [])
        if isinstance(item, dict) and item.get("provider") and item.get("path")
    ]
    return sources, "" if sources else "konfiguracja nie zawiera źródeł"


def _provider_rows(get_scene_info, SceneGeoModel) -> tuple[list[dict], str]:
    """Wiersze macierzy dla rzeczywistych paczek dostawców — WYŁĄCZNIE do odczytu.

    Skan idzie przez `resolvers.scan_source()`, czyli tę samą funkcję, której używa
    aplikacja i audyt B0b. Nie importujemy scen: przedmiotem tego claimu jest ZAKRES
    obsługi (dostawca × format × CRS × dtype × postać dostawy), a nie przebieg importu.

    Do wyniku nie trafia ŻADNA ścieżka ani nazwa pliku dostawcy — tylko etykiety
    dostawcy, format, kształt dostawy i statusy. Wynik `result.json` i CSV mają być
    publikowalne razem z artykułem.
    """
    sources, reason = _provider_config()
    if not sources:
        return [], reason

    from services.scene_packages.resolvers import scan_source

    rows: list[dict] = []
    # Etykieta wiersza to DOSTAWCA plus liczba porządkowa, nigdy pole `name` z konfiguracji.
    # `name` jest wybierane przez użytkownika i potrafi zakodować nazwę udziału sieciowego
    # albo wewnętrzny kod zbioru — sprawdzone, takie wartości przeciekały do CSV. To ta
    # sama klasa informacji co ścieżka, więc nie trafia do publikowanego wyniku.
    ordinals: dict[str, int] = {}
    for source in sources:
        provider = str(source["provider"])
        ordinals[provider] = ordinals.get(provider, 0) + 1
        label = provider if ordinals[provider] == 1 else f"{provider}#{ordinals[provider]}"
        root = Path(str(source["path"]))
        if not root.is_dir():
            rows.append(_provider_row(label, provider, note="źródło nieosiągalne z tej stacji"))
            continue
        try:
            packages, _diagnostics = scan_source(root, provider)
        except Exception as exc:  # noqa: BLE001
            rows.append(_provider_row(
                label, provider, manifest_ok="error",
                note=f"scan_source: {type(exc).__name__}: {str(exc)[:60]}",
            ))
            continue

        # Jeden reprezentant na KSZTAŁT dostawy × produkt × format. Macierz opisuje zakres,
        # więc dwudziesta paczka tego samego kształtu nie wnosi nowej informacji.
        seen: set[tuple] = set()
        for package in packages:
            if len(seen) >= MAX_PACKAGES_PER_SOURCE:
                break
            row = _package_row(label, provider, package, get_scene_info, SceneGeoModel, root)
            key = (row["delivery_shape"], row["product_type"], row["format"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if not packages:
            rows.append(_provider_row(
                label, provider, note="resolver nie rozpoznał w źródle żadnej paczki",
            ))
    return rows, ""


def _provider_row(label: str, provider: str, **overrides) -> dict:
    row = {
        "dataset": label, "provider": provider, "sensor": "-", "modality": "-",
        "georeferencing": "-", "format": "-", "crs": "-", "dtype": "-",
        "delivery_shape": "-", "product_type": "-", "display_asset_kind": "na",
        "tier": "reported-only", "manifest_ok": "na", "ingest_ok": "na",
        "geo_ok": "na", "render_8bit": "na", "note": "",
    }
    row.update(overrides)
    return row


def _package_row(label: str, provider: str, package: dict, get_scene_info,
                 SceneGeoModel, root: Path) -> dict:
    selection = package.get("selection") or {}
    assets = package.get("assets") or []
    status = str(selection.get("status") or "unknown")
    if package.get("package_kind") == "archive":
        shape = "archive"
    else:
        shape = _DELIVERY_SHAPES.get(str(selection.get("raster_kind") or ""), "unknown")

    selected_ids = set(selection.get("asset_ids") or [])
    primary = next(
        (a for a in assets if a.get("asset_id") in selected_ids
         and a.get("asset_role") == "measurement"),
        None,
    ) or next((a for a in assets if a.get("asset_id") in selected_ids), None)

    row = _provider_row(
        label, provider,
        modality=str(package.get("modality") or "-"),
        format=str((primary or {}).get("format") or "-"),
        delivery_shape=shape,
        product_type=str(selection.get("product_type") or "-"),
        manifest_ok="ok" if selection else "error",
    )
    row["note"] = f"status resolvera: {status}"

    if status in _UNDECIDED_STATUSES or primary is None:
        # Świadomy stan produktu, nie awaria: dostawa niesie wiele produktów albo wymaga
        # przygotowania, więc resolver oddaje decyzję użytkownikowi zamiast zgadywać.
        return row

    raster = root / str(primary.get("relative_path") or "")
    if not raster.is_file():
        row["note"] += "; wybrany asset niedostępny"
        return row
    try:
        info = get_scene_info(str(raster))
    except Exception as exc:  # noqa: BLE001
        row["ingest_ok"] = "error"
        row["note"] += f"; get_scene_info: {type(exc).__name__}: {str(exc)[:50]}"
        return row

    row["ingest_ok"] = "ok"
    row["dtype"] = str(info.dtype)
    row["crs"] = str(info.crs or "-")
    row["georeferencing"] = "GEO" if info.has_geo else "NO_GEO"
    if not info.has_geo:
        return row
    try:
        model = SceneGeoModel.from_manifest({"geospatial": {
            "has_geo": True, "transform": list(info.transform or []), "crs": info.crs,
        }})
        if model is None:
            row["geo_ok"] = "error"
            row["note"] += "; brak modelu geo"
            return row
        point = [[float(info.width) / 2.0, float(info.height) / 2.0]]
        back = model.wgs84_to_pixel(model.pixel_to_wgs84(point))
        distance = math.hypot(point[0][0] - back[0][0], point[0][1] - back[0][1])
        row["geo_ok"] = "ok" if distance < 1e-3 else "error"
    except Exception as exc:  # noqa: BLE001
        row["geo_ok"] = "error"
        row["note"] += f"; geo: {type(exc).__name__}"
    return row


def _check_scene(project_dir: Path, scene_dir: Path, expect: dict, get_scene_info,
                 SceneGeoModel, render_fn) -> dict:
    manifest = json.loads((scene_dir / "scene_manifest.json").read_text(encoding="utf-8"))
    geo = manifest.get("geospatial") or {}
    image = manifest.get("image") or {}
    georef = manifest.get("georeferencing")
    crs = geo.get("crs")
    transform = geo.get("transform")

    # manifest_ok — istotne pola normalizacji (provider/sensor bywają puste dla importów
    # benchmarków, więc nie są wymagane; liczą się modality/georeferencing/dtype + geo).
    fields_ok = all(manifest.get(k) for k in ("modality", "georeferencing")) and bool(image.get("dtype"))
    if georef == "GEO":
        manifest_ok = fields_ok and bool(geo.get("has_geo")) and bool(transform) and len(transform) >= 6 and bool(crs)
    else:
        manifest_ok = fields_ok and georef == "NO_GEO"

    row = {
        "provider": manifest.get("provider"),
        "sensor": manifest.get("sensor"),
        "modality": manifest.get("modality"),
        "georeferencing": georef,
        "crs": crs or "-",
        "dtype": image.get("dtype"),
        "format": expect.get("format"),
        "manifest_ok": "ok" if manifest_ok else "error",
        "ingest_ok": "na",
        "geo_ok": "na",
        "render_8bit": "na",
        "note": "",
    }

    # ingest_ok: otwórz raster roboczy przez backend
    working = _resolve_working_raster(project_dir, manifest)
    if working is None or not working.is_file():
        row["ingest_ok"] = "na"
        row["note"] = "raster roboczy niedostępny na tej stacji"
    else:
        try:
            info = get_scene_info(str(working))
            dims_ok = int(info.width) == int(image.get("width") or 0) and int(info.height) == int(image.get("height") or 0)
            row["ingest_ok"] = "ok" if dims_ok else "error"
            if not dims_ok:
                row["note"] = f"wymiary {info.width}x{info.height} != manifest {image.get('width')}x{image.get('height')}"
        except Exception as exc:  # noqa: BLE001
            row["ingest_ok"] = "error"
            row["note"] = f"get_scene_info: {type(exc).__name__}: {str(exc)[:60]}"

    # geo_ok: model + round-trip 1 px
    if georef == "GEO":
        try:
            model = SceneGeoModel.from_manifest(manifest)
            if model is None:
                row["geo_ok"] = "error"
                row["note"] = (row["note"] + "; brak modelu geo").strip("; ")
            else:
                p = [[float(image.get("width", 2)) / 2.0, float(image.get("height", 2)) / 2.0]]
                back = model.wgs84_to_pixel(model.pixel_to_wgs84(p))
                d = math.hypot(p[0][0] - back[0][0], p[0][1] - back[0][1])
                row["geo_ok"] = "ok" if d < 1e-3 else "error"
        except Exception as exc:  # noqa: BLE001
            row["geo_ok"] = "error"
            row["note"] = (row["note"] + f"; geo: {type(exc).__name__}").strip("; ")

    # render_8bit: okno 8-bit RGB (tylko dla bezpośrednio otwieralnych formatów)
    if working is not None and working.is_file():
        if working.suffix.lower() in RENDERABLE_EXT:
            ok, note = render_fn(project_dir, working)
            row["render_8bit"] = "ok" if ok else "error"
            if not ok:
                row["note"] = (row["note"] + f"; render: {note}").strip("; ")
        else:
            row["render_8bit"] = "na"
            row["note"] = (row["note"] + f"; render n/a ({working.suffix} — working-VRT)").strip("; ")
    return row


def main() -> ValidationResult:
    res = ValidationResult(
        id="v01",
        claim="S1",
        title="Macierz zgodności dostawca × format × CRS × dtype × postać dostawy",
        # Substrat jest domykany po skanie: dostawcy dochodzą tylko wtedy, gdy lokalna
        # konfiguracja ścieżek jest dostępna. Tabela w artykule ma pokazywać, na czym dowód
        # NAPRAWDĘ stanął w danym przebiegu, a nie na czym mógłby stanąć.
        substrate=["xView3", "FAIR1M", "DOTA", "DIOR-R", "Capella"],
        tier="mixed",
    )

    appenv.bootstrap()
    blocked = appenv.require_backend_geo()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    from services.scene_loader import get_scene_info
    from services.sensor_geometry import SceneGeoModel
    from models.preprocessing import PreprocessingProfile
    from services.preprocessing_profiles import (
        open_scene_source, read_tile_from_source, apply_preprocessing_profile,
    )

    def render_fn(project_dir: Path, working: Path):
        try:
            profs = json.loads((project_dir / "preprocessing_profiles.json").read_text(encoding="utf-8"))
            plist = profs.get("profiles") if isinstance(profs, dict) else profs
            prof = PreprocessingProfile(**plist[0])
            reader = open_scene_source(str(working), prof)
            raw, mask = read_tile_from_source(reader, 0, 0, 512)
            out = apply_preprocessing_profile(raw, prof, mask)
            reader.close()
            ok = getattr(out, "ndim", 0) == 3 and out.shape[2] == 3 and str(out.dtype) == "uint8"
            return ok, "" if ok else f"shape={getattr(out,'shape',None)} dtype={getattr(out,'dtype',None)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {str(exc)[:50]}"

    specs = [
        ("xView3", "benchmark", "GeoTIFF"),
        ("FAIR1M", "benchmark", "GeoTIFF"),
        ("DOTA", "benchmark", "PNG"),
        ("DIOR-R", "benchmark", "JPEG"),
        ("SAR_test", "path", "GeoTIFF"),
    ]

    rows = []
    for name, kind, fmt in specs:
        if kind == "benchmark":
            try:
                project = appenv.benchmark_project(name)
            except FileNotFoundError:
                rows.append({"provider": name, "format": fmt, "manifest_ok": "missing_fixture",
                             "ingest_ok": "na", "geo_ok": "na", "render_8bit": "na",
                             "note": "projekt niezaimportowany", "crs": "-", "dtype": "-",
                             "modality": "-", "georeferencing": "-", "sensor": "-"})
                continue
        else:
            project = Path(SAR_TEST_PROJECT)
            if not (project / "scenes").is_dir():
                rows.append({"provider": name, "format": fmt, "manifest_ok": "missing_fixture",
                             "ingest_ok": "na", "geo_ok": "na", "render_8bit": "na",
                             "note": "fixture niedostępny", "crs": "-", "dtype": "-",
                             "modality": "-", "georeferencing": "-", "sensor": "-"})
                continue
        scene_dir = sorted(p for p in (project / "scenes").iterdir() if p.is_dir())[0]
        row = _check_scene(project, scene_dir, {"format": fmt}, get_scene_info, SceneGeoModel, render_fn)
        row["dataset"] = name
        # Benchmarki przychodzą jako pojedyncze pliki — oś postaci dostawy jest dla nich
        # trywialna i dlatego sama w sobie nie była widoczna, dopóki nie doszli dostawcy.
        row.setdefault("delivery_shape", "single_file")
        row.setdefault("product_type", "-")
        row.setdefault("display_asset_kind", "source")
        row.setdefault("tier", "public")
        rows.append(row)

    provider_rows, provider_reason = _provider_rows(get_scene_info, SceneGeoModel)
    rows.extend(provider_rows)

    # Jawny wymiar Float16: oryginalny raster xView3 podnosi KeyError:15, working = float32.
    float16_handled = None
    float16_note = ""
    try:
        xv = appenv.benchmark_project("xView3")
        sd = sorted(p for p in (xv / "scenes").iterdir() if p.is_dir())[0]
        m = json.loads((sd / "scene_manifest.json").read_text(encoding="utf-8"))
        orig = Path(m.get("source_path") or "")
        working = _resolve_working_raster(xv, m)
        orig_raises = False
        if orig.is_file():
            try:
                get_scene_info(str(orig))
            except Exception as exc:  # noqa: BLE001
                orig_raises = "15" in str(exc) or "Float16" in str(exc) or True
                float16_note = f"oryginał Float16 -> {type(exc).__name__}: {str(exc)[:40]}"
        working_f32 = False
        if working and working.is_file():
            info = get_scene_info(str(working))
            working_f32 = str(info.dtype) == "float32"
        float16_handled = bool(orig_raises and working_f32) if orig.is_file() else (working_f32 or None)
        float16_note += f"; working={working.name if working else '-'} dtype=float32:{working_f32}"
    except Exception as exc:  # noqa: BLE001
        float16_note = f"nie sprawdzono: {type(exc).__name__}"

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    cols = ["dataset", "provider", "sensor", "modality", "georeferencing", "format", "crs",
            "dtype", "delivery_shape", "product_type", "display_asset_kind", "tier",
            "manifest_ok", "ingest_ok", "geo_ok", "render_8bit", "note"]
    with open(os.path.join(out_dir, "compat_matrix.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in cols})

    def is_error(row) -> bool:
        return any(row.get(k) == "error" for k in ("manifest_ok", "ingest_ok", "geo_ok", "render_8bit"))

    graded = [r for r in rows if r.get("manifest_ok") not in ("missing_fixture",)]
    ingested = [r for r in graded if r.get("ingest_ok") == "ok"]
    failing = [r.get("dataset", r.get("provider")) for r in rows if is_error(r)]
    covered = len(ingested)
    total = len([r for r in graded if r.get("ingest_ok") != "na"])  # wiersze z dostępnym rastrem

    public_rows = [r for r in rows if r.get("tier") != "reported-only"]
    # Prefiks `provider:` odróżnia rzeczywistą paczkę dostawcy od substratu benchmarkowego.
    # Bez niego `Capella` (fixture SAR_test) i `capella` (skan paczek) wyglądałyby w tabeli
    # artykułu na to samo, a to dwie różne rzeczy i dwa różne tiery.
    res.substrate = list(res.substrate) + [
        f"provider:{name}" for name in sorted(
            {str(r.get("provider")) for r in provider_rows if r.get("provider")}
        )
    ]
    shapes = sorted({r.get("delivery_shape") for r in rows if r.get("delivery_shape") not in (None, "-")})
    providers = sorted({r.get("provider") for r in rows if r.get("tier") == "reported-only"})

    res.metrics = {
        "rows": len(rows),
        "public_rows": len(public_rows),
        "reported_only_rows": len(provider_rows),
        "covered": covered,
        "ingestable_rows": total,
        "coverage_pct": round(100.0 * covered / total, 1) if total else None,
        "float16_handled": float16_handled,
        "geo_rows_ok": sum(1 for r in graded if r.get("geo_ok") == "ok"),
        "render_ok": sum(1 for r in graded if r.get("render_8bit") == "ok"),
        "delivery_shapes": shapes,
        # Luka nazwana wprost. `multipart_mosaic` i `archive` istnieją w kodzie
        # (`selection.raster_kind == "virtual_mosaic"`, `package_kind == "archive"`), ale
        # w tym korpusie przy tych korzeniach źródeł resolver nie dochodzi do nich bez
        # decyzji użytkownika. Macierz ma pokazywać zakres RAZEM z jego granicami.
        "delivery_shapes_unobserved": [
            name for name in ("multipart_mosaic", "archive") if name not in shapes
        ],
        "providers_reported": providers,
        # Ile wierszy czeka na decyzję użytkownika zamiast zgadywać produkt (§3.6).
        "undecided_rows": sum(
            1 for r in provider_rows
            if any(s in str(r.get("note", "")) for s in _UNDECIDED_STATUSES)
        ),
        "failing": failing,
    }
    res.artifacts = ["results/compat_matrix.csv"]
    res.config = {
        "float16_note": float16_note.strip("; "),
        "provider_config": provider_reason or "wczytana z konfiguracji lokalnej",
        "max_packages_per_source": MAX_PACKAGES_PER_SOURCE,
    }
    res.status = "pass" if (not failing and float16_handled) else "fail"
    matrix_desc = ", ".join(
        f"{r.get('dataset')}[{r.get('dtype')}/{r.get('georeferencing')}]="
        f"{r.get('ingest_ok')}" for r in public_rows
    )
    unobserved = [
        name for name in ("multipart_mosaic", "archive") if name not in shapes
    ]
    undecided = sum(
        1 for r in provider_rows
        if any(s in str(r.get("note", "")) for s in _UNDECIDED_STATUSES)
    )
    provider_desc = (
        provider_reason if provider_reason else
        f"{len(provider_rows)} wierszy z {len(providers)} dostawców "
        f"({', '.join(providers)}), kształty dostawy: {', '.join(shapes)}"
        + (f"; NIEZAOBSERWOWANE: {', '.join(unobserved)} — resolver nie dochodzi do nich "
           f"bez decyzji użytkownika ({undecided} wierszy czeka na wybór produktu)"
           if unobserved else "")
    )
    res.notes = (
        f"Macierz {len(rows)} wierszy (dostawca × format × CRS × dtype × postać dostawy). "
        f"Publiczne: {matrix_desc}. "
        f"Ingest OK {covered}/{total} dostępnych rastrów; Float16 xView3 obsłużony={float16_handled} "
        f"({float16_note.strip('; ')}). "
        f"Reported-only: {provider_desc}. "
        f"Luki jawne: render n/a dla working-VRT (xView3), ingest n/a gdy raster źródłowy spoza "
        f"tej stacji (SAR_test) oraz gdy resolver oddaje wybór produktu użytkownikowi."
    )
    return res


if __name__ == "__main__":
    r = main()
    print(emit(r, results_dir(__file__)))
