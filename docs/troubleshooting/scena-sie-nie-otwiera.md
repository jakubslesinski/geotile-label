# Scena się nie otwiera

## Najpierw sprawdź status

Status sceny w katalogu mówi, czego brakuje - i każdy ma inne rozwiązanie.

| Status | Co zrobić |
| --- | --- |
| **brak źródła** | podłącz nośnik albo [zrelinkuj źródło](../projects/relinkuj-zrodlo.md) |
| **wymaga decyzji** | wskaż właściwy produkt z paczki |
| **wymaga przygotowania** | utwórz widok roboczy |
| **źródło zmienione** | ustal, dlaczego plik się zmienił - patrz niżej |
| **brak obsługi w środowisku** | zgłoś zespołowi; nie naprawisz tego w projekcie |
| **niepoprawna** | sprawdź kompletność paczki dostawcy |
| **wymaga migracji** | wskaż produkt ponownie - patrz [niżej](#scena-wymaga-migracji) |

Pełny opis w [Statusach scen](../reference/statusy-scen.md).

## Scena ma status „gotowa", a i tak się nie otwiera

**Sprawdź prawa dostępu i dostępność dysku.** Sceny często leżą na zasobie sieciowym
albo dysku zewnętrznym.

**Daj czas dużym plikom.** Pierwsze wyświetlenie dużego GeoTIFF wymaga odczytu i
przygotowania podglądu. Kolejne otwarcia są szybsze.

**Sprawdź znacznik piramidy.** `w kolejce` lub `budowanie` oznacza, że scena jest już
dostępna, lecz niski zoom może być wolny. Przy `błąd` otwórz panel
[Zadania w tle](../reference/zadania-w-tle.md), sprawdź komunikat i ponów operację.
Możesz również przygotować źródłowy sidecar według instrukcji
[Piramidy i sidecary QGIS](../input-data/piramidy-qgis.md).

**Po dodaniu `.ovr` odśwież katalog lub ponownie otwórz scenę.** Aplikacja wykryje
zmianę i unieważni stare miniatury, histogram oraz cache kafli. Nie trzeba importować
sidecara osobno.

### JP2 ma podgląd, ale nie udostępnia poziomu 1×

Jeżeli duży generic JP2 wyświetla się na podglądzie `.ovr`, ale dalsze zbliżenie jest
zablokowane, sprawdź na pulpicie projektu w kafelku **Źródła scen** ustawienie
**Automatyczny COG pełnej rozdzielczości**:

1. **OFF** oznacza zachowanie zamierzone - aplikacja nie uruchomi automatycznie
   kosztownego przygotowania COG i pozostanie na podglądzie;
2. przy **ON** otwórz scenę i poczekaj na pierwszy kompletny viewport; dopiero wtedy
   zadanie COG trafia do kolejki;
3. otwórz **Zadania w tle** na lewym pasku, bezpośrednio nad Pomocą, i sprawdź etap
   `queued`, `building` albo `validating`;
4. po stanie `completed` warstwa sama przełączy się na zwalidowany COG i udostępni 1×.

Po błędzie aplikacja nie uruchamia kolejnej próby tylko dlatego, że ponownie otwarto
scenę. Sprawdź zapisany komunikat i użyj jawnie **Ponów**. Podgląd `.ovr` pozostaje
dostępny także po błędzie lub anulowaniu.

**Sprawdź logi.** `backend.stderr.log` w `%APPDATA%\GeoTileLabel\logs` zwykle mówi
wprost, na czym odczyt się zatrzymał.

## Sceny w ogóle nie ma na liście

**Dostawa może być tylko w archiwum.** Jeżeli w folderze jest wyłącznie ZIP, dostawa
pojawi się w podglądzie jako pozycja archiwalna, a nie jako scena. Aplikacja nie
rozpakowuje archiwów - rozpakuj dostawę obok archiwum i uruchom skan ponownie.

**Ekstrakcja mogła być niepełna.** Gdy obok archiwum jest katalog z częścią plików,
podgląd pokaże stan **niepełna ekstrakcja** wraz z listą brakujących plików. Uzupełnij
brakujące pliki i przeskanuj źródło.

**Sprawdź, czy wskazano właściwego dostawcę.** Przy niewłaściwym dostawcy aplikacja
szuka innego wzorca produktu; dostawa albo nie zostanie rozpoznana, albo trafi na listę
jako **wymaga decyzji** ze wszystkimi rastrami.

Gdy nie wiadomo, co się stało, skopiuj [raport importu](../input-data/importuj-paczki.md#raport-importu).
Zawiera przyczynę dla każdej sceny, także tych pominiętych.

## Scena wymaga migracji

Rozpoznawanie paczek zmieniło się i stara scena nie ma jednoznacznego następcy - na
przykład folder rozpoznawany wcześniej jako jedna scena rozpada się na kilkadziesiąt
produktów.

Adnotacje i identyfikator sceny pozostają nietknięte, a poprzedni manifest jest
zapisany jako kopia. Przy źródłach scen użyj **Sprawdź migrację**, przejrzyj dry-run
i wskaż następcę. Jeżeli źródło jest niedostępne, przywróć je i sprawdź migrację
ponownie. Scena pozostaje zablokowana do rozstrzygnięcia; plan wskaże widok roboczy
i piramidę wymagające odbudowy.

## Źródło zmienione

Ten status znaczy, że **piksele produktu różnią się** od zapisanych przy imporcie.

!!! warning "Nie obchodź tego statusu w pośpiechu"

    Aplikacja blokuje automatyczne użycie adnotacji, bo ramki są zapisane w pikselach -
    po podmianie rastra mogłyby wskazywać inne miejsce w terenie, **bez widocznego
    objawu**.

    Ustal najpierw, co się stało: czy plik został podmieniony celowo, czy to inna
    wersja produktu tej samej akwizycji. W drugim przypadku właściwym rozwiązaniem jest
    osobna scena, nie odblokowanie istniejącej.

## Obraz jest, ale wygląda źle

**Zbyt ciemny albo zbyt jasny** - to kwestia wyświetlania, nie danych. Wyreguluj
rozciągnięcie histogramu, jasność i kontrast. Ustawienia te nie zmieniają pikseli.

**Przekłamane kolory przy WorldView** - prawdopodobnie niepotwierdzona kolejność pasm
RGB. Patrz [Domyślne produkty dostawców](../reference/produkty-dostawcow.md).

**Dziura w środku obrazu** - brakuje kafla wymienionego w manifeście dostawy. Podgląd
importu wymienia brakujące części; uzupełnij je w folderze źródłowym i przeskanuj
źródło. Poszarpany brzeg obrazu jest natomiast normalny i nie oznacza braku.

**Szeroka czarna ramka wokół obrazu** - normalne dla produktów geokodowanych, gdy
obszar akwizycji jest obrócony względem układu współrzędnych. Ten obszar warto
[wykluczyć w siatce przeglądu](../annotation/siatka-przegladu.md).
