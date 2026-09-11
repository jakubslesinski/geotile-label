# Znajdź podobne

**Cel.** Oznaczyć jeden obiekt i znaleźć resztę tego samego typu na scenie - bez
rysowania każdego z osobna.

**Kiedy.** Gdy na scenie powtarza się ten sam obiekt (parking pojazdów, seria budynków,
flota jednostek). Zaznaczasz jeden i szukasz podobnych.

**Wymagania.** Co najmniej jedna zaznaczona adnotacja wzorcowa. Silnik **DINO** wymaga
dodatkowo wag DINO i najlepiej GPU; silnik **Szablon** działa offline na procesorze.

## Dwa silniki

| Silnik | Zasada | Kiedy |
| --- | --- | --- |
| **Szablon (NCC)** | korelacja pikseli/krawędzi | EO, szybko, offline |
| **DINO (few-shot)** | podobieństwo cech sieci DINO | semantycznie, EO **i SAR**, odporny na skalę/obrót/oświetlenie |

Silnik wybierasz w panelu **Predykcja** (sekcja „Silnik Znajdź podobne") - to **domyślne
ustawienie projektu** - albo doraźnie w menu narzędzia na mapie. Oba miejsca zapisują tę
samą wartość, więc się nie rozjeżdżają.

!!! info "DINO to few-shot bez trenowania"

    DINO zamienia wzorzec na wektor cech i szuka obiektów o podobnych cechach - **bez
    treningu modelu**. Jest domenowo mocniejszy niż szablon (zwłaszcza na materiale
    satelitarnym), ale liczy się wolniej; do interaktywnej pracy zakładany jest GPU.

## Kroki

1. Zaznacz adnotację, która ma być **wzorcem**. Możesz zaznaczyć **kilka** (++ctrl++ -
   pojedynczo, ++shift++ - zakres) - silnik **DINO** uśredni je w jeden prototyp few-shot.
2. W panelu AI na mapie kliknij **Znajdź podobne** (menu obok przycisku = wybór silnika i
   zakresu).
3. Przejrzyj propozycje i przyjmij albo usuń (także zbiorczo, z multi-selekcją).

<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="Wzorzec i znalezione podobne obiekty jako propozycje">
  <source src="../assets/images/find-similar.mp4" type="video/mp4">
</video>
*Jeden zaznaczony obiekt, reszta znaleziona automatycznie.*

## Zakres wyszukiwania

Wspólny dla obu silników:

- **Lokalnie** - okolica wzorca; najszybsze.
- **Bieżący widok** - to, co widać na mapie.
- **Cała scena** - pełne zobrazowanie (najdłużej; dla DINO na CPU może być bardzo wolne).

!!! tip "Zawężaj zakres na dużych scenach"

    Przeszukiwanie całej sceny trwa i zwraca więcej fałszywych trafień. Bieżący widok zwykle
    daje lepszy wynik szybciej.

## Ustawienia silnika Szablon

- **Minimalne podobieństwo** (próg) - wyżej = mniej, pewniejszych trafień.
- **Tolerancja skali** i **tolerancja obrotu** - o ile obiekty mogą różnić się od wzorca.
- **Krawędzie** - dopasowanie po krawędziach zamiast surowych pikseli; pomaga przy zmiennym
  oświetleniu.

## Ustawienia silnika DINO

- **Wagi DINO** - w panelu Predykcja wskazujesz katalog i wariant. Domyślnie preferowany
  jest **DINOv3-SAT** (wariant satelitarny) - mocniejszy na EO/SAR; można wybrać lżejszy
  DINOv2 (szybszy na CPU) albo własny plik.
- **Próg podobieństwa** - jak wyżej, cosine cech (zakres suwaka 0,2–1,0).
- **Kilka wzorców (few-shot)** - zaznacz kilka przykładów tej samej klasy przed
  uruchomieniem. Prototyp uśredniony z kilku obiektów jest odporniejszy na wariancję
  (różne ujęcia, cienie, orientacje) i zwykle daje mniej fałszywych trafień niż pojedynczy.

!!! tip "Jak dobrać wzorce dla lepszych wyników"

    Wybieraj przykłady **zróżnicowane** (różna orientacja, tło, jasność), nie kilka
    identycznych. 2–4 dobrze dobrane wzorce zwykle wystarczą; wzorzec nietypowy lub
    częściowo zasłonięty raczej rozmywa prototyp, niż pomaga.

!!! warning "Jakość zależy od wariantu i skali obiektów"

    Na bardzo drobnych obiektach lekki DINOv2 słabo różnicuje. Dla materiału satelitarnego
    używaj wariantu **SAT** i dostrajaj próg; wynik to **ramki** (obrys przenoszony z wzorca).

## Przegląd propozycji

Wynik to **propozycje**, nie adnotacje - przyjmujesz albo usuwasz. Przyjęta zapamiętuje
pochodzenie (wspomagana wzorcem / DINO), widoczne w audycie i rodowodzie.

!!! danger "Propozycja to jeszcze nie adnotacja"

    Nic nie trafia do datasetu bez akceptacji. „Zaakceptuj wszystkie" stosuj po obejrzeniu
    wyniku, nie zamiast niego.

## Powiązane

- [SAM - klik i tekst](sam.md) - obrys pojedynczego obiektu i tryb tekstowy
- [Predykcja YOLO](predykcja.md) - auto-label modelem
- [Analiza datasetu](../datasets/analiza.md) - te same cechy DINO w rewizji etykiet
