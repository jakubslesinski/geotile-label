# Zadania w tle

Dłuższe operacje są uruchamiane jako trwałe zadania. Możesz przejść do innego widoku,
a ich postęp pozostanie dostępny w panelu **Zadania w tle**.

Dotyczy to między innymi importu i przygotowania scen, budowy piramid i
pełnorozdzielczych COG dla JP2, datasetów, analizy embeddingów, inferencji całej
sceny, eksportu, backupu i czyszczenia artefaktów.

## Otwórz panel

Przycisk **Zadania w tle** znajduje się na **lewym pasku aplikacji, bezpośrednio nad
Pomocą**. Pokazuje liczbę aktywnych operacji, a panel po kliknięciu wysuwa się z lewej
strony. Każde zadanie ma:

- nazwę i typ operacji;
- status oraz aktualny etap;
- pasek postępu, jeśli operacja zna liczbę elementów;
- czas i komunikat ostatniego zdarzenia;
- dostępne akcje: **Anuluj**, **Ponów** albo **Pobierz**.

## Statusy

| Status | Znaczenie |
| --- | --- |
| **w kolejce** | zadanie czeka na zasób lub wolnego workera |
| **w toku** | operacja jest wykonywana |
| **ukończone** | wynik lub artefakt jest gotowy |
| **anulowane** | użytkownik zatrzymał operację w bezpiecznym punkcie |
| **niepowodzenie** | zadanie zapisało błąd i może wymagać ponowienia |

Anulowanie nie zawsze jest natychmiastowe. Raster, batch inferencji albo zapis archiwum
musi dojść do bezpiecznego punktu, aby nie pozostawić częściowo opublikowanego wyniku.
Wyłączenie przełącznika automatycznego COG w **Źródłach scen** zapobiega nowym
automatycznym zadaniom, ale nie anuluje już uruchomionej operacji - użyj tutaj
**Anuluj**.

## Ponawianie i wznawianie

**Ponów** tworzy bezpieczną kontynuację lub nową próbę z tą samą konfiguracją - zależnie
od typu zadania. Etapy zakończone i opublikowane atomowo mogą zostać wykorzystane
ponownie. Nie edytuj ręcznie plików `job.json` ani `state.json`.

Import scen i analiza datasetu zapisują checkpointy. Budowanie datasetu i eksport
publikują wynik dopiero po ukończeniu, dlatego katalog tymczasowy nie jest gotową
wersją datasetu.

## Artefakty do pobrania

Backup projektu i eksport datasetu kończą się artefaktem ZIP. Użyj **Pobierz** przy
ukończonym zadaniu i dopiero wtedy wskaż miejsce docelowe. Zamknięcie panelu nie usuwa
artefaktu.

## Po restarcie aplikacji

Stan zadań jest zapisany na dysku. Operacja, której procesu już nie ma, nie pozostaje
bez końca jako „w toku”: aplikacja oznacza ją zgodnie z możliwością wznowienia danego
typu. Szczegóły i diagnostyka znajdują się w folderze zadania opisanym w
[Lokalizacjach danych](lokalizacje-danych.md).
