# Klasy

**Cel.** Zdefiniować listę klas obiektów używanych w projekcie.

**Kiedy.** Zaraz po utworzeniu projektu, przed rozpoczęciem labelowania.

## Import z pliku

1. Otwórz projekt.
2. W sekcji **Klasy** wybierz **Importuj z pliku**.
3. Wskaż `classes.json`.

## Format pliku

Tablica obiektów. Każda klasa ma identyfikator, nazwę, kolor i opcjonalny numer
skrótu:

```json
[
  { "id": 0, "name": "samolot_transportowy", "color": "#FF0000", "hotkey": 1 },
  { "id": 1, "name": "smiglowiec",           "color": "#00FF00", "hotkey": 2 },
  { "id": 2, "name": "samolot",              "color": "#0000FF", "hotkey": 3 }
]
```

| Pole | Znaczenie |
| --- | --- |
| `id` | identyfikator klasy, liczony od zera |
| `name` | nazwa używana w interfejsie i w eksportach |
| `color` | kolor ramek na mapie |
| `hotkey` | numer klawisza ++1++–++9++ wybierającego klasę |

!!! tip "Numery skrótów przydziel tym klasom, których używasz najczęściej"

    Klawisze działają wyłącznie dla klas z przypisanym numerem - nie po kolejności na
    liście. Klasy bez numeru wybiera się przez listę albo wyszukiwarkę (++k++).
    Skrótów jest dziewięć, więc przy dłuższej liście warto je zarezerwować dla
    obiektów dominujących w materiale.

## Dodawanie ręczne

Klasy można też dodawać pojedynczo przyciskiem **Dodaj klasę**. Przy dłuższych listach
wygodniej przygotować plik i zaimportować go - ten sam plik można wtedy rozesłać
zespołowi.

Nowa klasa dostaje kolejny kolor z palety domyślnej. Kwadrat z próbką po lewej stronie
nazwy otwiera wybór: paleta, systemowe koło barw albo wpisanie wartości HEX. Ten sam
kwadrat przy klasie już istniejącej zmienia jej kolor.

!!! info "Kolor zapisuje się dopiero po zatwierdzeniu"

    Wybór trzeba potwierdzić przyciskiem **Zatwierdź**. Zmiana koloru jest zapisem do
    pliku klas projektu, więc samo klikanie po palecie niczego nie utrwala. Kolor dotyczy
    wyłącznie prezentacji: nie zmienia adnotacji ani eksportów.

## Uzgodnienie w zespole

!!! warning "Wszyscy analitycy muszą mieć ten sam plik klas"

    Nazwa klasy jest tym, po czym import paczki dopasowuje adnotacje. Rozjazd w
    nazewnictwie - nawet drobny, jak liczba mnoga albo inny zapis znaków - sprawia,
    że manager zobaczy przy imporcie **brakującą klasę** zamiast poprawnie
    dopasowanego obiektu.

Gdy paczka zawiera klasę nieznaną w projekcie docelowym, można ją utworzyć podczas
importu. Bez tej zgody **scena jest pomijana w całości**, żeby podmiana adnotacji nie
usunęła istniejących obiektów z powodu problemu z klasami.

## Zmiany po rozpoczęciu pracy

Dodanie nowej klasy w trakcie projektu jest bezpieczne. Zmiana nazwy istniejącej -
już nie: adnotacje wskazują na klasę, a eksporty i paczki niosą jej nazwę.

!!! tip "Nieużywane klasy wychodzą w audycie"

    Audyt datasetu pokazuje klasy zdefiniowane, ale nieużyte oraz obecne tylko w
    źródle. To dobry moment na uporządkowanie listy - przed treningiem, a nie po nim.
    Patrz [Audyt datasetu](../datasets/audyt.md).
