# Szybki start managera

**Cel.** Od projektu zbiorczego do zwalidowanej wersji datasetu.

**Rola projektu.** Recenzja.

## 1. Załóż projekt zbiorczy

Utwórz projekt ze **wszystkimi scenami** zadania i ustaw rolę **Recenzja**. Profil -
modalność, georeferencja, tryb adnotacji - musi być taki sam jak u analityków.

!!! warning "Klasy uzgodnij przed rozdaniem pracy"

    Nazwa klasy jest kluczem dopasowania przy imporcie. Rozdaj zespołowi ten sam plik
    `classes.json`, którego użyjesz w projekcie zbiorczym. Patrz
    [Klasy](../projects/klasy.md).

## 2. Zaimportuj paczki

1. Wybierz **Importuj adnotacje do projektu**.
2. Wskaż paczki - **można kilka naraz**. Zakresy różnych analityków są rozłączne i
   stosują się bezkonfliktowo.
3. Przejrzyj podgląd z decyzją **per scena**.
4. Zatwierdź.

Podgląd pokazuje dla każdej sceny liczbę adnotacji przed i po oraz bilans zmian:

```text
lot_A.tif · analyst@example.com     312 → 318   +8  ~4  −2   [x] przyjmij
lot_B.tif · analyst@example.com      87 →  87    0   0   0    -
lot_C.tif · analyst@example.com     154 →   0    0   0 −154  [ ] przyjmij  ⚠
```

!!! danger "Przyjęcie sceny podmienia ją w całości"

    Dla danego właściciela: poprawki wchodzą, a obiekty przez niego usunięte **znikają**.
    Adnotacje innych właścicieli pozostają nietknięte.

    Dlatego wiersz z dużym ujemnym bilansem - jak `lot_C` powyżej - wymaga uwagi. Może
    być poprawny (analityk słusznie skasował błędną pracę) albo oznaczać pomyłkę.

## Trzy zabezpieczenia

Działają automatycznie:

1. **Scena tracąca większość pracy** właściciela jest domyślnie odznaczona i wymaga
   jawnego potwierdzenia.
2. **Paczka nie do rozwiązania** - na przykład z brakującą klasą - **nie usuwa
   niczego**. Cała scena jest pomijana z podanym powodem, żeby problem konfiguracji nie
   skasował danych.
3. **Adnotacja poprawiona wcześniej przez Ciebie** nie wraca jako duplikat ze starszej
   paczki analityka.

Import jest transakcyjny. Raport i migawka danych sprzed importu zostają w folderze
projektu.

## 3. Skontroluj kompletność

Sekcja **Podsumowanie adnotacji** pokazuje kompletność scen, liczbę obiektów według
klasy i autora, adnotacje bez autora oraz historię importów.

To moment na wychwycenie rozjazdu w interpretacji klas - zanim trafi do datasetu.

## 4. Odeślij do poprawy

1. W kolumnie **Recenzja** kliknij werdykt sceny.
2. Wybierz **Zaakceptowana**, **Do poprawy** albo **Odrzucona** i dopisz komentarz.
3. Wyeksportuj recenzję w jednym z dwóch trybów:
    - **per-analityk** - podaj adres analityka; paczka obejmie tylko sceny, na których ma
      on adnotacje (osobna paczka dla każdego analityka);
    - **Wszystkie recenzje** - zaznacz to pole, by w jednej paczce wysłać werdykty dla
      wszystkich sprawdzonych scen, bez podawania e-maila.

Werdykt jest **na poziomie sceny** - oceniasz pracę, nie każdy obiekt z osobna. Paczka
recenzji zawiera wyłącznie werdykty i komentarze.

!!! info "Jak działa „Wszystkie recenzje”"

    Przy imporcie werdykty trafiają do scen dopasowanych **po identyfikatorze sceny**, więc
    jedną zbiorczą paczkę możesz dać każdemu analitykowi - u każdego zaktualizują się tylko
    jego sceny, a pozostałe zostaną pominięte z nieszkodliwym ostrzeżeniem. Taka paczka
    zawiera jednak nazwy scen i komentarze wszystkich analityków - przy ścisłej separacji
    użyj trybu per-analityk.

## 5. Zbuduj dataset

Po kontroli zbuduj **jeden katalog kafelków**, a z niego dowolną liczbę wersji
datasetu.

1. [Katalog kafelków](../datasets/katalog-kafelkow.md)
2. [Zbuduj dataset](../datasets/zbuduj-dataset.md)
3. [Audyt](../datasets/audyt.md)
4. [Publikowanie wersji](../datasets/publikowanie.md)

**Punkt kontrolny.** Wersja datasetu z audytem `ready` albo świadomie ocenionymi
ostrzeżeniami, opublikowana i gotowa do treningu.

## Co dalej

[Uruchom trening](../training/uruchom-trening.md) na opublikowanej wersji.
