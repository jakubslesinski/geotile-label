# Relinkuj źródło

**Cel.** Wskazać nowe położenie plików źródłowych bez utraty scen i adnotacji.

**Kiedy.** Gdy sceny mają status **brak źródła** - po zmianie litery dysku,
przeniesieniu danych albo otwarciu projektu na innym stanowisku.

## Zanim zaczniesz

Sprawdź, że nośnik jest podłączony i widoczny w systemie. Relinkowanie wskazuje nową
ścieżkę, ale nie odtworzy plików, których nie ma.

## Kroki

1. Otwórz projekt i przejdź do listy źródeł.
2. Przy źródle ze statusem niedostępnym wybierz **Relinkuj**.
3. Wskaż folder zawierający te same paczki, co poprzednio.
4. Przejrzyj **sprawdzenie zgodności** - patrz niżej.
5. Potwierdź.

Weryfikacja tożsamości sceny i brakujących piramid odbywa się jako zadanie w tle.
Scena może chwilowo pokazywać status oczekiwania; nie uruchamiaj w tym czasie drugiego
relinkowania tego samego źródła.

## Sprawdzenie zgodności przed zatwierdzeniem

Wskazanie folderu nie zmienia jeszcze niczego. Aplikacja najpierw porównuje wskazane
miejsce z tym, co zapisała podczas importu, i pokazuje wynik. Dopiero potwierdzenie
przepina źródło.

Gdy wskazany folder **nie odpowiada** zarejestrowanej dostawie, relinkowanie zostaje
odrzucone razem z raportem niezgodności - zamiast przepiąć projekt na inne dane
i zostawić Cię z adnotacjami opisującymi coś innego.

Samo przepięcie jest **transakcyjne**: albo przechodzą wszystkie sceny źródła, albo
żadna. Przerwane w połowie relinkowanie nie zostawia projektu w stanie mieszanym.

## Punkt kontrolny

Sceny wracają do statusu **gotowa**, a adnotacje pozostają na swoich miejscach.

Jeżeli korzystasz z zewnętrznych piramid, przenieś `scene.tif.ovr` razem z
`scene.tif`. Brak sidecara nie zmienia tożsamości pikseli i nie usuwa adnotacji -
aplikacja może odbudować piramidę. Podmiana samego `.ovr` unieważnia tylko cache
wyświetlania.

!!! info "Dlaczego adnotacje przeżywają przeniesienie"

    Aplikacja rozpoznaje paczki po stabilnym identyfikatorze źródła i sumach
    kontrolnych produktów, a nie po ścieżce. Dopóki wskazujesz te same pliki, ich
    nowe położenie nie ma znaczenia.

## Gdy relinkowanie nie pomaga

**Część scen nadal ma „brak źródła".** Wskazany folder nie zawiera wszystkich paczek.
Sprawdź, czy przeniesione zostało całe źródło, a nie jego fragment.

**Sceny mają status „źródło zmienione".** Pliki zostały znalezione, ale ich zawartość
różni się od zapisanej podczas importu.

!!! warning "Źródło zmienione to nie to samo co przeniesione"

    Ten status oznacza, że **piksele produktu są inne** niż w chwili labelowania.
    Aplikacja blokuje wtedy automatyczne użycie adnotacji, ponieważ ramki są zapisane
    w pikselach - mogłyby wskazywać inne miejsce w terenie.

    Zanim ruszysz dalej, ustal, co się stało: czy plik został podmieniony celowo, czy
    to inna wersja produktu tej samej akwizycji. Szczegóły w
    [Statusach scen](../reference/statusy-scen.md).

## Odświeżanie nie kasuje scen

Ponowne skanowanie źródła **nie usuwa** scen, których chwilowo nie widać. Zostają ze
statusem **brak źródła** wraz z adnotacjami. Odłączenie dysku zewnętrznego nie niszczy
pracy.
