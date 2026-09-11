# Produkty pochodne i widok roboczy

Nie każdy produkt dostawcy nadaje się do labelowania wprost. Ta strona wyjaśnia, co
aplikacja robi w takich przypadkach i dlaczego wybór jest później blokowany.

## Widok roboczy

**Widok roboczy** to postać sceny przygotowana do labelowania. Powstaje, gdy produkt
źródłowy wymaga złożenia albo przetworzenia:

- produkt podzielony na kafle (`R1C1`, `R1C2`, …) jest łączony w **wirtualny raster
  VRT**, bez kopiowania danych;
- Pleiades Neo `MS-FS RGB + PAN` i WorldView `MUL + PAN` wymagają **pansharpeningu**,
  który tworzy osobny plik roboczy w folderze projektu.

!!! info "Piksele źródłowe nie są zmieniane"

    VRT nie modyfikuje danych - to opis, jak złożyć istniejące pliki. Pansharpening
    tworzy **nowy, regenerowalny** plik w projekcie i również nie rusza oryginału.
    Dzięki temu aplikacja może zapisać pełne pochodzenie sceny.

Widoki robocze trafiają do `derived_scenes\` w folderze projektu. Można je skasować -
odtworzą się z tych samych danych źródłowych i tych samych parametrów.

### Pliki robocze scen

Ile miejsca zajmują te pliki, pokazuje rozwijany panel **Pliki robocze scen** na pulpicie
projektu. Panel liczy **wyłącznie** folder `derived_scenes\`, dzięki czemu jego suma
zgadza się z rozmiarem folderu, który otwiera przycisk **Otwórz folder plików roboczych**.
Poza sumą pozostają pliki źródłowe, adnotacje i katalogi kafelków - to **nie jest**
całkowity rozmiar projektu.

Statystyki liczą się dopiero po rozwinięciu panelu, żeby skan katalogu nie opóźniał
wejścia na pulpit ani nie konkurował o dysk z budową COG i piramid. Wynik jest zdjęciem z
chwili odczytu; jego godzinę widać pod listą, a **Odśwież** wykonuje nowy odczyt.

Panel dzieli miejsce na kategorie:

| Kategoria | Co obejmuje |
| --- | --- |
| COG pełnej rozdzielczości | `fullres/fullres.tif` - derywat 1× dla dużych JP2 |
| Piramidy projektowe | sidecary `.ovr` obok widoków |
| Materializowane widoki robocze | m.in. `rgb_pansharpened.cog.tif` |
| Widoki VRT | lekkie opisy złożenia, np. `overview.vrt` |
| Manifesty i profile | `processing_manifest.json`, `overview.profile.json`, logi |
| Pliki przejściowe budowy | ślady po przerwanym zadaniu: kandydat, RAW, `.partial` |

!!! info "Panel niczego nie usuwa"

    Wszystkie operacje panelu są odczytem. Aplikacja nie ma przycisku czyszczenia tych
    plików: są regenerowalne, ale odbudowa bywa kosztowna - COG pełnej rozdzielczości
    dużej sceny JP2 to kilkanaście minut i kilka GB. Jeśli zdecydujesz się coś usunąć,
    zrób to świadomie w Eksploratorze, po otwarciu folderu z panelu.

    Niezerowa pozycja **Pliki przejściowe budowy** oznacza, że jakieś zadanie zostało
    przerwane. Kolejna budowa tej sceny sprząta te pliki sama.

### Zanim ruszy przygotowanie

Przed każdym cięższym przygotowaniem aplikacja sprawdza **wolne miejsce**. Gdy go
brakuje, przygotowanie nie startuje - zamiast kończyć się w połowie zapisu z częściowym
plikiem na dysku. Szacowany rozmiar wyniku widać w podglądzie importu przy produktach,
które wymagają przygotowania.

Przygotowanie można **anulować w trakcie**. Po anulowaniu w folderze wariantu nie
zostaje ani plik częściowy, ani produkt - anulowany wynik nigdy nie jest publikowany.

### Co zapisuje pansharpening

Obok wyniku powstaje `processing_manifest.json` z opisem, z czego i jak scena
powstała: tożsamość każdego pliku wejściowego (rozmiar, czas modyfikacji, sygnatura),
wersja algorytmu i jego parametry, wersja GDAL oraz semantyka wyniku. Dwa uruchomienia
na tych samych plikach dają ten sam odcisk wejść i tę samą geometrię.

!!! info "Produkt pansharpened nie jest wielkością fizyczną"

    Pansharpening miesza pasma, więc kalibracja radiometryczna źródła przestaje
    obowiązywać. Manifest zapisuje to wprost: wynik jest opisany jako obraz do
    interpretacji wizualnej, a nie jako radiancja czy sigma0.

## Piramida wyświetlania nie jest widokiem roboczym

Piramida (`overview`) zmienia jedynie szybkość wyświetlania przy małych skalach. Nie
zmienia siatki pikseli sceny, produktu wybranego do adnotacji ani geometrii adnotacji.

Aplikacja rozróżnia:

- źródłowy sidecar, np. `scene.tif.ovr`;
- wewnętrzne poziomy GeoTIFF;
- natywną wielorozdzielczość JP2;
- regenerowalną piramidę projektową `overview.vrt.ovr`.

Zewnętrzny sidecar może być przygotowany w [QGIS](piramidy-qgis.md). Powinien być
przenoszony razem z rastrem, ale jego brak nie powoduje utraty adnotacji.
Duże JP2 mogą otrzymać piramidę projektową mimo obecności poziomów natywnych: jest to
optymalizacja dekodowania i nie tworzy nowej wersji sceny ani nie zmienia geometrii.
Pierwszy poziom projektowego `.ovr` jest kopiowany z odpowiedniego natywnego poziomu
JPEG 2000, a dopiero następne poziomy powstają z tego mniejszego rastra. Pozwala to
uniknąć ponownego dekodowania pełnej rozdzielczości. Poziom bazowy `2×`, `4×` albo `8×`
jest wybierany według dostępnej pamięci i liczby równoległych zadań; można go wymusić
zmienną `GEOTILE_JP2_OVERVIEW_BASE_FACTOR`.

### Duże generic JP2: podgląd 2× i pełna rozdzielczość 1×

Dla problematycznych, dużych scen generic JP2 przygotowanie jest celowo dwuetapowe:

1. import tworzy `overview.vrt` i samodzielny GeoTIFF `overview.vrt.ovr`; ten plik daje
   szybki podgląd do poziomu 2× i pozostaje trwałym fallbackiem;
2. po załadowaniu pierwszego viewportu aplikacja może uruchomić trwałe zadanie budowy
   pełnorozdzielczego COG. Do czasu jego ukończenia źródłowy JP2 nie jest używany do
   interaktywnego renderowania poziomu 1×.

COG powstaje najpierw jako `fullres.candidate.tif`. Aplikacja sprawdza wymiary, typ i
semantykę pasm, georeferencję, `nodata`, alpha/maskę, overviewy oraz piksele na
krawędziach i granicach pasów. Dopiero poprawny kandydat jest atomowo publikowany jako
`fullres.tif`. Samo istnienie pliku TIFF bez aktywnego rekordu publikacji nie wystarcza,
aby renderer go użył.

Nieudana budowa nie ponawia się automatycznie przy kolejnym wejściu w scenę. Podgląd
`.ovr` pozostaje dostępny, a jawne ponowienie lub anulowanie jest dostępne w widoku
labelowania. Podmiana źródła, relink, zmiana wariantu albo profilu unieważnia COG, ale
nie usuwa źródła, projektu ani adnotacji.

Budowa jednopasmowa może użyć pełnego dekodu tylko na komputerze z dużym zapasem RAM;
aplikacja zachowuje co najmniej 8 GiB dla systemu i pilnuje procesu limitem 16 GiB.
Sceny wielopasmowe używają domyślnie bezpieczniejszego dekodu pasowego z limitem około
4,5 GiB. Przekroczenie pamięci ponawia tylko bieżący, zmniejszony pas - nie kasuje
wcześniej zapisanych wierszy. Te limity wpływają na czas przygotowania COG, ale nie na
dostępność podglądu `.ovr`.

#### Włączanie i wyłączanie automatycznego COG

Na pulpicie projektu, w kafelku **Źródła scen**, znajduje się przełącznik
**Automatyczny COG pełnej rozdzielczości**. Ustawienie jest zapisywane osobno dla
każdego projektu i domyślnie ma wartość **OFF** - budowa COG kosztuje dziesiątki
minut i kilka GB na scenę, więc nie startuje bez Twojej decyzji.

- **ON** - po załadowaniu pierwszego kompletnego viewportu `.ovr` aplikacja może
  automatycznie zakolejkować COG 1× dla kwalifikującej się sceny generic JP2;
- **OFF** - scena pozostaje na podglądzie `.ovr`, a nowe zadanie COG nie jest
  uruchamiane automatycznie. Poziom 1× nie będzie dostępny, dopóki nie powstanie
  poprawny COG.

Zmiana na **OFF** nie anuluje zadania, które już trwa, i nie wyłącza wcześniej
zwalidowanego COG. Aktywne zadanie można anulować w panelu
[Zadania w tle](../reference/zadania-w-tle.md), otwieranym z lewego paska aplikacji.

Tryb importu i automatyczny COG są niezależne: tryb importu steruje przygotowaniem
`overview.vrt` + `overview.vrt.ovr`, natomiast COG może ruszyć dopiero po otwarciu
sceny. Administrator może nadrzędnie wyłączyć automatyczny start we wszystkich
projektach przez `GEOTILE_AUTO_FULLRES_COG_V2=0`. Przełącznik projektu nie omija tego
awaryjnego ograniczenia.

## Wybór pasm RGB (lokalne sceny wielopasmowe)

Paczki dostawców mają wybór pasm RGB już na etapie importu. Dla **lokalnych scen
wielopasmowych** (plik w folderze z więcej niż 3 pasmami) w oknie labelowania pojawia się
**wybór pasm** - trzy listy `R`/`G`/`B` - gdy scena ma ponad 3 pasma. Po zatwierdzeniu
aplikacja buduje **VRT selekcji pasm** (widok roboczy, bez kopiowania danych): wybrane pasma
stają się kanałami 1–3, więc widzą je jednakowo podgląd i generacja datasetu.

!!! info "Bez wyboru używane są pierwsze trzy pasma"

    Jeśli nie wskażesz pasm, scena renderuje pasma 1–3. Dla materiału, w którym `RGB` nie
    są pierwszymi pasmami (np. multispektralny z bliską podczerwienią na początku), warto
    wybrać pasma jawnie.

## Wariant sceny

Każdy widok roboczy ma **wariant** - identyfikator konkretnego wyboru produktu i
parametrów przygotowania. Wariant odpowiada na pytanie: „na jakiej dokładnie siatce
pikseli powstały te adnotacje".

## Blokada po rozpoczęciu pracy

Gdy scena ma pierwszą adnotację, zatwierdzoną komórkę przeglądu albo została użyta w
datasecie, **aplikacja blokuje zmianę wariantu**.

!!! warning "To zabezpieczenie, nie ograniczenie"

    Ramki są zapisane we współrzędnych pikselowych sceny. Podmiana rastra na inaczej
    przygotowany oznacza, że te same liczby wskazują inne miejsce w terenie - bez
    żadnego widocznego objawu. Adnotacje wyglądałyby poprawnie, a opisywały co innego.

Potrzebujesz innego produktu albo innego przygotowania tej samej akwizycji? **Utwórz
osobną scenę**, zamiast podmieniać istniejącą. Obie mogą współistnieć w projekcie.

Regeneracja brakującego pliku dla **tego samego** wariantu jest dozwolona - to nie
jest zmiana siatki, tylko odtworzenie tego, co zostało skasowane.

## Co oznaczają wartości pikseli

Aplikacja **nie przelicza radiometrii źródła**. Manifest sceny zapisuje natomiast, czym
te wartości są według metadanych dostawcy - na przykład nieskalibrowana amplituda
z współczynnikiem kalibracji obok (ICEYE) albo skalibrowane `sigma0` (Capella).

!!! warning "Dataset nie zmiesza niejawnie różnych kalibracji"

    Gdy do jednego datasetu trafiłyby sceny o sprzecznej kalibracji - skalibrowane obok
    nieskalibrowanych - generowanie zostaje **zatrzymane** z listą scen. Ta sama liczba
    znaczyłaby w nich co innego. Zmieszanie jest nadal możliwe, ale wyłącznie jako
    świadoma decyzja, która zapisuje się w manifeście datasetu.

    Sceny, dla których dostawca nie deklaruje radiometrii, mają stan „nieznana" i nie
    blokują generowania: brak informacji to nie to samo co informacja o niezgodności.

Rozciągnięcie kontrastu widoczne w podglądzie **nie wpływa na dane treningowe**. Kafle
datasetu powstają z profilu przetwarzania, a nie z ustawień wyświetlania sceny.

## Reprojekcja nie jest wykonywana automatycznie

Adnotacje nie są przenoszone między różnymi siatkami roboczymi. Jeśli w projekcie
zbiorczym pojawi się paczka z tej samej sceny źródłowej, ale przygotowanej inaczej,
import to zgłosi zamiast dopasować na siłę.

!!! tip "Uzgodnijcie przygotowanie scen w zespole"

    Najprostszy sposób uniknięcia tego problemu to wspólne ustalenie, kto i jak
    przygotowuje sceny, zanim zacznie się labelowanie. Rozjazd wychodzi dopiero przy
    imporcie, czyli po wykonaniu pracy.

## Powiązane

- [Domyślne produkty dostawców](../reference/produkty-dostawcow.md) - co zostanie
  wybrane z paczki
- [Statusy scen](../reference/statusy-scen.md) - kiedy scena wymaga przygotowania
- [Lokalizacje danych](../reference/lokalizacje-danych.md) - gdzie leżą widoki robocze
