# Predykcja jest niedostępna

Panel predykcji jest wygaszony albo zgłasza brak dostępności.

## Sprawdź diagnostykę

Otwórz **Ustawienia → Diagnostyka** i sprawdź, czy:

- funkcja `YOLO` ma status włączony;
- widoczne są wersje bibliotek `Torch` i `Ultralytics`.

Jeśli ich nie ma, środowisko backendu nie wczytało bibliotek predykcji.

## Naprawa

1. Wybierz **Wyczyść środowisko i pamięć podręczną**.
2. Uruchom aplikację ponownie.
3. Sprawdź diagnostykę jeszcze raz.

Jeżeli problem wraca, wyeksportuj ZIP diagnostyczny i przekaż go zespołowi.

## Predykcja działa, ale nie na karcie graficznej

To normalne. **Predykcja działa na procesorze** w każdej instalacji i nie wymaga
pakietu GPU.

!!! info "Pakiet GPU jest tylko do trenowania"

    Zainstalowanie [pakietu treningowego](../getting-started/pakiet-gpu.md) nie
    przyspieszy predykcji - służy wyłącznie trenowaniu własnych modeli.

## Ścieżka do modelu wskazuje nieistniejące miejsce

Ścieżki do modeli (YOLO `.pt`, folder i checkpoint **SAM**, folder i wagi **DINO**) są
zapisywane **osobno w każdym projekcie**. Po przeniesieniu projektu na inny komputer albo
zmianie lokalizacji modeli te ścieżki mogą wskazywać na miejsca, których już nie ma.

Objawy: zapis ustawień predykcji nie przechodzi (komunikat w rodzaju „SAM models directory
not found"), model się nie ładuje, a bywa, że błąd dotyczy innego modelu niż ten właśnie
zmieniany.

Naprawa w panelu predykcji:

- **YOLO** - wybierz **Wybierz model** i wskaż aktualny plik `.pt`.
- **SAM / DINO** - jeśli folder jest nieaktualny, użyj **Wyczyść ścieżkę**, a następnie
  **Zmień folder** i wskaż nowy katalog z wagami.

!!! info "Nieaktualna ścieżka jednego modelu nie blokuje już pozostałych"

    Aktualizacja waliduje tylko ścieżkę, którą faktycznie zmieniasz - nieaktualny wpis
    innego modelu nie przeszkodzi w zapisaniu poprawnej ścieżki. Możesz też wprost usunąć
    nieaktualny wpis przyciskiem **Wyczyść ścieżkę**.

## Predykcja działa, ale nic nie wykrywa

To nie jest problem z dostępnością narzędzia. Sprawdź:

- **próg ufności** - zbyt wysoki odrzuca wszystko;
- **modalność modelu** - model z obrazów optycznych nie zadziała na SAR i odwrotnie;
- **nazwy klas** - dopasowanie odbywa się po nazwie, więc klasa modelu bez
  odpowiednika w projekcie nie zostanie przypisana.

Szczegóły w [Predykcji YOLO](../ai-assistance/predykcja.md).
