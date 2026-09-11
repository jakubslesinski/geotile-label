# Katalog kafelków

Katalog kafelków to **zbiór kandydatów** na obrazy treningowe: geometria podziału,
powiązania z adnotacjami i stan przeglądu. Powstaje z siatki przeglądu i jest wejściem
do budowania datasetu.

!!! info "Katalog nie zawiera obrazów"

    Utworzenie katalogu **nie tworzy tysięcy plików**. Zapisywane są identyfikatory,
    geometria i relacje. Podgląd pojedynczego kafelka generowany jest na żądanie, a
    obrazy treningowe powstają dopiero przy [budowaniu
    datasetu](zbuduj-dataset.md) - i tylko dla kafelków wybranych do konkretnej wersji.

## Co zawiera

- identyfikatory i geometrię kafelków;
- relacje do adnotacji źródłowych;
- klasy, autora i źródło adnotacji;
- dla scen `GEO` - geometrię w układzie natywnym i w `WGS84`.

Jest **surowy i niepodzielony** - nie ma w nim podziału na zbiory uczący, walidacyjny
i testowy. Podział powstaje przy budowaniu konkretnej wersji datasetu.

## Stany komórek

| Stan | Znaczenie | Czy trafia do datasetu |
| --- | --- | --- |
| **Niesprawdzone** | brak potwierdzonego przeglądu | nie |
| **Sprawdzone** | fragment świadomie obejrzany | tak - z adnotacjami albo jako pusty przykład |
| **Wykluczone** | wyjątek techniczny | nie |

!!! warning "Puste przykłady biorą się wyłącznie ze sprawdzonych komórek"

    Kafelek bez adnotacji trafia do datasetu jako pusty przykład **tylko wtedy**, gdy
    ktoś potwierdził, że go obejrzał. Obszary niesprawdzone nie są losowane - bo brak
    adnotacji mógłby oznaczać po prostu, że nikt tam nie zaglądał.

Wykluczenie jest wyjątkiem technicznym - czarne tło, błędne dane, artefakty - a nie
zwykłym etapem postępu pracy.

## Kiedy trzeba zbudować katalog na nowo

**Nie wymagają przebudowy:** zmiana strategii podziału, ziarna losowania albo profilu
preprocessingu. Te parametry działają na gotowym katalogu, więc kolejne wersje
datasetu mogą z niego korzystać bez powtarzania pracy.

**Wymagają nowej wersji katalogu:** zmiana geometrii adnotacji, rozmiaru kafelka,
nakładania albo progu propagacji.

!!! tip "Eksperymentuj podziałem, nie katalogiem"

    Porównywanie strategii podziału i ziaren jest tanie - to ten sam katalog. Zmiana
    rozmiaru kafelka jest droga, bo unieważnia dotychczasowe powiązania.

## Cache kafli

Katalog kafelków jest metadata-only: zapisuje geometrię i stan przeglądu, a nie trwałe
obrazy kafli. Podglądy siatki renderują się na żądanie i nie wymagają obsługi.

Aplikacja nie udostępnia w interfejsie żadnego przycisku czyszczenia cache. Miejsce
zajęte przez pliki przygotowane do wyświetlania scen pokazuje panel
**[Pliki robocze scen](../input-data/produkty-pochodne.md)** na
pulpicie projektu - również wyłącznie do odczytu.

## Powiązane

- [Siatka przeglądu](../annotation/siatka-przegladu.md) - skąd biorą się stany komórek
- [Zbuduj dataset](zbuduj-dataset.md) - co dzieje się dalej
