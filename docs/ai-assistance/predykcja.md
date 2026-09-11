# Predykcja YOLO

**Cel.** Wstępnie oznaczyć obiekty na scenie modelem, żeby ograniczyć rysowanie od
zera.

**Kiedy.** Gdy masz model wytrenowany na podobnym materiale.

**Wymagania.** Model `.pt` dostępny na dysku. Predykcja działa na procesorze i jest
częścią każdej instalacji - nie wymaga pakietu GPU.

## Kroki

1. W panelu predykcji wybierz **Wybierz model** i wskaż plik `.pt`.
2. Ustaw **Próg ufności** i **Próg IoU**.
3. Wybierz **Uruchom predykcję**.
4. Poczekaj na wynik - długi przebieg można przerwać przyciskiem **Anuluj
   predykcję**.

Inferencja całej sceny jest trwałym zadaniem `scene_inference`. Jej postęp pozostaje
widoczny w [Zadaniach w tle](../reference/zadania-w-tle.md), również po przejściu do
innego widoku.

![Panel predykcji z wybranym modelem i progami](../assets/images/prediction-configuration.png)
*Konfiguracja predykcji przed uruchomieniem.*

## Punkt kontrolny

Na scenie pojawiają się propozycje, a licznik oczekujących wyników pokazuje ich liczbę.

!!! danger "Propozycja to jeszcze nie adnotacja"

    Wyniki predykcji są **tymczasowe**. Nie trafiają do adnotacji kanonicznych, nie
    wejdą do datasetu i nie zostaną przekazane w paczce, dopóki ich nie zaakceptujesz.
    To celowe: model podpowiada, decyzję podejmuje człowiek.

## Przegląd propozycji

Każdą propozycję można przyjąć, poprawić przed przyjęciem albo usunąć. Zaznaczaj wiele
propozycji naraz (++ctrl++ - pojedynczo, ++shift++ - zakres) i działaj zbiorczo:
**Zaakceptuj wszystkie** albo **Usuń zaznaczone**. Usunięcie propozycji i odrzucenie
to jedno i to samo - propozycja znika z listy i nie trafia do adnotacji.

!!! warning "„Zaakceptuj wszystkie" po obejrzeniu, nie zamiast"

    Masowa akceptacja jest wygodna, gdy model jest sprawdzony na tym materiale i
    przejrzałeś wynik. Użyta w ciemno wprowadza do datasetu błędy, których nikt już
    nie wychwyci - a model wytrenowany na nich powieli je w kolejnym cyklu.

## Progi

**Próg ufności** decyduje, od jakiej pewności model zgłasza obiekt. Niższy daje więcej
propozycji, w tym więcej fałszywych; wyższy pomija obiekty niepewne.

**Próg IoU** steruje usuwaniem duplikatów - nakładających się wykryć tego samego
obiektu.

!!! tip "Zacznij od progu, który daje trochę za dużo"

    Usunięcie nadmiarowej propozycji zajmuje sekundę. Znalezienie obiektu, którego
    model nie zgłosił, wymaga obejrzenia całej sceny - czyli tego, czego predykcja
    miała zaoszczędzić.

## Dopasowanie klas

Klasy modelu są dopasowywane do klas projektu **po nazwie**.

!!! warning "Nazwa musi się zgadzać"

    Klasa modelu bez odpowiednika w projekcie nie zostanie dopasowana. Jeśli wyniki
    trafiają do niewłaściwej klasy albo nie pojawiają się wcale, porównaj nazwy klas
    modelu z [klasami projektu](../projects/klasy.md).

## Preprocessing

Preprocessing predykcji jest **niezależny od ustawień wyświetlania**. Zmiana jasności
czy kontrastu na ekranie nie wpływa na to, co widzi model.

!!! info "Duże sceny są analizowane kafelkami"

    Predykcja dzieli scenę na fragmenty i składa wyniki. Dlatego działa na
    zobrazowaniach, które nie zmieściłyby się w pamięci w całości.

## Wydajność całej sceny

| Ustawienie | Znaczenie |
| --- | --- |
| **Batch** | `auto` dobiera konserwatywną liczbę okien do wolnego VRAM; można też podać 1–64 |
| **Prefetch batches** | liczba przygotowanych batchy w ograniczonej kolejce, 1–8 |
| **Merge method** | obecnie class-aware NMS dla wyników zachodzących kafli |
| **Progress interval** | jak często worker zapisuje postęp długiego przebiegu |

Na CPU `auto` wybiera batch 1. Po błędzie CUDA OOM automatyczny batch jest dzielony i
ponawiany; jawnie wybrany batch nie jest zmieniany bez wiedzy użytkownika. Pamięć
kolejki jest ograniczona przez `batch × prefetch`, dlatego zwiększanie obu parametrów
jednocześnie zwiększa użycie RAM i VRAM.

Anulowanie jest sprawdzane między batchami i przed scalaniem wyników. Częściowy wynik
nie staje się zestawem propozycji do akceptacji.

## Typowe problemy

**Predykcja jest niedostępna.** Patrz
[Predykcja jest niedostępna](../troubleshooting/predykcja-niedostepna.md).

**Model nie wykrywa nic.** Sprawdź próg ufności i to, czy model był trenowany na
podobnej modalności - model z materiału optycznego nie zadziała na SAR.

**Wyniki są przesunięte względem obiektów.** Sprawdź, czy scena nie ma statusu
**źródło zmienione**.

## Powiązane

- [Narzędzia AI](index.md) - SAM i dopasowanie wzorca
- [Rejestr modeli](../training/rejestr-modeli.md) - model wytrenowany w aplikacji
