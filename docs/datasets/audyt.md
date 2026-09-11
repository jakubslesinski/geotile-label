# Audyt datasetu

**Cel.** Sprawdzić, czy wersja datasetu nadaje się do treningu, zanim zaczniesz
trenować.

**Kiedy.** Po wygenerowaniu wersji, przed [publikacją](publikowanie.md).

## Gotowość

Wynik audytu sprowadza się do jednego z trzech stanów:

| Stan | Znaczenie | Co zrobić |
| --- | --- | --- |
| **ready** | brak problemów blokujących | można trenować |
| **ready_with_warnings** | są ostrzeżenia | oceń je świadomie przed treningiem |
| **not_ready** | są błędy | popraw przed użyciem |

!!! warning "„ready_with_warnings" to nie „prawie ready""

    Ostrzeżenie oznacza sytuację, której aplikacja nie potrafi rozstrzygnąć za Ciebie
    - na przykład niesprawdzone obszary użyte w datasecie. Może być akceptowalne albo
    może unieważniać wynik treningu. Różnicę zna tylko ktoś, kto zna materiał.

## Co jest sprawdzane

**Integralność podziału** - czy zbiory uczący, walidacyjny i testowy są rozłączne i
mają sensowne proporcje.

**Przeciek przestrzenny** - czy ten sam teren nie trafił do różnych zbiorów, czy między
nimi nie ma podobnych kafelków oraz czy kafelki uczące nie **sąsiadują** bezpośrednio z
walidacyjnymi i testowymi.

**Pokrycie akwizycji** - czy każda konfiguracja akwizycji obecna w zbiorze walidacyjnym
lub testowym występuje też w uczącym.

**Pokrycie przeglądem** - ile komórek pozostało niesprawdzonych i czy dataset korzysta
z obszarów, których nikt nie obejrzał albo które wykluczono technicznie.

**Kompletność** - klasy zdefiniowane, ale nieużyte; klasy obecne tylko w źródle; sceny
bez adnotacji; brakujące metadane i pliki towarzyszące.

## Przeciek przestrzenny

Najważniejsza kontrola dla danych satelitarnych i najczęstsza przyczyna wyniku, który
wygląda świetnie, a nie sprawdza się w praktyce.

!!! danger "Wynik po przecieku jest bezużyteczny, nie tylko zawyżony"

    Gdy model widział ten sam teren w nauce i w teście, metryki mówią o zapamiętaniu,
    a nie o zdolności do generalizacji. Nie da się z nich wnioskować, jak model
    zadziała na nowej scenie - a to zwykle jedyne, co naprawdę interesuje.

Zwykle wystarczy zmienić strategię podziału na scenową albo blokową i wygenerować
wersję ponownie. Patrz [Zbuduj dataset](zbuduj-dataset.md).

Osobne ostrzeżenie dotyczy **sąsiedztwa**: kafelki uczące leżące tuż przy walidacyjnych
lub testowych. Nawet gdy zbiory są rozłączne, stykające się kafelki pokazują niemal ten
sam teren tuż za granicą - to łagodniejsza odmiana przecieku. Podziały blokowe utrzymują
sąsiadów w jednym zbiorze i redukują ten efekt.

## Pokrycie akwizycji

Sprawdzenie istotne zwłaszcza dla danych **SAR**. Ostrzeżenie pojawia się, gdy jakaś
konfiguracja akwizycji trafia do zbioru walidacyjnego lub testowego, a nie ma jej w
uczącym.

Konfigurację akwizycji wyznaczają warunki obserwacji: dla SAR polaryzacja, kierunek
spojrzenia i kąt padania; dla EO tryb przetwarzania i zachmurzenie. Model oceniany na
geometrii, której nie widział w nauce, dostaje wynik mówiący więcej o **różnicy sensora**
niż o zdolności rozpoznawania obiektu.

!!! info "Brak metadanych akwizycji wyłącza to sprawdzenie"

    Gdy sceny nie niosą metadanych akwizycji, kontrola jest pomijana, a nie zgłaszana
    jako błąd.

## Niesprawdzone obszary

Audyt osobno zgłasza sytuację, w której dataset korzysta z komórek nieobejrzanych
przez człowieka.

Kafelek z takiego obszaru może zawierać nieoznaczony obiekt. Model dostaje wtedy
sprzeczny sygnał: obiekt jest na obrazie, ale nie ma go w etykietach - czyli uczy się,
że **nie należy go wykrywać**.

Rozwiązania są dwa: dokończyć przegląd albo zawęzić dataset filtrami.

## Raport

Ostrzeżenia i rekomendacje pokazywane są w języku interfejsu. Raport można zapisać
jako **JSON** albo **CSV** - przydaje się do archiwizacji razem z wersją datasetu i
do rozmowy z zespołem o tym, co poprawić.

!!! tip "Audyt przed publikacją, nie po treningu"

    Trening kosztuje czas i pamięć karty. Wszystkie problemy, które wykrywa audyt,
    są tańsze do naprawienia przed nim niż po.

## Powiązane

- [Statystyki datasetu](statystyki.md) - rozkłady i wykorzystanie scen
- [Zawartość datasetu](zawartosc.md) - obejrzeć zgłoszone przypadki na kafelkach
- [Publikowanie wersji](publikowanie.md) - kolejny krok po zaliczonym audycie
