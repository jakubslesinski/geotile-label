# Eksport

Format treningowy powstaje **automatycznie przy generowaniu** datasetu, zgodnie z
[geometrią zadeklarowaną w projekcie](../projects/utworz-projekt.md). Eksport służy do
konwersji na formaty wtórne i do spakowania przenośnej paczki - nie jest już osobnym
krokiem warunkującym trening.

!!! info "Format główny jest automatyczny"

    Folder wygenerowanej wersji jest od razu trenowalny w zadaniu zgodnym z projektem:
    projekt z ramkami osiowymi dostaje etykiety YOLO (`labels/`), a projekt z ramkami
    zorientowanymi - dodatkowo etykiety YOLO OBB (`labels_obb/`) i `data_obb.yaml`. Nie
    trzeba już „ręcznie eksportować YOLO", żeby ruszył trening.

## Pasek akcji przy wersji datasetu

Zamiast osobnej zakładki, akcje są przy **wybranej wersji datasetu** (moduł Dataset):

- **Konwertuj na COCO / Pascal VOC** - formaty wtórne do interopu z innymi narzędziami,
  zapisywane obok wygenerowanego datasetu, na żądanie;
- **Eksportuj paczkę ZIP** - wybór formatów uruchamia trwałe zadanie
  `dataset_export`; gotowy artefakt pobierasz z panelu
  [Zadania w tle](../reference/zadania-w-tle.md);
- **Otwórz folder** - folder wybranej wersji datasetu.

## Który format

| Format | Kiedy |
| --- | --- |
| **YOLO** | trening detekcji ramkami osiowymi (automatycznie dla projektu `bbox`) |
| **YOLO OBB** | trening na ramkach zorientowanych (automatycznie dla projektu `rotated_bbox`) |
| **COCO** | narzędzia oczekujące jednego pliku JSON (konwersja na żądanie) |
| **Pascal VOC** | starsze pipeline'y, jeden XML na obraz (konwersja na żądanie) |
| **GeoParquet** | kontrola w narzędziach GIS (w paczce ZIP) |

## Zawartość paczki ZIP

Formaty treningowe, manifesty, statystyki, raport audytu, sumy kontrolne SHA-256 oraz
metadane w CSV, Parquet i GeoParquet.

## Zadanie i cache eksportu

Eksport działa w tle, pokazuje postęp i można go anulować. Paczka pojawia się do
pobrania dopiero po atomowym zakończeniu zapisu. Ponowienie eksportu tej samej wersji
datasetu, tego samego zestawu formatów i tej samej wersji eksportera wykorzystuje
gotowy artefakt z cache. Kolejność zaznaczenia formatów nie tworzy innej kopii.

Zmiana datasetu, zestawu formatów lub wersji eksportera tworzy nowy artefakt. Cache
jest regenerowalny i może zostać usunięty podczas czyszczenia artefaktów.

!!! warning "Sceny źródłowe nie trafiają do paczki"

    ZIP zawiera **kafelki**, a nie pełne zobrazowania. Odbiorca datasetu nie dostaje
    materiału źródłowego - jeśli ma być inaczej, trzeba przekazać go osobno.

## Sumy kontrolne i pochodzenie

Każda paczka niesie sumy SHA-256 oraz manifest z opisem pochodzenia: z jakiej wersji
datasetu powstała, jaka była konfiguracja i podział. Dzięki temu paczkę można powiązać
z projektem nawet po latach.

## Kontrola przed przekazaniem

Przed wysłaniem paczki do treningu warto sprawdzić
[audyt](../datasets/audyt.md) i [statystyki](../datasets/statystyki.md), a adnotacje
GEO obejrzeć w [QGIS](geoparquet-qgis.md).

## Paczka datasetu a paczka adnotacji

To dwie różne rzeczy. Paczka datasetu jest **produktem końcowym** - wejściem do
treningu i materiałem archiwalnym. Do wymiany pracy w zespole służy paczka adnotacji.
Patrz [Rodzaje paczek](../reference/rodzaje-paczek.md).
