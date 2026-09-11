# Importuj paczki scen

**Cel.** Podłączyć folder z paczkami dostawcy i dodać rozpoznane sceny do projektu.

**Kiedy.** Przy zakładaniu projektu oraz gdy dochodzą nowe akwizycje.

**Wymagania wstępne.** Utworzony projekt i folder z paczkami **jednego dostawcy**.

## Jak ułożyć folder źródłowy

Zalecany układ:

```text
folder_dostawcy/
├── paczka_sceny_001/
│   ├── raster lub części rastra
│   └── metadane dostawcy
├── paczka_sceny_002/
│   └── ...
└── ...
```

Paczki mogą zachować oryginalne zagnieżdżenie. **Nie przenoś plików do wspólnego
folderu i nie zmieniaj ich nazw** - aplikacja rozpoznaje produkty po strukturze i
nazewnictwie dostawcy.

!!! info "Jedno źródło to jeden dostawca"

    Projekt może mieć kilka źródeł, ale wszystkie muszą dotyczyć tej samej modalności
    - `SAR` albo `EO`. Dostawcę wskazujesz przy dodawaniu źródła; nie jest zgadywany
    z zawartości folderu.

## Kroki

1. W projekcie dodaj źródło: wybierz **dostawcę** i folder z paczkami.
2. Użyj **Skanuj i sprawdź**. Aplikacja rozpozna granice paczek, metadane i dostępne
   produkty.
3. Przejrzyj [drzewo rozpoznanych scen](#co-pokazuje-podglad) i ich statusy.
4. Dla scen ze statusem **wymaga decyzji** wskaż właściwy produkt. Przy WorldView
   `MUL+PAN` potwierdź dodatkowo indeksy pasm RGB.
5. Wybierz [tryb przygotowania](#tryby-importu).
6. Zatwierdź import. Postęp pozostaje widoczny w
   [Zadaniach w tle](../reference/zadania-w-tle.md).

![Tabela rozpoznanych scen ze statusami po skanowaniu źródła](../assets/images/import-scene-packages.png)
*Wynik skanowania źródła przed zatwierdzeniem importu.*

![Okno postepu importu: skatalogowane sceny i liczona tozsamosc plikow](../assets/images/importing-scenes-progress.png)
*Import dziala jako zadanie - mozna je odeslac w tlo albo anulowac.*

## Co pokazuje podgląd

Wynik skanowania jest ułożony hierarchicznie:

```text
Źródło
└── Dostawa
    └── Akwizycja
        ├── GRD VV - gotowa
        ├── GRD VH - gotowa
        └── MUL 2 + PAN 2 - wymaga przygotowania
```

Dzięki temu widać od razu, które produkty pochodzą z **tego samego przelotu**, a które
są osobnymi akwizycjami w jednym folderze.

W wierszu produktu jest tylko to, co pozwala wychwycić problem: etykieta produktu
z polaryzacją albo pasmami, status, liczniki ostrzeżeń i konfliktów oraz kompletność -
ale **tylko wtedy, gdy jest niepełna**. Komplet części nie wymaga uwagi.

Rozwinięcie wiersza pokazuje szczegóły jednego produktu: powód automatycznego wyboru,
rozkład plików według roli, brakujące części, treść ostrzeżeń, powiązane archiwum oraz
- dla produktów wymagających przygotowania - szacowany rozmiar wyniku i wolne miejsce.

Archiwa ZIP znalezione obok dostaw są widoczne przy swojej dostawie. Nie są scenami
i nie zostaną zaimportowane; ich stan opisuje
[Statusy scen](../reference/statusy-scen.md#pozycje-ktore-nie-sa-scenami).

## Tryby importu

| Tryb | Kiedy wybrać | Zachowanie |
| --- | --- | --- |
| **Szybki import (na żądanie)** | chcesz od razu rozpocząć pracę | katalog i tożsamość scen powstają najpierw; cięższe przygotowanie rusza, gdy scena jest potrzebna |
| **Przygotuj piramidy w tle** | typowa praca z wieloma dużymi scenami | sceny stają się dostępne po katalogowaniu, a piramidy są uzupełniane w tle |
| **Przygotuj wszystko przed otwarciem** | stanowisko przygotowujące dane lub praca offline | import czeka na komplet widoków roboczych i piramid |

W trybie tła aktualnie otwierana scena otrzymuje wyższy priorytet. Import można
anulować i wznowić, a brakujące piramidy dokończyć później. Zamknięcie modala nie
zatrzymuje zadania.

!!! info "Piramida importu a pełna rozdzielczość JP2"

    Tryby importu sterują przygotowaniem szybkiego widoku `overview.vrt` +
    `overview.vrt.ovr`. Nie tworzą pełnorozdzielczego COG. Dla dużego generic JP2
    COG 1× może zostać zakolejkowany dopiero po otwarciu sceny i załadowaniu podglądu,
    jeśli przełącznik **Automatyczny COG pełnej rozdzielczości** w kafelku
    **Źródła scen** jest ustawiony na **ON**. Ustawienie **OFF** nie wydłuża ani nie
    zmienia samego importu - zapobiega późniejszemu automatycznemu zadaniu COG.

    Szczegóły procesu i wymagań pamięci opisują
    [Produkty pochodne](produkty-pochodne.md#duze-generic-jp2-podglad-2-i-pena-rozdzielczosc-1).

!!! tip "Piramidy przygotowane wcześniej"

    Jeśli obok rastra istnieje poprawny `scene.tif.ovr`, aplikacja wykorzysta go
    zamiast budować redundantną kopię w projekcie. Instrukcja wsadowego generowania:
    [Piramidy i sidecary QGIS](piramidy-qgis.md).

## Punkt kontrolny

W katalogu scen widzisz pozycje ze statusem **gotowa**. Znacznik piramidy może jeszcze
pokazywać `w kolejce` albo `budowanie` - scena jest wtedy używalna, lecz pierwsze
wyświetlenie w małej skali może być wolniejsze. Sceny wymagające działania mają status
wskazujący, czego brakuje - patrz [Statusy scen](../reference/statusy-scen.md).

## Raport importu

Po zakończeniu importu można skopiować **raport** - jeden dokument opisujący każdą
scenę: przyczynę statusu, diagnostykę rozpoznawania, kompletność, liczby plików według
roli, czasy etapów i stan piramid. Lista błędów nie jest skracana, więc przyczyna
znajdzie się tam także przy imporcie liczonym w setkach scen.

Dostępne są dwa warianty kopiowania: pełny oraz **bez ścieżek** - ten drugi zamienia
ścieżki i nazwy plików na stabilne skróty, więc raport można przekazać dalej bez
ujawniania struktury katalogów.

## Migracja istniejącego projektu

Po aktualizacji reguł rozpoznawania paczek użyj w nagłówku **Źródła scen** przycisku
**Sprawdź migrację**. Najpierw powstaje wyłącznie dry-run: zestawienie scen bez zmian,
migrowanych automatycznie, wymagających wyboru i tych, których źródło jest niedostępne.
Na tym etapie projekt nie jest modyfikowany.

Jeśli stary pakiet rozpadł się na kilka produktów, w kolumnie **Następca** wybierz
właściwy produkt dla każdej niejednoznacznej sceny. Przycisk zastosowania pozostaje
zablokowany do czasu wykonania wszystkich dostępnych wyborów. Zastosowanie migracji:

- ponownie skanuje źródła i odrzuca wybór, który przestał być aktualny;
- tworzy kopię `scene.json` i `scene_manifest.json`;
- zachowuje `scene_id`, katalog sceny i adnotacje;
- zapisuje pełny nowy pakiet oraz pochodzenie decyzji użytkownika;
- nie kasuje widoku roboczego ani piramid - raport wskazuje, co wymaga odbudowy.

Scena bez dostępnego źródła lub bez możliwego następcy dostaje status
**wymaga migracji** i nie można jej otworzyć do etykietowania. Po przywróceniu źródła
uruchom sprawdzenie ponownie.

## Zmiana źródła w trakcie skanowania

Przed skanowaniem i po nim aplikacja robi migawkę stanu plików. Gdy w trakcie coś się
zmieni, wynik **nie zostaje opublikowany** jako podgląd - zamiast tego dostajesz
komunikat o zmianie źródła. Chodzi o to, żeby nie zaimportować katalogu złożonego
z dwóch różnych stanów folderu.

## Co zostało zapisane

Pliki źródłowe **pozostają nietknięte i tylko do odczytu**. W folderze projektu
powstają:

- wpis źródła wraz z jego stabilnym identyfikatorem;
- katalog scen ze statusami;
- metadane każdej sceny: CRS, transformacja, pochodzenie;
- status, typ, poziomy i fingerprint piramid;
- widoki robocze w `derived_scenes\`, jeśli produkt ich wymagał.

## Typowe problemy

**Wszystkie sceny mają „wymaga decyzji".** Najczęściej wskazano niewłaściwego
dostawcę - aplikacja szuka wtedy innego wzorca produktu i pokazuje wszystkie rastry
jako równorzędne. Sprawdź wybór dostawcy przed przeglądaniem listy.

**Część paczek nie została rozpoznana.** Sprawdź, czy nie zostały wcześniej
rozpakowane do wspólnego folderu albo przemianowane.

**Scena pokazuje do wyboru tylko pliki JPG z logo dostawcy.** Rastry leżą głębiej niż
pozwala limit **260 znaków** ścieżki w Windows i przed poprawką były dla aplikacji
niewidoczne - bez żadnego komunikatu, bo system zgłasza wtedy brak ścieżki. Bieżąca
wersja czyta takie pliki poprawnie; w projekcie zeskanowanym wcześniej wystarczy
**przeskanować źródło ponownie**. Jeśli problem wraca w tej wersji, skróć nazwę folderu
dostawy - najdłuższa ścieżka rastra musi zmieścić się razem ze ścieżką udziału
sieciowego.

**Scena ma status „brak obsługi w środowisku".** Formatu nie obsługuje sterownik
dostępny w tej instalacji - to nie jest do naprawienia po stronie projektu, zgłoś
zespołowi.

**Scena ma status „wymaga migracji".** Użyj **Sprawdź migrację** przy źródłach scen,
przejrzyj plan i wskaż następcę. Nie próbuj otwierać sceny przed rozstrzygnięciem -
aplikacja blokuje ten stan, aby nie użyć adnotacji z innym rastrem.

!!! warning "Nie przygotowuj produktów poza aplikacją"

    Jeśli paczka zawiera produkt z
    [tabeli domyślnych](../reference/produkty-dostawcow.md), nie konwertuj go
    wcześniej. Aplikacja przygotuje widok roboczy bez zmiany pikseli i zapisze pełne
    pochodzenie danych. Ręczna konwersja ten łańcuch zrywa.

## Następny krok

Sceny wymagające przygotowania opisuje
[Produkty pochodne i widok roboczy](produkty-pochodne.md). Gdy sceny są gotowe,
przejdź do [Adnotowania](../annotation/index.md).
