# Lotnicze sceny NITF (geometria sensora)

Typ projektu do **ukośnych zobrazowań lotniczych w formacie NITF** (panchromatyczne,
mono, `UInt16`). Adnotacje powstają w **natywnej geometrii sensora** - na surowych
pikselach obrazu, a nie na podkładzie mapowym. Jednocześnie **położenie przestrzenne
kafli i adnotacji jest zachowywane** (przybliżone, przez transformację TPS z punktów
georeferencyjnych obrazu).

## Dlaczego geometria sensora

Ukośne zdjęcie lotnicze najlepiej interpretuje się w oryginalnej geometrii, a model
uczony na takich kaflach pracuje później na takich samych obrazach. Dlatego etykietujesz
bezpośrednio na scenie sensora, bez prostowania do mapy. Widok mapowy tych scen możesz
w razie potrzeby obejrzeć osobno we wtyczce QGIS (NTF Reader).

| | Sceny lokalne (EO/SAR) | Lotnicze NITF |
| --- | --- | --- |
| Geometria labelowania | piksele sceny | **piksele sensora** |
| Georeferencja | afiniczna (regularna) | **TPS z 4 punktów** (przybliżona) |
| Podkład mapowy | opcjonalnie (GEO) | **brak** (podgląd w QGIS) |
| Pasma / typ | dowolne | **mono `UInt16`** |
| Położenie labeli w bazie | tak | **tak** (przez TPS) |

## Utworzenie projektu

1. W formularzu nowego projektu wybierz **Typ projektu → NITF (geometria sensora)**.
2. Wskaż **folder z plikami `.ntf`** - albo folder z **podkatalogami-paczkami**, gdzie
   każdy podkatalog zawiera pliki `.ntf` (nazwa podkatalogu jest zapamiętywana przy scenie).
3. Wybierz **tryb adnotacji**: ramka osiowa albo ramka zorientowana (obrócona).
4. Opcjonalnie wskaż plik klas.

Przy tworzeniu projektu aplikacja otwiera każdy kontener NITF, wybiera segment obrazu,
odczytuje punkty georeferencyjne oraz metadane i buduje **roboczy raster sensora**
(kafelkowany `GeoTIFF` z piramidą podglądu). Surowe wartości `UInt16` pozostają
zachowane do eksportu.

!!! info "Zakres pierwszej wersji"

    Obsługiwane są sceny **jednosegmentowe, jednopasmowe (panchromatyczne) `UInt16`**.
    Kontenery wielosegmentowe, wielopasmowe/RGB oraz georeferencja RPC/DEM nie są
    obsługiwane w tej wersji.

## Zachowywane metadane sceny

Podczas importu z kontenera NITF (nagłówków i rekordów TRE) wyciągany jest istotny
podzbiór metadanych i zapisywany przy scenie. Część pól jest **przeszukiwalna** - trafia
na listę scen i do sidecara datasetu, więc można po nich filtrować i grupować zbiór.

| Grupa | Przykładowe pola (scena `A001.F0012`) | Do czego |
| --- | --- | --- |
| **Identyfikacja / pochodzenie** | id obrazu `A001.F0012` (nazwa sceny), misja `EXAMPLE-01`, nr sceny `2`, obszar celu `AREA07`, data **produkcji** `2025-03-11` (inna niż akwizycja `2025-02-04`) | rozróżnianie i grupowanie w bazie ML |
| **Platforma / sensor** | sensor `SENSOR-X`, wysokość platformy `7600 m`, ogniskowa `180.0 mm`, data kalibracji `2015-08-12` | filtrowanie po sensorze/geometrii akwizycji |
| **Rozdzielczość (przybliżona)** | `gsd_m ≈ 0,55` (przez `0,30` × wzdłuż `1,05`) | orientacyjna wielkość piksela na ziemi |
| **Radiometria pasma** | bity znaczące `10`, maska, `NoData`, skala/offset, jednostka | poprawna interpretacja wartości |
| **Klasyfikacja** | oznaczenia klasyfikacji/dystrybucji NITF | bezpieczeństwo (patrz niżej) |
| **Model sensora (retencja)** | pełny `ACFTB` + surowe rekordy `SENSRA` (pozycja/kąty/wysokość) | zapas pod dokładniejszą geometrię w przyszłości |

!!! info "GSD jest przybliżony i anizotropowy"

    Na scenie ukośnej rozdzielczość terenowa różni się w poprzek i wzdłuż pasa oraz
    zmienia się w kadrze. Zapisany `gsd_m` to pojedyncza wartość orientacyjna
    (oznaczona jako przybliżona), a nie deklaracja dokładności.

!!! note "Model sensora zapisujemy „na przyszłość”"

    Parametry kamery i sensora (ogniskowa, linia lotu, kąty i wysokość z `SENSRA`) są
    zachowywane, ale **nie są używane w tej wersji**. `SENSRA` trzymamy w postaci
    surowej, bo GDAL nie dekoduje tego rekordu - dzięki temu można go zinterpretować
    później, gdy pojawi się dokładniejszy model transformacji, bez ponownego importu.

## Widok sensora

Scena otwiera się w **widoku pikselowym** (bez mapy i bez podkładu), w domyślnej
orientacji sensora. Działają przesuwanie i przybliżanie, a **panel wyświetlania** z tym
samym zestawem regulacji co dla scen satelitarnych EO: histogram z dwoma uchwytami
percentyli, presety (`p2–98`, `1–99`, `μ±2σ`, `μ±3σ`, pełny zakres), skala log/liniowa oraz
- w „Zaawansowane" - jasność, kontrast i gamma. Domyślnie **liniowe `p2–98`**.

!!! tip "Bardzo wąski zakres jasności to norma"

    Panchromatyczne sceny 10-bitowe zajmują często niewielki wycinek zakresu. Bez
    rozciągnięcia percentylowego obraz byłby prawie czarny - dlatego domyślny profil
    `pan_uint16_percentile` stosuje `p2–p98`. Regulacje wyświetlania nie zmieniają
    surowych danych ani zapisanych adnotacji.

Obrót i pomniejszenie widoku to wyłącznie stan interfejsu - **nie zmieniają
współrzędnych zapisanych adnotacji**, które zawsze są w pikselach sensora.

## Kaflowanie, adnotacje i AI

Kaflowanie, ramki osiowe i zorientowane, siatka przeglądu, statystyki oraz narzędzia
AI (SAM, „Znajdź podobne"/DINO, predykcja YOLO) działają jak dla scen lokalnych -
operują na pikselach sceny sensora.

Dla każdego kafla i każdej adnotacji wyliczany jest **footprint przestrzenny** przez
densyfikację krawędzi i transformację TPS. Dzięki temu położenie obiektów jest zapisane
w bazie ML, mimo że sama praca odbywa się w geometrii sensora.

!!! warning "Narzędzia AI na danych panchromatycznych"

    Modele SAM i DINO były trenowane głównie na materiale EO-RGB. Na panchromatycznych
    scenach `UInt16` (renderowanych do 8-bit) mogą działać słabiej - traktuj ich wyniki
    jako propozycje do weryfikacji, nie jako pewnik.

## Eksport i pochodzenie

Eksport zbiorów ML (YOLO, COCO, VOC) działa tak samo jak dla pozostałych projektów.
Sidecary datasetu niosą dodatkowo pochodzenie geometryczne każdej sceny: model
transformacji (`gcp_tps`), oznaczenie `approximate` i informację, że **nie jest to
ortorektyfikacja**.

Dostępny jest też **eksport przestrzenny** adnotacji do **GeoJSON / GeoPackage** w
`EPSG:4326`: każdy obiekt ma densyfikowany poligon TPS oraz jawne pola
`transform_model`, `approximate=true` i `orthorectified=false`.

!!! danger "Georeferencja TPS jest przybliżeniem"

    Transformacja z czterech punktów narożnych nie usuwa przemieszczeń terenowych,
    paralaksy ani deformacji obiektów wysokich. Nie przedstawiaj mapowego zasięgu jako
    dokładnej geometrii obiektu. Do dokładnego podglądu w układzie współrzędnych użyj
    wtyczki QGIS.

!!! info "Pola klasyfikacji"

    Znaczniki klasyfikacji i dystrybucji NITF są zachowywane w metadanych sceny, ale
    **świadomie nie trafiają do eksportu przestrzennego**. Przestrzegaj zasad
    bezpieczeństwa obowiązujących w Twojej organizacji przy udostępnianiu danych.
