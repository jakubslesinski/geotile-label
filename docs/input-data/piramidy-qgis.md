# Piramidy i sidecary QGIS

Duże rastry otwierają się szybciej, gdy mają **piramidy** (overview) - pomniejszone
poziomy obrazu używane przy małych skalach. GeoTile Label może budować własne piramidy
w tle, ale może też wykorzystać zewnętrzny sidecar utworzony wcześniej w QGIS.

## Kiedy warto przygotować piramidy w QGIS

- gdy przed importem przygotowujesz dużą paczkę TIFF/GeoTIFF na wydajnej stacji;
- gdy te same rastry będą używane w QGIS i GeoTile Label;
- gdy nie chcesz, aby pierwsze uruchomienie projektu czekało na długi proces GDAL;
- gdy masz osobny proces kontroli i archiwizacji produktów pochodnych.

Natywne poziomy JP2 nie zawsze wystarczają do interaktywnego wyświetlania. Dla dużych
JP2 (domyślnie od 256 megapikseli) GeoTile Label przygotowuje w projekcie własną
piramidę `overview.vrt.ovr`, ponieważ dekodowanie natywnych poziomów OpenJPEG przy
dużym zbliżeniu może długo obciążać procesor. Mniejsze JP2 z kompletnymi poziomami są
nadal oznaczane jako **natywne** i nie wymagają dodatkowego pliku.
Projektowa piramida JP2 nie jest liczona od pełnej rozdzielczości: aplikacja kopiuje
natywny poziom `2×`, `4×` albo `8×` do kafelkowanego GeoTIFF i na nim buduje poziomy
dalsze. Dobór jest adaptacyjny, aby ograniczyć szczytowe użycie pamięci podczas importu.

Jeżeli chcesz przygotować sidecar dużego JP2 wcześniej w QGIS, użyj w kontrolowanym
wywołaniu `force_external_for_native=True`. Taki `.ovr` powstaje obok źródła i ma
pierwszeństwo przed piramidą projektową. Plik źródłowy pozostaje tylko do odczytu.

## Wynik

Sidecar musi leżeć **obok rastra** i mieć jego pełną nazwę z dopiskiem `.ovr`:

```text
scene.tif
scene.tif.ovr
```

Nie importuj `.ovr` jako osobnej sceny. GeoTile Label wykrywa go automatycznie podczas
importu, odświeżenia katalogu oraz przed odczytem obrazu.

## Uruchomienie w QGIS

1. Uruchom QGIS i otwórz **Wtyczki → Konsola Pythona** albo naciśnij
   ++ctrl+alt+p++.
2. W panelu konsoli wybierz **Pokaż edytor**.
3. Utwórz nowy skrypt, wklej [kod z tej strony](#pelny-skrypt-python) i zapisz go jako
   `qgis_batch_build_overviews.py`. Możesz też otworzyć plik bezpośrednio z repozytorium:
   `scripts/qgis_batch_build_overviews.py` w repozytorium.
4. Uruchom cały plik przyciskiem **Uruchom skrypt** albo skrótem
   ++ctrl+shift+e++.
5. W oknie systemowym wskaż katalog z rastrami. Skanowanie jest rekurencyjne.
6. Konsola wypisze PID niezależnego workera oraz ścieżki raportu i logu. Okno QGIS
   pozostaje responsywne. Aktualizowany po każdym rastrze raport JSON i CSV powstanie w
   `<wybrany katalog>\_geotile_overview_reports\`; osobne logi `gdaladdo` znajdziesz
   w katalogu z nazwą raportu i dopiskiem `_logs`.

![Konsola i edytor Pythona QGIS z otwartym skryptem do wsadowego generowania piramid](../assets/images/qgis_batch_build_overviews.png)
*Kolejno: otwarcie konsoli, pokazanie edytora, uruchomienie skryptu i wskazanie ścieżki.*

!!! info "Komenda `exec(compile(...))` jest normalna"

    QGIS wypisuje techniczną komendę uruchamiającą plik. Nie jest to błąd. Po niej
    powinno pojawić się okno wyboru katalogu. Jeśli skrypt został już załadowany, ale
    okno nie powstało, wpisz w konsoli `run_from_qgis_console()`.

Domyślny profil używa `AVERAGE`, kompresji `DEFLATE`, jednego pliku i jednego wątku
GDAL naraz oraz poziomów `2, 4, 8, ...` dobieranych do około 512–1024 pikseli
dłuższego boku. Cache pojedynczego procesu GDAL jest ograniczony do 512 MiB. Pliki
źródłowe są otwierane tylko do odczytu i kontrolowane przed oraz po operacji.

## Odporność na przerwanie i zawieszenie

QGIS służy tylko jako launcher. Każdy raster przetwarza osobny proces `gdaladdo`, więc
awaria sterownika lub jednego pliku nie blokuje interfejsu aplikacji. Worker:

- zapisuje raport atomowo po uruchomieniu i po każdym rastrze;
- rejestruje PID, komendę, kod wyjścia i czas ostatniej aktywności;
- kończy proces, który przez 15 minut nie wykazuje aktywności CPU, logu ani `.ovr`;
- przed budową tworzy znacznik `.ovr.geotile-building.json`;
- po przerwaniu przenosi niepewny sidecar do `.ovr.partial-<data>` zamiast go usuwać;
- sprawdza poziomy wszystkich pasm, odczytuje próbki danych i liczy checksumę
  najniższego poziomu, aby wykryć plik zawierający same nagłówki piramid.

Pozostawiony znacznik jest obsługiwany przy kolejnym uruchomieniu. Jeżeli zapisany w
nim proces nadal działa, drugi worker nie dotknie sidecara. Jeżeli proces już nie
istnieje, gotowy `.ovr` zostanie zweryfikowany, a niedokończony odizolowany i zbudowany
ponownie. Limit bezczynności można zmienić parametrem `inactivity_timeout`; wartość `0`
go wyłącza. Opcjonalny `max_file_seconds` ustawia niezależny limit całkowitego czasu.

## Przebieg kontrolowany

Jeśli chcesz najpierw sprawdzić decyzje bez tworzenia plików, załaduj skrypt do osobnej
przestrzeni i uruchom `dry_run`:

```python
from pathlib import Path

script_path = Path(r"C:\path\to\geotile-label\scripts\qgis_batch_build_overviews.py")
scope = {"__name__": "qgis_overview_batch"}
exec(compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec"), scope)

scope["run_from_qgis_console"](
    input_directory=r"C:\dane\sceny",
    max_files=2,
    dry_run=True,
)
```

Po sprawdzeniu raportu:

```python
scope["run_from_qgis_console"](
    input_directory=r"C:\dane\sceny",
    threads=1,
    gdal_cache_mb=512,
    inactivity_timeout=900,
    dry_run=False,
)
```

Dla katalogu zawierającego wyłącznie duże JP2 możesz wymusić sidecary mimo poziomów
natywnych:

```python
scope["run_from_qgis_console"](
    input_directory=r"C:\dane\sceny-jp2",
    force_external_for_native=True,
    dry_run=False,
)
```

Flaga dotyczy wszystkich rastrów z natywnymi lub wewnętrznymi poziomami w wybranym
katalogu. Nie używaj jej bez potrzeby dla mieszanej paczki zawierającej już poprawne
piramidy TIFF.

Ponowne uruchomienie jest bezpieczne: kompletne zewnętrzne piramidy są pomijane po
sprawdzeniu ich zawartości. Bez `force_external_for_native=True` skrypt pomija także
źródła z kompletnymi natywnymi poziomami. Wywołanie z konsoli zwraca PID workera, a nie
czeka na zakończenie całej paczki.

## Co robi GeoTile Label po dodaniu `.ovr`

Aplikacja zapisuje typ, poziomy i tani fingerprint piramidy. Dodanie, usunięcie albo
podmiana sidecara unieważnia zależne dane:

- miniaturę sceny;
- histogram i profil wyświetlania;
- cache kafli reprojektowanych;
- kontekst renderera i wersjonowany URL kafli.

Poprawny źródłowy `.ovr` ma pierwszeństwo przed redundantną piramidą projektową
`overview.vrt.ovr`. Nie trzeba go ręcznie „doczytywać” do projektu.

Jeżeli dużemu JP2 brakuje źródłowego `.ovr`, aplikacja oznacza przygotowanie jako
`pending`, buduje projektową piramidę w tle i po jej ukończeniu odświeża także profil
wyświetlania. Do tego czasu ciężkie odczyty JP2 są wykonywane w oddzielnej,
ograniczonej kolejce, dzięki czemu nie blokują katalogu projektów ani innych scen.

`.ovr` zapewnia szybki podgląd, ale nie zastępuje pełnej rozdzielczości 1×. Dla
kwalifikujących się dużych generic JP2 aplikacja może po pierwszym viewportcie
przygotować osobny, zwalidowany COG. Steruje tym przełącznik **Automatyczny COG pełnej
rozdzielczości** w kafelku **Źródła scen**; szczegóły opisują
[Produkty pochodne](produkty-pochodne.md#duze-generic-jp2-podglad-2-i-pena-rozdzielczosc-1).

## Pełny skrypt Python { #pelny-skrypt-python }

Rozwiń blok i użyj przycisku kopiowania w jego prawym górnym rogu. Kod w dokumentacji
jest wstawiany bezpośrednio z pliku używanego w repozytorium, więc nie stanowi osobnej,
rozjeżdżającej się kopii.

??? example "`qgis_batch_build_overviews.py` - kliknij, aby rozwinąć"

    ```python
    --8<-- "scripts/qgis_batch_build_overviews.py"
    ```

## Powiązane

- [Importuj paczki scen](importuj-paczki.md)
- [Produkty pochodne i widok roboczy](produkty-pochodne.md)
- [Statusy scen](../reference/statusy-scen.md)
- [Scena się nie otwiera](../troubleshooting/scena-sie-nie-otwiera.md)
