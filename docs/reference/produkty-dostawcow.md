# Domyślne produkty dostawców

Aplikacja rozpoznaje paczkę dostawcy i sama wybiera z niej **produkt roboczy** - plik
używany do labelowania. Ta strona mówi, co zostanie wybrane i kiedy aplikacja poprosi
o decyzję.

## Wybór produktu

| Dostawca | Produkt roboczy | Gdy go nie ma |
| --- | --- | --- |
| ICEYE | GRD TIFF | wymaga decyzji |
| Capella | GEO TIFF, w drugiej kolejności GEC | wymaga decyzji |
| UMBRA | GEC TIFF | wymaga decyzji |
| BlackSky | ortho RGB TIFF | wymaga decyzji |
| Pleiades Neo | PMS-FS RGB ORTHO | MS-FS RGB + PAN do przygotowania |
| WorldView | gotowy pansharpened RGB | MUL + PAN do przygotowania |
| Generic | pojedynczy plik rastrowy | - |

Modalność wynika z dostawcy: ICEYE, Capella i UMBRA to `SAR`, pozostali `EO`.

!!! info "Dostawcę wybierasz przy dodawaniu źródła"

    Rozpoznawanie nie zgaduje dostawcy z zawartości folderu - bierze go z Twojego
    wyboru. Wskazanie niewłaściwego dostawcy sprawia, że aplikacja szuka innego wzorca
    produktu i najczęściej kończy się statusem **wymaga decyzji** z listą wszystkich
    rastrów w paczce.

## ICEYE: starsze i obecne dostawy

ICEYE dostarcza dwa układy paczek i oba są rozpoznawane:

| Układ | Co zawiera | Uwagi |
| --- | --- | --- |
| starszy | `*.tif` + `*.xml` obok siebie | metadane w XML dostawcy |
| obecny | COG `*_GRD.tif` + `*.geojson` | sidecar GeoJSON z parametrami produktu |

Do labelowania używany jest **GRD** (albo `ORT`, jeśli dostawa go zawiera). Produkty
w kontenerach `.h5`, `.hdf5` i `.nc` - czyli `SLC` i `CSI` - **nie są kandydatami na
scenę**: to dane zespolone, nie obraz. Podgląd `VID` również nie jest produktem
roboczym.

Jedna dostawa może zawierać kilka polaryzacji tej samej akwizycji. Każda z nich jest
**osobną sceną** i dostaje wyłącznie własne metadane - sidecar polaryzacji `HH` nie
opisuje sceny `HV`.

## Capella: GEO i GEC

Capella dostarcza produkty geokodowane w dwóch wariantach. Gdy w akwizycji jest
więcej niż jeden, aplikacja wybiera **GEO**, a `GEC` traktuje jako drugi wybór.
Jeżeli akwizycja ma tylko GEC, wybrany zostaje GEC - kolejność zawęża wybór, ale
nigdy go nie blokuje.

Jeden folder Capelli potrafi zawierać **dwie akwizycje**. Są rozdzielane po
identyfikatorze satelity i znaczniku czasu, więc powstają dwie sceny, a nie jedna
scena z wymieszanymi metadanymi.

Pliki `*_preview.tif` są podglądem dostawcy i nie trafiają na listę produktów.

## Pleiades: PHR i PNEO

Obie misje przychodzą jako DIMAP i obsługuje je ten sam resolver. Sensor bierze się
z metadanych, nie z deklaracji źródła: `PNEO3`/`PNEO4` albo `PHR1A`/`PHR1B`.

| Misja | Produkt roboczy | Kolejność pasm RGB |
| --- | --- | --- |
| PNEO | `PMS-FS`, wariant `_RGB` | z metadanych, zwykle 1, 2, 3 |
| PHR | `PMS` | z metadanych, zwykle 3, 2, 1 |

Kolejność pasm **jest odczytywana z DIMAP-u**, a nie zakładana - obie misje deklarują ją
w polu przeznaczonym dokładnie do tego. Różnią się przy tym: PHR podaje pasma jako
`B0…B3`, gdzie barwy naturalne to trzecie, drugie i pierwsze, a PNEO od razu jako `R`,
`G`, `B`.

Dostawa PNEO `PMS-FS` zawiera **dwa pliki tego samego produktu**: `_RGB` w barwach
naturalnych i `_NED` (bliska podczerwień, red edge, deep blue) w barwach umownych.
Domyślnie wybierany jest `_RGB`; `_NED` jest dostępny jako alternatywa produktu.

Gdy w paczce nie ma gotowego pansharpened, powstaje produkt pochodny z MS-FS i PAN.

!!! info "Deklaracja źródła nie zmienia sensora"

    Zadeklarowanie źródła PHR jako Pleiades Neo nie zapisze sceny jako PNEO. Manifest
    odnotuje wtedy niezgodność między deklaracją a metadanymi - nadal jednak warto
    wskazywać właściwą misję, bo niezgodność zostaje w historii sceny.

### Wartości pikseli w produktach Pleiades

Dostawy z tego korpusu są produktami **gotowymi do wyświetlenia**: 8-bitowymi, po
rozciągnięciu wykonanym przez dostawcę. Nie są radiancją ani reflektancją, więc manifest
zapisuje je jako `display_ready` bez jednostki fizycznej i bez stanu kalibracji.

Ma to znaczenie przy generowaniu datasetu: takiej sceny **nie da się porównywać wprost**
ze sceną skalibrowaną - aplikacja zablokuje zmieszanie ich w jednym datasecie bez Twojej
świadomej zgody.

!!! warning "Zero znaczy brak danych, nie czerń"

    Produkty Pleiades deklarują w metadanych wartość `NODATA = 0` - mimo że sam plik
    rastrowy jej nie niesie. Aplikacja bierze ją z metadanych, bo w rzeczywistych dostawach
    ta „czarna ramka" obróconego produktu zajmuje **od jednej trzeciej do połowy obrazu**.
    Bez tego zera weszłyby do wyliczenia zakresu jasności i przyciemniły kafle z brzegu
    sceny.

    Wartość `SATURATED = 255` jest odczytywana tak samo - opisuje piksele prześwietlone.

## WorldView: PAN, MUL i MUL+PAN

Dostawa WorldView może zawierać komponent panchromatyczny, wielospektralny albo oba.
Rozpoznawane są wszystkie trzy przypadki:

| Zawartość dostawy | Produkt roboczy | Status |
| --- | --- | --- |
| gotowy pansharpened (`PSH`) | ten plik | gotowa |
| MUL + PAN | produkt pochodny RGB | wymaga przygotowania |
| tylko MUL | mozaika wielospektralna | gotowa |
| tylko PAN | mozaika panchromatyczna | gotowa |

Dostawa **wyłącznie panchromatyczna jest pełnoprawną sceną**, a nie brakiem
komponentu. Nie dostaje odwzorowania RGB: obraz jednopasmowy wyświetla się w skali
szarości.

### Lista i kolejność części z manifestu TIL

Produkty kafelkowane mają obok plik `.TIL` - manifest dostawcy z listą części i ich
kolejnością. Aplikacja bierze listę **stamtąd**, a nie z sortowania nazw plików, i na
tej podstawie ocenia kompletność:

- komplet części → scena **gotowa**;
- brak którejś z zadeklarowanych części → status **wymaga decyzji**, ostrzeżenie
  `partial_delivery` i lista braków. Niepełne pokrycie można zaimportować świadomie,
  ale musi to być Twoja decyzja.

### Kolejność pasm WorldView

Aplikacja próbuje odczytać ją z metadanych `IMD` w paczce:

| Co znajduje w metadanych | Przyjęte pasma RGB |
| --- | --- |
| `BAND_C`, `BAND_B`, `BAND_G`, `BAND_R` (produkt 8-pasmowy) | 5, 3, 2 |
| `BAND_B`, `BAND_G`, `BAND_R` (produkt 4-pasmowy) | 3, 2, 1 |
| nic z powyższych | pyta o potwierdzenie |

Gdy metadanych nie da się odczytać, scena dostaje status **wymaga decyzji** i
ostrzeżenie o konieczności potwierdzenia mapowania pasm. To celowe: zła kolejność
daje obraz o przekłamanych kolorach, co przy interpretacji obiektów potrafi
wprowadzić w błąd.

Metadane MUL i PAN **nie są scalane w jeden opis sceny**. Produkt MUL+PAN opisują
metadane komponentu wielospektralnego, bo to on wnosi charakterystykę spektralną;
metadane PAN pozostają dostępne osobno.

## Pliki wieloczęściowe

Produkty podzielone na kafle (`R1C1`, `R1C2`, …) są łączone w wirtualny raster **bez
kopiowania danych**. Nie trzeba ich wcześniej scalać ani przenosić do wspólnego
folderu.

Przed złożeniem mozaiki części są sprawdzane: układ współrzędnych, liczba i typ pasm,
rozdzielczość oraz wpasowanie we wspólną siatkę pikseli. Niezgodność któregokolwiek
z tych elementów blokuje budowę mozaiki i jest opisana w statusie sceny. Nakładające
się części i dziury w pokryciu **nie blokują** - są stanem dostawy i trafiają do
ostrzeżeń.

!!! info "Poszarpany brzeg to nie dziura"

    Kafle skrajnej kolumny i skrajnego wiersza często mają inne rozmiary niż
    pozostałe, więc prostokąt obejmujący scenę nie jest w całości pokryty. To normalne
    i nie jest zgłaszane. Ostrzeżenie pojawia się dopiero, gdy brakuje kafla **w
    środku** obrazu albo gdy niepokryte jest ponad 1% powierzchni.

!!! tip "TIFF ma pierwszeństwo przed JP2"

    Gdy Pleiades Neo dostarcza ten sam produkt w obu formatach, aplikacja wybiera
    TIFF. Nie trzeba samodzielnie konwertować JP2 - instalator zawiera sterownik
    `JP2OpenJPEG`, więc oba formaty otwierają się bez dodatkowej pracy.

## Archiwa ZIP obok dostawy

Archiwum nie jest sceną i nigdy nie zostanie wybrane jako produkt roboczy, ale jest
**widoczne** w podglądzie importu razem ze swoim stanem:

| Stan | Znaczenie |
| --- | --- |
| **duplikat archiwum** | wszystkie pliki z archiwum są już rozpakowane obok |
| **niepełna ekstrakcja** | rozpakowano tylko część plików; podana jest lista braków |
| **tylko w archiwum** | dostawa istnieje wyłącznie w ZIP-ie i nie ma rozpakowanej kopii |

Aplikacja **nie rozpakowuje archiwów**. Gdy dostawa jest tylko w archiwum albo
rozpakowana niekompletnie, rozpakuj ją samodzielnie obok - najlepiej do katalogu
o nazwie archiwum - i uruchom skan ponownie.

## Czego nie robić przed importem

!!! warning "Nie przetwarzaj paczek poza aplikacją"

    Jeżeli paczka zawiera któryś z produktów z tabeli, nie konwertuj go, nie zmieniaj
    nazw plików i nie przenoś ich do wspólnego folderu. Zachowanie oryginalnej
    struktury pozwala zapisać pełne pochodzenie danych, a przygotowanie widoku
    roboczego w aplikacji nie zmienia pikseli. Ręczna konwersja zrywa ten łańcuch i
    zwykle kończy się statusem **wymaga decyzji**.

Zasady organizacji folderów źródłowych opisuje
[Importuj paczki scen](../input-data/importuj-paczki.md), a znaczenie statusów -
[Statusy scen](statusy-scen.md).
