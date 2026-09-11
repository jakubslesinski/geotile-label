# importers/ — benchmark → projekt GeoTile Label

Cienkie importery zamieniające benchmarki z `E:\Datasets` na **kanoniczne projekty**
GeoTile Label (referencja do rastra + manifest geo + adnotacje `polygon_scene_px`).
Plan i mapowanie pól: [`../BENCHMARK_IMPORT_PLAN.md`](../BENCHMARK_IMPORT_PLAN.md).

## FAIR1M (zaimplementowany)

```
fair1m/
  geometry.py   # czyste: polygon → rotated_bbox/bbox (bez zależności od backendu)
  parse.py      # czyste: XML FAIR1M → kanoniczne rekordy (polygon_scene_px, klasa)
  build.py      # składa projekt sterując writerami backendu (schemat = aplikacja)
import_fair1m.py # CLI
tests/test_fair1m_parse.py  # testy rdzenia (feed dla v10/v02)
```

Zasada: schematu NIE duplikujemy — scenę/manifest/anotacje zapisują funkcje aplikacji
(`save_json`, `write_scene_manifest`, `rebuild_scenes_index`), więc wynik jest tym, co
aplikacja realnie otwiera. Rastry są **referencjonowane** (`project.scene_folder`), nie kopiowane;
`source_scene_uid` = sha256 rastra (prowenansja). Sceny bez geo degradują do pixel-frame.

### Uruchomienie

Wymaga interpretera środowiska backendu (rasterio/GDAL). GDAL_DATA/PROJ_LIB wykrywane
automatycznie z prefiksu conda (patrz memory `backend-tests-proj-env`).

```powershell
# test na 3 scenach
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_fair1m.py --limit 3 --out E:\GeoTileLabel_data\benchmark_projects

# pełny import
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_fair1m.py --out E:\GeoTileLabel_data\benchmark_projects
```

Argumenty: `--fair1m-root` (dom. `E:\Datasets\FAIR1M`), `--out` (project_location),
`--backend` (dom. `<repozytorium>\backend`), `--name`, `--limit`, `--author`.

### Zweryfikowane (smoke, `--limit 3`)

37 klas (podklasy FAIR1M), 3 sceny geo, 74 anotacje, 0 pominięć. Manifest: `crs=EPSG:4326`,
`source_scene_uid` policzone; adnotacja `polygon_scene_px` == 4 rogi z XML (bezstratnie).

### Testy rdzenia

```powershell
py importers/tests/test_fair1m_parse.py   # lub: python -m pytest importers/tests -q
```

Testy są czyste (bez backendu): parsowanie XML, geometria OBB, round-trip pola wielokąta
(dowód pod v10 — import lossless).

## DOTA (zaimplementowany)

```
dota/
  parse.py      # czyste: DOTA txt (8 wsp. + kategoria + difficult) → kanon; pomija nagłówki
  build.py      # projekt pixel-frame (NO_GEO); obsługa wielu splitów (train/val)
import_dota.py  # CLI
tests/test_dota_parse.py
```

DOTA to duże PNG **bez geo** (do 20000²) → projekt pixel-frame (`georeferencing=NO_GEO`,
`split=image_block_split`); kafelkowanie dużych scen robi aplikacja przy budowie datasetu.
Geometria współdzielona z `fair1m.geometry`. Wiele splitów obsłużone przez `filename` =
względny podpath `"<split>/images/<name>.png"` (resolver łączy z `scene_folder`=root dota-v2).
Oryginalny podział DOTA zapisany w sidecarze projektu `benchmark_import.json` (pod v13/v06).
`difficult` zachowane w rekordzie.

```powershell
# test 2 sceny na split (train+val)
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_dota.py --limit 2 --out E:\GeoTileLabel_data\benchmark_projects
# pełny import (train+val, version2.0)
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_dota.py --out E:\GeoTileLabel_data\benchmark_projects
```

Argumenty: `--dota-root` (dom. `E:\Datasets\DOTA\dota-v2`), `--splits` (dom. `train,val`),
`--version` (dom. `version2.0`), `--limit` (per split), `--out`, `--backend`, `--name`, `--author`.

**Zweryfikowane** (`--limit 2`): 18 klas (DOTA v2.0), 4 sceny (2 train + 2 val), 525 anotacji,
0 pominięć; manifest `NO_GEO` + `source_scene_uid`, `polygon_scene_px` z txt, split w sidecarze.

## xView3 (zaimplementowany)

```
xview3/
  parse.py      # czyste: CSV per-scena → kanon (bbox z HBB lub box wokół punktu); klasy z is_vessel/is_fishing
  build.py      # SAR/GEO; VRT Float16→Float32; tożsamość z oryginału VV_dB
import_xview3.py # CLI
tests/test_xview3_parse.py
```

SAR Sentinel-1 (UTM 10 m), rastry **Float16**. Dwie pułapki (obie rozwiązane):
1. **Odczyt:** rasterio nie mapuje `GDT_Float16` (`KeyError:15`); dodatkowo build datasetu
   (`preprocessing_profiles.py`) kieruje do rasterio **tylko `.tif/.tiff`** — VRT/`.vrt` spada
   do PIL („cannot identify image file"). → raster roboczy = **materializowany uint8 GeoTIFF**
   (`derived_scenes/<sid>/vv_u8.tif`), globalny stretch percentyl **2-98% dB**; lekki (~300 MB/scenę)
   i czytelny wszędzie. `source_scene_uid` liczony z **oryginalnego VV_dB** (prowenansja → źródło).
2. **Radiometria:** xView3 to **dB (ujemne)**; domyślny `sar_log_percentile` robi `log1p(clip(x,0))`
   → wyzerowałoby dB (czarne kafle). → profil projektu = **`sar_linear_percentile`** (percentyl bez log).

Etykiety punktowe (row/col, część z HBB) → `geometry_type="bbox"`; metadane
(`detect_lat/lon`, `distance_from_shore_km`, `is_vessel/is_fishing`, `confidence`…) w
`attributes` — pod v11 (dokładność geo) i v12 (stratyfikacja). Klasy: fishing_vessel /
non_fishing_vessel / vessel / non_vessel / unknown.

```powershell
# test 1 scena (validation — mniejszy split)
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_xview3.py --splits validation --limit 1 --out E:\GeoTileLabel_data\benchmark_projects
```

Argumenty: `--xview3-root` (dom. `E:\Datasets\xView3`), `--splits` (dom. `train,validation`),
`--limit` (per split), `--out`, `--backend`, `--name`, `--author`.

**Uwaga wydajność:** prowenansja hashuje pełne VV_dB (~1,4 GB/scenę) — pełny import całego
zbioru jest wolny (hash dominuje). Smoke `--limit 1`: 5 klas, 1 scena, 344 anotacje (27 s).

**Zweryfikowane:** manifest `SAR/GEO`, `crs=EPSG:32632` (UTM 10 m), `dtype=float32` (VRT),
`source_scene_uid` z VV_dB (1,4 GB); anotacja `bbox` + `attributes` z `detect_lat/lon`.

## DIOR-R (zaimplementowany)

```
diorr/
  parse.py      # czyste: VOC-XML robndbox (4 rogi) / bndbox (HBB) → kanon; difficult
  build.py      # pixel-frame (NO_GEO); train/val→JPEGImages-trainval, test→JPEGImages-test
import_diorr.py # CLI
tests/test_diorr_parse.py
```

JPEG 800×800 **bez geo** → pixel-frame; `robndbox` daje 4 jawne rogi → wprost `polygon_scene_px`
(geometria z `fair1m.geometry`); obiekty HBB (`bndbox`) → `geometry_type="bbox"`. 20 klas z
`YOLODIOR-R/classes.txt` (kanoniczna kolejność) lub zebrane z XML. Splity z `ImageSets/Main`,
oryginalny podział w sidecarze `benchmark_import.json`; `difficult` zachowane.

```powershell
& "$env:APPDATA\GeoTileLabel\runtime\backend-env-cuda\python.exe" `
    importers/import_diorr.py --limit 3 --out E:\GeoTileLabel_data\benchmark_projects
```

Argumenty: `--diorr-root` (dom. `E:\Datasets\DIOR-R\DIOR-R`), `--classes-file`
(dom. `…\YOLODIOR-R\classes.txt`), `--splits` (dom. `train,val`; też `test`), `--limit`, `--out`, `--backend`.

**Zweryfikowane** (`--limit 3`): 20 klas, 6 scen (3 train + 3 val), 14 anotacji, 0 pominięć;
manifest `EO/NO_GEO`, `polygon_scene_px` = rogi robndbox (bezstratnie), `class_id` z classes.txt.

## Uwaga o ponownym uruchomieniu

`project_id` jest deterministyczne z nazwy, a `create_project_root` **nie nadpisuje** —
jeśli folder docelowy istnieje, powstaje wariant `<Nazwa>_<id>`. Do czystego re-importu
usuń wcześniej stary folder projektu.

## Status

Wszystkie cztery formatery zaimplementowane i zweryfikowane (smoke): **FAIR1M**, **DOTA**,
**xView3**, **DIOR-R**. Każdy ma czysty parser + testy (feed pod v10/v02) i build sterujący
writerami backendu. Kolejny krok: **spięcie dowodów walidacji** na zaimportowanych projektach
(v02/v10 na FAIR1M/DOTA/DIOR-R, v11/v12 na xView3) — patrz `../claim-evidence-matrix.md`.
