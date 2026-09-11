# Narzędzia AI

Trzy narzędzia przyspieszające labelowanie. Wszystkie tworzą **propozycje**, a nie
adnotacje.

!!! danger "Propozycja staje się adnotacją dopiero po akceptacji"

    To zasada obowiązująca w całym rozdziale. Wynik narzędzia AI jest tymczasowy:
    nie wejdzie do datasetu, nie trafi do paczki adnotacji i nie zostanie policzony w
    statystykach, dopóki człowiek go nie przyjmie.

## Które narzędzie do czego

| Narzędzie | Kiedy | Czego wymaga |
| --- | --- | --- |
| **Predykcja YOLO** | masz model na podobny materiał | pliku `.pt` |
| **[SAM klik-w-ramkę](sam.md)** | pojedyncze obiekty, brak modelu | modelu SAM |
| **[SAM tekst](sam.md#tryb-tekstowy-sam3)** | wskaż wszystkie obiekty typu jednym promptem | modelu SAM3 |
| **[Znajdź podobne](znajdz-podobne.md)** | wiele podobnych obiektów na jednej scenie | zaznaczonej adnotacji wzorcowej |

Wszystkie działają na procesorze i są częścią instalacji bazowej. Silniki cięższe (SAM3
tekst, DINO few-shot) zyskują na GPU. Pakiet GPU jest wymagany wyłącznie do **trenowania**
własnych modeli.

## Predykcja YOLO

Analizuje całą scenę i zgłasza obiekty znanych modelowi klas. Najbardziej wydajna,
gdy dysponujesz modelem wytrenowanym na zbliżonym materiale.

Szczegóły: [Predykcja YOLO](predykcja.md).

## SAM - klik i tekst

Zamienia kliknięcie w obrys obiektu (**klik-w-ramkę**) albo znajduje wszystkie obiekty
opisane frazą (**tryb tekstowy**, wymaga SAM3). Nie zna klas projektu - podpowiada
**kształt**, klasę nadajesz sam. Model SAM wybierasz w panelu Predykcja; można wskazać
własny folder wag.

Szczegóły: [SAM - klik-w-ramkę i tekst](sam.md).

## Znajdź podobne

Szuka obiektów podobnych do zaznaczonej adnotacji - zaznaczasz jeden i szukasz reszty.
Dwa silniki: **Szablon (NCC)** (klasyczny, offline) i **DINO (few-shot)** (semantyczny,
EO i SAR, zalecane GPU). Silnik ustawiasz w panelu Predykcja albo w menu narzędzia na
mapie. Zakres wyszukiwania: okolica, bieżący widok albo cała scena.

Szczegóły: [Znajdź podobne](znajdz-podobne.md).

!!! tip "Zawężaj zakres, gdy scena jest duża"

    Przeszukiwanie całej sceny przy dużym zobrazowaniu trwa i zwraca więcej fałszywych
    trafień. Ograniczenie do bieżącego widoku zwykle daje lepszy wynik szybciej.

## Cykl pracy

1. **Propozycja** - narzędzie zgłasza kandydatów.
2. **Kontrola** - przeglądasz wynik.
3. **Korekta** - poprawiasz geometrię albo klasę tam, gdzie trzeba.
4. **Decyzja** - akceptujesz albo usuwasz.

Dopiero czwarty krok tworzy adnotację kanoniczną.

!!! info "Co zostaje po akceptacji"

    Przyjęta propozycja zapisuje informację o swoim pochodzeniu - że powstała z
    narzędzia AI, a nie została narysowana ręcznie. Dzięki temu w audycie i w rodowodzie
    modelu widać, jaka część materiału pochodzi z podpowiedzi.

## Ograniczenia jakościowe

Narzędzia AI działają najlepiej na materiale zbliżonym do tego, na którym powstały.

- model z zobrazowań optycznych **nie zadziała** na SAR i odwrotnie;
- inna rozdzielczość terenowa niż w treningu obniża skuteczność;
- SAR bywa trudniejszy dla SAM-a niż obraz optyczny, bo krawędzie obiektów są mniej
  wyraźne.

!!! warning "Narzędzie AI nie zastępuje przeglądu sceny"

    Brak propozycji w danym miejscu nie znaczy, że nic tam nie ma. Kontrolę pokrycia
    prowadź [siatką przeglądu](../annotation/siatka-przegladu.md), a nie liczbą
    propozycji.
