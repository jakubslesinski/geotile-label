# Utwórz projekt

**Cel.** Założyć projekt i ustawić profil decydujący o sposobie pracy.

**Kiedy.** Na początku nowego zadania labelowania.

Krok po kroku prowadzi [Szybki start analityka](../getting-started/szybki-start-analityka.md).
Ta strona opisuje **znaczenie poszczególnych pól** i konsekwencje wyborów.

## Nazwa i lokalizacja

**Nazwa** identyfikuje projekt na liście.

**Folder nadrzędny** jest opcjonalny. Bez wskazania projekt trafia do lokalizacji
domyślnej w danych aplikacji. Wskazanie własnego folderu - na dysku roboczym albo
współdzielonym - ułatwia backup i pracę zespołową.

!!! info "Lokalizacji nie trzeba pamiętać"

    Aplikacja zapisuje rzeczywistą ścieżkę każdego projektu we własnym spisie, więc
    projekty spoza lokalizacji domyślnej też pojawiają się na liście. Patrz
    [Lokalizacje danych](../reference/lokalizacje-danych.md).

## Typ projektu

| Typ | Kiedy |
| --- | --- |
| **Sceny lokalne** | masz paczki produktów dostawcy |
| **Lotnicze sceny NITF** | masz zobrazowania w geometrii sensora |

Wybór typu decyduje o reszcie formularza.

## Profil projektu

### Modalność

`EO` - zobrazowania optyczne, `SAR` - radarowe. Wspólna dla wszystkich źródeł
projektu, dlatego nie da się w jednym projekcie mieszać materiału optycznego z
radarowym.

### Georeferencja

`GEO` albo `NO GEO`. Sceny bez georeferencji mają adnotacje wyłącznie w pikselach i nie
trafiają do warstw geograficznych przy eksporcie.

### Tryb adnotacji

**Ramka** albo **Ramka zorientowana**.

!!! warning "To decyzja na cały projekt"

    Tryb adnotacji przekłada się na dwie rzeczy, których nie widać przy zakładaniu
    projektu:

    - **jakie architektury będzie można trenować** - ramki osiowe wymagają modeli
      detekcji, zorientowane modeli OBB, a lista jest filtrowana automatycznie;
    - **kształt eksportów** - YOLO OBB zapisuje cztery wierzchołki zamiast prostokąta.

    Ramki zorientowane mają sens tam, gdzie liczy się kierunek obiektu - statki,
    samoloty, pojazdy na parkingach. Dla obiektów o nieokreślonej orientacji zwykle
    wystarczą ramki osiowe.

### E-mail autora

Trafia do adnotacji jako informacja o autorstwie i przechodzi dalej - aż do
[rodowodu modelu](../training/rejestr-modeli.md). W projekcie zbiorczym po nim
rozpoznaje się, czyja praca jest importowana.

### Profil preprocessingu i strategia podziału

Wartości domyślne dla późniejszego budowania datasetu. Można je zmienić przy każdym
dataset runie, więc nie są decyzją nieodwracalną.

## Siatka kafli

Przy tworzeniu projektu (źródła lokalne) ustawiasz wstępnie **rozmiar kafla** i **nakładanie** -
to ważny parametr datasetu, więc definiuje się go już na starcie. Domyślnie 640 px, bez nakładania.

Te wartości można zmienić później w [Siatce przeglądu](../annotation/siatka-przegladu.md); zakładka
**Budowanie datasetu** je dziedziczy (tam tylko do wglądu). Obowiązuje zasada **co przeglądasz = na
czym trenujesz**, więc zmiana rozmiaru kafla po rozpoczęciu przeglądu wymaga potwierdzenia (zeruje
postęp przeglądu).

## Czego w profilu nie ma

!!! info "Dostawcy i sensora nie wybierasz"

    Wynikają ze źródeł scen i metadanych wykrytych podczas skanowania. W formularzu
    projektu ich nie ma - dostawcę wskazujesz przy dodawaniu źródła.

## Następny krok

[Zaimportuj klasy](klasy.md), a potem
[podłącz paczki scen](../input-data/importuj-paczki.md).
