# Workflow analityka

**Cel.** Przekazać swoją pracę managerowi i wprowadzić poprawki po recenzji.

**Rola projektu.** Labelowanie.

Analityk pracuje na przydzielonych **pełnych scenach**. Nie tworzy kafelków ani
datasetów - to zadanie projektu zbiorczego.

## Wyślij pracę

1. Otwórz pulpit projektu i wybierz **Eksportuj paczkę adnotacji**.
2. Sprawdź podsumowanie: autora, liczbę scen, adnotacji, klas oraz ostrzeżenia
   dotyczące scen `NO GEO`.
3. Jeśli w podsumowaniu widzisz przycisk **Przygotuj paczkę**, kliknij go i poczekaj
   na pasek postępu. To jednorazowy etap dla danej sceny - patrz niżej.
4. Zapisz plik `GeoTileAnnotations_<projekt>_<autor>_<data>.zip` i przekaż go
   managerowi.

**Punkt kontrolny.** Podsumowanie pokazuje ten adres autora, którym posługujesz się w
zespole, i spodziewaną liczbę scen.

!!! warning "Sprawdź adres autora przed pierwszym eksportem"

    Adres z profilu projektu decyduje o tym, czyją pracę podmieni import. Literówka
    tworzy w projekcie zbiorczym „drugiego analityka", którego poprawki nie zastąpią
    wcześniejszych obiektów, tylko dołożą się obok. Patrz
    [Role projektu](role-projektu.md).

## Przygotowanie paczki

Paczka niesie adnotacje bez obrazów, więc po drugiej stronie musi jednoznacznie wskazać,
do którego pliku należą. Służy do tego **dokładny odcisk sceny źródłowej**: pełna suma
kontrolna rastra, a nie sama nazwa czy rozmiar. Tylko ona odróżnia dwa przetworzenia tego
samego zobrazowania - a właśnie takie pliki wyglądają podobnie i mają identyczne nagłówki.

Import scen liczy szybki odcisk próbkowany, który do tego nie wystarcza. Dlatego przy
pierwszym eksporcie z projektu podsumowanie proponuje **Przygotuj paczkę**: aplikacja
czyta wtedy pliki źródłowe scen i uzupełnia brakujące odciski.

- **koszt** - jednorazowy odczyt plików źródłowych, pokazany przed startem: liczba scen
  i objętość do przeczytania. Postęp idzie w bajtach, a zadanie można przerwać;
- **jednorazowość** - wynik zostaje zapamiętany przy niezmienionym pliku, więc kolejna
  paczka z tego projektu nie czyta już nic;
- **kiedy wraca** - po podmianie źródła, relinku albo zmianie wariantu roboczego, bo
  wtedy odcisk przestaje odpowiadać plikowi.

Etap dotyczy wyłącznie scen, którym odcisku brakuje. Gdy przycisku nie ma, wszystko jest
gotowe i przechodzisz prosto do zapisu.

## Co jest w paczce

Adnotacje źródłowe, klasy, manifesty scen, GeoParquet i sumy kontrolne. **Nie ma
obrazów scen, kafelków ani datasetów** - dlatego paczka jest lekka.

Paczka deklaruje też **zakres**: dla którego autora i których scen jest kompletem,
kiedy powstała i którą wcześniejszą paczkę zastępuje. Łańcuch buduje się automatycznie
- kolejny eksport tych samych scen wskazuje poprzedni jako zastąpiony.

!!! tip "Eksportuj całe sceny, nie fragmenty pracy"

    Paczka jest **kompletem dla danej sceny**, a nie przyrostem. Import podmienia
    zawartość sceny w całości, więc wysłanie jej w trakcie pracy jest bezpieczne -
    kolejna paczka zastąpi poprzednią.

## Gdy wróci recenzja

1. Wybierz **Importuj recenzję** i wskaż plik od managera.
2. Werdykty pojawią się w kolumnie **Recenzja** w katalogu scen.
3. Popraw sceny oznaczone do poprawy.
4. Wyeksportuj nową paczkę adnotacji.

!!! info "Import recenzji nie zmienia ani jednej adnotacji"

    Paczka recenzji niesie wyłącznie werdykty i komentarze. Nie ma w niej geometrii,
    więc nic w Twoich scenach się nie przesunie ani nie zniknie. Poprawki wprowadzasz
    sam - recenzja tylko wskazuje, gdzie.

## Werdykty

| Werdykt | Co znaczy |
| --- | --- |
| **Zaakceptowana** | scena przyjęta, nic nie trzeba robić |
| **Do poprawy** | wróć do sceny zgodnie z komentarzem |
| **Odrzucona** | praca w tej scenie wymaga zasadniczej zmiany |

Werdykt dotyczy **całej sceny**, nie pojedynczych obiektów - szczegóły są w
komentarzu.

## Rytm pracy

```text
labeluj → eksportuj paczkę → (recenzja) → popraw → eksportuj ponownie
```

Nie czekaj z eksportem do końca całego zadania. Wcześniejsze paczki pozwalają
managerowi wychwycić rozjazd w interpretacji klas, zanim powtórzysz go na kilkunastu
scenach.

## Powiązane

- [Rodzaje paczek](../reference/rodzaje-paczek.md) - czym paczka adnotacji różni się
  od backupu
- [Siatka przeglądu](../annotation/siatka-przegladu.md) - kontrola pokrycia przed
  wysyłką
