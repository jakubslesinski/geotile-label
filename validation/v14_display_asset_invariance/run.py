"""v14 — Niezmienność adnotacji przy zmianie assetu wyświetlania  (Claim C3).

SUBSTRATE / TIER
    RAMIĘ A: fixture generowany — JP2 6000×4000 uint8, EPSG:32633, siatka bloków
    kodujących · public.
    RAMIĘ B: realna scena JP2 2,63 Gpx z jednocześnie obecną piramidą projektową
    i opublikowanym COG-iem · reported-only.  (mixed, gdy ramię B jest dostępne)

CLAIM
    Współrzędne adnotacji nie zależą od tego, który asset wyświetlania jest aktywny.
    Przejście sceny ze stanu „piramida projektowa od 2×" do „opublikowany COG 1×" zmienia
    wyłącznie dostępny poziom zoomu i rewizję assetu; układ współrzędnych zostaje nietknięty.

    Ryzyko jest realne, bo `max_zoom` pełni w kodzie trzy role naraz (R0.1 planu JP2):
    limit żądań, mnożnik okna odczytu `pixels_per_tile = TILE_SIZE * 2**(max_zoom - z)`
    oraz poziom referencyjny dla geometrii adnotacji. Zlanie ich w jedno przesunęłoby
    okna odczytu — i adnotacje — w momencie publikacji derywatu.

METHOD
    Jedna scena w dwóch stanach wyświetlania, oba budowane FUNKCJAMI PRODUKTU:
      stan 1: `working_view.build_direct_overviews` -> piramida projektowa od 2×,
      stan 2: `scene_import.run_scene_fullres_derivative_job` -> zbudowany, zwalidowany
              i atomowo opublikowany COG 1× (ta sama droga, którą idzie aplikacja).
    W obu stanach mierzone są:
      A. OKNO ODCZYTU — scena jest siatką bloków 512 px o stałych, różnych wartościach.
         Kod odczytany z wnętrza bloku mówi wprost, który fragment źródła trafił do
         danego kafla. Kafle produkuje `routers.scenes._produce_scene_tile_uncoordinated`
         wołane na TYCH SAMYCH `(z, x, y)` w obu stanach.
      B. `source_max_zoom` oraz współrzędne WGS84 dla ustalonych pikseli sceny
         (`SceneGeoModel.pixel_to_wgs84`, manifest czytany świeżo z dysku w obu stanach —
         zadanie fullres przepisuje manifest, więc to nie jest porównanie ze sobą samym).
      C. warunek wstępny: `available_native_zoom` MUSI się różnić między stanami, a próba
         MUSI zawierać co najmniej dwa różne układy kodów.

    Kafel jest produkowany FUNKCJĄ PRODUKCYJNĄ, nie własną kopią wzoru na okno. To jest
    cała moc tego dowodu: gdyby ktoś podmienił w niej `max_zoom` na `available_native_zoom`,
    skrypt z własną kopią wzoru przeszedłby taką zmianę bez mrugnięcia.

    Dlaczego kody bloków, a nie pozycja obiektu: patrz komentarz przy `BLOCK`. Krótko —
    dwie różne piramidy tego samego rastra mają różną fazę jądra decymacji, więc KAŻDY
    estymator położenia liczony z jasności mierzy radiometrię krawędzi, a nie układ
    współrzędnych. Wnętrze stałego bloku jest niezmienne przy dowolnym jądrze.

    RAMIĘ B mierzy tę samą niezmienność na REALNEJ scenie, ale w węższym zakresie i to
    jest świadome. Na realnym projekcie COG jest już opublikowany, więc serwer kafli zawsze
    odpowie z niego; żeby zobaczyć stan „podgląd 2×" trzeba by derywat wycofać, a to
    modyfikacja danych użytkownika. Ramię B porównuje więc ASSETY — georeferencję (pełna
    precyzja transformacji, CRS, wymiary, round-trip piksela) oraz kontrakt zoomu policzony
    dla obu ścieżek wyświetlania — a nie odpowiedzi serwera kafli. Dowód end-to-end przez
    funkcję produkcyjną niesie ramię A.

    Ramię B jest falsyfikowalne mimo węższego zakresu: gdyby budowa derywatu przesunęła
    georeferencję choćby o ułamek piksela, adnotacja z ramki źródła wylądowałaby na COG-u
    w innym miejscu, a porównanie transformacji by to pokazało. Oba assety muszą istnieć
    jednocześnie — publikacja COG-a nie usuwa `overview.vrt.ovr`, więc na realnym projekcie
    jest to stan normalny.

INPUTS
    - ramię A: brak danych zewnętrznych; fixture jest generowany (substrat publiczny)
    - ramię B: opcjonalnie projekt z piramidą projektową I opublikowanym derywatem 1×
      w katalogu danych aplikacji (`V14_REAL_DATA_DIR`, dom. `%APPDATA%/GeoTileLabel/data`)
    - backend: routers/scenes.py, services/scene_packages/*, services/sensor_geometry.py,
      services/scene_display_contract.py

OUTPUTS
    - results/tiles.csv  (stan, z, x, y, odczytane kody, faza krawędzi)
    - metrics: {window_mismatches, world_coord_max_delta_m, zoom_levels_differ,
                pyramid_phase_px_max, real_scene_arm{transforms_identical,
                pixel_roundtrip_max_px, source_max_zoom_stable, …}, …}

PASS CRITERION
    `window_mismatches == 0` I `world_coord_max_delta_m == 0` I `source_max_zoom_stable`.
    Zero, nie „małe": to nie jest pomiar dokładności, tylko sprawdzenie, czy okno odczytu
    i układ współrzędnych w ogóle drgnęły. Warunki z punktu C chronią przed dowodem-atrapą:
    jeśli stany są nierozróżnialne albo próba nie ma zmienności, wynik to `todo`, nie `pass`.

    `pyramid_phase_px_max` jest RAPORTOWANA, nie bramkująca — opisuje różnicę fazy dwóch
    piramid, która jest oczekiwana i nie dotyczy współrzędnych. Ogranicza ją tylko próg
    sensowności (`PHASE_SANITY_BOUND_PX`), żeby przesunięcie obrazu o rząd wielkości
    większe nie przeszło niezauważone.

    RAMIĘ B bramkuje TYLKO wtedy, gdy jest dostępne: wymaga identycznej transformacji, CRS
    i wymiarów w źródle, podglądzie i COG-u oraz stabilnego `source_max_zoom` przy zmianie
    dostępnego zoomu. Jego brak (dane pod licencją, inna stacja) nie unieważnia dowodu
    publicznego — na tym polega tier `reported-only`.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common.result import ValidationResult, emit, results_dir
from _common import appenv

PROJECT_ID = os.environ.get("V14_PROJECT_ID", "v14-display-invariance")
SCENE_ID = "fixture-01"
VARIANT_ID = "v14"
WIDTH = int(os.environ.get("V14_WIDTH", "6000"))
HEIGHT = int(os.environ.get("V14_HEIGHT", "4000"))

TILE_SIZE = 256

#: Bok bloku kodującego, w pikselach ŹRÓDŁA. Każdy blok ma jedną stałą wartość, która
#: jednoznacznie identyfikuje jego położenie w scenie.
#:
#: To jest przyrząd całego dowodu i jego wybór nie jest kosmetyczny. Pierwsza wersja
#: mierzyła centroid kwadratowego markera i dawała 0,054 px różnicy między stanami —
#: ZMIERZONE, nie założone. Przyczyna: stan 1 czyta piramidę projektową skopiowaną
#: z natywnego poziomu JP2 (falka), a stan 2 decymuje COG uśrednianiem pudełkowym.
#: Dwie różne piramidy tego samego rastra mają różną fazę jądra — stan 1 zwraca na
#: krawędzi markera wartości 223 i 32 tam, gdzie stan 2 zwraca twarde 255 i 0.
#: Każdy estymator położenia liczony z JASNOŚCI dziedziczy tę różnicę, więc mierzył
#: radiometrię krawędzi, a nie układ współrzędnych.
#:
#: WNĘTRZE stałego bloku jest niezmienne przy dowolnym znormalizowanym jądrze. Odczyt
#: kodu z wnętrza mówi więc DOKŁADNIE, który fragment źródła trafił do danego kafla —
#: a to jest dokładnie ta wielkość, o którą chodzi w claimie, i daje się porównać
#: bez tolerancji.
BLOCK = 512
#: Kody bloków. Zakres z zapasem od zera, żeby żadna wartość nie kolidowała z tłem.
CODE_BASE = 10

#: Kody są czytelne, bo render NIE normalizuje wartości per kafel — sprawdzone: kafel bez
#: jasnych obiektów wraca z `max=30`, a nie rozciągnięty do 255. Gdyby profil zaczął
#: normalizować, kody przestałyby być odróżnialne i dowód skończyłby się na `todo`
#: (za mało różnych układów kodów), a nie fałszywym `pass`.
#:
#: Górna granica sensowności dla RAPORTOWANEJ różnicy fazy między piramidami. Nie jest
#: to tolerancja geometrii — bramkuje wyłącznie kody bloków, świat i poziom referencyjny.
#: Ten próg ma złapać sytuację, w której „różnica jądra" urosłaby do rozmiaru realnego
#: przesunięcia obrazu.
PHASE_SANITY_BOUND_PX = 1.0

#: Od ilu pikseli produkt uznaje JP2 za „duży", czyli wymagający piramidy projektowej
#: zamiast natywnych poziomów. Domyślnie 256 Mpx (`DEFAULT_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS`),
#: więc fixture 24 Mpx byłby display-ready i drugi stan wyświetlania nigdy by nie powstał.
#:
#: Obniżamy PRODUKTOWE pokrętło (`GEOTILE_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS`), zamiast
#: generować raster 280 Mpx: badaną zmienną jest zmiana assetu wyświetlania, a nie próg,
#: przy którym produkt ją uruchamia. Fixture zastępuje dużą scenę dokładnie w tym jednym
#: wymiarze — reszta ścieżki (budowa piramidy, budowa i publikacja COG, render kafla)
#: jest identyczna jak dla sceny 2,6 Gpx.
DISPLAY_READY_THRESHOLD_PX = "1000000"


def _blocks_across() -> tuple[int, int]:
    return (math.ceil(WIDTH / BLOCK), math.ceil(HEIGHT / BLOCK))


def _block_code(bx: int, by: int) -> int:
    """Wartość piksela dla bloku `(bx, by)` — różna dla każdego bloku sceny.

    Różnorodność jest warunkiem mocy dowodu: gdyby wszystkie bloki miały tę samą wartość,
    porównanie kodów przechodziłoby również przy przesuniętym oknie odczytu.
    """
    across, _ = _blocks_across()
    return CODE_BASE + (by * across + bx)


def _probe_pixels() -> list[tuple[int, int]]:
    """Piksele sceny, dla których liczymy WGS84 — rogi, środek i granice bloków."""
    margin = 200
    return [
        (margin, margin),
        (WIDTH - margin, margin),
        (margin, HEIGHT - margin),
        (WIDTH - margin, HEIGHT - margin),
        (WIDTH // 2, HEIGHT // 2),
        (BLOCK * 2, BLOCK * 4),
        (BLOCK * 4, BLOCK * 2),
    ]


def _build_fixture(target_dir: Path) -> Path:
    """Wygeneruj JP2 z markerami. Zwraca ścieżkę pliku źródłowego sceny.

    JP2, nie GeoTIFF: `classify_display_assets()` nadaje `finest_display_factor > 1`
    wyłącznie na ścieżce piramidy projektowej i dekodu JP2. Dla GeoTIFF-a ścieżka
    wyświetlania równa się źródłu, oba stany wyszłyby identyczne i dowód nie sprawdziłby
    niczego.

    JP2 musi mieć NATYWNE poziomy (`RESOLUTIONS=6`). Bez nich `build_direct_overviews()`
    nie użyje strategii natywnej JP2, `direct_preview_asset()` zwróci `None`, ścieżką
    wyświetlania zostanie VRT oparty o JP2 — a takiej sceny produkt świadomie NIE serwuje
    (`Display preview is unavailable; source JP2 decode is disabled`). Stan 1 nie
    wyrenderowałby wtedy ani jednego kafla. Sprawdzone: przy `RESOLUTIONS=1` dowód kończy
    się na `todo` z zerem wspólnych kafli.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from osgeo import gdal

    gdal.UseExceptions()
    target_dir.mkdir(parents=True, exist_ok=True)
    across, down = _blocks_across()
    raster = np.zeros((HEIGHT, WIDTH), dtype="uint8")
    for by in range(down):
        for bx in range(across):
            raster[by * BLOCK:(by + 1) * BLOCK, bx * BLOCK:(bx + 1) * BLOCK] = _block_code(bx, by)

    tif = target_dir / "fixture.tif"
    with rasterio.open(
        tif, "w", driver="GTiff", width=WIDTH, height=HEIGHT, count=1, dtype="uint8",
        crs="EPSG:32633", transform=from_origin(500000.0, 5000000.0, 0.5, 0.5),
    ) as dst:
        dst.write(raster, 1)

    jp2 = target_dir / "fixture.jp2"
    jp2.unlink(missing_ok=True)
    source = gdal.Open(str(tif))
    written = gdal.GetDriverByName("JP2OpenJPEG").CreateCopy(
        str(jp2), source, options=["RESOLUTIONS=6", "QUALITY=100", "REVERSIBLE=YES"],
    )
    written = None
    source = None
    tif.unlink(missing_ok=True)
    return jp2


def _mount_project() -> Path:
    """Zbuduj projekt od zera i zwróć ścieżkę rastra sceny.

    Projekt jest kasowany i tworzony na nowo przy każdym uruchomieniu: dowód ma być
    powtarzalny, a resztka po poprzednim przebiegu (opublikowany COG!) zmieniłaby stan
    startowy i unieważniła warunek wstępny.
    """
    import hashlib
    from db.storage import create_project_root, project_dir, save_json, save_scene_json

    root = project_dir(PROJECT_ID)
    shutil.rmtree(root, ignore_errors=True)
    create_project_root(PROJECT_ID, PROJECT_ID)
    root = project_dir(PROJECT_ID)

    jp2 = _build_fixture(root / "sources")
    relative = jp2.relative_to(root).as_posix()
    fingerprint = "sha256:" + hashlib.sha256(jp2.read_bytes()).hexdigest()[:32]
    raster_ref = {"storage": "project", "relative_path": relative}

    save_json(PROJECT_ID, "project", {
        "id": PROJECT_ID,
        "name": PROJECT_ID,
        "source_type": "managed",
    })
    save_scene_json(PROJECT_ID, SCENE_ID, "scene", {
        "id": SCENE_ID,
        "filename": jp2.name,
        "display_name": jp2.name,
        "raster_kind": "direct",
        "working_variant_id": VARIANT_ID,
        "raster_ref": raster_ref,
    })
    save_scene_json(PROJECT_ID, SCENE_ID, "scene_manifest", {
        "working_view": {
            "raster_kind": "direct",
            "variant_id": VARIANT_ID,
            "raster_ref": raster_ref,
        },
        # Fingerprint źródła jest warunkiem aktywacji derywatu: `published_fullres_cog()`
        # zwraca ścieżkę tylko wtedy, gdy zapis publikacji zgadza się z manifestem.
        "source_identity": {"source_scene_fingerprint": fingerprint},
    })
    return jp2


class _JobContext:
    """Minimalny kontekst zadania: tyle, ile woła kod produkcyjny."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def cancel_requested(self) -> bool:
        return False


def _contract():
    """Kontrakt zoomu liczony tak, jak liczy go endpoint kafla."""
    from routers.scenes import _scene_render_context, _zoom_contract

    ctx = _scene_render_context(PROJECT_ID, SCENE_ID)
    return _zoom_contract(PROJECT_ID, SCENE_ID, ctx), ctx


def _render_tile(z: int, x: int, y: int):
    """Kafel z FUNKCJI PRODUKCYJNEJ -> tablica 2D uint8 albo None, gdy odrzucony.

    `_produce_scene_tile_uncoordinated` to dokładnie to, co wykonuje endpoint po zdjęciu
    warstwy asynchronicznej. Odrzucenie poziomu (HTTP 409 powyżej `available_native_zoom`)
    jest tu poprawną odpowiedzią, nie błędem — stan 1 MA nie serwować 1×.
    """
    import numpy as np
    from PIL import Image
    from fastapi import HTTPException
    from routers.scenes import _produce_scene_tile_uncoordinated

    try:
        png, _ = _produce_scene_tile_uncoordinated(
            PROJECT_ID, SCENE_ID, z, x, y, 1.0, 1.0, 1.0, 0.0, 100.0,
        )
    except HTTPException:
        return None
    image = Image.open(io.BytesIO(png)).convert("L")
    return np.asarray(image)


def _sample_codes(tile) -> tuple[int, ...] | None:
    """Kody bloków odczytane z WNĘTRZA pięciu obszarów kafla: środek i cztery ćwiartki.

    Mediana z małej łatki, nie pojedynczy piksel: chroni przed sytuacją, w której punkt
    próbkowania wypadnie dokładnie na granicy bloków. Próbki są brane głęboko we wnętrzu,
    więc rozmycie granicy przez jądro decymacji ich nie dotyka.
    """
    import numpy as np

    if tile is None:
        return None
    height, width = tile.shape[:2]
    patch = 5
    points = [
        (height // 2, width // 2),
        (height // 4, width // 4),
        (height // 4, 3 * width // 4),
        (3 * height // 4, width // 4),
        (3 * height // 4, 3 * width // 4),
    ]
    codes = []
    for row, col in points:
        window = tile[
            max(0, row - patch):row + patch + 1,
            max(0, col - patch):col + patch + 1,
        ]
        if window.size == 0:
            return None
        codes.append(int(np.median(window)))
    return tuple(codes)


def _edge_phase(tile) -> float | None:
    """Podpikselowe położenie najsilniejszej pionowej granicy bloków w kaflu.

    Wielkość RAPORTOWANA, nie bramkująca. Mierzy różnicę fazy między dwiema piramidami
    tego samego rastra (natywny poziom falkowy JP2 vs uśrednianie pudełkowe w COG-u).
    Taka różnica jest oczekiwana i nie ma nic wspólnego z układem współrzędnych — ale
    warto ją znać i ograniczyć, bo przesunięcie o pół kafla ujawniłoby się i tutaj.
    """
    import numpy as np

    if tile is None:
        return None
    row = tile[tile.shape[0] // 2].astype("float64")
    gradient = np.abs(np.diff(row))
    if gradient.size < 20 or gradient.max() <= 0:
        return None
    index = int(np.argmax(gradient))
    if index < 10 or index > row.size - 11:
        return None
    low = float(np.median(row[index - 9:index - 2]))
    high = float(np.median(row[index + 4:index + 11]))
    if low == high:
        return None
    half = (low + high) / 2.0
    # Przejście przez połowę skoku, interpolowane liniowo. Dla symetrycznego jądra
    # położenie tego przejścia nie zależy od szerokości rozmycia.
    for offset in range(index - 6, index + 6):
        first, second = row[offset], row[offset + 1]
        if (first - half) * (second - half) <= 0 and first != second:
            return float(offset) + (half - first) / (second - first)
    return None


def _candidate_tiles(z: int, max_zoom: int) -> list[tuple[int, int]]:
    """Kafle, na których porównujemy stany.

    To jest wyłącznie ograniczenie przeszukiwania, nigdy punkt odniesienia: dowód
    porównuje stan 1 ze stanem 2 na tych samych `(z, x, y)`, więc nietrafiony wybór
    kafla może co najwyżej zmniejszyć próbkę, nigdy sfałszować wynik na „pass".
    """
    span = TILE_SIZE * (2 ** (max_zoom - z))
    across = math.ceil(WIDTH / span)
    down = math.ceil(HEIGHT / span)
    return [(x, y) for y in range(down) for x in range(across)]


def _measure(state: str, max_zoom: int, available: int, rows: list[dict]) -> dict[tuple, tuple]:
    """Kody bloków (i faza krawędzi) dla wszystkich badanych kafli w danym stanie."""
    found: dict[tuple, tuple] = {}
    for z in (available, available - 1):
        if z < 0:
            continue
        for x, y in _candidate_tiles(z, max_zoom):
            tile = _render_tile(z, x, y)
            codes = _sample_codes(tile)
            if codes is None:
                continue
            found[(z, x, y)] = codes
            rows.append({
                "state": state, "z": z, "x": x, "y": y,
                "codes": " ".join(str(code) for code in codes),
                "edge_phase": _edge_phase(tile),
            })
    return found


#: Katalog danych zainstalowanej aplikacji — tam leżą realne projekty z opublikowanymi
#: derywatami. Ramię B jest opcjonalne i `reported-only`.
REAL_DATA_DIR = os.environ.get("V14_REAL_DATA_DIR", "")


def _find_real_scene():
    """Największa scena z JEDNOCZEŚNIE obecną piramidą projektową i opublikowanym COG-iem.

    Oba assety muszą istnieć naraz, bo tylko wtedy da się porównać dwa stany wyświetlania
    bez modyfikowania czegokolwiek. Publikacja COG-a nie usuwa `overview.vrt.ovr`, więc na
    realnym projekcie jest to stan normalny, a nie wyjątek.
    """
    root = Path(REAL_DATA_DIR) if REAL_DATA_DIR else (
        Path(os.environ.get("APPDATA", "")) / "GeoTileLabel" / "data"
    )
    projects = root / "projects"
    if not projects.is_dir():
        return None
    best = None
    for cog in projects.glob("*/derived_scenes/*/*/fullres/fullres.tif"):
        preview = cog.parent.parent / "overview.vrt"
        if not (preview.is_file() and preview.with_name("overview.vrt.ovr").is_file()):
            continue
        try:
            size = cog.stat().st_size
        except OSError:
            continue
        if best is None or size > best[0]:
            best = (size, cog, preview)
    return best[1:] if best else None


def _real_source_raster(project: Path, manifest: dict) -> Path | None:
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


def _real_scene_arm():
    """Ramię B — ta sama niezmienność na REALNEJ scenie JP2 (`reported-only`).

    Zwraca `(metryki, "")` albo `(None, powód)`. Wyłącznie odczyt.

    ZAKRES jest węższy niż w ramieniu A i to jest świadome. Na realnym projekcie COG jest
    już opublikowany, więc serwer kafli zawsze odpowie z niego; żeby zobaczyć stan „podgląd
    2×" trzeba by derywat wycofać, a to modyfikacja danych użytkownika. Ramię B porównuje
    więc ASSETY — georeferencję i kontrakt zoomu — a nie odpowiedzi serwera kafli. Dowód
    end-to-end przez funkcję produkcyjną niesie ramię A.

    To wystarcza, żeby ramię B było falsyfikowalne: gdyby budowa derywatu przesunęła
    georeferencję choćby o ułamek piksela, adnotacje z ramki źródła wylądowałyby na COG-u
    w innym miejscu, a porównanie transformacji by to pokazało.
    """
    found = _find_real_scene()
    if found is None:
        return None, (
            "brak realnej sceny z JEDNOCZESNIE obecna piramida projektowa i opublikowanym "
            "COG-iem (ramie B konsumuje gotowe assety, nie buduje ich)"
        )
    cog, preview = found
    variant_id = cog.parent.parent.name
    scene_id = cog.parent.parent.parent.name
    project = cog.parents[4]
    manifest_path = project / "scenes" / scene_id / "scene_manifest.json"
    if not manifest_path.is_file():
        return None, "derywat bez manifestu sceny"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest.get("source_identity") or {}

    from services.scene_packages.fullres_cog_builder import published_fullres_cog
    from services.scene_display_contract import (
        classify_display_assets, compute_zoom_contract,
    )
    from services.scene_overviews import inspect_source_overviews

    published = published_fullres_cog(
        project, scene_id, variant_id,
        source_fingerprint=identity.get("source_scene_fingerprint")
        or identity.get("source_package_fingerprint"),
    )
    if published is None:
        return None, "derywat istnieje, ale nie jest aktywny wg zapisu publikacji"

    source = _real_source_raster(project, manifest)
    if source is None or not source.is_file():
        return None, "raster zrodlowy niedostepny z tej stacji"

    import rasterio
    import rasterio.transform as rio_transform

    grids: dict[str, tuple] = {}
    factors: list[int] = []
    for label, path in (("source", source), ("preview", preview), ("cog", published)):
        with rasterio.open(path) as dataset:
            grids[label] = (tuple(dataset.transform), str(dataset.crs),
                            dataset.width, dataset.height)
            if label == "preview":
                factors = [int(v) for v in dataset.overviews(1) if int(v) > 1]

    transforms_identical = (
        grids["preview"][0] == grids["source"][0] == grids["cog"][0]
    )
    crs_identical = grids["preview"][1] == grids["source"][1] == grids["cog"][1]
    size_identical = (
        grids["preview"][2:] == grids["source"][2:] == grids["cog"][2:]
    )

    width, height = grids["source"][2], grids["source"][3]
    probes = [(0, 0), (width - 1, height - 1), (width // 2, height // 2),
              (width // 3, 2 * height // 3)]
    source_affine = rasterio.Affine(*grids["source"][0][:6])
    worst = 0.0
    for label in ("preview", "cog"):
        affine = rasterio.Affine(*grids[label][0][:6])
        for px, py in probes:
            x, y = rio_transform.xy(source_affine, py, px, offset="ul")
            row, col = rio_transform.rowcol(affine, x, y, op=float)
            worst = max(worst, abs(col - px), abs(row - py))

    scene_info = {
        "width": width, "height": height,
        "source_overviews": inspect_source_overviews(source),
    }
    contracts = {}
    for label, display in (("preview", preview.with_name("overview.vrt.ovr")),
                           ("cog", published)):
        assets = classify_display_assets(
            source_path=source, display_path=display, raster_kind="direct",
            scene_info=scene_info, overview_factors=factors,
        )
        contracts[label] = compute_zoom_contract(scene_info=scene_info, assets=assets)

    return {
        "provider": str(manifest.get("provider") or "unknown"),
        "scene_wh": [width, height],
        "scene_px": width * height,
        "transforms_identical": transforms_identical,
        "crs_identical": crs_identical,
        "dimensions_identical": size_identical,
        "pixel_roundtrip_max_px": float(f"{worst:.3e}"),
        "probe_pixels": len(probes),
        "source_max_zoom": contracts["cog"].source_max_zoom,
        "source_max_zoom_stable": (
            contracts["preview"].source_max_zoom == contracts["cog"].source_max_zoom
        ),
        "available_native_zoom_preview": contracts["preview"].available_native_zoom,
        "available_native_zoom_cog": contracts["cog"].available_native_zoom,
        "zoom_levels_differ": (
            contracts["preview"].available_native_zoom
            != contracts["cog"].available_native_zoom
        ),
        "display_asset_kind_preview": contracts["preview"].assets.display_asset_kind,
        "display_asset_kind_cog": contracts["cog"].assets.display_asset_kind,
    }, ""


def _world_points() -> list[dict]:
    """WGS84 dla ustalonych pikseli sceny, z manifestu czytanego ŚWIEŻO z dysku."""
    from db.storage import load_scene_json
    from services.sensor_geometry import SceneGeoModel

    manifest = load_scene_json(PROJECT_ID, SCENE_ID, "scene_manifest", default={}) or {}
    model = SceneGeoModel.from_manifest(manifest)
    if model is None:
        return []
    return [
        {"px": px, "py": py, "lon": point[0], "lat": point[1]}
        for (px, py), point in zip(
            _probe_pixels(), model.pixel_to_wgs84(_probe_pixels())
        )
    ]


def _geospatial_block(scene_info: dict) -> dict:
    """Blok `geospatial` manifestu z metadanych sceny wypełnionych przez backend."""
    return {
        "has_geo": bool(scene_info.get("crs") and scene_info.get("transform")),
        "transform": scene_info.get("transform"),
        "crs": scene_info.get("crs"),
    }


def _max_world_delta(before: list[dict], after: list[dict]) -> float:
    """Największa rozbieżność współrzędnych w METRACH (przybliżenie równoleżnikowe)."""
    if not before or not after or len(before) != len(after):
        return float("nan")
    worst = 0.0
    for a, b in zip(before, after):
        lat = math.radians((a["lat"] + b["lat"]) / 2.0)
        dx = (a["lon"] - b["lon"]) * 111_320.0 * math.cos(lat)
        dy = (a["lat"] - b["lat"]) * 110_540.0
        worst = max(worst, math.hypot(dx, dy))
    return worst


def main() -> ValidationResult:
    res = ValidationResult(
        id="v14",
        claim="C3",
        title="Niezmienność adnotacji przy zmianie assetu wyświetlania",
        substrate=["fixture_jp2"],
        tier="public",
    )

    appenv.bootstrap()
    blocked = appenv.require_backend_geo() or appenv.require_jp2_driver()
    if blocked:
        res.status = "todo"
        res.notes = blocked
        return res

    # Ustawiane PRZED pierwszym wywołaniem klasyfikacji — funkcje produktu czytają tę
    # zmienną przy każdym wywołaniu, nie przy imporcie.
    os.environ["GEOTILE_JP2_EXTERNAL_OVERVIEW_MIN_PIXELS"] = DISPLAY_READY_THRESHOLD_PX

    from db.storage import load_scene_json, project_dir, save_scene_json
    from services.scene_packages.fullres_cog_builder import published_fullres_cog
    from services.scene_packages.fullres_derivative import build_job_payload
    from services.scene_packages.working_view import build_direct_overviews, direct_overview_vrt

    rows: list[dict] = []
    source = _mount_project()

    # --- stan 1: piramida projektowa od 2x --------------------------------------------
    build_direct_overviews(PROJECT_ID, SCENE_ID, source, VARIANT_ID)
    if direct_overview_vrt(PROJECT_ID, SCENE_ID, VARIANT_ID) is None:
        res.status = "todo"
        res.notes = (
            "Piramida projektowa nie powstała — fixture został uznany za wystarczający "
            "w 1x. Bez dwóch RÓŻNYCH stanów wyświetlania dowód nie ma czego porównać."
        )
        return res

    contract_1, ctx = _contract()
    scene_info = ctx["si"]
    # Blok geospatial dopisujemy po pierwszym otwarciu rastra (metadane wypełnia backend),
    # zanim zadanie fullres przepisze manifest — dzięki temu pomiar B porównuje manifest
    # PO publikacji z manifestem sprzed niej, a nie sam ze sobą.
    manifest = load_scene_json(PROJECT_ID, SCENE_ID, "scene_manifest", default={}) or {}
    manifest["geospatial"] = _geospatial_block(scene_info)
    save_scene_json(PROJECT_ID, SCENE_ID, "scene_manifest", manifest)

    max_zoom = contract_1.source_max_zoom
    codes_1 = _measure("preview_2x", max_zoom, contract_1.available_native_zoom, rows)
    world_1 = _world_points()
    full_zoom_rejected = _render_tile(max_zoom, 0, 0) is None

    # --- stan 2: opublikowany COG 1x ---------------------------------------------------
    from routers.scene_import import run_scene_fullres_derivative_job

    scene = load_scene_json(PROJECT_ID, SCENE_ID, "scene", default={}) or {}
    identity = (load_scene_json(PROJECT_ID, SCENE_ID, "scene_manifest", default={}) or {}) \
        .get("source_identity") or {}
    payload = build_job_payload(
        scene_id=SCENE_ID,
        source_fingerprint=identity.get("source_scene_fingerprint"),
        variant_id=VARIANT_ID,
        source_revision=scene.get("overview_fingerprint"),
    )
    run_scene_fullres_derivative_job(
        {"project_id": PROJECT_ID, "payload": payload, "job_id": "v14"}, _JobContext(),
    )
    published = published_fullres_cog(
        project_dir(PROJECT_ID), SCENE_ID, VARIANT_ID,
        source_fingerprint=identity.get("source_scene_fingerprint"),
    )
    if published is None:
        res.status = "fail"
        res.notes = "Zadanie fullres zakończyło się, ale derywat nie jest aktywny."
        return res

    contract_2, _ = _contract()
    codes_2 = _measure("cog_1x", max_zoom, contract_1.available_native_zoom, rows)
    world_2 = _world_points()
    full_zoom_served = _render_tile(max_zoom, 0, 0) is not None

    # --- porównanie ---------------------------------------------------------------------
    shared = sorted(set(codes_1) & set(codes_2))
    mismatched = [key for key in shared if codes_1[key] != codes_2[key]]
    # Moc pomiaru: ile RÓŻNYCH układów kodów widać w próbie. Gdyby wszystkie kafle
    # zwracały to samo, zgodność kodów nie świadczyłaby o niczym.
    distinct = len({codes_1[key] for key in shared})

    phases = {
        (row["z"], row["x"], row["y"], row["state"]): row["edge_phase"]
        for row in rows if row["edge_phase"] is not None
    }
    phase_deltas = [
        abs(phases[(z, x, y, "preview_2x")] - phases[(z, x, y, "cog_1x")])
        for (z, x, y) in shared
        if (z, x, y, "preview_2x") in phases and (z, x, y, "cog_1x") in phases
    ]
    phase_max = max(phase_deltas, default=None)

    world_delta = _max_world_delta(world_1, world_2)
    zoom_differs = contract_1.available_native_zoom != contract_2.available_native_zoom
    zoom_stable = contract_1.source_max_zoom == contract_2.source_max_zoom

    real, real_reason = _real_scene_arm()

    out_dir = results_dir(__file__)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "tiles.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["state", "z", "x", "y", "codes", "edge_phase"]
        )
        writer.writeheader()
        writer.writerows(rows)
    res.artifacts = ["results/tiles.csv"]

    res.metrics = {
        "source_max_zoom": contract_1.source_max_zoom,
        "available_native_zoom_state1": contract_1.available_native_zoom,
        "available_native_zoom_state2": contract_2.available_native_zoom,
        "display_asset_kind_state1": contract_1.assets.display_asset_kind,
        "display_asset_kind_state2": contract_2.assets.display_asset_kind,
        "zoom_levels_differ": zoom_differs,
        "source_max_zoom_stable": zoom_stable,
        "tiles_compared": len(shared),
        "distinct_code_patterns": distinct,
        "window_mismatches": len(mismatched),
        "world_points": len(world_1),
        "world_coord_max_delta_m": None if not world_1 else round(world_delta, 9),
        "full_zoom_rejected_state1": full_zoom_rejected,
        "full_zoom_served_state2": full_zoom_served,
        # Raportowane, NIE bramkujące — różnica fazy dwóch piramid, nie układu współrzędnych.
        "pyramid_phase_px_max": None if phase_max is None else round(phase_max, 6),
        "real_scene_arm": real or {"status": "na", "reason": real_reason},
    }
    res.config = {
        "project_id": PROJECT_ID, "scene_id": SCENE_ID, "variant_id": VARIANT_ID,
        "width": WIDTH, "height": HEIGHT, "block_px": BLOCK,
        "jp2_external_overview_min_pixels": DISPLAY_READY_THRESHOLD_PX,
        "phase_sanity_bound_px": PHASE_SANITY_BOUND_PX,
    }

    if not zoom_differs or not shared or not world_1 or distinct < 2:
        res.status = "todo"
        res.notes = (
            "Dowód nie miał czego porównać: "
            f"stany wyświetlania {'różnią się' if zoom_differs else 'są identyczne'}, "
            f"wspólnych kafli {len(shared)}, różnych układów kodów {distinct}, "
            f"punktów geo {len(world_1)}. "
            "To NIE jest przejście — brak porównania nie jest dowodem niezmienności."
        )
        return res

    phase_ok = phase_max is None or phase_max < PHASE_SANITY_BOUND_PX
    # Ramię B bramkuje TYLKO wtedy, gdy jest dostępne. Jego brak (dane pod licencją,
    # inna stacja) nie może unieważnić dowodu publicznego — to sens tieru `reported-only`.
    real_ok = real is None or (
        real["transforms_identical"]
        and real["crs_identical"]
        and real["dimensions_identical"]
        and real["source_max_zoom_stable"]
        and real["zoom_levels_differ"]
    )
    passed = (
        not mismatched
        and world_delta == 0.0
        and zoom_stable
        and phase_ok
        and real_ok
    )
    res.status = "pass" if passed else "fail"
    if real:
        res.substrate = list(res.substrate) + [
            f"provider:{real.get('provider') or 'unknown'} (realna scena JP2)"
        ]
        res.tier = "mixed"
    res.notes = (
        f"Fixture JP2 {WIDTH}×{HEIGHT}, bloki {BLOCK} px, "
        f"source_max_zoom={contract_1.source_max_zoom}. "
        f"Stan 1 ({contract_1.assets.display_asset_kind}) serwuje do "
        f"z={contract_1.available_native_zoom}, stan 2 "
        f"({contract_2.assets.display_asset_kind}) do z={contract_2.available_native_zoom} "
        f"— poziom referencyjny bez zmian ({zoom_stable}). "
        f"Okno odczytu: {len(shared)} wspólnych kafli, {distinct} różnych układów kodów, "
        f"niezgodnych {len(mismatched)}. "
        f"WGS84 dla {len(world_1)} pikseli sceny — maks. różnica {world_delta:.9f} m. "
        f"1× odrzucone w stanie 1: {full_zoom_rejected}; serwowane w stanie 2: "
        f"{full_zoom_served}. "
        f"Faza krawędzi między piramidami (raportowana, nie bramkująca): "
        f"{'brak pomiaru' if phase_max is None else f'{phase_max:.6f} px'}. "
        + (
            f"RAMIĘ B (realna scena JP2, reported-only): "
            f"{real['scene_wh'][0]}×{real['scene_wh'][1]} ({real['scene_px']/1e9:.2f} Gpx), "
            f"piramida projektowa i opublikowany COG obecne jednocześnie. Transformacja, CRS "
            f"i wymiary identyczne w źródle, podglądzie i COG-u "
            f"({real['transforms_identical']}/{real['crs_identical']}/"
            f"{real['dimensions_identical']}); round-trip piksela "
            f"{real['pixel_roundtrip_max_px']:.2e} px. Poziom referencyjny "
            f"{real['source_max_zoom']} bez zmian, dostępny zoom "
            f"{real['available_native_zoom_preview']}->{real['available_native_zoom_cog']} "
            f"({real['display_asset_kind_preview']}->{real['display_asset_kind_cog']})."
            if real else f"RAMIĘ B: na — {real_reason}."
        )
    )
    return res


if __name__ == "__main__":
    # Konsola Windows bywa w cp1250 i wywraca sie na strzalce w notatce.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    result = main()
    path = emit(result, results_dir(__file__))
    print(f"{result.id} {result.status} -> {path}")
    print(result.notes)
