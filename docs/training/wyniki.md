# Porównanie wyników

Zakładka **Wyniki** służy do jednego zadania: wybrania najlepszej konfiguracji
treningu **dla konkretnego datasetu**.

## Najpierw wybierasz dataset

Porównanie zawsze odbywa się w obrębie jednej wersji datasetu. Nie da się zestawić
przebiegów z różnych datasetów obok siebie.

!!! info "To ograniczenie celowe"

    Metryki liczone na różnych zbiorach testowych **nie są porównywalne**. Model o
    wyższym `mAP` na łatwiejszym zbiorze nie jest lepszy - tylko miał łatwiej.
    Zestawienie takich liczb w jednej tabeli sugerowałoby wniosek, którego z nich nie
    da się wyciągnąć.

Chcesz porównać dwa datasety? Porównuj **konfiguracje na każdym z osobna**, a wnioski
wyciągaj z tego, jak zachowuje się ta sama konfiguracja.

## Grupowanie powtórzeń

Przebiegi o identycznej konfiguracji są grupowane razem. Widać wtedy nie tylko wynik,
ale i **rozrzut między powtórzeniami**.

!!! tip "Rozrzut bywa większy niż różnica między konfiguracjami"

    Przy małych datasetach dwa identyczne przebiegi potrafią różnić się bardziej niż
    dwie różne konfiguracje. Zanim uznasz, że jedna jest lepsza, sprawdź, czy różnica
    wykracza poza ten rozrzut. Jeśli nie - powtórz przebieg, zamiast wybierać.

## Panel porównania

Po prawej jest **zwijany panel przebiegów** (pogrupowane po konfiguracji, najlepszy `mAP50-95`
na górze, z wyszukiwarką). Zaznaczasz w nim checkboxami te, które chcesz zestawić - każdy
dostaje stały kolor używany we wszystkich wykresach. Panel można **zwinąć** (chevron w
nagłówku), żeby oddać całą szerokość wykresom; rozwijasz go przyciskiem „Przebiegi treningu".

- **Tabela porównania** - metryki w wierszach, zaznaczone przebiegi w kolumnach. Jeden
  przebieg oznaczasz gwiazdką jako **baseline (★)**; przy pozostałych pojawia się wtedy
  **delta** względem niego (zielona = wyżej). Przełącznik **Tylko różnice** ukrywa metryki
  bez zmian.
- **Radar metryk** - szybkie porównanie „kształtu" kilku przebiegów naraz (`mAP50-95`,
  `mAP50`, precyzja, czułość i `F1` w skali 0–1). `F1` to średnia harmoniczna precyzji i
  czułości - jedna liczba na ich równowagę.
- **Radar per klasa** - obok radaru ogólnego; ten sam zestaw osi, ale dla **jednej klasy
  wybranej z listy** (klas może być wiele, więc wybierasz je z rozwijanej listy zamiast
  pokazywać wszystkie naraz). Pozwala porównać przebiegi na konkretnej, np. słabej klasie.
- **Krzywe treningu** - nakładka krzywych (strata, `mAP`, precyzja, czułość) po epokach dla
  zaznaczonych przebiegów.

Każdy wykres ma ikonę **Kopiuj PNG** (schowek) - do wklejenia do raportu.

Klik w nazwę przebiegu ustawia **focus** - poniżej rozwija się jego szczegół: metryki per
klasa, macierz pomyłek i ocena na teście (opisane niżej). W nagłówku szczegółu jest też
**Otwórz folder** (wersja desktop) - otwiera katalog runu z wagami, `results.csv` i manifestami.

!!! info "Porównanie bez baseline też działa"

    Baseline służy tylko do liczenia delt. Bez niego widzisz surowe metryki obok siebie -
    delta pojawia się dopiero, gdy wskażesz punkt odniesienia gwiazdką.

!!! tip "Podgląd na żywo w trakcie treningu"

    Zaznacz **trwający** przebieg, a **krzywe treningu** (strata, `mAP`, precyzja, czułość)
    będą rosły na żywo, odświeżane co ~3 s, a tabela porównania pokaże **metryki z ostatniej
    epoki** - jeszcze zanim trening się zakończy. Nie musisz zostawać w zakładce treningu.

![Radar metryk, radar per klasa i krzywe treningu dla trzech zaznaczonych przebiegow](../assets/images/training-results-comparison.png)
*Trzy przebiegi zestawione na jednym datasecie.*

## Które metryki czytać

**mAP50** jest łagodniejsza - wystarczy przybliżone trafienie w obiekt.

**mAP50-95** uśrednia po wielu progach dopasowania i karze za niedokładne obrysy. Jest
bardziej wymagająca i zwykle lepiej odzwierciedla użyteczność praktyczną.

Do porównywania konfiguracji używaj **metryk walidacyjnych**. Zbiór testowy zostaw na
jedną ocenę końcową.

## Jednorazowa ocena na zbiorze testowym

Ocena na zbiorze testowym jest wykonywana **raz na przebieg** i zapisywana jako wynik
finalny. Powtórzenie jest blokowane.

!!! danger "Wielokrotne zaglądanie do testu unieważnia jego sens"

    Zbiór testowy działa tylko dopóty, dopóki nie miał wpływu na Twoje decyzje. Gdy
    wybierasz model, patrząc na jego wyniki testowe, test staje się drugim zbiorem
    walidacyjnym - a ocena przestaje mówić cokolwiek o zachowaniu na nowych danych.

    Blokada nie jest ograniczeniem interfejsu. Jest zabezpieczeniem przed
    najczęstszym błędem metodycznym w tym miejscu.

Kolejność jest więc taka: iteruj po metrykach walidacyjnych, wybierz model, dopiero
wtedy sprawdź go raz na teście.

## Metryki per klasa

Wynik zbiorczy potrafi ukryć, że model dobrze radzi sobie z klasą dominującą, a
ignoruje rzadkie. Rozbicie na klasy pokazuje to wprost.

Klasa o wyraźnie gorszym wyniku zwykle znaczy jedno z trzech: za mało przykładów,
niekonsekwentne oznaczanie albo mylenie z klasą podobną.

## Macierz pomyłek

To ostatnie - mylenie klas - pokazują dwa uzupełniające się widoki, pod metrykami, dla
przebiegu z zapisanymi danymi walidacji.

**Macierz pomyłek** (interaktywna, zamiast statycznego obrazu z YOLO): wiersze = predykcja,
kolumny = prawda; przekątna to trafienia, a ostatni wiersz/kolumna to **tło** - pominięcia
(FN) i fałszywe wykrycia (FP). Przełączniki:

- **Normalizuj** - udział w kolumnie (dla danej klasy prawdziwej), jak w obrazie z YOLO
  (domyślnie), albo zliczenia surowe;
- **Log** - skala logarytmiczna dla szerokiego zakresu wartości;
- **Grupowanie** - kolejność klastrująca (mylone klasy sąsiadują) albo oryginalna.

Oryginalny obraz z YOLO pobierzesz przyciskiem **Pobierz PNG**.

Obok jest **widok podobieństwa klas** - cieplejszy kafel oznacza parę klas, którą model
myli częściej; im chłodniejszy, tym rzadziej.

![Macierz pomyłek z klasami uporządkowanymi w bloki i wyróżnionymi gorącymi parami](../assets/images/training-confusion-matrix.png)
*Mylące się klasy sąsiadują, więc gorące pary układają się przy przekątnej.*

Klasy nie są ułożone alfabetycznie, lecz **pogrupowane** tak, by pary, które model myli,
sąsiadowały ze sobą. Dzięki temu przy dużej liczbie klas problem widać jako zwarte bloki
przy przekątnej, a nie rozrzucone pojedyncze kafle. Pod macierzą jest lista
**najczęściej mylonych par**.

Trzy przełączniki sterują czytelnością:

- **Grupowanie** - układ pogrupowany (domyślny) albo oryginalna kolejność klas;
- **Log** - skala logarytmiczna, wydobywa słabe, rzadkie pomyłki;
- **Przekątna** - przekątna to trafienia własne klasy; jej ukrycie odsłania same pomyłki.

!!! info "Macierz pokazuje, że pomyłka istnieje - nie dlaczego"

    Dwie klasy mogą się mylić, bo obiekty są naprawdę podobne, bo część etykiet jest
    błędna, albo bo różni je sensor, a nie treść. Rozstrzyga to dopiero obejrzenie
    przykładów. Decyzja o scaleniu klas czy zmianie taksonomii należy do Ciebie -
    macierz jest wskazówką, gdzie patrzeć, nie zaleceniem, co zrobić.

## Diagnostyka wydajności przebiegu

Folder przebiegu zawiera `training_performance.json`. Jest to raport techniczny do
porównywania szybkości i wykorzystania zasobów, a nie metryka jakości modelu. Zawiera
między innymi:

- czas epoki i przybliżoną liczbę obrazów na sekundę;
- efektywny batch, liczbę workerów i cache;
- szczytowe RSS procesu oraz jego drzewa procesów;
- szczytowe VRAM i średnie wykorzystanie GPU, jeśli urządzenie udostępnia pomiar;
- `data_wait` o semantyce `inter_batch_callback_gap_proxy` - wskaźnik przerw między
  batchami, a nie bezpośredni pomiar czasu samego DataLoadera.

Porównuj telemetrię tylko dla przebiegów na tym samym sprzęcie, datasecie i rozmiarze
obrazu. Wyższe obrazy/s nie oznaczają lepszego modelu; do wyboru modelu nadal służą
metryki walidacyjne i ocena testowa.

## Kasowanie przebiegu

Ikona kosza przy przebiegu w railu **trwale** usuwa go z dysku (wagi, logi, manifest) -
bez kosza. Aktywnego przebiegu nie da się skasować; anuluj go najpierw.

Obowiązuje **miękka bramka rodowodu**: jeśli z przebiegu zarejestrowano model, okno
wymaga świadomego potwierdzenia. Po usunięciu wpisy tych modeli znikają, a jeśli któryś
był modelem projektu - wskaźnik modelu projektu zostaje wyczyszczony (predykcja nie będzie
wskazywać na nieistniejące wagi).

!!! tip "Do czego to służy"

    Wagi zajmują najwięcej miejsca - kasuj nieudane albo słabsze przebiegi, których nie
    zarejestrowałeś jako model. Przebieg stojący za promowanym modelem zwykle zostawiasz.

## Następny krok

Model wart zachowania [zarejestruj i promuj](rejestr-modeli.md).
