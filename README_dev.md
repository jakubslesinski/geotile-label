# GeoTile Label — dokumentacja deweloperska

Aktualna wersja aplikacji: `1.4.0`.

## Architektura

GeoTile Label jest aplikacją desktopową z lokalnym backendem:

- `frontend/` — React 19, TypeScript, Vite, Chakra UI, Leaflet i ApexCharts;
- `frontend/src-tauri/` — shell Tauri v2, lifecycle backendu, diagnostyka i instalator NSIS;
- `backend/` — FastAPI, rasterio/GDAL, pyarrow, tiling, dataset runs, eksporty i opcjonalne YOLO;
- `scripts/` — przygotowanie runtime, build release oraz smoke testy;
- `DESIGN_DECISIONS.md` — decyzje projektowe cytowane w komentarzach kodu (etapy, świadome wyłączenia zakresu).

Tauri uruchamia backend na losowym porcie `127.0.0.1`, generuje token sesji i przekazuje frontendowi `{ baseUrl, token, capabilities }`. Frontend wysyła token w `X-GeoTile-Token`; URL-e kafelków używają tokenu w query string.

## Główne zasady domenowe

```text
pełna scena + metadane + georeferencja + adnotacje źródłowe = dane kanoniczne
kafelki + YOLO/COCO/VOC + ZIP = wersjonowane produkty pochodne
```

- Pełna scena i adnotacje sceny są źródłem prawdy.
- Każda scena, adnotacja, płytka i wersja datasetu ma stabilny identyfikator.
- `dataset_runs/<run_id>/` jest trwałym wynikiem generowania.
- `dataset/` pozostaje kompatybilnym cache ostatniego udanego runu.
- Eksporty zachowują pochodzenie danych w manifestach, CSV, Parquet i GeoParquet.
- Dane kontekstowe są wersjonowane oddzielnie od kanonicznych adnotacji.

## Wymagania deweloperskie

- Windows 10/11 x64;
- Node.js 18+;
- Python 3.11+;
- Rust stable i Cargo;
- Microsoft C++ Build Tools wymagane przez Tauri;
- Miniconda lub Anaconda z poleceniem `conda` — tylko do budowy spakowanego runtime i instalatora.

Do zmian frontendowych nie trzeba za każdym razem budować Tauri ani conda-pack. Pełny build desktopowy wykonuj dopiero przed testem integracyjnym lub release.

Na komputerach z restrykcyjnym PowerShell używaj `npm.cmd` zamiast `npm`, a skrypty uruchamiaj z `-ExecutionPolicy Bypass`.

## Instalacja zależności frontendu

```powershell
cd <katalog repozytorium>\frontend
npm.cmd install
```

## Backend deweloperski

```powershell
cd <katalog repozytorium>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Zależności YOLO (w buildzie desktopowym instalowane zawsze; w środowisku deweloperskim opcjonalne):

```powershell
pip install -e ".[yolo]"
```

!!! uwaga o stosie geoprzestrzennym

Sam `pip install -e .` **nie postawi GDAL-a ze sterownikiem JP2** na Windows — koła pip nie
niosą kompletu bibliotek natywnych. Odtwarzalne środowisko z przypiętymi wersjami opisuje
[`environment.yml`](environment.yml) w korzeniu repozytorium:

```powershell
conda env create -f environment.yml -p .\.venv-backend
conda run -p .\.venv-backend python -m pip install -e ".[dev]"
conda run -p .\.venv-backend python -m pytest backend/tests
```

`pyproject.toml` niesie extrasy: `yolo` (torch + ultralytics), `sam3` (enkoder tekstu) i
`dev` (pytest). Bez extrasa `dev` **37 z 76 plików testowych backendu nie uruchomi się** —
runtime pakowany z aplikacją nie zawiera pytesta.

Backend importuje `torch`/`ultralytics` leniwie — bez nich uruchamia się poprawnie, a `/api/capabilities` raportuje faktyczną dostępność funkcji.

## Frontend w przeglądarce

Uruchom backend pod `127.0.0.1:8000`, a następnie:

```powershell
cd <katalog repozytorium>\frontend
npm.cmd run dev
```

Zmiana celu proxy:

```powershell
$env:VITE_BACKEND_DEV_URL = "http://127.0.0.1:8000"
npm.cmd run dev
```

Tryb przeglądarkowy używa fallbacków dla natywnych dialogów. Operacje takie jak systemowy wybór pliku, otwieranie folderu i `Save ZIP as...` należy ostatecznie sprawdzać w Tauri.

## Tauri dev

Tauri dev wybiera interpreter backendu w następującej kolejności:

1. `GEOTILE_BACKEND_PYTHON`;
2. `backend\.venv\Scripts\python.exe`;
3. `python` z `PATH`.

```powershell
cd <katalog repozytorium>\frontend
npm.cmd run tauri:dev
```

Wskazanie konkretnego środowiska:

```powershell
$env:GEOTILE_BACKEND_PYTHON = "C:\ścieżka\do\python.exe"
npm.cmd run tauri:dev
```

`tauri:dev` nie buduje instalatora i nie wymaga ponownego tworzenia conda-pack przy każdej zmianie. Zmiany React/Vite są odświeżane na bieżąco, zmiany backendu wymagają restartu procesu Tauri.

## Walidacja zmian

### Frontend

```powershell
cd frontend
npm.cmd run build
```

Polecenie uruchamia `tsc -b` oraz produkcyjny build Vite.

### Backend

```powershell
cd backend
python -m compileall .
```

### Rust/Tauri

```powershell
cd frontend\src-tauri
cargo check
```

Najpierw uruchamiaj testy najbliższe zmienianemu obszarowi. Pełny build NSIS jest końcową walidacją, nie częścią każdej iteracji.

## Smoke testy

Lokalne sceny testowe nie są wersjonowane. Przygotuj konfigurację:

```powershell
Copy-Item .\test-scenes.local.example.json .\test-scenes.local.json
notepad .\test-scenes.local.json
```

Podstawowy test backendu:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-backend.ps1 -Config .\test-scenes.local.json
```

Testy obszarowe:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-metadata-parsers.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scene-identity.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-source-annotation-export.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-annotation-workflow.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-attribute-engine.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-export-sidecars.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-dataset-runs.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-preprocessing-profiles.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-tile-catalog.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-project-migration.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-split-validation.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-dataset-audit.ps1
```

Workflow zespołowy V2 (role, zakres własności, podmiana, pętla recenzji):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-atomic-writes.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-import-reporting.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-project-role.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-utc-timestamps.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-package-scope.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scoped-replacement.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-review-loop.ps1
```

Warsztat treningu (publikacja datasetów, wagi bazowe, przebiegi, OBB, rejestr modeli):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-dataset-publication.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-base-models.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-training-runs.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-training-obb.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-model-registry.ps1
```

Te testy działają bez GPU i bez wag bazowych — `smoke-training-runs.ps1` i
`smoke-training-obb.ps1` ustawiają `GEOTILE_TRAIN_MOCK=1`, więc worker symuluje
przebieg zamiast wołać ultralytics. Pokrywają cykl życia (start, postęp, anulowanie,
wykrycie porzuconego przebiegu), a **nie** to, że realny trening rusza z danymi
parametrami. To wymaga ręcznej weryfikacji na maszynie z GPU.

Najważniejsze pojedyncze asercje:

- `smoke-training-obb.ps1` sprawdza, że katalog stagingu zawiera etykiety
  9-polowe, a nie 5-polowe. Ultralytics wyprowadza ścieżkę etykiet z `/images/`
  przez podmianę na `/labels/`, więc bez stagingu trening OBB po cichu czytałby
  etykiety poziome i uczył się złych geometrii — bez żadnego błędu.
- `smoke-model-registry.ps1` sprawdza, że kafelek obecny w katalogu, ale
  nieużyty w treningu, **nie** pojawia się w rodowodzie modelu. Rodowód ma
  odpowiadać na pytanie „czyje adnotacje trafiły do tego modelu”, więc nadmiarowy
  wpis jest równie błędny jak brakujący.
- `smoke-dataset-publication.ps1` sprawdza, że nie da się usunąć datasetu, od
  którego zależy przebieg treningu lub zarejestrowany model.

Kolejność odpowiada etapom T0–T6 pętli recenzji (`DESIGN_DECISIONS.md`, team-review). Najważniejsze:
`smoke-scoped-replacement.ps1` drukuje `CORRECTION PROPAGATED` / `DELETION PROPAGATED`
— oba muszą być `True`, bo to jest cała różnica między jednokierunkowym handoffem a
działającą pętlą poprawek. `smoke-review-loop.ps1` sprawdza pełny obieg i to, że
import recenzji **nie zmienia żadnej adnotacji**.

Test zainstalowanej aplikacji:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-installed-app.ps1
```

Release nie powinien być przekazywany użytkownikom bez testu EO/SAR, GEO/NO GEO, GeoTIFF 8/16-bit, backupu/importu, generowania datasetu i eksportu ZIP.

## Profil projektu i storage

Projekt przechowuje profil zawierający:

- `modality`: `EO` albo `SAR`;
- `georeferencing`: `GEO` albo `NO_GEO`;
- listę sensorów;
- `annotation_mode`: `bbox` albo `rotated_bbox`;
- `labeling_author_email`;
- domyślny preprocessing i strategię splitu.

Użytkownik może wskazać lokalizację projektu podczas tworzenia. Bez wyboru projekt trafia do:

```text
%APPDATA%\GeoTileLabel\data\projects\<project_id>
```

Runtime i logi zawsze pozostają w:

```text
%APPDATA%\GeoTileLabel\runtime
%APPDATA%\GeoTileLabel\logs
```

Resolver musi obsługiwać projekty zapisane poza `AppData` oraz importowane projekty legacy.

## Resolver paczek scen

Import scen lokalnych działa w modelu `źródło -> paczka dostawcy -> logiczny produkt -> widok roboczy`. Źródło jest przypisane do jednego dostawcy i pozostaje tylko do odczytu; zagnieżdżona struktura oryginalnej dostawy może zostać zachowana.

Najważniejsze moduły:

- `backend/services/scene_packages/` — discovery, inventory, ranking produktów, identity i przygotowanie widoków roboczych;
- `backend/services/scene_sources.py` — `scene_sources.json`, migracja legacy i bezpieczne rozwiązywanie ścieżek względnych;
- `backend/services/scene_raster_resolver.py` — centralna fasada dostępu do rastra używana przez mapę, miniatury, tiling, dataset, eksport i predykcję;
- `backend/routers/scene_import.py` — preview/apply/rescan/relink, wybór assetu i przygotowanie COG.

Każda scena otrzymuje manifest v4 z sekcjami `source_package`, `source_identity` i `working_view`. `scene.json` przechowuje wyłącznie szybkie podsumowanie dla UI. Projekt zapisuje:

```text
project/
├── scene_sources.json
├── import_reports/
├── scenes/<scene_id>/scene_manifest.json
└── derived_scenes/<scene_id>/<variant_id>/
    ├── scene.vrt
    ├── rgb_pansharpened.cog.tif
    └── processing_manifest.json
```

`source_scene_uid` zależy wyłącznie od rastrów konkretnego logicznego produktu. `source_package_fingerprint` obejmuje pełne inventory paczki, natomiast `working_grid_uid` wiąże adnotacje z wymiarami, CRS, transformacją, liczbą pasm i wariantem roboczym. Widok jest blokowany po pierwszej adnotacji, review, tilingu albo dataset runie.

Obsługiwane reguły V1: ICEYE GRD, Capella GEC, UMBRA GEC, BlackSky ortho RGB, Pleiades Neo PMS-FS RGB ORTHO (`TIFF`/`JP2`) oraz WorldView RGB lub `MUL+PAN`. Produkty wieloczęściowe używają VRT. Pansharpening tworzy odtwarzalny COG wizualny tylko na żądanie.

Endpointy importu znajdują się pod `/api/scene-import/*` i `/api/projects/{id}/scene-sources/*`. Preview ma TTL 30 minut i przed zastosowaniem ponownie weryfikuje rozmiar oraz `mtime` assetów.

### Słownik pojęć

| Pojęcie                         | Znaczenie                                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Źródło (`source`)           | Folder tylko do odczytu, przypisany do jednego dostawcy. Projekt może mieć kilka źródeł zgodnych z jedną modalnością.        |
| Paczka (`package`)             | Granica pojedynczej dostawy/akwizycji wykryta pod źródłem. Może zawierać wiele produktów, metadane i podglądy.                |
| Asset                            | Pojedynczy plik należący do paczki, np. raster, XML, JSON, IMD, TIL albo quicklook.                                                |
| Logiczny produkt                 | Zestaw jednego lub wielu assetów tworzących jedną scenę do labelowania, np. PNEO`PMS-FS RGB` w częściach `R1C1`, `R1C2`. |
| Widok roboczy (`working_view`) | Konkretny raster używany przez mapę i adnotacje: plik źródłowy, VRT albo projektowy COG.                                        |
| Wariant (`variant_id`)         | Stabilny identyfikator wyboru assetów i parametrów przygotowania widoku roboczego.                                                 |

### Przepływ importu

1. Frontend pobiera listę resolverów i przypisanych modalności przez `GET /api/scene-import/providers`.
2. Użytkownik wskazuje dostawcę oraz folder źródłowy. Folder nie jest kopiowany ani modyfikowany.
3. `POST /api/scene-import/previews` uruchamia skan bez zmiany projektu:
   - waliduje dostępność folderu;
   - wykrywa granice paczek;
   - buduje inventory assetów;
   - klasyfikuje i rankinguje produkty;
   - zwraca logiczne sceny, alternatywy, ostrzeżenia i wymagane decyzje.
4. Preview trafia do `DATA_DIR/runtime/scene-import-previews/`, ma TTL 30 minut i nie jest częścią projektu.
5. Przy `POST /api/projects/from-sources` lub `scene-sources/apply` backend ponownie porównuje ścieżki, rozmiary i `mtime_ns`. Zmieniony preview musi zostać wykonany ponownie.
6. Dla zaakceptowanej sceny backend zapisuje `scene_manifest.json` v4 oraz skrócone `scene.json` i `scenes_index.json`.
7. Po imporcie w tle może zostać obliczona pełna tożsamość zawartości. Duże rastry nie są hashowane podczas synchronicznego preview.
8. Mapa, miniatury, kafle, preprocessing, dataset i YOLO pobierają raster wyłącznie przez `SceneRasterResolver`.

Projekt wieloźródłowy może łączyć różnych dostawców, ale w V1 wszystkie źródła muszą mieć modalność zgodną z `project.profile.modality`. Resolver `generic` nie deklaruje modalności i służy do płaskich folderów ze zwykłymi rastrami.

### Discovery i kontrakt resolvera

Wspólny kontrakt znajduje się w `backend/services/scene_packages/base.py`:

```python
class ProviderResolver:
    provider: str
    version: int
    modality: str | None

    def validate_source(self, root: Path) -> list[dict]: ...
    def discover_packages(self, root: Path) -> list[PackageCandidate]: ...
    def build_inventory(self, source_root: Path, candidate: PackageCandidate) -> dict: ...
    def rank_products(self, inventory: dict, source_root: Path) -> dict: ...
    def identity_assets(self, selection: dict, inventory: dict) -> list[dict]: ...
```

Domyślny discovery sprawdza, czy sam root jest paczką, a następnie foldery pierwszego poziomu. Wewnątrz kandydata inventory jest budowane rekurencyjnie. Dzięki temu zagnieżdżone struktury DIMAP i WorldView pozostają nienaruszone, a każdy podfolder wewnętrzny nie staje się błędnie osobną sceną. Granice paczek są deduplikowane po rozwiązanej ścieżce.

Inventory obejmuje obsługiwane rastry (`TIF/TIFF`, `JP2`, `PNG`, `JPEG`) i sidecary (`JSON`, `XML`, `IMD`, `TIL`, `RPB`, world file, KML/KMZ, HTML, TXT). Każdy asset zapisuje `asset_id`, rolę, ścieżkę względem źródła i paczki, format, część `R<n>C<n>`, rozmiar oraz `mtime_ns`. Pełny SHA-256 pozostaje początkowo pusty.

`rank_products()` musi zwrócić jawny status. Brak produktu preferowanego lub kilka równorzędnych kandydatów daje `decision_required`; niedozwolony jest cichy fallback do quicklooku, maski, PAN, SLC albo innego produktu pomocniczego.

### Reguły dostawców

| Resolver         | Preferowany produkt                                    | Metadane semantyczne                                                                | Zachowanie alternatyw                                                                                                                                 |
| ---------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `iceye`        | `GRD` TIFF                                           | odpowiadający XML: czas, satelita, polaryzacja, geometria obserwacji i spacing     | CSI, SLC, QUICKLOOK i VID nie są wybierane automatycznie; różne polaryzacje mogą utworzyć oddzielne logiczne sceny                               |
| `capella`      | `GEC` TIFF                                           | JSON i extended JSON: collect, tryb, polaryzacja, kąty i spacing                   | GEO, SLC, SICD i preview pozostają alternatywami/pomocniczymi assetami                                                                               |
| `umbra`        | `GEC` TIFF                                           | STAC JSON lub JSON dostawcy: collect, tryb, polaryzacja, kąty i rozdzielczość    | CSI, SICD i SIDD nie zastępują GEC po cichu                                                                                                         |
| `blacksky`     | dokładnie`*_ortho.tif` jako RGB `[1,2,3]`         | metadata JSON: czas, GSD, zachmurzenie, geometria Słońca i off-nadir              | `*_ortho-mask.tif`, `*_ortho-pan.tif` i browse są auxiliary; nie mogą zostać wybrane jako obraz RGB                                            |
| `pleiades_neo` | `PMS-FS RGB ORTHO`; TIFF ma pierwszeństwo przed JP2 | `DIM_*.XML`/DIMAP: czas, PNEO, spectral processing, pasma, zachmurzenie i kąty   | wieloczęściowe`R<n>C<n>` tworzą VRT; `MS-FS RGB + PAN` ma `prepare_required`                                                                 |
| `worldview`    | gotowy`PSH`/pansharpened/RGB TIFF                    | IMD/XML: WV, czas, GSD, zachmurzenie, Słońce i off-nadir; TIL/RPB jako pomocnicze | bez gotowego RGB resolver wybiera`MUL+PAN`; brak potwierdzonego mapowania RGB daje `decision_required`, a poprawne mapowanie `prepare_required` |
| `generic`      | jeden raster bez interpretacji dostawcy                | raster i sidecary dopasowane ogólnymi regułami                                    | każdy raster w płaskim folderze jest osobną sceną; kilka produktów w jednej paczce nie jest semantycznie rozstrzyganych                          |

Pliki PNEO `PMS-FS RGB ORTHO` w TIFF i JP2 są równoważnymi wejściami logicznymi. Nie należy konwertować JP2 poza aplikacją tylko na potrzeby importu. Runtime desktopowy musi zawierać GDAL `JP2OpenJPEG`; brak sterownika daje `runtime_unsupported` i blokuje release preflight.

### Metadane i precedencja

Geometria rastra pochodzi zawsze z Rasterio/GDAL: wymiary, CRS, affine transform, bounds, dtype i liczba pasm. `backend/services/metadata_parser.py` uzupełnia semantykę z sidecarów dostawcy. Nazwa pliku jest wyłącznie fallbackiem.

Precedencja danych:

1. Rasterio/GDAL — siatka pikselowa i georeferencja.
2. Sidecary dostawcy — identyfikator, czas akwizycji, sensor, produkt, polaryzacja/pasma i geometria obserwacji.
3. Nazwa pliku — ostatnia możliwość dla brakujących pól.

Parser nie nadpisuje konfliktów bez śladu. Wybrana i odrzucona wartość wraz ze źródłami trafiają do `metadata_conflicts` w diagnostyce manifestu. `scene_manifest.json` przechowuje także `metadata_sources`, nazwę i wersję parsera oraz status `ok`, `partial_metadata`, `no_parser_match` albo `raster_only`.

### Manifest v4 i referencje rastrów

`scene_manifest.json` jest źródłem prawdy dla importowanej paczki:

```text
scene_manifest.json
├── source_package
│   ├── package_id, source_id, provider, provider_scene_id
│   ├── package_root_relative, product_type, assets[]
│   ├── selection, identity_asset_ids, mosaic_parts_order
│   └── resolver + selection_provenance
├── source_identity
│   ├── source_scene_uid, source_scene_fingerprint
│   ├── source_package_fingerprint, provider_scene_id
│   └── status, computed_at
└── working_view
    ├── variant_id, raster_kind, raster_ref
    ├── working_grid_uid, preparation_status
    └── locked, locked_at, lock_reason, processing_manifest
```

Ścieżki assetów w manifeście są względne. Referencja źródłowa ma postać:

```json
{"storage":"source","source_id":"src_...","relative_path":"package/image.tif"}
```

Produkt pochodny projektu ma postać:

```json
{"storage":"project","relative_path":"derived_scenes/<scene_id>/<variant_id>/scene.vrt"}
```

`scene.json` zawiera tylko podsumowanie wymagane przez katalog i UI: dostawcę, produkt, status, `raster_ref`, `working_variant_id`, `working_grid_uid`, identity i podstawowe metadane. `filename` jest polem kompatybilności dla projektów legacy, nie kanoniczną lokalizacją nowej sceny.

### Tożsamość, siatka i blokada widoku

- `source_scene_uid` jest oparty wyłącznie na assetach tworzących logiczny produkt. Dodanie quicklooku lub pliku licencji nie zmienia tożsamości sceny.
- `source_package_fingerprint` obejmuje całe inventory i wykrywa zmianę kompletności paczki.
- `working_grid_uid` obejmuje wymiary, CRS, affine transform, liczbę kanałów, wariant i parametry przygotowania. Chroni adnotacje pikselowe przed przypisaniem do innej siatki tej samej akwizycji.
- Pełne hashe plików są liczone strumieniowo po zatwierdzeniu importu. Cache używa rozmiaru i `mtime_ns`; zmiana assetu wymusza ponowne hashowanie.

Widok roboczy zostaje zablokowany po pierwszej adnotacji, zatwierdzeniu komórki przeglądu, tilingu lub użyciu sceny w dataset runie. Próba wyboru wariantu z innym `variant_id` zwraca `409 Conflict`. Regeneracja brakującego VRT/COG dla tego samego wariantu jest dozwolona. Reprojekcja istniejących adnotacji między różnymi `working_grid_uid` nie jest wykonywana automatycznie.

### Centralny dostęp do pikseli

`SceneRasterResolver.resolve(project_id, scene_id)` jest jedynym punktem rozwiązywania rastra. Kolejność dispatchu:

1. strukturalny `raster_ref` produktu projektu lub źródła;
2. kontrolowany błąd dla zarządzanej sceny, której widok nie jest gotowy;
3. legacy `project.scene_folder + scene.filename` wyłącznie dla starych projektów.

Warstwa `storage: source` jest bezpiecznie rozwiązywana pod rootem z `scene_sources.json`; ścieżka próbująca wyjść poza root jest odrzucana. `storage: project` jest analogicznie ograniczony do katalogu projektu. Kod funkcjonalny nie powinien samodzielnie sklejać ścieżek do scen — musi używać `SceneRasterResolver` albo wyższej fasady `scene_loader`.

### VRT i przygotowanie COG

Produkty wieloczęściowe są łączone przez GDAL VRT w `derived_scenes/<scene_id>/<variant_id>/`. Przed budową sprawdzane są CRS, dtype, liczba pasm, rozdzielczość, część liniowa affine transform i wyrównanie do wspólnej siatki pikselowej.

Pansharpening PNEO `MS-FS+PAN` i WorldView `MUL+PAN`:

- jest uruchamiany jawnie przez użytkownika i raportuje postęp przez SSE;
- stosuje GDAL weighted Brovey z wagami `1/3` dla RGB;
- zachowuje dtype źródła;
- zapisuje COG z blokiem 512, DEFLATE, `BIGTIFF=IF_SAFER` i overview 2/4/8/16/32;
- najpierw zapisuje `*.partial`, a po sukcesie wykonuje atomową podmianę;
- zapisuje `processing_manifest.json` z wejściami, metodą, pasmami i wariantem;
- oznacza wynik jako produkt wizualny do labelowania, a nie produkt radiometrycznie naukowy.

VRT zawiera ścieżki zależne od bieżącej lokalizacji źródła i dlatego jest regenerowalnym cache’em. Po relinkowaniu nie należy ufać staremu VRT bez ponownej budowy.

### Stany sceny i operacje utrzymaniowe

| Status                  | Znaczenie i dozwolona akcja                                                                                       |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `ready`               | Widok roboczy istnieje i może być otwarty.                                                                      |
| `decision_required`   | Resolver nie może bezpiecznie wybrać produktu lub mapowania RGB; użytkownik wybiera assety.                    |
| `prepare_required`    | Assety są wybrane, ale trzeba zbudować VRT/COG.                                                                 |
| `runtime_unsupported` | Brakuje funkcji runtime, np. sterownika JP2; paczka nie jest oznaczana jako uszkodzona.                           |
| `invalid`             | Paczka lub wybór nie spełnia wymagań produktu.                                                                 |
| `missing_source`      | Root/paczka/asset nie jest dostępny; wymagane relinkowanie albo ponowny dostęp do dysku.                        |
| `source_changed`      | Zmieniły się identity assets po rozpoczęciu pracy; automatyczne użycie dotychczasowych labeli jest blokowane. |

Rescan tworzy preview i nigdy nie usuwa scen ani adnotacji. Brakujące paczki otrzymują `missing_source`; trwałe usunięcie sceny jest osobną potwierdzaną operacją. Relink aktualizuje `root_path`, zachowując `source_id`; po relinku należy ponownie wykonać scan/apply, obliczyć identity i odtworzyć VRT. Backup zawiera definicje źródeł i manifesty, ale nie zawiera oryginalnych rastrów ani dużych COG; po odtworzeniu na innej stacji źródła mogą wymagać relinkowania.

### API importu

| Metoda i endpoint                                                | Odpowiedzialność                                   |
| ---------------------------------------------------------------- | ---------------------------------------------------- |
| `GET /api/scene-import/providers`                              | Lista providerów i modalności.                     |
| `POST /api/scene-import/previews`                              | Skan źródeł bez zmiany projektu.                  |
| `POST /api/projects/from-sources`                              | Utworzenie projektu z zatwierdzonego preview.        |
| `GET /api/projects/{id}/scene-sources`                         | Definicje źródeł projektu.                        |
| `POST /api/projects/{id}/scene-sources`                        | Dodanie źródła i wykonanie preview.               |
| `POST /api/projects/{id}/scene-sources/{source_id}/rescan`     | Preview zmian jednego źródła.                     |
| `POST /api/projects/{id}/scene-sources/apply`                  | Zastosowanie preview i decyzji użytkownika.         |
| `POST /api/projects/{id}/scene-sources/{source_id}/relink`     | Zmiana rootu istniejącego źródła.                |
| `POST /api/projects/{id}/scenes/{scene_id}/select-asset`       | Jawne rozstrzygnięcie assetów i pasm RGB.          |
| `POST /api/projects/{id}/scenes/{scene_id}/prepare`            | SSE przygotowania VRT/COG.                           |
| `POST /api/projects/{id}/scenes/{scene_id}/prepare/cancel`     | Anulowanie aktywnego przygotowania.                  |
| `DELETE /api/projects/{id}/scene-sources/derived/unreferenced` | Usunięcie niereferencowanych produktów pochodnych. |

### Dodawanie kolejnego dostawcy

1. Dodać provider i modalność do `PROVIDER_MODALITY` oraz instancję resolvera do rejestru w `resolvers.py`.
2. Zdefiniować sygnaturę paczki, wzorce produktu preferowanego, assety identity i reguły alternatyw bez cichego fallbacku.
3. Rozszerzyć `metadata_parser.py`, zachowując wspólny model `sar`/`eo`, provenance i raport konfliktów.
4. Dodać syntetyczne drzewo paczki do `smoke-scene-resolvers.ps1` oraz realną próbkę do lokalnego configu.
5. Sprawdzić direct raster, produkt wieloczęściowy, brak produktu, dwa równorzędne produkty, zmianę pliku, relink i backup/import.
6. Jeżeli dochodzi nowy format rastra, dodać dependency runtime, capability i obowiązkowy release preflight.

Decyzje pierwszej generacji resolvera (etapy R0–R5) streszcza `DESIGN_DECISIONS.md`, scene-import-resolver. README deweloperskie opisuje stan aktualnie zaimplementowany.

Walidacja deweloperska:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scene-resolvers.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scene-identity.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scene-preparation.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-scene-packages-real.ps1 -Config .\test-scene-packages.local.json
```

Build release uruchamia resolver smoke z `-RequireJp2`; brak sterownika GDAL `JP2OpenJPEG` blokuje wydanie. Lokalne ścieżki do realnych próbek można zapisać w ignorowanym `test-scene-packages.local.json`, korzystając z `test-scene-packages.local.example.json` jako szablonu.

## Adnotacje

Obsługiwane geometrie:

- `bbox` — ramka osiowa;
- `rotated_bbox` — zorientowany prostokąt ze wskazanym przodem obiektu.

Ramka zorientowana przechowuje geometrię, wektor przodu i orientację. UI umożliwia rysowanie, przesuwanie, skalowanie, obracanie i kopiowanie przez `C` + drag centroidu.

`backend/services/attribute_engine/` wylicza atrybuty geometrii, GEO, orientacji i provenance. Atrybuty źródłowe są przechowywane przy adnotacji; surowe relacje do adnotacji kafelkowych trafiają do `tile_catalogs/<catalog_id>/tile_annotation_links.parquet`, a snapshot użyty przez run do jego metadanych i sidecarów eksportu.

### Zespołowa wymiana adnotacji

- `backend/services/annotation_package.py` buduje lekką paczkę analityka bez scen i kafelków.
- `backend/services/annotation_import.py` weryfikuje ZIP i SHA-256, dopasowuje sceny oraz klasy, przygotowuje dry run i wykonuje import atomowy.
- podglądy importu są zapisywane w `annotation_import_previews/`;
- trwałe raporty i snapshoty trafiają do `annotation_imports/<import_id>/`;
- ponowny import tej samej paczki jest idempotentny.
- `backend/services/annotation_summary.py` agreguje kompletność scen, klasy, autorów, provenance i historię importów dla panelu managera.
- importowane rekordy zachowują `source_package_id` i otrzymują lokalny `import_id`, dzięki czemu UI może filtrować pochodzenie bez zmiany oryginalnego `annotation_source`.

## Workspace datasetu

`DatasetView` ma pięć niezależnych zakładek:

1. **Build Dataset** — preprocessing, split, filtry i materializacja kafelków treningowych;
2. **Statistics** — klasy, splity, sceny i wykorzystanie kafelków;
3. **Content** — podgląd kafli opublikowanego runu (boxy, klasy, deep-link do edytora);
4. **Audit** — readiness, quality score, leakage i kompletność;
5. **Export** — formaty treningowe, otwarcie folderu i zapis ZIP.

Panel historii pozwala wybrać konkretny `dataset_run_id`. Wszystkie odczyty statystyk, audytu, treści i eksportu działają dla wybranego runu, nie niejawnie dla najnowszego katalogu.

**Wydajność (run jest niezmienny → licz raz, czytaj z cache).** Statystyki, audyt i indeks Content są **prekomputowane przy budowie runu** i serwowane jako odczyt pliku, a nie liczone przy każdym otwarciu: `GET …/dataset/audit` czyta `metadata/dataset_audit.json` (przelicza tylko `POST …/audit/refresh`), a indeks Content jest persystowany w `metadata/content_index.json` (zimny cache czyta z dysku zamiast przebudowy z tysięcy plików etykiet). Front (`DatasetView`, `AnalysisView`) cache'uje wynik po `run_id`/projekcie i pokazuje spinner ładowania zamiast mylącego pustego komunikatu.

**Publikacja to decyzja człowieka (zapisana).** Błędy audytu i walidacji splitu **uwidaczniają** problemy, ale nie blokują twardo publikacji: bramki jakości są miękkie i przechodzą po świadomym potwierdzeniu (`acknowledge_issues`), które jest zapisywane w manifeście publikacji (`published_with_known_issues` + snapshot: readiness, quality_score, liczba błędów, kto/kiedy). Blokady **strukturalne** pozostają twarde: run musi być `complete`, a etykieta wymagana i unikalna w projekcie.

## Metadata-only tile catalog

`backend/services/tile_catalog.py` oddziela definicję siatki przeglądu od materializacji obrazów treningowych. Katalog kafelków jest metadata-only i służy jako surowy, niesplitowany zbiór kandydatów dla dataset runs. Aktywny katalog ma postać:

```text
tile_catalogs/<catalog_id>/
├── tile_catalog_manifest.json
├── tiles.parquet
├── tile_annotation_links.parquet
├── review_state.json
└── catalog_statistics.json
```

`catalog_id` zależy od tożsamości scen, wersji adnotacji, rozmiaru i overlapu siatki, progu propagacji oraz wersji algorytmu. Split, seed i preprocessing nie zmieniają katalogu. Podglądy trafiają wyłącznie do czyszczalnego `preview_cache`, natomiast `dataset_builder` czyta okna ze scen źródłowych i zapisuje tylko kafelki wybrane do runu.

Schema katalogu V2 zapisuje dla kafelków geometrię pixel/native/WGS84, a dla relacji adnotacji klasę, autora, `annotation_source`, `import_id` i `source_package_id`. `filter_options` w manifeście zasila UI filtrów scen, klas, autorów i źródeł.

Status review kafelka jest przechowywany jako `review_status` (`unreviewed` lub `reviewed`) oraz kompatybilne pole `reviewed`. Techniczne wykluczenie jest przechowywane jako `exclude_from_dataset` oraz kompatybilne pole `excluded`. Wykluczenie nie jest zwykłym statusem postępu pracy; służy wyłącznie dla obszarów technicznie nieprzydatnych.

Każdy run zapisuje `dataset_selection.json` zawierający snapshot filtrów, candidate/eligible/used tile IDs oraz split użytych kafelków. `split_manifest.json` zawiera dodatkowo przypisanie `tile_id -> split`. Filtry są częścią `DatasetConfig` i hasha wejściowego runu.

Zasady wyboru kafelków do datasetu:

- kafelki z adnotacjami są kandydatami pozytywnymi niezależnie od statusu review;
- sprawdzone kafelki bez adnotacji są kandydatami na negative samples;
- niesprawdzone puste komórki nie są używane jako negative samples;
- kafelki z `exclude_from_dataset=true` są pomijane;
- rzeczywiste obrazy PNG/JPG kafelków powstają dopiero podczas generowania dataset runu.

Endpointy katalogu znajdują się pod `/api/projects/{project_id}/tiling/catalog`, a legacy `scenes/*/tiles.json` jest migrowane automatycznie przy pierwszym odczycie. Obrazy `scenes/*/tiles/images` można usunąć przez endpoint `/tiling/legacy-cache` lub panel storage projektu.

## Preprocessing

Profile znajdują się w `preprocessing_profiles.json`. Profil ma stabilne `profile_id`, wersję i hash parametrów. Kafelki datasetu powstają bezpośrednio z pikseli źródłowej sceny.

Presety V1 obejmują:

- EO RGB percentile i linear full range;
- SAR linear percentile i log percentile;
- kopiowanie parametrów z panelu Display do profilu datasetu.

Ustawienia panelu Display nie zmieniają źródła. Predykcja YOLO ma osobny preprocessing.

## Strategie splitu

Obsługiwane tryby:

- `random_tile`;
- `scene_split`;
- `image_block_split`;
- `spatial_block_split`;
- `class_balanced_spatial`.

Podział jest deterministyczny przez `split_seed`. Kafelki powiązane z tym samym `source_annotation_id` są grupowane. `split_manifest.json` i `validation_report.json` przechowują wynik oraz kontrole leakage, w tym podobne kafelki pomiędzy splitami.

## Dataset audit

Każdy run zapisuje:

```text
metadata/dataset_audit.json
metadata/dataset_audit.csv
```

Audyt obejmuje integralność splitów, spatial leakage, podobne kafelki, pokrycie klas i scen, provenance autora, metadane sensorów/GEO oraz kompletność sidecarów.

Osobna kategoria `review` sprawdza:

- procent aktywnych komórek siatki oznaczonych jako sprawdzone;
- sceny z niskim pokryciem review;
- liczbę niesprawdzonych komórek;
- liczbę komórek pozytywnych i sprawdzonych pustych kandydatów;
- czy dataset używa kafelków z niesprawdzonych obszarów;
- czy dataset zawiera lub przecina obszary `exclude_from_dataset=true`.

Niskie pokrycie review jest ostrzeżeniem. Użycie albo przecięcie obszarów wykluczonych przez kafelki datasetu jest błędem audytu.

Endpointy:

```text
GET  /api/projects/{project_id}/dataset/audit?run_id=...
POST /api/projects/{project_id}/dataset/audit/refresh?run_id=...
```

Audyt jest **prekomputowany przy budowie runu** i zapisany w `metadata/dataset_audit.json`; `GET …/audit` serwuje ten plik (run niezmienny), a pełne przeliczenie wymusza dopiero `POST …/audit/refresh`. Błędy audytu **nie blokują twardo** publikacji — run z błędami można opublikować po świadomym potwierdzeniu (patrz „Workspace datasetu").

## Eksport datasetu

Wspólny builder paczki generuje formaty YOLO AABB, YOLO OBB, COCO i Pascal VOC oraz warstwę metadanych:

- `dataset_run_manifest.json` i `project_profile.json`;
- `README_DATASET.md` i `SHA256SUMS.txt`;
- `scenes.parquet`, `tile_metadata.*`, `annotation_links.*`;
- `annotations_wgs84.geoparquet` i `annotations_native.geoparquet`;
- manifest splitu, statystyki, audyt i raporty kontekstu;
- `logs/export_log.txt`.

Zawartość paczki opisuje `DESIGN_DECISIONS.md`, dataset-package. Źródłowe sceny nie są kopiowane do paczki.

## Backup i import

Backup projektu jest lekki i nie zawiera źródłowych scen, dataset runs ani tabel kontekstowych. Import ZIP i import istniejącego folderu wymagają ponownego wskazania folderu scen. Dane kontekstowe należy ponownie zaimportować.

Usuwanie projektu w UI jest operacją destrukcyjną chronioną przez `AlertDialog`. Backend nie usuwa źródłowych scen.

## Migracje projektów

Aktualny schemat projektu to v2. `load_json(..., "project")` wykonuje migrację leniwie. Przed pierwszym zapisem tworzy jednokrotny snapshot w:

```text
<project>/.migration_backups/schema-<from>-to-<to>-<timestamp>/
```

Snapshot zawiera wyłącznie JSON-y projektu i scen. Nie kopiuje obrazów, dataset runs ani cache. `.migration_state.json` zapobiega tworzeniu kolejnych identycznych snapshotów. Normalizacja adnotacji zachowuje istniejące `id` i `source_annotation_id`, a brakujące provenance uzupełnia bez zmiany geometrii.

## Internacjonalizacja i pomoc kontekstowa

Konfiguracja i18n znajduje się w `frontend/src/i18n.ts`. Domyślny język to polski, wybór jest zapisywany w `localStorage`, a angielski jest fallbackiem.

Zasady:

- teksty UI dodawaj przez `t(...)`, nie bezpośrednio w JSX;
- polskie i angielskie wpisy utrzymuj razem;
- dłuższe wyjaśnienia używają `InfoPopover` z `frontend/src/components/common/InfoPopover.tsx`;
- zasady doboru treści pomocy: `DESIGN_DECISIONS.md`, ui-popovers;
- nie dodawaj ikon informacji przy oczywistych przyciskach i standardowych kolumnach tabel.

## Diagnostyka desktopowa

Tauri udostępnia:

- `get_backend_info`;
- `get_app_diagnostics`;
- `open_logs_dir`;
- `export_diagnostics_zip`;
- `restart_backend`;
- `clear_runtime_cache`.

Logi są rotowane w `%APPDATA%\GeoTileLabel\logs`. ZIP diagnostyczny nie powinien zawierać scen źródłowych, pełnych adnotacji ani geometrii projektu.

Backendowy endpoint `GET /api/diagnostics/project-summary` zwraca sanitizowane informacje o wersjach schematów, brakujących lub zduplikowanych tożsamościach scen, ostatnim imporcie oraz rozmiarze cache. Tauri zapisuje wynik jako `project-diagnostics.json` w ZIP diagnostycznym. Geometrie i rekordy adnotacji są usuwane.

## Przygotowanie backendu Tauri

Kopiowanie źródeł backendu:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare-tauri-backend.ps1
```

Budowa archiwum conda-pack. Wariant `analyst` (domyślny, torch CPU) trafia do
instalatora, wariant `studio` (torch CUDA) służy do zbudowania osobnego pakietu
treningowego GPU:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-backend-env.ps1 -Variant analyst
powershell -ExecutionPolicy Bypass -File .\scripts\build-backend-env.ps1 -Variant studio
```

Wyniki wariantu `analyst`:

```text
frontend/src-tauri/resources/backend/
frontend/src-tauri/resources/backend-env.tar.gz
```

Archiwum jest rozpakowywane przy pierwszym uruchomieniu do `%APPDATA%\GeoTileLabel\runtime\backend-env`. Pakowanie archiwum zamiast tysięcy osobnych plików ogranicza problemy NSIS przy dużym środowisku z torch.

Duże archiwa są dzielone na części (`-PartSizeMB`), a Tauri skleja je strumieniowo
przy rozpakowaniu — bez materializowania całości w pamięci.

## Pakiet treningowy GPU

Trening wymaga bibliotek CUDA, których nie ma w instalatorze bazowym. Pakiet
buduje się osobno:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-cuda-pack.ps1
```

Wersja nie jest parametrem — skrypt czyta ją z
`frontend/src-tauri/tauri.conf.json`, bo runtime i aplikacja są wydawane razem.
Jeżeli archiwum wariantu `studio` już istnieje, można pominąć jego przebudowę
przez `-FromArchive <ścieżka>`. `-PartSizeMB` tnie pakiet na części, gdy nośnik
lub kanał przesyłu ma limit rozmiaru pliku.

Wynik trafia do `release/cuda-pack-<version>/` wraz z sumami SHA-256 i README dla
odbiorcy. Użytkownik wskazuje pobrany plik
ręcznie w **Ustawieniach → Pakiet treningowy GPU**; aplikacja niczego nie pobiera
z sieci. Po instalacji wymagany jest restart aplikacji, bo runtime wybierany jest
przy starcie procesu backendu.

Oba runtime'y współistnieją w `%APPDATA%\GeoTileLabel\runtime\`. Przy starcie
aplikacja sonduje pakiet CUDA (`import torch; torch.cuda.is_available()`) i używa
go tylko wtedy, gdy sonda przejdzie — uszkodzony lub niekompatybilny pakiet
powoduje powrót do runtime bazowego, a nie brak startu aplikacji.

**Aktualność — buduj świeży pakiet dla każdej wersji aplikacji.** Runtime CUDA
**zastępuje cały backend** (nie tylko trening), a instalacja akceptuje go po samej
sondzie `import torch`, bez sprawdzania wersji. Dlatego nie dołączaj starszego
`cuda-pack` do nowszego instalatora: zestaw pakietów (np. `ultralytics`) i katalog
architektur mogły się zmienić (np. dojście YOLO26/YOLO12), a pakiety sprzed poprawki
MAX_PATH niosą głębokie drzewa licencji torcha. Kolejność u dewelopera to zawsze
`fetch-base-models.ps1` → `build-cuda-pack.ps1` na tej samej wersji co instalator.

`build-cuda-pack.ps1` musi być zapisany jako **UTF-8 z BOM** — bez BOM PowerShell
psuje polskie znaki w komunikatach. Odwrotnie przy plikach czytanych przez Tauri:
`Set-Content -Encoding UTF8` dokłada BOM, który psuje parsowanie JSON.

### Dlaczego pakiet jest osobny, a nie w instalatorze

Instalator był początkowo jeden, zunifikowany. Próba dołożenia do niego CUDA
nie powiodła się: **NSIS i WiX obcinają się przy ~2 GB całkowitego payloadu**
(`mmapping file (1621978592, ...)`). To limit sumy, nie pojedynczego pliku —
dzielenie archiwum na części ani wyłączenie kompresji nie pomogło.

Rozważane i odrzucone: instalator online (wymaga infrastruktury hostingowej i
łamie założenie pracy bez sieci). Przyjęte rozwiązanie zachowuje offline: pakiet
dystrybuowany jest osobno i wskazywany ręcznie.

Skutek uboczny jest korzystny: analityk, który tylko labeluje i uruchamia
predykcję, instaluje ~0,72 GB zamiast kilku GB. Trening jest jedyną funkcją
wymagającą pakietu.

**Nie scalaj wariantów z powrotem** bez sprawdzenia, czy limit payloadu
instalatora nadal obowiązuje — to jest powód rozdzielenia, nie preferencja
architektoniczna.

## Wagi bazowe

Trening startuje z wag bazowych YOLO11, których nie ma w repozytorium. Pobiera
się je jednorazowo po stronie dewelopera:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch-base-models.ps1
```

Skrypt pobiera **24 pliki** wag do `data\models\base` wraz z `SHA256SUMS.txt`;
inny katalog wskazuje `-Destination`. To jedyny moment, w którym potrzebna jest
sieć — aplikacja u użytkownika nigdy nie pobiera modeli w czasie działania.

### Jak wagi trafiają do zainstalowanej aplikacji

`MODELS_ROOT` jest przekazywane przez Tauri i wskazuje
`%APPDATA%\GeoTileLabel\data\models`. Wcześniej nie było ustawiane wcale, przez co
backend spadał na ścieżkę obok własnych źródeł — czyli do katalogu instalacji,
tylko do odczytu dla zwykłego użytkownika i kasowanego przy aktualizacji. Nie
usuwaj tej zmiennej z uruchomienia backendu.

Wagi jadą razem z **pakietem treningowym GPU**, jako osobne archiwum
`geotile-base-models-<wersja>.tar.gz` obok runtime. Kolejność u dewelopera:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\fetch-base-models.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\build-cuda-pack.ps1
```

Bez pierwszego kroku drugi wypisze ostrzeżenie i zbuduje pakiet bez wag. Żeby
pominąć je świadomie (np. odbiorca ma już wagi, a wydajesz sam runtime), użyj
`-SkipBaseModels`.

Archiwum wag jest **osobne od runtime**, bo cykle życia są różne: dołożenie
architektury nie powinno wymagać przepakowania ~3 GB CUDA, a odbiorca z aktualnym
runtime może wskazać same wagi. Aplikacja rozpoznaje je po fragmencie
`base-models` w nazwie pliku i rozpakowuje do `MODELS_ROOT\base\`; runtime
zostaje wtedy nietknięty. Rozpakowanie idzie przez katalog przejściowy
`.base-incoming`, bo dostępność architektury backend ocenia po samej obecności
pliku — przerwane rozpakowanie nie może wyglądać jak gotowe wagi.

Rodziny w katalogu, w rozmiarach n/s/m:

| Rodzina | detect | OBB                                   |
| ------- | ------ | ------------------------------------- |
| YOLOv8  | wagi   | wagi                                  |
| YOLOv10 | wagi   | brak w upstreamie                     |
| YOLO11  | wagi   | wagi                                  |
| YOLO12  | wagi   | **brak wag — trening od zera** |
| YOLO26  | wagi   | wagi                                  |

Tag release'u jest przypięty do `v8.4.0`. Nie zmieniaj go na `/releases/latest/`
— pobranie ma dawać ten sam wynik za pół roku. Poprzednio przypięty `v8.3.0` nie
zawiera wag YOLO26 (404), co jest powodem podniesienia.

**Wpisy trenowane od zera.** YOLO12-OBB ma w pakiecie ultralytics konfigurację
architektury, ale upstream nie opublikował dla niej wag. Zamiast pomijać rodzinę,
katalog oferuje ją z `pretrained=False`: wpis wskazuje `.yaml` zamiast `.pt`,
niczego nie trzeba przygotowywać i jest dostępny nawet przy pustym
`MODELS_ROOT\base\`. Worker dostaje samą nazwę configu, którą ultralytics
rozwiązuje ze swojego pakietu — bez sieci.

Trening od zera daje wyraźnie słabsze wyniki niż start z wag COCO, więc UI
ostrzega o tym przy wyborze. Licencja jest inna: nie dziedziczy się wag, ale kod
treningowy nadal jest AGPL-3.0.

`params_m` w katalogu jest **mierzone** z konfiguracji przy nc=80, nie
przepisywane z tabel marketingowych — dzięki temu porównania między rodzinami są
spójne.

**Telemetria ultralytics jest wyłączana w workerze.** `SETTINGS["sync"]` domyślnie
ma wartość `true`, co powoduje wysyłanie anonimowych zdarzeń do endpointu Google
Analytics z wątku w tle podczas treningu i walidacji. Łamie to invariant offline,
więc `_silence_ultralytics_telemetry()` ustawia to na `false` przed każdym użyciem
ultralytics. Nie usuwaj tego wywołania.

Rejestr modeli bazowych jest **allowlistą**, a nie skanem katalogu. `torch.load`
wykonuje pickle, więc dowolny plik `.pt` jest wykonywalnym kodem; ładowane są
wyłącznie pliki z `BASE_MODEL_CATALOG` w `backend/services/training_models.py`.

Wagi YOLO11 są objęte licencją **AGPL-3.0** (Ultralytics). Zastosowanie
komercyjne wymaga licencji Ultralytics Enterprise. Informacja jest pokazywana w
UI przy wyborze architektury.

## Ścieżki modeli

W zainstalowanej aplikacji `MODELS_ROOT` = `%APPDATA%\GeoTileLabel\data\models`
(ustawiane przez Tauri; patrz „Zmienne środowiskowe desktopu"). To domyślna,
automatycznie skanowana lokalizacja wag, z umownymi podkatalogami:

| Ścieżka               | Zawartość                                                                   | Kod                                   |
| ----------------------- | ----------------------------------------------------------------------------- | ------------------------------------- |
| `MODELS_ROOT\base\`   | modele bazowe do treningu (allowlista architektur YOLO)                       | `training_models.base_models_dir()` |
| `MODELS_ROOT\sam\`    | checkpointy SAM (np.`sam3.pt`, `mobile_sam.pt`)                           | `sam_assist.bundled_sam_dir()`      |
| `MODELS_ROOT\dino\`   | wagi DINO`.pth` (few-shot + analiza); opcjonalnie repo `dino\dinov3_repo` | `embedding_backbone.DINO_DIR`       |
| `MODELS_ROOT\**\*.pt` | modele YOLO do predykcji (skan rekurencyjny z pominięciem`\sam`)           | `predictor.scan_models_dir()`       |

Poza domyślną lokalizacją:

- **SAM / DINO / YOLO do predykcji** użytkownik wskazuje w panelu Predykcja natywnym
  oknem wyboru — plik/folder może leżeć na dysku wewnętrznym, zewnętrznym albo sieciowym;
  ścieżka zapisuje się **per-projekt** w `prediction_config` (`model_path`, `sam_models_dir`,
  `sam_checkpoint`, `dino_models_dir`, `dino_checkpoint`).
- **Dozwolone korzenie** dla wbudowanej przeglądarki i walidacji ścieżki YOLO daje
  `utils/browse_roots.get_model_browse_roots()`: podstawowy `MODELS_ROOT` oraz opcjonalny
  drugi korzeń przez zmienne `MODEL_ROOT_2_TARGET` (ścieżka) i `MODEL_ROOT_2_LABEL` (etykieta).
  Ścieżka poza korzeniami daje `Path outside configured model roots`.
- **Rejestr bazowy to allowlista, nie skan** — do treningu ładowane są tylko pliki o nazwach
  z `BASE_MODEL_CATALOG` (patrz „Wagi bazowe"); dowolny `.pt` w `\base` nie staje się przez to
  modelem bazowym.

Nieaktualne ścieżki (np. po przeniesieniu projektu na inny komputer) naprawia się w panelu
Predykcja: **Wyczyść ścieżkę** → **Zmień folder**. Aktualizacja waliduje tylko pole realnie
zmieniane, więc nieaktualny wpis jednego modelu nie blokuje zapisu pozostałych.

## Dokumentacja MkDocs

Źródłem dokumentacji użytkownika są pliki Markdown w `docs/`. Konfiguracja
znajduje się w `mkdocs.yml`, a przypięte zależności w
`docs-requirements.txt`. Wygenerowany katalog `.docs-build/` nie jest
wersjonowany.

Pierwszy build tworzy izolowane środowisko Python tylko dla autora
dokumentacji, instaluje MkDocs Material i generuje stronę offline:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-docs.ps1
```

Podgląd z automatycznym odświeżaniem:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\serve-docs.ps1
```

Podgląd z edycją plików Markdown bezpośrednio w przeglądarce:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\serve-docs.ps1 -LiveEdit
```

Tryb `-LiveEdit` używa deweloperskiej konfiguracji `mkdocs.live.yml` i zapisuje
zmiany bezpośrednio w `docs/`. Potrafi też zmieniać nazwy i usuwać pliki, dlatego
serwer jest celowo ograniczony do `127.0.0.1` i nie wolno udostępniać go w sieci.
Jawna nawigacja w `mkdocs.yml` nie jest automatycznie aktualizowana po utworzeniu,
zmianie nazwy ani usunięciu strony.

Dokumentacja jest wtedy dostępna pod `http://127.0.0.1:8088`. Walidacja strict,
zasobów offline, wyszukiwarki i wersji aplikacji:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-docs.ps1
```

Wersja dokumentacji jest pobierana z
`frontend/src-tauri/tauri.conf.json`. Zależności MkDocs są potrzebne wyłącznie
do opracowywania dokumentacji i nie są wymagane na komputerach użytkowników.

Ten sam skrypt uruchamia najpierw **bramkę dryfu diagramów architektury**
(`scripts/check-architecture-model.py`) i przerywa walidację, zanim zbuduje
dokumentację. Bramka pilnuje, żeby `docs/architecture/explorer.html` odpowiadał
modelowi treści `docs/architecture/architecture-model.json` — to plik **generowany**,
więc ręczna zmiana w nim przepada; edytuje się model albo szablon
`scripts/templates/explorer.template.html`, a potem uruchamia:

```powershell
python .\scripts\generate-architecture-explorer.py
python .\scripts\generate-architecture-d2.py
```

Bramkę można uruchomić samą (ułamek sekundy, sama biblioteka standardowa):

```powershell
python .\scripts\check-architecture-model.py
```

Sprawdza dodatkowo, czy eksporty SVG rysunków artykułu zawierają etykiety z modelu.
Rysunki żyją w osobnym repozytorium `geotile-label-paper`, którego nie musi być na
maszynie budującej wydanie — bramka je wtedy pomija. Ścieżkę wskazuje zmienna
środowiskowa `GEOTILE_PAPER_ROOT`.

### Dokumentacja użytkownika jest w `docs/`

Treść użytkowa — pakiet GPU i wymóg restartu, licencja AGPL-3.0 wag bazowych,
jednorazowość oceny na zbiorze testowym, przerwanie treningu przy zamknięciu
aplikacji — mieszka w `docs/` i to jest jej jedyne źródło prawdy. Nie opisuj jej
ponownie tutaj. `README_dev.md` zostaje deweloperski: build, warianty runtime,
smoke testy.

## Build desktopowy

**Zanim pierwszy raz wywołasz cokolwiek z `cargo` albo `tauri`, zbuduj runtime backendu.**
`tauri.conf.json` deklaruje zasób `resources/backend-env.tar.gz.*`, a części tego archiwum
(spakowane środowisko Pythona, ~800 MB) świadomie nie są trzymane w repozytorium — są w
`.gitignore`. Glob, który niczego nie dopasuje, przerywa skrypt budowania Tauri kodem 101
**zanim** cokolwiek się skompiluje, więc komunikat nie wygląda na brakujący plik:

```text
error: failed to run custom build command for `geotile-label-desktop`
  glob pattern resources/backend-env.tar.gz.* path not found or didn't match any files.
```

Na świeżym klonie zacznij od:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-backend-env.ps1
```

Skrypt tworzy `frontend\src-tauri\resources\backend-env.tar.gz.001` i kolejne części.
Dopiero po nim `cargo check`, `cargo build` i `npm run desktop:build` mają czym się karmić.
`build-release-desktop.ps1` wykonuje ten krok samodzielnie, więc uwaga dotyczy wyłącznie
ręcznych wywołań cargo i tauri.

Szybki build deweloperski:

```powershell
cd frontend
npm.cmd run desktop:build
```

Instalator trafia do:

```text
frontend\src-tauri\target\release\bundle\nsis\
```

Budowany jest tylko instalator NSIS `.exe`; MSI nie jest częścią workflow.

## Release

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-release-desktop.ps1 -Version 0.1.10
```

Skrypt:

1. synchronizuje wersję w npm, Cargo i Tauri;
2. przygotowuje backend oraz runtime bazowy (torch CPU-only);
3. uruchamia smoke test migracji, tile catalog i dataset runs przy użyciu spakowanego runtime;
4. buduje NSIS;
5. tworzy `release/GeoTileLabel-<version>`;
6. kopiuje instalator i README oraz tworzy `release_manifest.json` i `SHA256SUMS.txt`.

Opcji `-SkipSmokeTests` używaj wyłącznie do lokalnej diagnostyki procesu budowy, nie do finalnego release.

Po buildzie sprawdź czystą instalację, uruchomienie bez systemowego Pythona, import scen z dysku wewnętrznego i zewnętrznego oraz eksport diagnostyki.

Budowa runtime wymaga dostępu do `repo.anaconda.com`, `conda-forge`, PyPI i `download.pytorch.org`. Błąd `CondaHTTPError: HTTP 000 CONNECTION FAILED` oznacza problem z siecią, DNS, VPN/proxy albo blokadą tych domen, a nie błąd kodu aplikacji. Po przywróceniu połączenia uruchom tę samą komendę ponownie.

Jeżeli archiwum `frontend/src-tauri/resources/backend-env.tar.gz` zostało już poprawnie zbudowane, a zależności backendu nie zmieniły się, można pominąć ich ponowne pobieranie:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-release-desktop.ps1 -Version 0.1.10 -ReuseBackendRuntime
```

Nie używaj `-ReuseBackendRuntime` po zmianie zależności w `backend/pyproject.toml` lub `scripts/build-backend-env.ps1`.

### Edycja „lite" (uproszczony instalator)

Uproszczona edycja dla użytkowników końcowych ukrywa zaawansowane zakładki projektu —
**Analiza datasetu, Trening i Wyniki** — zostawiając kompletowanie danych i labelowanie.
To zmiana wyłącznie w UI: backend i endpointy są te same, więc pełny i „lite" różnią się
tylko interfejsem.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-release-desktop.ps1 -Version 1.2.2 -Lite
```

Mechanizm: przełącznik `-Lite` ustawia na czas builda zmienną `VITE_GEOTILE_EDITION=lite`,
którą Vite wstrzykuje do `import.meta.env`. Flagę czyta `frontend/src/config/edition.ts`
(`isLiteEdition`); `Sidebar.tsx` filtruje wtedy trzy zakładki, a `App.tsx` przekierowuje ich
trasy do Datasetu, żeby nie były osiągalne bezpośrednim URL-em. Bez flagi build jest pełny
(domyślnie).

Wynik lite trafia do osobnego katalogu `release/GeoTileLabel-<version>-lite/`, instalator
dostaje sufiks `-lite`, a `release_manifest.json` pole `edition: "lite"` — obie edycje tej
samej wersji nie kolidują. Szybki podgląd bez pełnego release'u:
`cd frontend; $env:VITE_GEOTILE_EDITION="lite"; npm run dev`.

## Obsługa YOLO

Instalator zawiera CPU-only `torch`, `torchvision` i `ultralytics`. Bez pakietu
treningowego GPU backend raportuje `cuda_available=false` i `device=cpu`;
predykcja, SAM i egzemplarz działają na CPU. Predykcja czyta duże rastry oknami,
obsługuje anulowanie i mapuje klasy po nazwie.

Po zainstalowaniu pakietu treningowego i restarcie backend startuje na runtime
CUDA i loguje `Using CUDA runtime pack (12.6 True)`.

Jeżeli import bibliotek się nie powiedzie, `/api/capabilities` raportuje
`yolo=false`. Predykcja jest wtedy **wygaszona z czytelnym powodem**, a nie
ukryta i nie zgłasza stack trace'u — niedostępne narzędzie ma być widoczne wraz
z przyczyną.

`/api/capabilities` jest odpytywane z 3 próbami po 15 s. Krótszy limit był
błędem: samo `import torch` zajmuje na zimno 4–5 s, więc pojedyncza próba z
timeoutem 2 s fałszywie raportowała brak GPU na obu runtime'ach.

## Zmienne środowiskowe desktopu

Tauri ustawia co najmniej:

```text
DATA_DIR=%APPDATA%\GeoTileLabel\data
MODELS_ROOT=%APPDATA%\GeoTileLabel\data\models
GEOTILE_DESKTOP=1
GEOTILE_BUILD_VARIANT=yolo
GEOTILE_ENABLE_YOLO=1
GEOTILE_AUTH_TOKEN=<token sesji>
```

Zmienne deweloperskie, używane przez smoke testy — nie ustawiaj ich w buildzie
dystrybuowanym użytkownikom:

```text
GEOTILE_TRAIN_MOCK=1          # worker symuluje trening zamiast wołać ultralytics
GEOTILE_SAM_MOCK=1            # SAM zwraca deterministyczną maskę
GEOTILE_SAR_EXEMPLAR_MOCK=1   # dopasowanie egzemplarza bez modelu
```

`MODELS_ROOT` nie jest zmienną deweloperską — ustawia ją Tauri. Nadpisuj ją tylko w
testach, żeby nie mieszać wag testowych z prawdziwymi.

Środowisko Pythona z `torch`, `ultralytics`, `psutil` i `cv2` używane przez
smoke testy to `.desktop-build/backend-env/python.exe` — nie systemowy Python.

## Docker

Docker nie jest formą dystrybucji użytkowej i nie powinien wracać do instrukcji użytkownika. Ewentualne kontenery mogą służyć wyłącznie jako pomocnicze środowisko developerskie.

## Kryteria gotowości release

- `npm.cmd run build` przechodzi bez błędów TypeScript;
- `cargo check` przechodzi;
- build NSIS kończy się poprawnie;
- backend uruchamia się bez systemowego Pythona;
- działają sceny EO/SAR, GEO/NO GEO oraz GeoTIFF 8/16-bit;
- działają bbox i rotated bbox, tiling i review;
- przechodzą generowanie, statystyki, audyt i eksport wybranego runu;
- ZIP zawiera manifesty, checksumy, Parquet/GeoParquet i raporty;
- backup/import oraz diagnostyka zostały sprawdzone;
- aplikacja raportuje `yolo=true`, a predykcja działa na modelu testowym.
