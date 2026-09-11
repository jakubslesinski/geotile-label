# Lokalizacje danych

Gdzie aplikacja trzyma projekty, modele, logi i środowisko uruchomieniowe. Ścieżki
przydają się przy backupie, przenoszeniu pracy na inny komputer i przy zgłaszaniu
błędów.

## Zasada nadrzędna

Pliki źródłowe - sceny, metadane dostawcy, pliki klas - **zostają tam, gdzie je
wskazałeś**. Aplikacja otwiera je tylko do odczytu i nie kopiuje do swojego katalogu.
Przeniesienie albo odłączenie dysku ze scenami nie niszczy projektu: adnotacje są
zapisane osobno, a źródło można wskazać ponownie przez
[Relinkuj źródło](../projects/relinkuj-zrodlo.md).

## Dane aplikacji

Wszystko poniżej znajduje się w `%APPDATA%\GeoTileLabel`.

| Ścieżka | Zawartość |
| --- | --- |
| `data\projects\` | projekty zapisane w lokalizacji domyślnej |
| `data\projects_index.json` | spis projektów wraz z rzeczywistą ścieżką każdego z nich |
| `data\models\base\` | wagi bazowe architektur używanych do treningu |
| `data\models\sam\` | modele SAM do narzędzia click-to-box |
| `logs\` | `app.log`, `backend.stdout.log`, `backend.stderr.log` |
| `runtime\` | rozpakowane środowisko backendu; przy zainstalowanym pakiecie GPU leżą obok siebie dwa |

!!! tip "Projekt nie musi leżeć w `data\projects`"

    Podczas tworzenia projektu można wskazać dowolny folder nadrzędny, na przykład na
    dysku roboczym albo współdzielonym. `projects_index.json` pamięta wtedy prawdziwą
    ścieżkę - dlatego to on, a nie zawartość `data\projects`, jest listą projektów.

Katalog `runtime\` można skasować bez utraty danych - odtworzy się przy następnym
starcie. Służy do tego **Ustawienia → Diagnostyka → Wyczyść środowisko i pamięć
podręczną**.

## Folder projektu

Niezależnie od miejsca zapisu projekt ma zawsze tę samą budowę.

| Ścieżka | Zawartość |
| --- | --- |
| `project.json` | profil projektu: modalność, georeferencja, typ geometrii, rola |
| `classes.json` | definicje klas wraz z numerami skrótów |
| `scene_sources.json` | podłączone źródła scen i ich identyfikatory |
| `scene_import_config.json` | tryb importu i ustawienie automatycznego COG pełnej rozdzielczości |
| `scenes_index.json` | katalog scen wraz ze statusami |
| `scenes\<id>\annotations.json` | **adnotacje sceny - dane kanoniczne** |
| `scenes\<id>\scene.json` | podsumowanie sceny dla katalogu i UI |
| `scenes\<id>\scene_manifest.json` | pełne metadane: CRS, transformacja, pochodzenie |
| `scenes\<id>\assistance\` | wyniki narzędzi AI oczekujące na decyzję |
| `derived_scenes\<id>\<wariant>\` | widoki robocze VRT/COG, gdy produkt ich wymaga |
| `tile_catalogs\` | katalogi kafelków: geometria i stan przeglądu, bez obrazów |
| `dataset_runs\` | wersje datasetów wraz z manifestami |
| `training_runs\` | przebiegi treningu, metryki i punkty kontrolne modeli |
| `jobs\<job_id>\` | trwałe zadania: `job.json`, `state.json`, `events.jsonl`, `performance.json`, `artifacts.json` |
| `artifacts\dataset_exports\` | cache gotowych paczek eksportowych ZIP; regenerowalny |
| `artifacts\training_datasets\` | współdzielony cache stagingu treningowego; regenerowalny i zależny od konfiguracji wydania |
| `import_reports\` | raporty importu paczek adnotacji |
| `*_config.json` | ustawienia tilingu, datasetu, predykcji i profili preprocessingu |

W katalogu wariantu `derived_scenes\<id>\<wariant>\` mogą znajdować się
`overview.vrt`, `overview.vrt.ovr` i `overview.profile.json`. Są to regenerowalne
produkty wyświetlania. Zewnętrzny `scene.tif.ovr` leży przy pliku źródłowym, czyli
**poza folderem projektu**.

Dla dużego generic JP2 katalog wariantu może zawierać również `fullres.state.json`
oraz aktywny `fullres.tif`. Plik `fullres.candidate.tif`, surowy RAW i pomocniczy VRT
są artefaktami przejściowymi budowy: renderer ich nie wybiera, a po publikacji lub
błędzie są sprzątane. Wszystkie te pliki są regenerowalne; nie zawierają adnotacji.

Miejsce zajęte przez cały `derived_scenes\` pokazuje panel **Pliki robocze scen** na
pulpicie projektu - z podziałem na kategorie, listą największych scen i przyciskiem
otwierającym ten folder w Eksploratorze. Panel jest wyłącznie odczytowy i nie liczy
niczego spoza `derived_scenes\`, więc nie jest miarą całkowitego rozmiaru projektu.
Opis panelu: [Produkty pochodne](../input-data/produkty-pochodne.md#pliki-robocze-scen).

Przebieg treningowy może zawierać `training_performance.json` obok `metrics.json`,
`results.csv`, macierzy pomyłek i wag. Raport wydajności nie zastępuje metryk jakości.

!!! info "Co jest źródłem prawdy, a co produktem pochodnym"

    Kanoniczne są **pełne sceny, ich metadane i `annotations.json`**. Katalogi
    kafelków, dataset runy i eksporty to produkty pochodne - można je skasować i
    wygenerować ponownie z tych samych adnotacji. Zasadę tę opisuje
    [Katalog kafelków](../datasets/katalog-kafelkow.md).

Wersjonowane indeksy i agregaty JSON również są pochodne. Ich usunięcie może
spowolnić pierwszy odczyt, ale aplikacja odbudowuje je z plików kanonicznych. Nie
edytuj indeksów ręcznie i nie traktuj ich jako kopii zapasowej adnotacji.

## Co zabrać przy przenoszeniu pracy

Wystarczy **cały folder projektu** oraz dostęp do tych samych plików źródłowych.
Katalog `runtime\` odtwarza się sam, a `data\models\`
wypełnia się przy instalacji pakietu treningowego.

Gotową procedurę opisuje [Backup i przenoszenie projektu](../projects/backup.md).

!!! warning "Przeniesienie samego folderu projektu nie przenosi scen"

    Sceny leżą poza projektem. Na nowym komputerze trzeba podłączyć ten sam nośnik
    albo wskazać kopię źródeł, a potem zrelinkować źródła. Adnotacje przetrwają, bo
    są zapisane w folderze projektu.
