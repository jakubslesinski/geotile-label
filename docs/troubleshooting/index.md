# Diagnostyka

Punktem wyjścia przy każdym problemie jest **Ustawienia → Diagnostyka**: stan usługi,
wersje komponentów, logi i eksport paczki diagnostycznej.

## Znajdź swój problem

<div class="grid cards" markdown>

-   :material-application-off: **Aplikacja nie startuje**

    ---

    Puste okno albo komunikat o usłudze.

    [Rozwiąż](aplikacja-nie-startuje.md)

-   :material-image-off: **Scena się nie otwiera**

    ---

    Statusy scen i problemy z odczytem.

    [Rozwiąż](scena-sie-nie-otwiera.md)

-   :material-robot-off: **Predykcja jest niedostępna**

    ---

    Wygaszony panel, brak bibliotek.

    [Rozwiąż](predykcja-niedostepna.md)

-   :material-package-variant-closed: **Import paczki nie przechodzi**

    ---

    Odrzucone sceny, brakujące klasy, konflikt paczek.

    [Rozwiąż](import-paczek.md)

</div>

## Dostępne narzędzia

| Narzędzie | Do czego |
| --- | --- |
| **Uruchom ponownie usługę** | pierwszy krok przy problemach z backendem |
| **Otwórz folder logów** | podgląd `app.log` i logów usługi |
| **Eksportuj diagnostykę ZIP** | materiał do zgłoszenia |
| **Wyczyść środowisko i pamięć podręczną** | odtworzenie środowiska bez utraty projektów |

!!! info "Czyszczenie środowiska nie usuwa projektów"

    Kasowane jest wyłącznie rozpakowane środowisko backendu i pamięć podręczna
    kafelków. Projekty, adnotacje i datasety leżą osobno - patrz
    [Lokalizacje danych](../reference/lokalizacje-danych.md).

## Logi

```text
%APPDATA%\GeoTileLabel\logs
```

Przy problemach z uruchomieniem zacznij od `backend.stderr.log`.

## Zgłaszanie problemu

Dołącz ZIP z **Eksportuj diagnostykę ZIP**. Zawiera wersje schematów, stan tożsamości
scen, sanityzowany raport ostatniego importu i statystyki pamięci podręcznej -
**bez scen źródłowych, adnotacji i pełnych geometrii**.

!!! danger "Logi sprawdź przed wysłaniem poza organizację"

    ZIP diagnostyczny jest sanityzowany, ale surowe pliki logów mogą zawierać nazwy
    projektów, ścieżki użytkownika i informacje o lokalizacji danych.

Adres, pod który wysłać zgłoszenie, oraz to, co dołączyć do opisu, podaje
[Kontakt](../reference/kontakt.md).
