# SAM - klik-w-ramkę i tekst

**Cel.** Zamienić kliknięcie (albo prompt tekstowy) w gotowy obrys obiektu, żeby nie
rysować ramek od zera. SAM podpowiada **kształt** - klasę nadajesz sam.

**Kiedy.** Pojedyncze obiekty o wyraźnej krawędzi (klik), albo wskazanie wszystkich
obiektów danego typu w kadrze jednym poleceniem (tekst), gdy nie masz modelu detekcji.

**Wymagania.** Wskazany model SAM (plik `.pt`). Tryb tekstowy wymaga **SAM3**. Wszystko
działa offline, na procesorze; duży model DINO/SAM3 zyskuje na GPU.

## Modele

Model SAM wybierasz w panelu **Predykcja** (albo prawym-klikiem na ikonie SAM w panelu AI
na mapie), z możliwością wskazania własnego folderu wag. Obsługiwane rodziny:

| Rodzina | Przykładowe wagi | Uwagi |
| --- | --- | --- |
| SAM1 | `mobile_sam`, `sam_b/l/h` | lekki, uniwersalny |
| SAM2 | `sam2.1_t/s/b/l` | nowsza generacja |
| **SAM3** | `sam3.pt` | wymagany do **trybu tekstowego** |
| FastSAM | `FastSAM-s/x` | szybki, mniej dokładny |

!!! info "SAM3 potrzebuje pełnego runtime"

    Tryb tekstowy działa tylko z modelem SAM3 i odpowiednim środowiskiem (enkoder tekstu).
    Gdy go brak, narzędzie tekstowe jest **wygaszone z czytelnym powodem** - nie zawiesza się
    ani nie zgłasza „network error".

## Klik-w-ramkę

1. Ustaw **aktywną klasę**.
2. Włącz narzędzie **SAM** w panelu AI na mapie (ikona różdżki).
3. **Kliknij na obiekcie** - pojawia się propozycja ramki (lub ramki skośnej, zależnie od
   trybu adnotacji).

<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="Kliknięcie na pojeździe i wynikowa ramka SAM">
  <source src="../assets/images/sam-click-to-box.mp4" type="video/mp4">
</video>
*Jedno kliknięcie zamienia się w obrys pojedynczego obiektu.*

!!! tip "SAM wybiera pojedynczy obiekt, nie grupę"

    Na gęstych scenach satelitarnych klik potrafił złapać całą kępę sąsiednich obiektów.
    Narzędzie wybiera teraz maskę **pojedynczego obiektu** - po jakości i dopasowaniu
    rozmiaru - zamiast największej powierzchni.

!!! tip "Zaznaczona adnotacja podpowiada rozmiar"

    Jeśli masz **zaznaczoną adnotację tej samej klasy**, jej rozmiar staje się wskazówką
    wielkości dla kolejnych kliknięć. Pomaga, gdy obok siebie są obiekty różnej skali.

## Tryb tekstowy (SAM3)

1. Ustaw **aktywną klasę** i przybliż na interesujący fragment.
2. Kliknij **SAM3 tekst** w panelu AI - otwiera się okno promptu.
3. **Wpisz prompt** (pole jest wstępnie wypełnione nazwą klasy, ale możesz je zmienić).
4. Wybierz **Uruchom na bieżącym widoku** albo **Narysuj obszar** i zaznacz prostokąt.

<video controls autoplay loop muted playsinline style="width:100%;height:auto" title="Okno promptu SAM3 z polem tekstowym i wyborem obszaru">
  <source src="../assets/images/sam3-text-prompt.mp4" type="video/mp4">
</video>
*Prompt wpisujesz ręcznie; obszar to bieżący widok albo narysowany prostokąt.*

!!! warning "Wpisz naturalną frazę, nie kod klasy"

    SAM3 rozumie **otwarty słownik po angielsku**. Nazwa klasy bywa skrótem lub zawiera
    znaki jak `_` (np. `pojazd_transportowy`) - to słaby prompt. Wpisz naturalne określenie,
    np. `military vehicle`, `aircraft`, `ship`.

**Ustawienia trybu tekstowego:**

- **Próg pewności** (domyślnie 0.25) - na zobrazowaniach EO/SAR, zwłaszcza poglądowych,
  pewności bywają umiarkowane; niższy próg zwraca więcej kandydatów.
- **Obszar** - bieżący widok albo narysowany prostokąt; działa w **pełnej rozdzielczości**,
  więc obszar jest ograniczony (przy zbyt szerokim widoku pojawia się prośba „przybliż").

## Przegląd propozycji

Wynik obu trybów to **propozycje**, nie adnotacje. Przeglądasz je i przyjmujesz albo
usuwasz (także zbiorczo, z zaznaczeniem wielu przez ++ctrl++/++shift++). Ramki skośne
wymagają potwierdzenia kierunku grota.

!!! danger "Propozycja to jeszcze nie adnotacja"

    Nic nie wejdzie do datasetu, dopóki nie zaakceptujesz. Przyjęta propozycja zapamiętuje
    pochodzenie (wspomagana SAM), widoczne w audycie i rodowodzie.

## Typowe problemy

- **Brak modelu / SAM niedostępny** - wskaż wagi w panelu Predykcja.
- **Tryb tekstowy wygaszony** - potrzebny model SAM3 i jego runtime.
- **Klik łapie tło zamiast obiektu** - obiekt bez wyraźnej krawędzi; spróbuj trybu
  tekstowego albo dopasowania wzorca.

## Powiązane

- [Znajdź podobne](znajdz-podobne.md) - szablon i few-shot DINO
- [Predykcja YOLO](predykcja.md) - auto-label całej sceny modelem
- [Narzędzia AI](index.md) - cykl pracy i ograniczenia
