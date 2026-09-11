# Słownik pojęć

Pojęcia używane w aplikacji i w tej dokumentacji. W nawiasach podano odpowiednik
angielski, jeśli spotkasz go w metadanych, formatach ML albo materiałach dostawcy.

## Dane źródłowe i sceny

Scena
:   Logiczny produkt obrazowy jednej akwizycji, a **nie pojedynczy plik**. Może
    składać się z wielu kafli i mieć własne metadane dostawcy.

Paczka dostawcy *(package)*
:   Folder z produktami jednej sceny w postaci, w jakiej dostarczył go dostawca.
    Aplikacja czyta go tylko do odczytu i nie zmienia.

Produkt roboczy
:   Plik wybrany z paczki do labelowania - na przykład GRD dla ICEYE albo GEC dla
    Capelli. Zestawienie w [Domyślne produkty dostawców](produkty-dostawcow.md).

Widok roboczy *(working view)*
:   Postać sceny przygotowana do labelowania, gdy produkt nie nadaje się do tego
    wprost - na przykład połączenie części w VRT albo pansharpening. Nie zmienia
    pikseli źródłowych.

Wariant *(variant)*
:   Identyfikator konkretnego wyboru produktu i parametrów przygotowania. Po
    rozpoczęciu labelowania wariant sceny jest zablokowany.

Źródło *(source)*
:   Folder z paczkami jednego dostawcy podłączony do projektu. Projekt może mieć
    kilka źródeł, o ile dotyczą tej samej modalności.

Relinkowanie
:   Ponowne wskazanie folderu źródła po przeniesieniu danych, bez utraty scen i
    adnotacji. Patrz [Relinkuj źródło](../projects/relinkuj-zrodlo.md).

Modalność *(modality)*
:   `EO` - zobrazowania optyczne, `SAR` - radarowe. Ustawiana w profilu projektu i
    wspólna dla wszystkich jego źródeł.

GEO / NO GEO
:   Scena z georeferencją albo bez niej. Sceny `NO GEO` mają adnotacje wyłącznie w
    pikselach i nie trafiają do warstw geograficznych przy eksporcie.

GSD *(ground sample distance)*
:   Wielkość terenowa jednego piksela, zwykle w metrach.

CRS *(coordinate reference system)*
:   Układ współrzędnych sceny, na przykład `EPSG:4326` (stopnie) albo strefa UTM
    (metry).

Nodata
:   Wartość oznaczająca brak danych. Produkty geokodowane często mają szeroką ramkę
    nodata, gdy footprint akwizycji jest obrócony względem układu współrzędnych.

VRT
:   Wirtualny raster łączący wiele plików bez kopiowania danych.

COG *(cloud optimized GeoTIFF)*
:   GeoTIFF o układzie przyspieszającym odczyt fragmentów.

## Adnotacje

Adnotacja kanoniczna
:   Adnotacja zapisana na pełnej scenie - **źródło prawdy**. Etykiety w kafelkach i
    plikach eksportu są z niej wyliczane.

Ramka osiowa *(bounding box, bbox)*
:   Prostokąt o bokach równoległych do osi obrazu.

Ramka zorientowana *(oriented bounding box, OBB)*
:   Prostokąt obrócony do kształtu obiektu, z zachowanym kierunkiem przodu.

Propozycja AI
:   Wynik predykcji, SAM-a albo dopasowania wzorca, który **nie jest jeszcze
    adnotacją**. Staje się nią dopiero po akceptacji.

Właściciel adnotacji
:   Osoba, do której adnotacja należy w projekcie zbiorczym. Zmienia się przy
    przekazaniu pracy - w odróżnieniu od autora, który pozostaje na stałe.

Atrybuty obliczone
:   Wymiary, pole, proporcje i azymut wyliczane automatycznie z geometrii i
    georeferencji sceny. Liczone w metrach niezależnie od układu współrzędnych.

## Siatka i datasety

Siatka przeglądu
:   Regularny podział sceny służący do kontroli, co zostało już sprawdzone. Sama nie
    tworzy obrazów treningowych. Patrz
    [Siatka przeglądu](../annotation/siatka-przegladu.md).

Komórka sprawdzona *(reviewed)*
:   Fragment sceny świadomie przejrzany - również wtedy, gdy nie znaleziono w nim
    żadnego obiektu.

Komórka wykluczona *(excluded)*
:   Fragment celowo pominięty, na przykład chmury, brak danych albo obszar poza
    zakresem zadania.

Katalog kafelków *(tile catalog)*
:   Zapisana geometria siatki wraz z powiązaniami adnotacji i stanem przeglądu.
    **Nie zawiera obrazów** - podglądy powstają na żądanie.

Kafelek *(tile)*
:   Wycinek sceny o zadanym rozmiarze, będący pojedynczym obrazem treningowym.

Overlap
:   Zachodzenie sąsiednich kafelków na siebie. Zmniejsza ryzyko przecięcia obiektu
    na granicy.

Dataset run
:   Niezmienna wersja datasetu: konkretna konfiguracja, preprocessing, podział i
    wygenerowane artefakty. Można ją odtworzyć i porównać z inną.

Publikacja datasetu
:   Oznaczenie wersji jako produkcyjnej, dzięki czemu staje się dostępna do treningu.

Split *(train / val / test)*
:   Podział na zbiór uczący, walidacyjny i testowy.

Spatial leakage
:   Przeciek przestrzenny - sytuacja, w której ten sam teren trafia do zbioru
    uczącego i testowego, przez co wynik jest zawyżony.

Pusty przykład *(negative)*
:   Kafelek bez obiektów, świadomie włączony do datasetu, żeby model uczył się także
    tego, czego nie oznaczać.

## Praca zespołowa

Rola projektu
:   Ustawienie decydujące, czy projekt służy do labelowania, czy do scalania i
    recenzji pracy zespołu.

Zakres paczki *(scope)*
:   Para „scena × właściciel", w której import podmienia adnotacje hurtowo. Dzięki
    temu propagują się także poprawki i usunięcia.

Łańcuch zastępowania *(supersedes)*
:   Wskazanie, którą wcześniejszą paczkę zastępuje nowa. Bez niego import kolejnej
    paczki na tę samą scenę jest odrzucany.

Runda recenzji
:   Numer obiegu poprawek. Werdykt z wcześniejszej rundy staje się nieaktualny po
    odesłaniu poprawionej pracy.

Werdykt
:   Ocena sceny wystawiona przez managera wraz z komentarzem. Nie niesie geometrii.

## Trening

Wagi bazowe
:   Punkt startowy treningu - model nauczony wcześniej na dużym zbiorze ogólnym.

Architektura
:   Rodzina i rozmiar sieci, na przykład YOLO11m-OBB. Patrz
    [Architektury bazowe](architektury.md).

Trening od zera *(from scratch)*
:   Nauka od losowej inicjalizacji, bez wag bazowych. Wymaga znacznie większego
    datasetu i dłuższego treningu.

Przebieg treningu *(training run)*
:   Pojedyncze uruchomienie treningu wraz z konfiguracją, metrykami i punktem
    kontrolnym modelu.

Epoka *(epoch)*
:   Jedno przejście przez cały zbiór uczący.

Preflight
:   Kontrola przed startem treningu: urządzenie, pamięć karty, miejsce na dysku i
    spójność danych. Patrz [Preflight](../training/preflight.md).

Zbiór walidacyjny
:   Dane używane w trakcie treningu do porównywania konfiguracji i wyboru modelu.

Zbiór testowy
:   Dane odłożone do **jednorazowej** oceny końcowej. Wielokrotne wybieranie modelu
    po jego wynikach zamienia go w drugi zbiór walidacyjny i zawyża ocenę.

mAP50, mAP50-95
:   Miary jakości detekcji. `mAP50` jest łagodniejsza, `mAP50-95` uśrednia po wielu
    progach dopasowania i jest bardziej wymagająca.

Rejestr modeli
:   Lista modeli zarejestrowanych w projekcie wraz z historią promocji.

Promocja modelu
:   Wskazanie modelu używanego przez projekt do predykcji.

Rodowód *(lineage)*
:   Powiązanie modelu z datasetem, kafelkami i autorstwem adnotacji, na których
    powstał.

## Formaty eksportu

YOLO
:   Etykiety tekstowe ze znormalizowanymi ramkami osiowymi.

YOLO OBB
:   Wariant dla ramek zorientowanych: cztery wierzchołki zamiast prostokąta.

COCO
:   Pojedynczy plik JSON z obrazami, kategoriami i adnotacjami.

Pascal VOC
:   Jeden plik XML na obraz.

GeoParquet
:   Format tabelaryczny z geometrią, używany do kontroli adnotacji w narzędziach GIS.
    Patrz [GeoParquet w QGIS](../export/geoparquet-qgis.md).
