# Aplikacja nie startuje

Okno otwiera się, ale pozostaje puste albo pojawia się komunikat o niedziałającej
usłudze.

!!! info "Pierwsze uruchomienie po instalacji trwa dłużej"

    Rozpakowywane jest środowisko backendu. Zanim uznasz, że coś jest nie tak, daj
    aplikacji dokończyć - kolejne starty są szybkie.

## Kroki

Na ekranie startowym dostępne są trzy działania. Wykonuj je po kolei:

1. **Uruchom ponownie usługę** - najczęściej wystarcza.
2. **Otwórz folder logów** - sprawdź, co się wydarzyło.
3. **Eksportuj diagnostykę ZIP** - gdy problem się powtarza.

## Co jest w logach

| Plik | Zawartość |
| --- | --- |
| `app.log` | zdarzenia aplikacji |
| `backend.stdout.log` | wyjście usługi |
| `backend.stderr.log` | błędy usługi - **zacznij tutaj** |
| logi rozpakowywania środowiska | problemy pierwszego uruchomienia |

Logi znajdują się w `%APPDATA%\GeoTileLabel\logs`.

## Gdy restart nie pomaga

Użyj **Ustawienia → Diagnostyka → Wyczyść środowisko i pamięć podręczną**, a następnie
uruchom aplikację ponownie.

!!! info "Ta operacja nie usuwa projektów"

    Kasowane jest wyłącznie rozpakowane środowisko i pamięć podręczna kafelków.
    Odtworzą się przy następnym starcie. Projekty, adnotacje i datasety leżą gdzie
    indziej - patrz [Lokalizacje danych](../reference/lokalizacje-danych.md).

## Zgłoszenie problemu

Do zgłoszenia dołącz ZIP z **Eksportuj diagnostykę ZIP**. Zawiera wersje schematów,
stan tożsamości scen, sanityzowany raport ostatniego importu i statystyki pamięci
podręcznej.

!!! info "ZIP diagnostyczny nie zawiera Twoich danych"

    Nie ma w nim scen źródłowych, adnotacji ani pełnych geometrii - można go przekazać
    bez ujawniania materiału, nad którym pracujesz.
