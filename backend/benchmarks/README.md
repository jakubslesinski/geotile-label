# Benchmarki wydajności GeoTile Label

Benchmarki z tego katalogu implementują etap B0 z
[`DESIGN_DECISIONS.md`](../../DESIGN_DECISIONS.md) (performance-roadmap).
Nie zapisują danych do projektu. Benchmarki datasetu i embeddingów korzystają z danych
syntetycznych w katalogu tymczasowym, a benchmarki projektu ustawiają
`GEOTILE_BENCHMARK_READ_ONLY=1`, co blokuje leniwe zapisy migracyjne podczas odczytu.

## Zalecane uruchomienie

Z repozytorium, w PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run-performance-baseline.ps1 `
  -ProjectId <project-id> `
  -StorageProfile virtual_hdd
```

Opcjonalna inferencja wymaga jawnego wskazania obu plików:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run-performance-baseline.ps1 `
  -ProjectId <project-id> `
  -ScenePath C:\data\scene.tif `
  -ModelPath C:\models\best.pt `
  -InferenceDevice cuda
```

Skrypt wybiera kolejno spakowany runtime `.desktop-build/backend-env`, środowisko
`backend/.venv` albo `python` z `PATH`. Wyniki trafiają do ignorowanego przez Git katalogu:

```text
benchmark-results/<UTC timestamp>/
  baseline_manifest.json
  dataset_synthetic.json
  embedding_search_synthetic.json
  catalog_cold.json
  catalog_warm.json
  catalog_snapshot_cold.json
  catalog_snapshot_warm.json
  project_summary_cold.json
  project_summary_warm.json
  whole_scene_inference.json       # tylko gdy podano scenę i model
```

## Znaczenie cold i warm

- `cold` oznacza nowy proces Pythona i brak aplikacyjnego cache w danym benchmarku.
- `warm` wykonuje jawny warm-up aplikacyjnego cache przed mierzonym etapem.
- Skrypt nie opróżnia cache systemu operacyjnego. Takie działanie wymaga uprawnień
  administracyjnych, wpływa na inne aplikacje i utrudnia bezpieczne powtarzanie pomiaru.

Dlatego każdy raport zapisuje opis semantyki cache. Przy porównywaniu wyników należy używać
tego samego sprzętu, profilu dysku, danych i kolejności uruchomienia.

## Pojedyncze benchmarki

Każdy skrypt ma `--help`, np.:

```powershell
python backend/benchmarks/benchmark_catalog.py --help
python backend/benchmarks/benchmark_catalog_snapshot.py --help
python backend/benchmarks/benchmark_dataset_build.py --help
python backend/benchmarks/benchmark_project_summary.py --help
python backend/benchmarks/benchmark_inference.py --help
python backend/benchmarks/verify_inference_parity.py --help
python backend/benchmarks/benchmark_embedding_search.py --help
python backend/benchmarks/benchmark_content_identity.py --help
python backend/benchmarks/benchmark_job_manager.py --help
python backend/benchmarks/benchmark_artifact_export.py --help
python backend/benchmarks/benchmark_scene_import.py --help
python backend/benchmarks/benchmark_training_resources.py --help
python backend/benchmarks/benchmark_pansharpen_pipeline.py --help
python backend/benchmarks/benchmark_jp2_fullres_strategies.py --help
python backend/benchmarks/display_stretch_audit.py --help
```

`display_stretch_audit.py` ([`DESIGN_DECISIONS.md`](../../DESIGN_DECISIONS.md), display-stretch F0)
przepuszcza okna prawdziwej sceny przez warianty rozciagniecia tonalnego - dawna podwojna
kwantyzacje, renderer kafli, rozciaganie na kaflu, zakres "Widok" i domyslne SAR - i liczy
miary z bramek: odcienie w kaflu, najwieksza przerwe miedzy poziomami, czern/biel, odsetek
jasnych celow wypalonych do bieli oraz skok jasnosci na granicy kafli. Tylko odczyt:
histogram liczony w pamieci, domyslnie dane z `%APPDATA%\GeoTileLabel\data`.

## Eksperyment JP2: OpenJPEG, Grok i COG (zamknięty)

Etapy E0–E5 porównywały sterowniki `JP2OpenJPEG` i `JP2Grok` oraz ścieżkę opartą na COG
na zamrożonym, deterministycznym workloadzie pełnej rozdzielczości. Rozstrzygnięcie —
budujemy COG-i, a Grok został odrzucony jako bezpośredni sterownik runtime — opisuje
[`DESIGN_DECISIONS.md`](../../DESIGN_DECISIONS.md) (jp2-fullres).

**Skrypty orkiestrujące ten eksperyment nie są częścią tego repozytorium.** Stawiały
izolowane środowisko Conda z własną budową Groka i sterowały wieloetapowym przebiegiem na
konkretnych scenach. Bez nich `probe_jp2_grok_advise_read.py` i `verify_jp2_grok_lab.py`
nie mają gdzie się uruchomić — zostają jako zapis metody, nie jako narzędzia.

Skrypty pomiarowe, które działają samodzielnie (ścieżki podaje się argumentem):

| Skrypt | Co robi |
| --- | --- |
| `benchmark_jp2_fullres_strategies.py` | zamraża workload: fingerprinty źródeł, parametry codestreamu, 100 deterministycznych okien 1× na scenę i ślad viewportu; tryb `verify` sprawdza istniejący manifest bez dekodowania pikseli |
| `benchmark_jp2_cog_construction.py` | buduje i waliduje COG-i (etap E4) |
| `summarize_jp2_cog_e4.py` | zestawia wyniki budowy COG |
| `evaluate_jp2_e5_gate.py` | liczy bramkę runtime E5 |
| `diagnose_jp2_warp_parity.py` | `capture` i `compare` — parzystość odczytu przez WarpedVRT |
| `verify_jp2_multiband_layout.py` | domyka lukę wielopasmową z E4 |

Żaden z nich nie zapisuje do katalogów źródłowych ani do danych projektu.


`benchmark_pansharpen_pipeline.py` porównuje warianty pipeline'u pansharpeningu na
rzeczywistej dostawie MUL+PAN (DESIGN_DECISIONS.md, scene-import P1.4b). Poza czasem
i I/O mierzy szczytowy RSS całego drzewa procesów oraz szczytowy scratch, a równoważność
sprawdza DOKŁADNIE — porównaniem pikseli na losowanych oknach pełnej rozdzielczości, nie
statystyką zdecymowaną. `--repeat 2` sprawdza powtarzalność każdego wariantu.

`benchmark_artifact_export.py` tworzy syntetyczny, niekompresowalny payload i mierzy osobno
pierwsze strumieniowe zbudowanie ZIP oraz identyczne ponowienie z cache P1.6. Raport zapisuje
rozmiar archiwum, SHA-256 i flagi `cold_cache_hit`/`warm_cache_hit`; payload nie jest wczytywany
w całości do pamięci.

Raport ma schemat `geotile_performance_report` w wersji 1. Zawiera środowisko, wejście,
etapy, liczniki, czasy wall/CPU i pamięć procesu. Zapisy są atomowe.

## P2.5: adaptacyjny pipeline treningowy

Krótki benchmark preflightu dekoduje deterministyczną próbkę maksymalnie 24 obrazów,
szacuje bezpieczny budżet cache RAM i rekomenduje `batch`, `workers` oraz `cache`. Nie
uruchamia treningu, nie tworzy cache Ultralytics i nie modyfikuje opublikowanego datasetu:

```powershell
python backend/benchmarks/benchmark_training_resources.py `
  --dataset-dir C:\data\project\dataset_runs\run-id `
  --device cuda --imgsz 640 --batch -1 `
  --storage-profile local_nvme `
  --output benchmark-results\p2_5\resource_probe.json
```

Stan cache systemu operacyjnego pozostaje jawnie `unspecified`. Do zamknięcia bramki
przepustowości należy osobno wykonać co najmniej jedną epokę baseline i rekomendowaną
na dwóch profilach sprzętu; `training_performance.json` zapisuje images/s, czas epoki,
przybliżony data-wait, utylizację GPU, peak VRAM, RSS całego drzewa procesów oraz
efektywny batch i liczbę workerów.

Kontrolowany trening jednej konfiguracji można uruchomić bez wykonywania całej macierzy
P2.3. Runner ładuje wspólny bootstrap packed Conda przed uruchomieniem procesów Windows
`spawn`, a źródłowy dataset jest kontrolowany podpisami przed i po pomiarze:

```powershell
<backend-env>\python.exe backend\benchmarks\benchmark_training_cache_ultralytics.py `
  --dataset-dir C:\data\project\dataset_runs\run-id `
  --weights E:\models\yolo11n-obb.pt `
  --output-dir benchmark-results\p2_5\training-profile `
  --imgsz 640 --batch -1 --workers 8 `
  --variants legacy --telemetry
```

`batch=-1` oznacza dobór pojemności przez Ultralytics AutoBatch, a nie obietnicę maksymalnej
przepustowości. Do porównania wydajności należy dodać przebieg ze stałym batchem; do parytetu
metryk używać identycznej konfiguracji, ponieważ zmiana efektywnego batcha zmienia trajektorię
optymalizacji modelu.

## P1.7: import scen i piramidy wyświetlania

Benchmark P1.7 nie modyfikuje źródeł. VRT i sidecar `.ovr` powstają w osobnym katalogu
tymczasowym i są usuwane po pomiarze. Do fazy `overview` potrzebny jest runtime z
`osgeo.gdal`, najlepiej środowisko spakowane z aplikacją.

Obecny koszt metadanych można zmierzyć osobno:

```powershell
python backend/benchmarks/benchmark_scene_import.py `
  --phase metadata `
  --scene-root C:\data\scenes `
  --storage-profile local_nvme
```

Profile overview należy porównywać na rozłącznych, zbalansowanych grupach. Przykład dwóch
z czterech grup:

```powershell
python backend/benchmarks/benchmark_scene_import.py `
  --phase overview --scene-root C:\data\scenes `
  --group-count 4 --group-index 0 --workers 1 `
  --compression DEFLATE --gdal-threads 1 --work-dir E:\benchmark-work

python backend/benchmarks/benchmark_scene_import.py `
  --phase overview --scene-root C:\data\scenes `
  --group-count 4 --group-index 1 --workers 2 `
  --compression DEFLATE --gdal-threads 1 --work-dir E:\benchmark-work
```

Raport zawiera listę scen i rozmiar każdej grupy, czas i CPU per scena, rozmiar sidecara,
liczniki I/O, poziomy decymacji oraz kontrolę `source_unchanged`. `--keep-artifacts` służy
wyłącznie do ręcznej inspekcji; bez tej flagi duże pliki testowe są sprzątane automatycznie.

Od P1.7 charakterystyka metadanych raportuje też `raster_open_count`, `sample_read_count`
i `deferred_display_profiles`. TIFF bez overview jest próbkowany ograniczoną siatką natywnych
bloków. JP2/OpenJPEG pozostaje bez odczytu pikseli w fazie katalogowania, ponieważ nawet odczyt
najniższego wirtualnego overview może zdekodować cały codestream i podnieść peak RSS o kilka GiB;
taki raster ma `display_stats.method=deferred_jp2` i jest przygotowywany w osobnej fazie.

Referencyjne raporty P1.7 znajdują się w `benchmark-results/p1_7/`, w szczególności:

- `metadata_current_baranovichi.json` — baseline starego próbkowania,
- `overview_current_serial_deflate_baranovichi.json` — baseline piramid,
- `overview_workers2_deflate_baranovichi.json` — dwa procesy i predictor 2,
- `overview_workers2_zstd_baranovichi.json` — przyjęty profil referencyjny: dwa procesy,
  ZSTD i predictor 2,
- `metadata_candidate_block_baranovichi.json` — regresja PAN/PANSHARP,
- `metadata_candidate_deferred_jp2_gb_wat_39.json` — stress test 39 rastrów / około 98 GiB.

Produkcja wybiera maksymalnie dwa procesy dopiero przy co najmniej 8 CPU i 24 GiB wolnego RAM;
w przeciwnym razie buduje serialnie. Ustawienia można jawnie powtórzyć przez
`GEOTILE_OVERVIEW_WORKERS=1..4` i `GEOTILE_OVERVIEW_COMPRESSION=ZSTD|DEFLATE|LZW`.
Bezpieczny smoke packed runtime, obejmujący dwa procesy, atomową publikację profilu i kontrolę
niezmienności źródeł:

```powershell
<backend-env>\python.exe scripts\verify-p1-7-overviews.py
```

## P1.4: embeddingi, ANN i near-duplikaty

Mały fixture pozostaje trybem exact. Bramka skali buduje USearch, mierzy Recall@k względem
exact na próbce pełnego indeksu i uruchamia masowe cosine-LSH bez macierzy N×N:

```powershell
python backend/benchmarks/benchmark_embedding_search.py `
  --objects 350000 `
  --dimensions 384 `
  --exact-limit 10000 `
  --candidate-k 64 `
  --recall-queries 20 `
  --ram-budget-mib 4096
```

`--artifact-dir` zachowuje albo reużywa natywny indeks, co pozwala powtarzać strojenie i
testy zapytań bez ponownego kosztu budowy. Raport rozróżnia `search_backend=usearch` dla
nearest od `duplicate_search_backend=cosine_lsh` dla skanu całego zbioru.

## P1.2: batchowana inferencja i zgodność wyników

`benchmark_inference.py` przyjmuje `--batch-size auto|1..64`,
`--prefetch-batches` i `--progress-interval-ms`. Raport zawiera osobno czas odczytu,
preprocessingu aplikacji, wywołań Ultralytics, postprocessingu i merge, a także peak
ograniczonej kolejki, liczbę wywołań modelu, retry po CUDA OOM i deterministyczny podpis
predykcji.

Zgodność `batch=1` z `batch=auto` należy sprawdzać tolerancyjnie, ponieważ CUDA może
powodować niewielki dryf numeryczny confidence i obiektów leżących dokładnie na granicy
NMS:

```powershell
python backend/benchmarks/verify_inference_parity.py `
  --scene-path C:\data\scene.ntf `
  --model-path C:\models\best.pt `
  --device cuda `
  --candidate-batch-size auto `
  --run-order candidate-first `
  --output benchmark-results\inference-parity.json
```

Weryfikator wymaga identycznej liczby detekcji w każdej parze klasa/geometria,
co najmniej 99,5% geometrii zgodnych do 0,5 px, średniej różnicy confidence do 0,001
i maksymalnej różnicy kąta OBB do 0,5 stopnia. `candidate-first` jest konserwatywną
kolejnością pomiaru czasu: baseline korzysta z rozgrzanego runtime jako drugi przebieg.
Do porównań wydajności między wersjami nadal należy używać osobnych raportów
`benchmark_inference.py` z takim samym stanem cache i kolejnością uruchomień.
