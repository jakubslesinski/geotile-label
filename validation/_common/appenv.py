"""Wspólny bootstrap środowiska dla dowodów, które sięgają do backendu aplikacji.

Dowody v03/v10/v11 uruchamiaj interpreterem środowiska backendu (rasterio/GDAL),
np. runtime aplikacji:

    & "$env:APPDATA\\GeoTileLabel\\runtime\\backend-env-cuda\\python.exe" ^
        validation/v11_geo_accuracy_external/run.py

``bootstrap()`` ustawia GDAL_DATA/PROJ_LIB z prefiksu środowiska (patrz memory
``backend-tests-proj-env`` — instalacja PROJ z PostgreSQL potrafi przesłonić GDAL),
dokłada backend i katalog importerów do ``sys.path`` i zapisuje ``APP_REPO`` (rewizja
kodu produktu trafia do ``result.json``). Ścieżki można nadpisać zmiennymi:

    GEOTILE_BACKEND          (domyślnie `backend/` w repozytorium, w którym leży ten plik)
    BENCHMARK_PROJECTS_ROOT  (domyślnie `data/benchmark_projects/` w tym repozytorium)

Domyślne wartości są wyliczane WZGLĘDEM repozytorium, nie zaszyte na sztywno: suite
jest częścią publicznego wydania i nie może nieść ścieżek jednej stacji. Benchmarków
(DOTA, FAIR1M, DIOR-R, xView3) nie ma w repozytorium — trzeba je zaimportować jako
projekty aplikacji i wskazać przez BENCHMARK_PROJECTS_ROOT.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Korzeń repozytorium: validation/_common/appenv.py -> validation -> <repo>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BACKEND = str(_REPO_ROOT / "backend")
_DEFAULT_BENCHMARK_ROOT = str(_REPO_ROOT / "data" / "benchmark_projects")


def backend_path() -> Path:
    return Path(os.environ.get("GEOTILE_BACKEND", _DEFAULT_BACKEND))


def app_repo() -> Path:
    # Rewizję kodu produktu liczymy z korzenia repo aplikacji (rodzic backendu).
    return backend_path().parent


def benchmark_root() -> Path:
    return Path(os.environ.get("BENCHMARK_PROJECTS_ROOT", _DEFAULT_BENCHMARK_ROOT))


def benchmark_project(name: str) -> Path:
    root = benchmark_root() / name
    if not root.is_dir():
        raise FileNotFoundError(
            f"Nie znaleziono zaimportowanego projektu benchmarku '{name}' w {benchmark_root()}. "
            f"Zaimportuj go najpierw (patrz ../importers/README.md)."
        )
    return root


def _configure_geo_env() -> None:
    """GDAL_DATA/PROJ_LIB/GDAL_DRIVER_PATH z prefiksu środowiska — PRZED importem rasterio."""
    env_root = Path(sys.prefix)
    gdal_data = env_root / "Library" / "share" / "gdal"
    proj_lib = env_root / "Library" / "share" / "proj"
    if gdal_data.is_dir():
        os.environ.setdefault("GDAL_DATA", str(gdal_data))
    if proj_lib.is_dir():
        os.environ.setdefault("PROJ_LIB", str(proj_lib))
        os.environ.setdefault("PROJ_DATA", str(proj_lib))
    # Sterowniki wtyczkowe leżą poza domyślną ścieżką GDAL-a w tym runtime. Bez tego
    # rasterio widzi 190 sterowników i ŻADNEGO JP2, mimo że `gdal_JP2OpenJPEG.dll` jest
    # na dysku. Cichy brak sterownika jest gorszy od błędu: dowód poszedłby inną ścieżką
    # odczytu i zmierzył nie to, co deklaruje. To samo robi
    # `backend/benchmarks/benchmark_scene_import.py`.
    driver_path = env_root / "Library" / "lib" / "gdalplugins"
    if driver_path.is_dir():
        os.environ.setdefault("GDAL_DRIVER_PATH", str(driver_path))
    # Samo wskazanie katalogu wtyczek NIE wystarcza: `gdal_JP2OpenJPEG.dll` zależy od
    # `openjp2.dll` z `Library/bin`, a GDAL ładuje wtyczkę zwykłym `LoadLibrary`, który
    # szuka zależności w PATH — `os.add_dll_directory()` tu nie działa. Bez tego wpisu
    # sterownik JEST na liście `env.drivers()`, ale każde otwarcie JP2 kończy się
    # `Can't load requested DLL … 126: Nie można odnaleźć określonego modułu`.
    binaries = env_root / "Library" / "bin"
    if binaries.is_dir():
        current = os.environ.get("PATH", "")
        if str(binaries) not in current.split(os.pathsep):
            os.environ["PATH"] = str(binaries) + os.pathsep + current


def _ensure_native_env() -> None:
    """Ustaw OMP/MKL/KMP i — jeśli trzeba — RE-EXEC procesu, by env był obecny PRZY STARCIE.

    numpy w środowisku runtime crashuje na `@` (BLAS/MKL, 0xc06d007f), a `os.environ` ustawiony
    już w trakcie NIE propaguje do załadowanego MKL — zmienne muszą być w środowisku procesu od
    startu (tak jak robi to `backend/main.py`). Dlatego przy pierwszym wejściu ustawiamy je i
    restartujemy proces z tym samym argv; strażnik zapobiega pętli. MUSI być wołane PRZED
    importem numpy/torch (bootstrap jest pierwszą rzeczą w main() każdego dowodu).
    """
    if os.environ.get("GEOTILE_VALIDATION_REEXEC") == "1":
        return
    # Nie re-spawnuj skryptu ze stdin/REPL (`python - <<EOF`, -c) — nie da się go odtworzyć.
    if not sys.argv or sys.argv[0] in ("-", "", "-c"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ["MKL_THREADING_LAYER"] = "SEQUENTIAL"
        return
    # Re-spawn przez subprocess (NIE os.execv — na Windows execv jest emulowany jako spawn+exit
    # rodzica, gubi stdout i kod wyjścia). Dziecko dostaje zmienne od STARTU → MKL nie crashuje.
    import subprocess

    env = dict(os.environ)
    env["GEOTILE_VALIDATION_REEXEC"] = "1"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    # KLUCZOWE: bez SEQUENTIAL numpy `@` crashuje (0xc06d007f) po załadowaniu GDAL+torch,
    # nawet z KMP_DUPLICATE_LIB_OK. To domyka konflikt runtime'ów OpenMP w MKL.
    env["MKL_THREADING_LAYER"] = "SEQUENTIAL"
    completed = subprocess.run([sys.executable] + sys.argv, env=env)
    sys.exit(completed.returncode)


def bootstrap() -> None:
    """Przygotuj proces do importu modułów backendu i importerów benchmarków."""
    _ensure_native_env()
    _configure_geo_env()
    os.environ.setdefault("APP_REPO", str(app_repo()))
    backend = str(backend_path())
    if backend not in sys.path:
        sys.path.insert(0, backend)
    importers = str(Path(__file__).resolve().parents[2] / "importers")
    if importers not in sys.path:
        sys.path.insert(0, importers)


def require_backend_geo() -> str | None:
    """Zwróć komunikat, jeśli środowisko nie ma stosu geo backendu (np. zły interpreter).

    Dowody importują backend leniwie; ten helper daje czytelny powód, gdy skrypt
    odpalono zwykłym pythonem zamiast interpreterem środowiska backendu.
    """
    try:
        import rasterio  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return (
            "Brak stosu geo backendu (rasterio) w tym interpreterze: "
            f"{exc}. Uruchom skrypt interpreterem środowiska backendu — patrz docstring appenv."
        )
    return None


def require_jp2_driver() -> str | None:
    """Zwróć komunikat, jeśli GDAL nie potrafi ZAPISAĆ I ODCZYTAĆ JP2.

    Dowód dotykający JP2 MUSI to sprawdzić na wejściu i skończyć się `todo`, a nie `pass`:
    bez działającego `JP2OpenJPEG` scena albo się nie otworzy, albo — groźniej — zostanie
    przeczytana inną ścieżką, a wynik będzie opisywał coś innego niż deklaruje.

    Sprawdzany jest REALNY round-trip 32×32, nie obecność nazwy na liście sterowników.
    Wtyczka jest ładowana leniwie: `env.drivers()` pokazuje `JP2OpenJPEG` również wtedy,
    gdy `gdal_JP2OpenJPEG.dll` nie da się załadować z powodu brakującej zależności —
    lista sterowników mówi wtedy „jest", a każde otwarcie pliku kończy się błędem.
    Koszt sondy: ~70 ms.
    """
    try:
        from osgeo import gdal
    except Exception as exc:  # noqa: BLE001
        return f"Brak osgeo.gdal: {exc}"
    gdal.UseExceptions()
    driver = gdal.GetDriverByName("JP2OpenJPEG")
    if driver is None:
        return (
            "GDAL bez sterownika JP2OpenJPEG. Wtyczka powinna leżeć w "
            "<prefix>/Library/lib/gdalplugins (GDAL_DRIVER_PATH)."
        )
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        probe = os.path.join(tmp, "probe.jp2")
        try:
            source = gdal.GetDriverByName("MEM").Create("", 32, 32, 1, gdal.GDT_Byte)
            written = driver.CreateCopy(probe, source)
            written = None  # noqa: F841 — zamknij uchwyt przed odczytem (Windows)
            opened = gdal.Open(probe)
            if opened is None:
                return "JP2OpenJPEG zapisał plik, ale GDAL nie potrafi go otworzyć."
            opened = None  # noqa: F841 — zwolnij plik przed sprzątaniem katalogu
        except UnicodeDecodeError as exc:
            # Komunikat GDAL-a przychodzi w kodowaniu systemu (na PL Windows cp1250)
            # i binding wywraca się na dekodowaniu, zanim pokaże przyczynę. Typowo jest
            # to `126: Nie można odnaleźć określonego modułu` — brak `openjp2.dll` w PATH.
            return (
                "JP2OpenJPEG nie ładuje się (komunikat GDAL-a w kodowaniu systemowym: "
                f"{exc}). Zwykle brakuje <prefix>/Library/bin w PATH."
            )
        except Exception as exc:  # noqa: BLE001
            return f"JP2OpenJPEG nie działa: {type(exc).__name__}: {exc}"
    return None
