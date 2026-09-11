# Analiza datasetu

**Cel.** Zobaczyć **strukturę zbioru zanim ruszy trening** - które klasy są do siebie
wizualnie podobne, które obiekty wyglądają na źle opisane, gdzie są duplikaty i czy nie ma
przecieku między zbiorem uczącym a walidacyjnym. Bez trenowania modelu.

**Gdzie.** Osobna pozycja **Analiza datasetu** na pasku bocznym, między *Dataset* a *Trening*.
Liczy się na **oznaczonych obiektach projektu** (wycięte ramki), nie na gotowych kafelkach.

![Widok Analizy datasetu: macierz podobieństwa klas, kolejka podejrzanych etykiet i lista duplikatów](../assets/images/dataset-analysis-overview.png)
*Podobieństwo klas, podejrzane etykiety i near-duplikaty - wszystko z odnośnikiem do edytora.*

## Jak to działa

Każdy oznaczony obiekt jest zamieniany na **wektor cech** przez zamrożony backbone **DINO**
(model fundamentowy, bez dotrenowania). Na tych wektorach liczone są podobieństwa. To daje
sygnał o **jakości zbioru dostępny od razu** - nie trzeba czekać na wynik treningu.

!!! info "Potrzebne wagi DINO"

    Analiza wymaga wag DINO w katalogu modeli (`models/dino`, np.
    `dinov2_vits14_reg4_pretrain.pth`). Kod modelu jedzie z aplikacją - Ty dostarczasz tylko
    plik wag. Bez wag przycisk zgłosi czytelny powód, zamiast liczyć po cichu byle co.
    Wszystko działa **offline**. Na GPU liczy się szybciej, ale CPU też wystarcza.

!!! warning "Kod DINOv3 nie jest na licencji otwartej"

    Dołączane są dwa repozytoria backbone'u: `dinov2` (Apache-2.0) i `dinov3`. To drugie jest
    objęte **licencją DINOv3**, która pozwala na redystrybucję, ale wiąże każdego odbiorcę
    i zabrania zastosowań **wojskowych, wywiadowczych, jądrowych oraz objętych ITAR**. Pełna
    treść instalowana jest obok kodu. Jeżeli te warunki nie pasują do Twojego wdrożenia,
    warianty DINOv2 pokrywają tę samą analizę, tylko bez DINOv3-SAT.

Analiza chodzi **w tle** - pasek postępu pokazuje etapy (wczytanie backbone'u → embeddingi →
podobieństwo → duplikaty → przykłady klas). Wynik zostaje zapisany, więc wracasz do niego bez
ponownego liczenia (**Przelicz ponownie** odświeża).

Zadanie `embedding_analysis` jest trwałe: można opuścić widok, anulować je w
[Zadaniach w tle](../reference/zadania-w-tle.md) i wznowić od bezpiecznego checkpointu.
Wynik jest związany z rewizją adnotacji; zmiana obiektów wymaga przeliczenia.

## Skalowanie do dużych projektów

Dla mniejszych zbiorów podobieństwo i near-duplikaty są liczone dokładnie. Po
przekroczeniu bezpiecznego limitu pamięci aplikacja używa przybliżonego indeksu ANN i
ograniczonego `top-k`, zamiast budować pełną macierz wszystkich par w RAM.

!!! info "Co oznacza wyszukiwanie przybliżone"

    Najbliższe, najbardziej oczywiste duplikaty pozostają priorytetem, ale kolejność
    mniej podobnych kandydatów może minimalnie różnić się od pełnego porównania.
    Backend i zastosowany limit są zapisywane w wyniku analizy, aby przebieg był
    audytowalny.

## Co dostajesz

### Podobieństwo klas

Macierz cieplna par klas - **cieplejszy kafel = klasy, które DINO widzi jako wizualnie
bliższe**. To ten sam widget co macierz pomyłek z treningu, ale sygnał pochodzi z embeddingów,
więc jest dostępny **przed** treningiem. Klasy są ułożone tak, że mylące się rodziny sąsiadują.
Pod spodem lista najbardziej podobnych par.

Kafle mają **zaokrąglone rogi**, a przy niewielu klasach wpisaną w środku wartość podobieństwa.
Paleta jest **rozciągnięta na faktyczny zakres** wartości (podobieństwa z embeddingów leżą blisko
siebie, ~0.85–0.99), żeby różnice między klasami były widoczne, a nie zlane w jeden kolor.
**Legenda** pod macierzą podaje liczbowo dolny, środkowy i górny kraniec skali. Trzy przełączniki
sterują czytelnością: **Cluster order** (układ pogrupowany vs oryginalny), **Log** (skala
logarytmiczna), **Diagonal** (przekątna = podobieństwo klasy do siebie).

**Klik w kafel** poza przekątną - lub w wiersz listy podobnych par - **wybiera parę klas** i
otwiera dla niej [galerię przykładów](#przykady-klas) obok siebie; wybrana para jest podświetlona
w macierzy i na liście.

### Przykłady klas

Pod macierzą jest **galeria wycinków ze sceny** (ramka obiektu + trochę kontekstu) - żeby
zobaczyć, **na czym** polega podobieństwo, a nie tylko że istnieje. Wycinki są renderowane raz,
razem z analizą, i zapisane, więc ładują się szybko.

Dla każdej klasy przykłady są rozdzielone na dwie **podpisane grupy**:

- **typowe** - obiekty najbliższe „wzorcowi" klasy, czyli jak typowo wygląda ta klasa;
- **graniczne** - obiekty najbliższe **sąsiedniej, mylonej klasie** (podpis mówi, z którą); to
  właśnie one napędzają pomyłki.

Klik w kafel otwiera **powiększony podgląd**; strzałkami ← → przewijasz kolejne wycinki, a
kliknięcie poza podglądem (lub `Esc`) go zamyka.

Gdy wybierzesz parę klas (klikiem w macierzy lub na liście par), galeria pokazuje **oba zestawy
obok siebie** - bezpośrednie porównanie „A vs B" na konkretnych obiektach. Bez wyboru pary widać
paski wszystkich klas, w tej samej kolejności co macierz.

!!! info "Galeria wymaga ponownej analizy"

    Przykłady powstają podczas analizy embeddingów. Wyniki policzone starszą wersją aplikacji ich
    nie mają - uruchom **Przelicz ponownie**, żeby galeria się pojawiła.

![Najczesciej mylone pary oraz przyklady typowe i graniczne dla kazdej klasy](../assets/images/dataset-analysis-confused-pairs.png)
*Lista par i galeria przykladow pod macierza.*

### Spójność klas

Dla każdej klasy: **średnie podobieństwo jej obiektów do własnego „wzorca"**. Niska spójność
(albo duży rozrzut) oznacza klasę wizualnie niejednorodną - kandydatkę do rozbicia lub rewizji
definicji.

### Podejrzane etykiety

Obiekty **bliższe wzorcowi innej klasy niż własnej** - typowy objaw pomyłki w etykiecie.
Kolejka jest posortowana wg „pewności pomyłki" i pokazuje sugerowaną klasę. Przycisk **Popraw**
otwiera obiekt w edytorze.

### Obiekty odstające

Obiekty **daleko od wzorca własnej klasy**, ale nie bliżej obcej - trudne przypadki albo złe
wycięcia. Warte obejrzenia, choć niekoniecznie błędne.

### Near-duplikaty i przeciek train/val

Pary obiektów o **niemal identycznych cechach**: redundantne adnotacje lub - po włączeniu
sprawdzenia przecieku - **przeciek między zbiorami**.

Wykrywanie near-duplikatów działa **zawsze**. Osobna, opcjonalna kontrolka nad panelem -
**Sprawdzenie przecieku train/val** - pozwala wskazać **opublikowaną wersję datasetu**; wtedy
aplikacja mapuje każdy obiekt na jego zbiór (uczący / walidacyjny / testowy) tej wersji i oznacza
pary przecinające train↔val jako **przeciek** (czerwona plakietka i licznik). Bez wskazania wersji
nie ma podziału, więc widać tylko redundancję. Jeśli nie masz jeszcze opublikowanej wersji,
kontrolka podpowie, że trzeba ją najpierw [opublikować](publikowanie.md).

!!! warning "Przeciek train/val to cichy zabójca metryk"

    Jeśli ten sam (lub niemal ten sam) obiekt trafi do zbioru uczącego i walidacyjnego, model
    „widzi odpowiedzi" - wynik na walidacji jest zawyżony. Licznik **Przecieki train/val** i
    etykiety zbiorów na parach pokazują, gdzie to się dzieje. Poprawka: skoryguj adnotacje lub
    zmień tryb podziału i **wygeneruj nową wersję** datasetu.

### 20 najbardziej podobnych

Dla dowolnego obiektu (**Podobne**) - lista 20 najbliższych mu obiektów w całym zbiorze, każdy
z odnośnikiem do edytora. Szybkie domykanie klasy i wyłapywanie rozjazdów etykiet.

## Od wyniku do poprawki

Każda lista (etykiety, odstające, duplikaty, podobne) prowadzi **jednym kliknięciem do edytora**
ustawionego na danym obiekcie. To domyka pętlę *analiza → popraw adnotację*.

!!! info "Rekomendacje, nie decyzje"

    Analiza **uwidacznia** strukturę zbioru - nie scala klas ani nie kasuje obiektów za Ciebie.
    Kolejność, podobieństwa i flagi są wskazówką; decyzja należy do analityka.

## Kiedy tu zaglądać

- **Przed treningiem** - wyłapać pomyłki etykiet i przeciek, zanim zainwestujesz w trening.
- **Po zbudowaniu wersji** - sprawdzić przeciek względem konkretnego, opublikowanego podziału.
- **Przy sporze o taksonomię** - zobaczyć, które klasy są realnie nierozróżnialne wizualnie.

## Powiązane

- [Zawartość datasetu](zawartosc.md) - kafelki z ramkami z gotowej wersji
- [Audyt](audyt.md) - gotowość wersji do treningu (przeciek przestrzenny, integralność podziału)
- [Publikowanie wersji](publikowanie.md) - wersja potrzebna do sprawdzenia przecieku
- [Porównanie wyników](../training/wyniki.md) - macierz pomyłek już po treningu
