# Od sceny do datasetu

Przewodnik po całej drodze danych - od paczki dostawcy do modelu. Nie zastępuje
instrukcji poszczególnych kroków; pokazuje **kolejność, produkty pośrednie i miejsca,
w których warto się zatrzymać**.

Każdy etap coś wytwarza i coś zakłada o etapie poprzednim. Pominięcie kontroli
przenosi problem dalej, gdzie naprawa jest droższa.

## Mapa drogi

```text
paczka dostawcy
   │  import i rozpoznanie produktu
   ▼
scena w projekcie            ← źródło prawdy
   │  labelowanie
   ▼
adnotacje kanoniczne         ← źródło prawdy
   │  siatka przeglądu
   ▼
potwierdzone pokrycie
   │  katalog kafelków
   ▼
kandydaci na kafelki         (metadane, bez obrazów)
   │  budowanie datasetu
   ▼
wersja datasetu              (obrazy, podział, statystyki)
   │  audyt i publikacja
   ▼
opublikowana wersja
   │  trening
   ▼
model + rodowód
```

Wszystko poniżej linii „adnotacje kanoniczne" jest **odtwarzalne**. Wszystko powyżej -
nie.

## 1. Od paczki do sceny

**Co robisz.** Podłączasz folder dostawcy i importujesz rozpoznane sceny.

**Co powstaje.** Katalog scen ze statusami; dla niektórych produktów widok roboczy.

**Zanim pójdziesz dalej.** Sceny mają status **gotowa**. Sceny wymagające decyzji
rozstrzygnięte świadomie - przy produktach SAR uważaj, żeby nie wybrać pliku
poglądowego zamiast danych pomiarowych.

→ [Importuj paczki scen](../input-data/importuj-paczki.md)

!!! warning "Tu zapadają decyzje trudne do cofnięcia"

    Po pierwszej adnotacji wariant sceny jest blokowany. Wybór produktu i sposobu
    przygotowania to najtańszy moment na zastanowienie. Patrz
    [Produkty pochodne](../input-data/produkty-pochodne.md).

## 2. Od sceny do adnotacji

**Co robisz.** Rysujesz ramki na pełnych scenach.

**Co powstaje.** Adnotacje kanoniczne - **jedyna rzecz, której nie da się odtworzyć**.

**Zanim pójdziesz dalej.** Klasy uzgodnione z zespołem, interpretacja spójna,
orientacja obiektów ustawiona starannie, jeśli projekt używa ramek zorientowanych.

→ [Adnotowanie](../annotation/index.md)

## 3. Od adnotacji do potwierdzonego pokrycia

**Co robisz.** Oznaczasz sprawdzone fragmenty siatką przeglądu.

**Co powstaje.** Informacja, gdzie ktoś naprawdę patrzył.

**Zanim pójdziesz dalej.** Puste obszary sprawdzone, a nie tylko puste. Ramka nodata
wykluczona.

→ [Siatka przeglądu](../annotation/siatka-przegladu.md)

!!! info "Ten etap bywa pomijany i mści się później"

    Bez potwierdzonego pokrycia nie ma skąd wziąć pustych przykładów, a audyt zgłosi
    korzystanie z obszarów nieobejrzanych. Oba problemy wracają wtedy, gdy dataset jest
    już zbudowany.

## 4. Od pokrycia do kandydatów

**Co robisz.** Budujesz katalog kafelków.

**Co powstaje.** Geometria podziału i powiązania z adnotacjami - **bez obrazów**.

**Zanim pójdziesz dalej.** Rozmiar kafelka i nakładanie przemyślane: ich zmiana wymaga
nowej wersji katalogu, w odróżnieniu od podziału czy preprocessingu.

→ [Katalog kafelków](../datasets/katalog-kafelkow.md)

## 5. Od kandydatów do wersji datasetu

**Co robisz.** Generujesz dataset z wybranym podziałem, udziałem pustych i filtrami.

**Co powstaje.** Niezmienna wersja z obrazami, podziałem i statystykami.

**Zanim pójdziesz dalej.** Strategia podziału dopasowana do danych - dla materiału
satelitarnego scenowa albo blokowa, nie losowanie kafelków.

→ [Zbuduj dataset](../datasets/zbuduj-dataset.md)

## 6. Kontrola i publikacja

**Co robisz.** Czytasz statystyki, uruchamiasz audyt, publikujesz wersję.

**Co powstaje.** Wersja oznaczona jako materiał do treningu.

**Zanim pójdziesz dalej.** Audyt `ready` albo ostrzeżenia ocenione **świadomie** -
zwłaszcza dotyczące przecieku przestrzennego i obszarów niesprawdzonych.

→ [Audyt](../datasets/audyt.md) · [Publikowanie](../datasets/publikowanie.md)

!!! danger "To ostatni moment przed kosztownym błędem"

    Przeciek przestrzenny nie objawia się jako awaria - objawia się jako **dobry
    wynik**. Model wygląda na skuteczny, a zawodzi na nowej scenie. Wykrycie tego po
    treningu oznacza powtórzenie całej pracy od etapu 5.

## 7. Od datasetu do modelu

**Co robisz.** Uruchamiasz preflight, trenujesz, porównujesz przebiegi, rejestrujesz
model.

**Co powstaje.** Model wraz z rodowodem sięgającym autorstwa adnotacji.

**Zanim uznasz to za skończone.** Porównanie po metrykach walidacyjnych, ocena testowa
raz na końcu.

→ [Uruchom trening](../training/uruchom-trening.md) ·
[Porównanie wyników](../training/wyniki.md) ·
[Rejestr modeli](../training/rejestr-modeli.md)

## Gdzie wchodzi zespół

Przy pracy zespołowej etapy 1–3 wykonują analitycy w swoich projektach, a etapy 4–7
manager w projekcie zbiorczym. Paczki adnotacji przenoszą wynik etapu 2 i 3.

→ [Praca zespołowa](../teamwork/index.md)

## Co wraca do początku

Model rzadko jest końcem. Typowe pętle:

- **słaby wynik dla klasy** → więcej przykładów tej klasy → etap 2;
- **fałszywe wykrycia na tle** → więcej sprawdzonych pustych obszarów → etap 3;
- **przeciek przestrzenny w audycie** → inna strategia podziału → etap 5;
- **model gotowy do pomocy** → predykcja jako wsparcie labelowania → etap 2.

Ostatnia pętla jest najbardziej wartościowa i zarazem najbardziej ryzykowna:
propozycje modelu przyspieszają pracę, ale przyjmowane bez kontroli **utrwalają jego
własne błędy** w kolejnym datasecie.

→ [Narzędzia AI](../ai-assistance/index.md)
