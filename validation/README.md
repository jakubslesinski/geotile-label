# validation/ — dowody domknięcia workflow

Jeden podfolder = jeden dowód z `../claim-evidence-matrix.md`. Każdy dowód to **skrypt
reprodukowalny**, nie zrzut ekranu. To materiał uzupełniający artykuł; w samym tekście
idzie tylko zwarta tabela wyników.

Konwencja: **claim × substrat × tier** (patrz `../claim-evidence-matrix.md`). Każdy dowód
deklaruje, na jakim **substracie** działa i w jakim **tierze odtwarzalności**:
**public** (benchmark — recenzent rerunuje), **reported-only** (Capella/`SAR_test` pod
licencją — tylko raport), **mixed**. NITF/TPS jest **poza** suite (pipeline afiniczny).

## Indeks

| Folder | Claim | Substrat / tier | Co mierzy |
| --- | --- | --- | --- |
| `v01_compat_matrix/` | S1 | xView3+FAIR1M+Capella+paczki dostawców · mixed | macierz dostawca×format×CRS×**dtype**×**postać dostawy** (pokrycie + status; Float16 jawnie) |
| `v02_geometry_roundtrip/` | C3 | FAIR1M/xView3 (+SAR_test) · mixed | residuum pixel→world→pixel (afiniczna); OBB niesiony jako `polygon_scene_px` bez skewu |
| `v03_run_reproducibility/` | C2b/C2c | benchmark · public | ten sam seed+config → identyczny manifest/lista kafli |
| `v04_split_leakage/` | C1 (mechanizm) | xView3/DOTA · public | nachodzące/sąsiednie kafle między splitami: random vs spatial |
| `v05_id_provenance_chain/` | C2a | benchmark+SAR_test · mixed | integralność ID scena→adnotacja→kafel→eksport |
| `v06_training_example/` | C4 | FAIR1M/DOTA · public | trening OBB: loss/mAP + confusion matrix (realny benchmark) |
| `v07_performance/` | S3 | xView3 XXL Float16 · public + scena JP2 2,63 Gpx · reported-only | czas/pamięć dla sceny XXL (ingest→build→eksport) **oraz odczyt 1× ze źródła JP2 vs z opublikowanego COG** |
| `v08_offline_egress/` | C5 | dowolny run · public | brak połączeń wychodzących przy odciętej sieci |
| `v09_class_similarity/` | S2 | FAIR1M (37 podklas) · public | pary klas o wysokim podobieństwie — **skorelowane z confusion z v06** |
| `v10_import_fidelity/` | C2a | DOTA/DIOR/FAIR1M · public | **import lossless**: benchmark OBB → kanon → GeoParquet → re-parse == oryginał |
| `v11_geo_accuracy_external/` | C3 | xView3 · public | **dokładność geo vs GT**: app pixel→world vs publikowane `lat/lon` (residuum w metrach) |
| `v12_metadata_stratified_eval/` | C6 | xView3 · public | mAP/recall w binach metadanych (`distance_from_shore`, kąt padania) |
| `v13_split_leakage_effect/` | C1 (skutek) | FAIR1M/DOTA · public | **luka mAP** random−spatial (jedna liczba; ablacje → osobny artykuł) |
| `v14_display_asset_invariance/` | C3 | fixture JP2 · public + realna scena JP2 2,63 Gpx · reported-only | **okno odczytu i geometria nie zależą od aktywnego assetu wyświetlania** (piramida 2× → opublikowany COG 1×) |

## Konwencje

- Każdy folder ma `run.py` z **docstringiem-kontraktem** (Claim / Substrate / Tier / Method /
  Inputs / Outputs / Pass criterion) i funkcją `main()` zwracającą `ValidationResult` z
  ustawionymi `substrate=[...]` i `tier=...`.
- Wynik jest zapisywany jako `results/result.json` (kontrakt w `_common/result.py`) oraz
  ewentualne artefakty (CSV/PNG) w tym samym `results/`.
- Każdy wynik niesie w `environment` **przypisanie do stanu kodu produktu**: `app_rev`
  (rewizja gita albo `nogit`, gdy repo aplikacji nie jest repozytorium), `app_version`
  (z `frontend/src-tauri/tauri.conf.json`) i `app_fingerprint` — sha256 po treści
  `backend/**/*.py` w formacie `<16 hex>:<liczba plików>`. Odcisk jest liczony z treści, nie
  z czasu modyfikacji, więc ten sam kod na innej maszynie daje ten sam odcisk. Wspólny
  odcisk we wszystkich wynikach potwierdza, że cały przebieg opisuje jeden stan kodu.
- Skrypty odwołują się do **backendu aplikacji** przez import z `../geotile-label/backend`
  albo przez publiczne API — bez duplikowania logiki produktu.
- Uruchamianie backendu bezpośrednio wymaga nadpisania `PROJ_LIB`/`GDAL_DATA` na środowisko
  backendu (patrz README_dev aplikacji) — inaczej instalacja PROJ z PostgreSQL przesłania GDAL.
  `bootstrap()` ustawia też `GDAL_DRIVER_PATH` na `<prefix>/Library/lib/gdalplugins`: bez tego
  rasterio widzi 190 sterowników i **żadnego JP2**, mimo obecnej wtyczki `gdal_JP2OpenJPEG.dll`.
  Dowód dotykający JP2 woła `appenv.require_jp2_driver()` i przy braku sterownika kończy się
  `todo` z jawnym powodem — cichy brak sterownika oznaczałby pomiar innej ścieżki odczytu
  niż deklarowana.
- **Substraty (dane):** benchmarki w `E:\Datasets` (`DIOR-R`, `DOTA`, `FAIR1M`, `xView3`);
  reported-only: `../geotile-label/data/SAR_test` (realna scena Capella SAR, 205 OBB) —
  warstwa narracyjna end-to-end, nie dowód odtwarzalny.
- **Pułapka xView3 Float16:** rasterio nie mapuje `GDT_Float16` (`KeyError: 15`) — SAR VV/VH
  trzeba przekonwertować do Float32 na ingeście (część kontraktu `v01`/`v07`).
- Import benchmarków do postaci projektu opisuje `../BENCHMARK_IMPORT_PLAN.md`.

## Uruchomienie

Dowody sięgające do backendu (geo/rasterio) uruchamiaj **interpreterem środowiska
backendu** — inaczej brak `rasterio` i skrypt zwróci `todo` z powodem. Bootstrap
(`_common/appenv.py`) sam ustawia `GDAL_DATA`/`PROJ_LIB` z prefiksu środowiska i dokłada
`backend/` oraz `importers/` do `sys.path`.

```powershell
$PY = "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe"
& $PY validation/v11_geo_accuracy_external/run.py
& $PY validation/v10_import_fidelity/run.py
& $PY validation/v03_run_reproducibility/run.py
& $PY validation/run_all.py     # zbiorczo, agreguje results/ do figures/summary
```

Nadpisania (opcjonalne): `BENCHMARK_PROJECTS_ROOT` (dom. `E:\GeoTileLabel_data\benchmark_projects`),
`GEOTILE_BACKEND` (dom. `<repozytorium>\backend`), `V10_SCENES_PER_DATASET`, `V03_SCENES`, `V03_N_RUNS`,
`PROVIDER_PATHS_JSON` (dom. `<repo aplikacji>/scripts/provider-import-paths.json`).

**Paczki dostawców w `v01`** są opcjonalne i `reported-only`. Ścieżki czyta wyłącznie
`PROVIDER_PATHS_JSON` — plik lokalny i gitignorowany, bo dane dostawców nie mogą trafić do
repozytorium ani do CI (roadmapa importu §14). Jego brak to normalny stan u recenzenta:
wiersze reported-only znikają, publiczne zostają kompletne i dowód **nadal przechodzi**.
Do `result.json` ani do CSV nie trafia żadna ścieżka, nazwa pliku ani nazwa źródła
z konfiguracji — wiersz jest etykietowany samym dostawcą i liczbą porządkową.

**Ramię JP2/COG w `v07`** jest opcjonalne i `reported-only`. Szuka opublikowanego derywatu 1×
w katalogu danych aplikacji (`V07_JP2_DATA_DIR`, dom. `%APPDATA%/GeoTileLabel/data`) i tylko
go **czyta** — nie buduje (przygotowanie sceny to ~19 min) i nie woła
`SceneRasterResolver.resolve()`, bo ten po drodze potrafi zapisać `scene.json`, a to realne
dane użytkownika. Bez derywatu ramię raportuje `na` z powodem, a dowód przechodzi na samym
ramieniu publicznym. Wartości bezwzględne tego ramienia zależą od stanu cache'u systemu
plików (obserwowano od ~60× do ~700×), więc wnioskiem jest rząd wielkości, nie liczba.

**Ramię B w `v14`** (`V14_REAL_DATA_DIR`, ten sam domyślny katalog) wymaga sceny, która ma
**jednocześnie** piramidę projektową i opublikowany COG — publikacja derywatu nie usuwa
`overview.vrt.ovr`, więc na realnym projekcie jest to stan normalny. Zakres jest tam węższy
niż w ramieniu A: porównywane są **assety** (transformacja, CRS, wymiary, kontrakt zoomu),
a nie odpowiedzi serwera kafli — żeby zobaczyć stan „podgląd 2×" przez serwer, trzeba by
wycofać derywat, czyli zmodyfikować dane użytkownika. Dowód end-to-end niesie ramię A.

Status pojedynczego dowodu: `pass` / `fail` / `todo` (szkielet startuje jako `todo`).

### Wdrożone dowody

| Dowód | Status | Co pokazuje (skrót wyniku) |
| --- | --- | --- |
| `v01_compat_matrix` | **wdrożony** | macierz **13 wierszy** (dostawca×format×CRS×dtype×**postać dostawy**). Publiczne 5: SAR+EO, GeoTIFF/PNG/JPEG, UTM/EPSG:4326/NO_GEO, **Float16 xView3 obsłużony** (oryginał→`KeyError:15`, working→float32). Reported-only 8: rzeczywiste paczki ICEYE / Capella / Airbus PHR-PNEO / WorldView / generic, skanowane `resolvers.scan_source()` wyłącznie do odczytu, bez importu — w tym **JP2 uint16** i produkt **MUL+PAN**. Ingest 7/7 dostępnych rastrów. Luki jawne: render n/a dla VRT, ingest n/a dla rastra spoza stacji **oraz gdy resolver oddaje wybór produktu użytkownikowi** (5 wierszy). Nieobserwowane kształty (`multipart_mosaic`, `archive`) są nazwane w metrykach. |
| `v02_geometry_roundtrip` | **wdrożony** | round-trip afiniczny pixel→WGS84→pixel (`SceneGeoModel`): p95 ≈ 1e-9 px; wierność kształtu OBB (SAR_test+FAIR1M): min IoU = 1.0, przesunięcie wierzchołków 0 px (brak skewu). |
| `v03_run_reproducibility` | **wdrożony** | ten sam seed+config → identyczny input_hash i split (random+spatial) w N runach; inny seed → inny split; snapshot filtrów obecny. Realne funkcje: `compute_grid`, `stable_tile_id`, `_assign_splits`, `dataset_input_hash`. |
| `v04_split_leakage` | **wdrożony** | xView3, kafle z nachodzeniem: kafle treningowe nachodzące na val/test — random ≫ spatial ≫ spatial+buffer(=0). Realne funkcje: `_assign_splits`, `_apply_spatial_buffer`, `dataset_audit._tile_center_3857`. |
| `v05_id_provenance_chain` | **wdrożony** | FAIR1M+SAR_test: 100% adnotacji odtworzonych w eksporcie, 0 zgubionych/orphan/źle przypisanych/złej sceny. Realna propagacja `annotation_propagator.propagate_annotations_with_attributes` (+`compute_tile_attributes`). |
| `v06_training_example` | **wdrożony** | konsumuje ukończony przebieg treningu z aplikacji (`training_runs/<run>`). DOTA / yolo26n-obb / 50 epok: best mAP50=0.635, mAP50-95=0.477, precyzja 0.805, recall 0.585, 18 klas; krzywe loss/mAP + confusion (feed dla v09). |
| `v07_performance` | **wdrożony** | **Ramię publiczne:** xView3 30402×26560 float32 (807 Mpx) → 1221 kafli: ingest ~40 ms, ~0,12 s/kafel, build ≈150 s; **szczyt RSS ~90 MB** — przy odczycie okienkowym GeoTIFF pamięć nie skaluje się z rozmiarem sceny. **Ramię JP2/COG (reported-only):** realna scena 60476×43476 uint16 (2,63 Gpx), okno 512 px ze świeżym uchwytem na kafel — źródłowy JP2 ~4,3 s/kafel wobec ~11 ms z opublikowanego COG, czyli **dwa rzędy wielkości** różnicy; derywat waży **1,56×** rozmiaru źródła. Pamięć ograniczona w OBU ścieżkach (Δ RSS rzędu pojedynczych MB). |
| `v08_offline_egress` | **wdrożony** | lokalny pipeline FAIR1M (ingest→geo→propagate→render→export): **0 prób egresu** (sonda socket), pełny run przechodzi przy zablokowanym egresie. |
| `v09_class_similarity` | **wdrożony** | FAIR1M: embeddingi DINO (`build_object_index`/`class_similarity`) vs confusion z przebiegu FAIR1M OBB. 2502 obiekty / 33 klasy → **Spearman podobieństwo↔pomyłki = 0.63**; top pary sensowne i mylone (Small Car↔Van 4591 pomyłek, Dry↔Liquid Cargo Ship, A321↔Boeing737). |
| `v10_import_fidelity` | **wdrożony** | FAIR1M/DOTA/DIOR-R: maks. odchylenie wierzchołków ≈7e-4 px, klasy i liczności 100%; IoU wypukłych ≈1.0. Werdykt bezstratności na odchyleniu wierzchołków (odporne na zdegenerowane/niewypukłe quady źródła). |
| `v11_geo_accuracy_external` | **wdrożony** | xView3: pixel→WGS84 (`SceneGeoModel.affine`) vs publikowany `detect_lat/lon`, residuum w metrach (CRS sceny, UTM). p95 ≪ 1 px GSD. |
| `v12_metadata_stratified_eval` | **wdrożony** | xView3: inferencja modelu na kaflach held-out val + recall w binach `distance_from_shore` (7078 obiektów): brzeg 0.20 < przybrzeże 0.25 < offshore 0.25 (rozrzut 5.4 pp) — recall niższy przy brzegu. Ilustracja C6 (małe dane: 50 scen val). |
| `v13_split_leakage_effect` | **wdrożony** | konsumuje 2 przebiegi OBB tego samego projektu (random_tile vs spatial_block). FAIR1M yolo26n-obb 50 epok seed 42: mAP50 random 0.330 vs spatial 0.268 → **luka +0.062** (mAP50-95 +0.059) — split losowy zawyża. Razem z v04 domyka C1. |

| `v14_display_asset_invariance` | **wdrożony** | **Ramię A (public):** fixture JP2 6000×4000 w dwóch stanach wyświetlania budowanych funkcjami produktu (`build_direct_overviews` → `run_scene_fullres_derivative_job`). Scena to siatka bloków 512 px o stałych kodach, więc odczyt z wnętrza bloku mówi wprost, który fragment źródła trafił do kafla. 120 wspólnych kafli, 120 różnych układów kodów, **0 niezgodności okna**; WGS84 dla 7 pikseli — różnica **0,0 m**; `source_max_zoom` bez zmian, `available_native_zoom` 4→5. 1× odrzucone w stanie 1, serwowane w stanie 2. **Ramię B (reported-only):** realna scena 2,63 Gpx z piramidą projektową i opublikowanym COG-iem obecnymi jednocześnie — transformacja, CRS i wymiary **identyczne co do bitu** w źródle, podglądzie i COG-u, round-trip piksela 7,5e-09 px, poziom referencyjny 8 bez zmian przy dostępnym zoomie 7→8. |

**Wszystkie 14 dowodów wdrożone i przechodzą (14/14).**

> **Dlaczego `v14` nie mierzy pozycji obiektu.** Pierwsza wersja liczyła centroid markera
> i dawała 0,054 px różnicy między stanami. Przyczyna nie leżała w geometrii: stan 1 czyta
> piramidę skopiowaną z natywnego poziomu JP2 (falka), stan 2 decymuje COG uśrednianiem
> pudełkowym. Dwie różne piramidy tego samego rastra mają różną fazę jądra — na krawędzi
> markera stan 1 zwraca 223 i 32 tam, gdzie stan 2 zwraca twarde 255 i 0. **Każdy** estymator
> położenia liczony z jasności dziedziczy tę różnicę i mierzy radiometrię krawędzi zamiast
> układu współrzędnych. Wnętrze stałego bloku jest niezmienne przy dowolnym znormalizowanym
> jądrze, więc kody bloków dają porównanie bez tolerancji. Faza krawędzi jest nadal
> raportowana (`pyramid_phase_px_max`), ale nie bramkuje.

**Uwaga (Windows runtime):** dowody sięgające do backendu robią RE-SPAWN procesu z
`KMP_DUPLICATE_LIB_OK=TRUE` + `MKL_THREADING_LAYER=SEQUENTIAL` + `OMP/MKL_NUM_THREADS=1`
(patrz `_common/appenv.py`) — bez tego `numpy @` crashuje (0xc06d007f) po załadowaniu GDAL+torch.
`run_all.py` uruchamia każdy dowód jako **izolowany subprocess** (DATA_DIR/MODELS_ROOT są
cache'owane przy imporcie, więc współdzielony proces mieszałby konfigurację między dowodami).
