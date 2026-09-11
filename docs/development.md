# Rozwój

Ta strona jest dla osób, które chcą zbudować GeoTile Label ze źródeł, uruchomić jego
kontrole albo zmienić kod. Do tych zadań jest samowystarczalna: wszystko, czego trzeba,
żeby skompilować, przetestować i wyprodukować instalator, jest tutaj.

[`README_dev.md`](https://github.com/jakubslesinski/geotile-label/blob/main/README_dev.md)
w korzeniu repozytorium jest pełnym odniesieniem - schodzi do wewnętrzności resolvera
i reguł produktów poszczególnych dostawców.

## Co jest w repozytorium

| Katalog | Zawartość |
| --- | --- |
| `frontend/` | React 19, TypeScript, Vite, Chakra UI, Leaflet, ApexCharts |
| `frontend/src-tauri/` | powłoka Tauri v2: cykl życia backendu, diagnostyka, instalator NSIS |
| `backend/` | FastAPI, rasterio/GDAL, pyarrow, tiling, dataset runy, eksporty, opcjonalne YOLO |
| `scripts/` | przygotowanie runtime, build release, smoke testy, narzędzia dokumentacji |
| `docs/` | ta dokumentacja; jest też pakowana do aplikacji jako pomoc offline |
| `validation/` | zestaw dowodów stojących za opublikowanymi twierdzeniami |
| `importers/` | parsery zbiorów benchmarkowych używanych przez zestaw dowodów |

Tauri uruchamia backend na losowym porcie `127.0.0.1`, generuje token sesji i przekazuje
frontendowi `{ baseUrl, token, capabilities }`. Frontend wysyła token w nagłówku
`X-GeoTile-Token`; adresy kafelków niosą go w query stringu.

Interaktywny [explorer architektury](architecture/index.md) pokazuje tę samą strukturę jako
potok i jako model danych, z odnośnikami z każdego elementu do kodu.

## Zasady, na których to stoi

```text
pełna scena + metadane + georeferencja + adnotacje źródłowe = dane kanoniczne
kafelki + YOLO/COCO/VOC + ZIP                               = wersjonowane produkty pochodne
```

- pełna scena i jej adnotacje są źródłem prawdy;
- każda scena, adnotacja, płytka i wersja datasetu ma stabilny identyfikator;
- `dataset_runs/<run_id>/` jest trwałym wynikiem generowania;
- `dataset/` pozostaje kompatybilnym cache ostatniego udanego runu;
- eksporty zachowują pochodzenie w manifestach, CSV, Parquet i GeoParquet;
- dane kontekstowe są wersjonowane oddzielnie od kanonicznych adnotacji.

Decyzje projektowe cytowane w komentarzach kodu przez identyfikator etapu zebrane są
w [`DESIGN_DECISIONS.md`](https://github.com/jakubslesinski/geotile-label/blob/main/DESIGN_DECISIONS.md),
łącznie z tymi, które zapisują, czego oprogramowanie świadomie **nie** robi.

## Wymagania

- Windows 10 albo 11, x64;
- Node.js 18+;
- Python 3.11+;
- Rust stable z Cargo;
- Microsoft C++ Build Tools, wymagane przez Tauri;
- Miniconda albo Anaconda z poleceniem `conda` - potrzebne **wyłącznie** do zbudowania
  spakowanego runtime i instalatora.

Zmiany we frontendzie nie wymagają ani builda Tauri, ani conda-pack. Pełny build desktopowy
uruchamiaj przed testem integracyjnym albo release, a nie przy każdej iteracji.

!!! tip "Restrykcyjna polityka PowerShella"

    Używaj `npm.cmd` zamiast `npm`, a skrypty uruchamiaj z `-ExecutionPolicy Bypass`.

## Uruchomienie w trybie deweloperskim

### Backend

```powershell
cd <repozytorium>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

!!! warning "Sam pip nie postawi działającego stosu geoprzestrzennego"

    `pip install -e .` **nie zainstaluje GDAL-a ze sterownikiem JP2** na Windows - koła pip
    nie niosą kompletu bibliotek natywnych. Odtwarzalne środowisko z przypiętymi wersjami
    opisuje `environment.yml` w korzeniu repozytorium:

    ```powershell
    conda env create -f environment.yml -p .\.venv-backend
    conda run -p .\.venv-backend python -m pip install -e ".[dev]"
    conda run -p .\.venv-backend python -m pytest backend/tests
    ```

`pyproject.toml` deklaruje trzy extrasy: `yolo` (torch i ultralytics), `sam3` (enkoder
tekstu) i `dev` (pytest). **Bez extrasa `dev` 37 z 76 plików testowych backendu się nie
uruchomi** - runtime pakowany z aplikacją nie zawiera pytesta.

Backend importuje `torch` i `ultralytics` leniwie, więc uruchamia się poprawnie bez nich,
a `/api/capabilities` raportuje faktyczną dostępność funkcji.

### Frontend w przeglądarce

Uruchom backend pod `127.0.0.1:8000`, a następnie:

```powershell
cd <repozytorium>\frontend
npm.cmd install
npm.cmd run dev
```

Cel proxy zmienia zmienna `VITE_BACKEND_DEV_URL` ustawiona przed `npm.cmd run dev`.

Tryb przeglądarkowy używa zamienników natywnych dialogów. Systemowy wybór pliku, otwieranie
folderu i *Save ZIP as…* trzeba sprawdzić w Tauri.

### Tauri dev

```powershell
cd <repozytorium>\frontend
npm.cmd run tauri:dev
```

Interpreter backendu wybierany jest w kolejności:

1. `GEOTILE_BACKEND_PYTHON`;
2. `backend\.venv\Scripts\python.exe`;
3. `python` z `PATH`.

`tauri:dev` nie buduje instalatora i nie uruchamia ponownie conda-pack. Zmiany React i Vite
odświeżają się na bieżąco, zmiany backendu wymagają restartu procesu Tauri.

## Najpierw zbuduj spakowany runtime

!!! danger "To jest pułapka na świeżym klonie"

    **Przed pierwszym poleceniem `cargo` albo `tauri` zbuduj runtime backendu.**
    `tauri.conf.json` deklaruje zasób `resources/backend-env.tar.gz.*`, a części tego
    archiwum - spakowane środowisko Pythona, około 800 MB - świadomie nie są trzymane
    w repozytorium przez `.gitignore`. Glob, który niczego nie dopasuje, przerywa skrypt
    budowania Tauri **kodem 101, zanim cokolwiek się skompiluje**, więc komunikat nie
    wygląda na brakujący plik:

    ```text
    error: failed to run custom build command for `geotile-label-desktop`
      glob pattern resources/backend-env.tar.gz.* path not found or didn't match any files.
    ```

    ```powershell
    powershell -ExecutionPolicy Bypass -File .\scripts\build-backend-env.ps1
    ```

    Dopiero po nim `cargo check`, `cargo build` i `npm run desktop:build` mają czym się
    karmić. `build-release-desktop.ps1` wykonuje ten krok samodzielnie, więc uwaga dotyczy
    ręcznych wywołań cargo i tauri.

Ten skrypt instaluje najnowsze zgodne wersje zależności, a nie zestaw przypięty, więc
zapisuje to, co faktycznie zainstalował, do `runtime-lock.txt` - pakiety conda z pełnymi
adresami URL, pakiety pip z wersjami. **To ten plik, a nie `environment.yml`, mówi, co
dokładnie zawiera wydany instalator.**

## Kontrole

| Kontrola | Polecenie | Co obejmuje |
| --- | --- | --- |
| Frontend | `npm.cmd run build` w `frontend/` | `tsc -b` oraz produkcyjny build Vite |
| Backend | `python -m pytest backend/tests` | wymaga extrasa `dev` |
| Backend, szybko | `python -m compileall .` w `backend/` | sama składnia |
| Rust | `cargo check` w `frontend\src-tauri\` | wymaga spakowanego runtime, patrz wyżej |
| Dokumentacja | `scripts\build-docs.ps1` | musi się zbudować bez ostrzeżeń o martwych linkach |
| Dokumentacja, pełna | `scripts\validate-docs.ps1` | tryb strict, zasoby offline, wyszukiwarka, wersja, dryf diagramów |
| Instalator | `scripts\build-release-desktop.ps1` | jedyny dowód, że drzewo jest kompletne |

Najpierw uruchamiaj kontrole najbliższe zmienianemu obszarowi. Pełny build NSIS jest
walidacją końcową, nie częścią każdej iteracji.

Do podglądu dokumentacji z odświeżaniem służy `scripts\serve-docs.ps1`; przełącznik
`-LiveEdit` pozwala edytować Markdown wprost w przeglądarce. Ten tryb zapisuje bezpośrednio
w `docs/` i potrafi zmieniać nazwy oraz usuwać pliki, dlatego serwer jest celowo ograniczony
do `127.0.0.1`. Jawna nawigacja w `mkdocs.yml` **nie** aktualizuje się sama po dodaniu,
przemianowaniu ani usunięciu strony.

## Diagramy architektury są generowane

`scripts\validate-docs.ps1` uruchamia najpierw **bramkę dryfu diagramów**
(`scripts/check-architecture-model.py`) i przerywa walidację, zanim cokolwiek zbuduje.

Jedynym źródłem jest `docs/architecture/architecture-model.json`. Zarówno
`docs/architecture/explorer.html`, jak i pliki `.d2` w `docs/architecture/src/` są
**generowane** - ręczna zmiana w nich przepada przy następnej generacji. Zmienia się model
(albo szablon `scripts/templates/explorer.template.html`), a potem:

```powershell
python .\scripts\generate-architecture-explorer.py
python .\scripts\generate-architecture-d2.py
```

Bramkę można uruchomić samą; potrzebuje wyłącznie biblioteki standardowej:

```powershell
python .\scripts\check-architecture-model.py
```

Porównuje też model z eksportami rysunków do artykułu, gdy repozytorium artykułu leży obok.
Gdy go nie ma, rysunki są **pomijane**, a nie zgłaszane jako błąd; wskazuje je
`GEOTILE_PAPER_ROOT`.

## Budowa instalatora

Szybki build deweloperski:

```powershell
cd <repozytorium>\frontend
npm.cmd run desktop:build
```

Instalator trafia do `frontend\src-tauri\target\release\bundle\nsis\`. Budowany jest tylko
`.exe` NSIS; MSI nie jest częścią workflow.

Build wydania:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-release-desktop.ps1 -Version 1.4.0
```

Skrypt synchronizuje wersję w npm, Cargo i Tauri; przygotowuje backend i runtime bazowy
(torch tylko CPU); uruchamia smoke testy **na spakowanym runtimie**; buduje NSIS; tworzy
`release/GeoTileLabel-<wersja>` z instalatorem, `release_manifest.json` i `SHA256SUMS.txt`.

- `-ReuseBackendRuntime` pomija ponowne budowanie spakowanego środowiska. Nie używaj po
  zmianie `backend/pyproject.toml` ani `scripts/build-backend-env.ps1`.
- `-SkipSmokeTests` służy do diagnozowania samego procesu budowy, nigdy do wydania.

Budowa runtime wymaga dostępu do `repo.anaconda.com`, conda-forge, PyPI i
`download.pytorch.org`. `CondaHTTPError: HTTP 000 CONNECTION FAILED` oznacza problem
z siecią, DNS, VPN/proxy albo blokadę domen, a nie błąd aplikacji; po przywróceniu
połączenia uruchom to samo polecenie ponownie.

### Edycje

`-Lite` ustawia na czas builda `VITE_GEOTILE_EDITION=lite`. Vite wstrzykuje tę wartość do
`import.meta.env`, `frontend/src/config/edition.ts` ją czyta, a trzy zakładki projektu -
**Analiza datasetu, Trening i Wyniki** - zostają ukryte. To zmiana wyłącznie w interfejsie:
backend i endpointy są w obu edycjach identyczne.

## Zmienne środowiskowe

Tauri ustawia co najmniej:

```text
DATA_DIR=%APPDATA%\GeoTileLabel\data
MODELS_ROOT=%APPDATA%\GeoTileLabel\data\models
GEOTILE_DESKTOP=1
GEOTILE_BUILD_VARIANT=yolo
GEOTILE_ENABLE_YOLO=1
GEOTILE_AUTH_TOKEN=<token sesji>
```

Zmienne deweloperskie, używane przez smoke testy - nigdy nie ustawiaj ich w buildzie
dystrybuowanym użytkownikom:

```text
GEOTILE_TRAIN_MOCK=1          # worker symuluje trening zamiast wołać ultralytics
GEOTILE_SAM_MOCK=1            # SAM zwraca deterministyczną maskę
GEOTILE_SAR_EXEMPLAR_MOCK=1   # dopasowanie egzemplarza bez modelu
```

`MODELS_ROOT` nie jest zmienną deweloperską - ustawia ją Tauri. Nadpisuj ją tylko
w testach, żeby nie mieszać wag testowych z prawdziwymi.

Środowisko Pythona z `torch`, `ultralytics`, `psutil` i `cv2` używane przez smoke testy to
`.desktop-build/backend-env/python.exe`, a nie systemowy Python.

## Kryteria gotowości wydania

- `npm.cmd run build` przechodzi bez błędów TypeScriptu;
- `cargo check` przechodzi;
- build NSIS kończy się poprawnie;
- backend uruchamia się bez systemowego Pythona;
- działają sceny EO/SAR, GEO/NO GEO oraz GeoTIFF 8/16-bit;
- działają ramki osiowe i zorientowane, tiling i przegląd;
- przechodzą generowanie, statystyki, audyt i eksport wybranego runu;
- ZIP zawiera manifesty, sumy kontrolne, Parquet/GeoParquet i raporty;
- backup, import i diagnostyka zostały sprawdzone;
- aplikacja raportuje `yolo=true`, a predykcja działa na modelu testowym.

## Gdzie szukać dalej

- [Architektura](architecture/index.md) - interaktywny potok i model danych
- `README_dev.md` - pełne odniesienie deweloperskie
- `DESIGN_DECISIONS.md` - decyzje cytowane z komentarzy kodu
- `validation/` - zestaw dowodów i sposób jego powtórzenia
