# Trening modeli

Warsztat treningu pozwala wytrenować model detekcji na datasecie zbudowanym w
aplikacji, porównać kilka konfiguracji na tych samych danych i wskazać model
używany przez projekt do predykcji.

Trening wymaga karty NVIDIA oraz **pakietu treningowego GPU**, który instaluje się
osobno - instalacja bazowa działa na CPU i obejmuje labelowanie, narzędzia AI oraz
predykcję.

## Zakres

- instalacja pakietu treningowego GPU i weryfikacja, że runtime jest aktywny;
- wskazanie **wszystkich** plików pakietu naraz - razem z archiwum wag bazowych,
  bez którego architektury pozostaną niedostępne;
- przygotowanie wag bazowych i lista dostępnych architektur (YOLOv8, YOLOv10,
  YOLO11, YOLO12, YOLO26 w rozmiarach n/s/m);
- dlaczego część architektur jest wygaszona - geometria projektu decyduje, czy
  potrzebny jest model detekcji, czy OBB;
- wybór datasetu, architektury i hiperparametrów;
- konfiguracja przez formularz oraz edytor YAML;
- adaptacyjny preflight: GPU, VRAM, dysk, spójność danych, test przepustowości oraz
  rekomendacja `batch/workers/cache`;
- przebieg treningu, postęp i przerywanie;
- porównanie przebiegów w obrębie jednego datasetu;
- finalna ocena na zbiorze testowym;
- rejestr modeli, promocja modelu projektu i rodowód danych.
- telemetria wydajności treningu zapisana z przebiegiem.

!!! note "Trening od zera daje słabsze wyniki"

    Większość architektur startuje z wag wstępnie trenowanych na COCO. YOLO12-OBB
    jest wyjątkiem - nie ma dla niej opublikowanych wag, więc sieć uczy się od
    losowej inicjalizacji. Przy typowym rozmiarze datasetu adnotacyjnego wyniki będą
    wyraźnie gorsze niż przy modelu wstępnie trenowanym. Aplikacja oznacza takie
    pozycje etykietą **od zera**.

!!! warning "Zamknięcie aplikacji przerywa trening"

    Trening działa jako proces potomny aplikacji. Zamknięcie okna przerywa go, a
    przebieg zostaje oznaczony jako przerwany.

!!! note "Zbiór testowy jest jednorazowy"

    Ocena na zbiorze testowym jest wykonywana raz na przebieg i zapisywana jako
    wynik finalny. Wielokrotne wybieranie modelu po zbiorze testowym zamienia go w
    drugi zbiór walidacyjny i zawyża ocenę - dlatego porównania i iteracje idą po
    metrykach walidacyjnych.
