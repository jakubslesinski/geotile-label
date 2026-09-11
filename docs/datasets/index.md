# Datasety

Dataset jest **wersjonowanym produktem pochodnym** kanonicznych scen i adnotacji. Ten
sam projekt może wygenerować wiele wersji z różnymi parametrami - i wszystkie zostają.

## Droga danych

```text
adnotacje na scenie      → źródło prawdy
   ↓  siatka przeglądu   → co zostało sprawdzone
   ↓  katalog kafelków   → kandydaci, bez obrazów
   ↓  wersja datasetu    → obrazy, podział, statystyki
   ↓  publikacja         → materiał do treningu
```

Każdy krok jest odtwarzalny z poprzedniego. Skasowanie wersji datasetu nie rusza
adnotacji; skasowanie katalogu nie rusza siatki.

## Zadania

<div class="grid cards" markdown>

-   :material-grid: **Przygotowuję kandydatów**

    ---

    Co zawiera katalog kafelków i kiedy trzeba go przebudować.

    [Katalog kafelków](katalog-kafelkow.md)

-   :material-cog-play: **Generuję dataset**

    ---

    Podział, puste przykłady i filtry źródeł.

    [Zbuduj dataset](zbuduj-dataset.md)

-   :material-chart-bar: **Sprawdzam, co powstało**

    ---

    Rozkłady klas, wykorzystanie scen, braki.

    [Statystyki](statystyki.md)

-   :material-image-search: **Oglądam, co poszło do treningu**

    ---

    Kafelki z ramkami i klasami, wprost do edytora.

    [Zawartość](zawartosc.md)

-   :material-clipboard-check: **Kontroluję gotowość**

    ---

    Przeciek przestrzenny, integralność podziału, pokrycie przeglądem.

    [Audyt](audyt.md)

-   :material-publish: **Wybieram wersję do treningu**

    ---

    Stany publikacji i blokada usuwania.

    [Publikowanie wersji](publikowanie.md)

-   :material-scatter-plot: **Badam strukturę zbioru**

    ---

    Podobieństwo klas, podejrzane etykiety, duplikaty i przeciek train/val - przed treningiem.

    [Analiza datasetu](analiza.md)

</div>

## Zanim wygenerujesz

Punkty, które najczęściej decydują o jakości wyniku:

1. **Czy przegląd jest wystarczająco zaawansowany?** Puste przykłady biorą się
   wyłącznie ze sprawdzonych komórek.
2. **Czy podział pasuje do danych?** Losowanie kafelków przy danych satelitarnych
   zwykle daje przeciek przestrzenny.
3. **Czy klasy są uzgodnione?** Klasy nieużywane i rozjazd nazw wychodzą w
   statystykach i audycie.
4. **Czy filtry obejmują to, co zamierzasz?** Brak zaznaczenia oznacza użycie
   wszystkiego.

!!! info "Wersja datasetu jest niezmienna"

    Wygenerowanej wersji się nie edytuje - tworzy się nową. Dzięki temu wynik treningu
    zawsze da się powiązać z konkretną konfiguracją danych, a dwie wersje można
    porównać.

## Powiązane

- [Siatka przeglądu](../annotation/siatka-przegladu.md) - wejście do katalogu
- [Eksport](../export/index.md) - formaty paczek datasetu
- [Uruchom trening](../training/uruchom-trening.md) - co dalej z opublikowaną wersją
