# Statusy scen

Status w katalogu scen mówi, czy scena nadaje się do labelowania, a jeśli nie - czego
brakuje. Każdy status ma jedno konkretne działanie naprawcze.

| Status | Co oznacza | Co zrobić |
| --- | --- | --- |
| **gotowa** | produkt roboczy jest rozpoznany i czytelny | można zacząć labelowanie |
| **wymaga przygotowania** | produkt wymaga zbudowania widoku roboczego, np. połączenia części w VRT | uruchom przygotowanie sceny |
| **wymaga decyzji** | resolver znalazł kilka równorzędnych produktów i nie wybiera za Ciebie | wskaż właściwy produkt |
| **brak obsługi w środowisku** | formatu nie obsługuje sterownik dostępny w tej instalacji | zgłoś zespołowi; nie da się tego naprawić po stronie projektu |
| **brak źródła** | folder lub paczka zniknęła z widocznej ścieżki | podłącz nośnik albo [zrelinkuj źródło](../projects/relinkuj-zrodlo.md) |
| **źródło zmienione** | piksele produktu zmieniły się po imporcie | patrz niżej |
| **niepoprawna** | paczki nie udało się odczytać jako spójnej sceny | sprawdź kompletność paczki dostawcy |
| **wymaga migracji** | rozpoznawanie paczek zmieniło się i nie da się jednoznacznie wskazać następcy tej sceny | wskaż produkt ponownie - patrz [niżej](#wymaga-migracji) |

## Pozycje, które nie są scenami

Podgląd importu pokazuje też archiwa znalezione obok dostaw. Nie są scenami i nie
zostaną zaimportowane, ale ich stan bywa jedynym śladem po dostawie:

| Stan archiwum | Co oznacza |
| --- | --- |
| **duplikat archiwum** | zawartość ZIP-a jest już rozpakowana obok - nic nie trzeba robić |
| **niepełna ekstrakcja** | rozpakowano tylko część plików; podana jest lista braków |
| **tylko w archiwum** | dostawa istnieje wyłącznie w ZIP-ie |
| **archiwum nieczytelne** | pliku nie udało się otworzyć albo format nie jest obsługiwany |

Aplikacja nie rozpakowuje archiwów. Przy dwóch ostatnich stanach rozpakuj dostawę
samodzielnie obok archiwum i uruchom skan ponownie.

## Status piramidy

Status sceny i status piramidy odpowiadają na dwa różne pytania. Scena **gotowa** może
jeszcze mieć budowaną piramidę i nadal nadawać się do pracy.

| Status piramidy | Znaczenie |
| --- | --- |
| **pending / oczekuje** | potrzeba piramidy została rozpoznana |
| **queued / w kolejce** | trwałe zadanie czeka na worker |
| **building / budowanie** | GDAL tworzy poziomy wyświetlania |
| **ready / gotowa** | kompletna piramida jest dostępna |
| **native / natywna** | raster ma własne poziomy wystarczające do płynnego wyświetlania; duży JP2 może mimo nich wymagać piramidy projektowej |
| **error / błąd** | przygotowanie nie powiodło się; scena nadal może otworzyć się wolniej |

Podpowiedź znacznika pokazuje typ i poziomy overview. Zewnętrzny `.ovr` przygotowany
po imporcie zostanie wykryty przy odświeżeniu albo kolejnym odczycie sceny.
Duży JP2 z natywnymi poziomami może przejść ze starego statusu **native** do
**pending** po pierwszym odświeżeniu; jest to kontrolowana migracja naprawiająca
wydajność wysokich poziomów zbliżenia, a nie zmiana danych źródłowych.

### Status pełnej rozdzielczości dużego JP2

Podgląd i pełna rozdzielczość mają osobne statusy. `preview_status=ready` oznacza, że
można pracować na `.ovr` do poziomu 2×; nie obiecuje jeszcze poziomu 1×.

| Status | Znaczenie |
| --- | --- |
| **missing / stale** | brak aktualnego COG; podgląd `.ovr` pozostaje aktywny; przy wyłączonym automatycznym COG jest to oczekiwany stan |
| **queued / building / validating** | COG czeka, jest budowany lub sprawdzany |
| **ready** | zwalidowany COG jest aktywny i dostępne jest 1× |
| **error** | zadanie zakończyło się błędem; nie uruchomi się ponownie bez jawnego polecenia |

Po udanej publikacji warstwa przełącza się na COG bez zmiany układu współrzędnych
adnotacji. Po błędzie można sprawdzić przyczynę, ponowić przygotowanie albo pozostać na
podglądzie. Anulowanie nie usuwa `.ovr`.

Automatyczny start kontroluje przełącznik **Automatyczny COG pełnej rozdzielczości**
w kafelku **Źródła scen**. Jego wyłączenie nie zmienia statusu istniejącego, aktywnego
COG i nie anuluje trwającego zadania. Szczegóły procesu opisują
[Produkty pochodne](../input-data/produkty-pochodne.md#duze-generic-jp2-podglad-2-i-pena-rozdzielczosc-1).

## Wymaga decyzji

Aplikacja nie zgaduje, gdy produkty są równorzędne - prosi o wybór. Typowe przypadki:

- paczka zawiera kilka polaryzacji SAR;
- dostępny jest zarówno gotowy produkt, jak i materiał wymagający przygotowania;
- dla WorldView `MUL+PAN` trzeba dodatkowo potwierdzić indeksy pasm RGB.

!!! warning "Wybieraj produkt, a nie podgląd"

    Na liście alternatyw mogą znaleźć się pliki poglądowe dostawcy - z `preview`,
    `browse` albo `quicklook` w nazwie. Wyglądają jak scena i mają tę samą
    georeferencję, ale są przetworzonym obrazem 8-bitowym, nie danymi pomiarowymi.
    Dla SAR ma to znaczenie przy wszystkim, co zależy od jasności. Jeśli nie masz
    pewności, wybierz produkt wskazany w
    [tabeli domyślnych produktów](produkty-dostawcow.md).

## Wymaga migracji

Ten status pojawia się po zmianie sposobu rozpoznawania paczek, gdy stara scena nie ma
jednoznacznego następcy. Typowy przypadek: folder, który był wcześniej rozpoznawany
jako **jedna** scena, rozpada się na kilkadziesiąt osobnych produktów.

Aplikacja **nie wybiera wtedy za Ciebie** i blokuje otwarcie sceny. W sekcji źródeł scen
wybierz **Sprawdź migrację**, przejrzyj dry-run i wskaż następcę w każdym niejednoznacznym
wierszu. Adnotacje i identyfikator sceny pozostają nietknięte. Kopia poprzedniego
manifestu jest zapisywana przed migracją, a widoki robocze i piramidy nie są kasowane -
plan migracji wymienia je jako wymagające odbudowy.

## Źródło zmienione

Ten status pojawia się, gdy hash produktu przestał zgadzać się z zapisanym podczas
importu. Aplikacja **blokuje wtedy automatyczne użycie istniejących adnotacji**,
ponieważ ramki są zapisane w pikselach - zmiana rastra oznacza, że mogą wskazywać
inne miejsce niż w chwili rysowania.

To zabezpieczenie, nie awaria. Rozstrzygnij, co się stało, zanim ruszysz dalej:
czy plik został podmieniony celowo, czy to inna wersja produktu tej samej akwizycji.

## Brak źródła a usunięcie sceny

Odświeżenie katalogu **nie usuwa** scen, których źródła chwilowo nie widać. Scena
zostaje ze statusem **brak źródła**, a adnotacje pozostają nietknięte. Dzięki temu
odłączenie dysku zewnętrznego nie kasuje pracy.

!!! info "Blokada zmiany produktu po rozpoczęciu pracy"

    Gdy scena ma już adnotacje, aplikacja nie pozwala zmienić jej produktu roboczego.
    Ta sama zasada, co przy statusie **źródło zmienione**: ramki są przypisane do
    konkretnej siatki pikseli. Potrzebujesz innego produktu tej samej akwizycji -
    utwórz osobną scenę zamiast podmieniać istniejącą.
