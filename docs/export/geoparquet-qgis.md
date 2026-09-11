# Kontrola GeoParquet w QGIS

**Cel.** Obejrzeć adnotacje w narzędziu GIS - na tle innych warstw i z pełną tabelą
atrybutów.

**Kiedy.** Przy kontroli jakości pracy zespołu, gdy przeglądanie scena po scenie jest
niepraktyczne.

## Kroki

1. Wyeksportuj źródłowe adnotacje GeoParquet z panelu managera.
2. W QGIS wybierz **Warstwa → Dodaj warstwę → Dodaj warstwę wektorową**.
3. Wskaż `annotations_wgs84.geoparquet`.
4. Kontroluj geometrię, klasę, scenę i autora w tabeli atrybutów.

Dla danych w układzie natywnym użyj `annotations_native.geoparquet` - QGIS odczyta
układ odniesienia zapisany w pliku.

## Który plik wybrać

| Plik | Kiedy |
| --- | --- |
| `annotations_wgs84.geoparquet` | zestawienie wielu scen, praca na tle map |
| `annotations_native.geoparquet` | pomiary w układzie metrycznym sceny |

!!! warning "Adnotacje NO GEO nie trafiają do warstwy WGS84"

    Sceny bez georeferencji mają adnotacje wyłącznie w pikselach, więc nie da się ich
    umieścić na mapie. Informacja o pominięciu znajduje się w podsumowaniu eksportu -
    warto ją przeczytać, zanim uznasz, że obiektów brakuje.

## Co daje ten widok

Kilka rzeczy jest tu widocznych lepiej niż w aplikacji:

- **rozkład przestrzenny pracy** - które obszary są pokryte, a które puste;
- **spójność między analitykami** - filtr po autorze pokazuje różnice w interpretacji
  klas;
- **duplikaty na styku scen** - ten sam obiekt oznaczony w dwóch nachodzących scenach;
- **obiekty odstające** - nietypowe rozmiary rzucają się w oczy przy sortowaniu tabeli.

!!! tip "Filtr po autorze przed rozmową z zespołem"

    Zestawienie adnotacji jednego analityka obok pozostałych szybciej pokazuje rozjazd
    w interpretacji klas niż przeglądanie scen po kolei.

## To kontrola, nie edycja

Zmiany wprowadzone w QGIS **nie wracają** do projektu. GeoParquet jest produktem
eksportu - poprawki nanosi się w aplikacji, na scenach.
