# Architektury bazowe

Lista architektur dostępnych w [zakładce Trening](../training/uruchom-trening.md).
Trening zawsze startuje z wag bazowych - aplikacja nigdy nie pobiera ich sama, więc
muszą zostać wcześniej przygotowane wraz z
[pakietem treningowym GPU](../getting-started/pakiet-gpu.md).

## Dostępne rodziny

Każda rodzina występuje w trzech rozmiarach: **n** (najmniejszy), **s** i **m**.
Kolumna „parametry" podaje wielkość modelu w milionach parametrów - im większy, tym
wolniejszy trening i większe zapotrzebowanie na pamięć karty.

| Architektura | Zadanie | Parametry (n / s / m) |
| --- | --- | --- |
| YOLO11 | detekcja | 2,6 / 9,4 / 20,1 M |
| YOLO11-OBB | ramki zorientowane | 2,7 / 9,7 / 20,9 M |
| YOLOv8 | detekcja | 3,2 / 11,2 / 25,9 M |
| YOLOv8-OBB | ramki zorientowane | 3,2 / 11,5 / 26,5 M |
| YOLOv10 | detekcja | 2,8 / 8,1 / 16,6 M |
| YOLO12 | detekcja | 2,6 / 9,3 / 20,2 M |
| YOLO12-OBB | ramki zorientowane | 2,7 / 9,6 / 21,0 M |
| YOLO26 | detekcja | 2,6 / 10,0 / 21,9 M |
| YOLO26-OBB | ramki zorientowane | 2,7 / 10,6 / 23,6 M |

## Geometria projektu decyduje o wyborze

Lista jest filtrowana przez typ geometrii ustawiony w profilu projektu:

| Geometria projektu | Dostępne architektury |
| --- | --- |
| Ramki osiowe | warianty detekcji |
| Ramki zorientowane | warianty OBB |

Architektury niepasujące do projektu pozostają widoczne, ale **wygaszone wraz z
podaniem powodu**. Nie są ukrywane - brak modelu na liście zawsze ma czytelne
wyjaśnienie.

!!! info "Dlaczego nie ma YOLOv10-OBB"

    Nie każda rodzina ma wariant dla ramek zorientowanych. YOLOv10 nie został
    opublikowany w takiej odmianie, dlatego w projekcie z ramkami zorientowanymi ta
    rodzina nie pojawi się wcale.

## Trening od zera

**YOLO12-OBB** jest wyjątkiem: architektura istnieje, ale nie opublikowano dla niej
wag wstępnie trenowanych. Aplikacja udostępnia ją z oznaczeniem **od zera** - sieć
zaczyna naukę od losowej inicjalizacji.

!!! warning "Trening od zera daje wyraźnie słabsze wyniki"

    Wagi wstępnie trenowane niosą wiedzę z dużego zbioru ogólnego, dzięki czemu model
    uczy się nowych klas z relatywnie małej liczby przykładów. Bez nich potrzeba
    znacznie większego datasetu i znacznie dłuższego treningu, żeby osiągnąć
    porównywalną jakość. Wybieraj tę opcję świadomie, a nie dlatego, że jest to
    najnowsza dostępna architektura.

Pozycje „od zera" nie wymagają żadnych plików wag i są dostępne nawet wtedy, gdy
katalog wag jest pusty.

## Licencja

Wszystkie wagi bazowe pochodzą od Ultralytics i są objęte licencją **AGPL-3.0**.

!!! danger "Zastosowanie komercyjne wymaga osobnej licencji"

    Model wytrenowany z tych wag dziedziczy zobowiązania licencyjne. Zastosowanie
    komercyjne wymaga licencji Ultralytics Enterprise. Dotyczy to również pozycji
    trenowanych od zera - wag się wtedy nie dziedziczy, ale kod treningowy nadal jest
    objęty AGPL-3.0.

    Licencja jest pokazywana w aplikacji pod wyborem architektury. Przed wdrożeniem
    modelu w produkcie skonsultuj to z osobą odpowiedzialną za zgodność prawną.

## Który wariant wybrać

Rozmiar **n** nadaje się do szybkiego sprawdzenia, czy dataset w ogóle się uczy.
Rozmiary **s** i **m** dają zwykle lepszą jakość kosztem czasu i pamięci karty.

Nie zakładaj z góry, że nowsza rodzina jest lepsza dla Twoich danych. Zakładka
[Porównanie wyników](../training/wyniki.md) służy właśnie do sprawdzenia tego na
jednym datasecie, zamiast opierać się na ogólnych zestawieniach.
