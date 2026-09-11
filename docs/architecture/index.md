# Architektura

Interaktywny przegląd architektury GeoTile Label: **potok pracy** - od danych źródłowych,
przez kanoniczny zbiór, po eksport - oraz **model danych** (encje i relacje). Klikaj
elementy, przełączaj widoki i korzystaj z wyszukiwarki; każdy element linkuje do
dokumentacji. Odnośniki do kodu są aktywne tylko wtedy, gdy dla instalacji
dokumentacji skonfigurowano adres repozytorium.

[Otwórz interaktywny explorer :material-open-in-new:](explorer.html){ .md-button .md-button--primary }

## Co zawiera

- **Pipeline** - siedem etapów z kanonicznym zbiorem w centrum; obejmuje trwałe zadania,
  import progresywny, piramidy, cache artefaktów i adaptacyjny trening; klik w etap pokazuje jego
  elementy, dokumentację i pliki źródłowe.
- **Data model** - warstwa kanoniczna wobec pochodnej, encje i relacje z licznościami;
  klik w encję pokazuje atrybuty i powiązania (z podświetleniem relacji).
- **Cross-linking** - z etapu potoku skoczysz do encji modelu i odwrotnie.
- Wyszukiwarka po obu diagramach, jasny/ciemny motyw, głębokie linki (`#p:…`, `#m:…`).

!!! note "Ten sam model, dwie postaci"

    Explorer odwzorowuje te same schematy, które w wersji statycznej (PDF/SVG) trafiają do
    publikacji. Interaktywna wersja służy do zrozumienia działania i nawigacji po kodzie.

!!! info "Linki do kodu"

    Pakowana dokumentacja offline nie zakłada publicznego repozytorium, dlatego
    niekonfigurowane odnośniki „Source” są nieaktywne zamiast prowadzić do adresu
    zastępczego. W wewnętrznym wydaniu ustawia się `REPO` i `REF` w szablonie
    `scripts/templates/explorer.template.html`, po czym generuje stronę poleceniem
    `python scripts/generate-architecture-explorer.py` - sam `explorer.html` jest
    plikiem generowanym i ręczne zmiany w nim przepadają przy kolejnej generacji.
