# Indeks komunikatów błędów

Komunikaty pojawiają się jako **treść powiadomienia** (toast) po nieudanej akcji.
Większość pochodzi z lokalnej usługi aplikacji i jest **po angielsku** - poniżej ich
znaczenie i jedno konkretne działanie naprawcze. Lista jest reprezentatywna, nie
wyczerpująca; przy dłuższych scenariuszach zajrzyj do
[rozwiązywania problemów](../troubleshooting/index.md).

## Projekt i sceny

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `Project not found` | projekt o tym identyfikatorze nie istnieje | odśwież listę projektów; mógł zostać usunięty lub przeniesiony |
| `Selected folder is not a GeoTile Label project folder` | wskazany folder nie jest projektem aplikacji | wskaż katalog z plikami projektu (patrz [lokalizacje danych](lokalizacje-danych.md)) |
| `A project cannot combine SAR and EO scene sources` | próba dodania źródła innej modalności niż projekt | utwórz osobny projekt dla drugiej modalności |
| `Scene is not georeferenced` | akcja wymaga sceny `GEO`, a ta jej nie ma | użyj sceny z georeferencją; patrz [statusy scen](statusy-scen.md) |
| `Only scenes imported from a managed source can be removed here` | scena nie pochodzi z katalogowanego źródła | usuń całe źródło zamiast pojedynczej sceny |

## Import scen i paczek

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `No image files found in folder` / `No .ntf/.nitf files found…` | w folderze nie ma rozpoznanych plików sceny | wskaż właściwy folder; sprawdź [produkty dostawców](produkty-dostawcow.md) |
| `Scene import preview is invalid` / `Invalid preview_id` | podgląd importu wygasł po zmianie projektu | wygeneruj podgląd importu ponownie |
| `Three RGB band indexes are required for this product` | produkt wielopasmowy wymaga wyboru pasm | podaj trzy indeksy pasm RGB (np. `3,2,1`) |
| `Selected asset does not belong to this scene package` | wskazany plik nie należy do tej paczki | wybierz zasób z właściwej paczki |

Więcej: [Import paczek](../troubleshooting/import-paczek.md).

## Dataset

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `No tile catalog found. Build the tile catalog first.` | brak katalogu kafelków dla tej wersji | zbuduj [katalog kafelków](../datasets/katalog-kafelkow.md) |
| `No tile annotations. Generate dataset first.` | eksport/operacja przed wygenerowaniem datasetu | najpierw [zbuduj dataset](../datasets/zbuduj-dataset.md) |
| `Dataset not found. Generate it first.` | brak wygenerowanych plików wersji | wygeneruj wersję datasetu |
| `No project classes match the dataset class filter` | filtr klas wykluczył wszystkie klasy | poszerz filtr klas w konfiguracji datasetu |
| `Buffer must be smaller than tile size` | niepoprawna geometria kaflowania | ustaw nakładanie mniejsze niż rozmiar kafla |

## Trening i ocena

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `Dataset run must be published before training` | trening rusza tylko z opublikowanej wersji | opublikuj wersję datasetu; patrz [publikowanie](../datasets/publikowanie.md) |
| `Dataset run is not complete` | wybrana wersja nie zakończyła generowania | poczekaj na zakończenie albo wygeneruj ponownie |
| `This run did not finish, so it cannot be evaluated.` | ocena na teście dla nieukończonego przebiegu | dokończ trening przed oceną; patrz [wyniki](../training/wyniki.md) |
| `No labels_obb/ files in the dataset run…` | projekt `rotated_bbox`, a run bez etykiet OBB | wygeneruj dataset ponownie - etykiety OBB powstają automatycznie |

## Predykcja i modele

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `Model not configured or file not found` / `Model file not found` | ścieżka do modelu YOLO jest pusta lub nieaktualna | wskaż aktualny plik `.pt`; patrz [predykcja niedostępna](../troubleshooting/predykcja-niedostepna.md) |
| `SAM models directory not found` / `DINO models directory not found` | folder wag SAM/DINO nie istnieje | użyj **Wyczyść ścieżkę**, potem **Zmień folder** i wskaż nowy katalog |
| `SAM checkpoint not found` / `DINO checkpoint not found` | wskazany plik wag nie istnieje | wybierz istniejący checkpoint z aktualnego folderu |
| `No SAM checkpoint configured or bundled` | brak modelu SAM do narzędzia klik-w-ramkę | wskaż folder modeli SAM |
| `Text mode requires a SAM3 checkpoint (e.g. sam3.pt)` | tryb tekstowy działa tylko z modelem SAM3 | wskaż checkpoint `sam3.pt` |
| `Path outside configured roots` | ścieżka poza dozwolonymi katalogami | wybierz plik z katalogu w obrębie skonfigurowanych lokalizacji |

## Recenzje i paczki zespołowe

| Komunikat | Znaczenie | Co zrobić |
| --- | --- | --- |
| `Exporting a review package requires a 'review' project; this project is '…'` | eksport recenzji tylko w projekcie roli *review* | wykonaj to w projekcie zbiorczym managera; patrz [role projektu](../teamwork/role-projektu.md) |
| `No reviewed scenes to export` | żadna scena nie ma jeszcze werdyktu | oceń sceny przed eksportem recenzji |
| `output_path must be an absolute path` | podano ścieżkę względną | wskaż pełną, bezwzględną ścieżkę zapisu |

## Powiązane

- [Rozwiązywanie problemów](../troubleshooting/index.md) - dłuższe scenariusze naprawcze
- [Statusy scen](statusy-scen.md) - stany w katalogu scen
- [Lokalizacje danych](lokalizacje-danych.md) - gdzie aplikacja trzyma pliki
