# Dane wejściowe

GeoTile Label pracuje na paczkach produktów dostawców.
Ten rozdział opisuje, jak przygotować dane i co się z nimi dzieje po imporcie.

## Scena to produkt, nie plik

Najważniejsze pojęcie tego rozdziału. **Scena jest logicznym produktem obrazowym
jednej akwizycji**, a nie pojedynczym plikiem znalezionym w folderze. Może składać się
z kilku kafli i mieć własne metadane dostawcy.

Dlatego aplikacja rozpoznaje **paczki**, a nie luźne rastry - i dlatego nie należy
rozpakowywać paczek do wspólnego folderu ani zmieniać nazw plików.

## Od czego zacząć

<div class="grid cards" markdown>

-   :material-folder-search: **Mam paczki dostawcy**

    ---

    Podłącz folder, przeskanuj go i dodaj sceny do projektu.

    [Importuj paczki scen](importuj-paczki.md)

-   :material-layers-triple: **Scena wymaga przygotowania**

    ---

    Kiedy powstaje widok roboczy i dlaczego potem jest zablokowany.

    [Produkty pochodne](produkty-pochodne.md)

-   :material-image-filter-center-focus-strong: **Mam bardzo duże rastry**

    ---

    Przygotuj wsadowo zewnętrzne piramidy `.ovr` w QGIS i wykorzystaj je bez
    osobnego importu.

    [Piramidy i sidecary QGIS](piramidy-qgis.md)

-   :material-airplane: **Mam lotnicze sceny NITF**

    ---

    Etykietuj w geometrii sensora, z zachowaniem położenia przez TPS.

    [Lotnicze sceny NITF](nitf-lotnicze.md)

</div>

## Obsługiwane formaty i dostawcy

| | Zakres |
| --- | --- |
| Rastry | TIFF, GeoTIFF, JPEG 2000 (`.jp2`) |
| Lotnicze NITF | `.ntf` / `.nitf`, panchromatyczne mono `UInt16`, geometria sensora |
| Metadane | JSON, XML, IMD, TIL, RPB, TFW |
| Dostawcy SAR | ICEYE, Capella, UMBRA |
| Dostawcy EO | Pleiades Neo, WorldView, BlackSky |
| Pozostałe | źródło Generic dla prostych katalogów obrazów |

Który produkt zostanie wybrany z paczki, opisuje
[tabela domyślnych produktów](../reference/produkty-dostawcow.md).

## Sceny GEO i NO GEO

Sceny z georeferencją pozwalają wyliczać wymiary w metrach, pokazywać podkład mapowy
i eksportować geometrię geograficzną. Sceny bez georeferencji mają adnotacje wyłącznie
w pikselach i **nie trafiają do warstw geograficznych** przy eksporcie - informacja o
ich pominięciu znajduje się w podsumowaniu.

## Dyski zewnętrzne

Sceny mogą leżeć na dysku zewnętrznym. Po jego odłączeniu sceny dostają status **brak
źródła**, ale **nie znikają z projektu** i nie tracą adnotacji. Po ponownym podłączeniu
- albo po przeniesieniu danych w inne miejsce - użyj
[Relinkuj źródło](../projects/relinkuj-zrodlo.md).

!!! warning "Nie porządkuj paczki po utworzeniu projektu"

    Zmiana plików wewnątrz zaimportowanej paczki zostaje wykryta jako **źródło
    zmienione**, a aplikacja blokuje wtedy automatyczne użycie istniejących adnotacji.
    To zabezpieczenie: ramki są zapisane w pikselach, więc podmiana rastra mogłaby
    przesunąć je względem terenu. Patrz
    [Statusy scen](../reference/statusy-scen.md).
