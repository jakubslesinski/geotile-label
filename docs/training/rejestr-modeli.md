# Rejestr modeli

**Cel.** Zachować wytrenowany model wraz z informacją, skąd pochodzi, i wskazać ten,
którego używa projekt.

## Rejestracja

Model rejestruje się z zakończonego przebiegu treningu. Do rejestru trafia punkt
kontrolny wraz z metrykami i pełnym opisem pochodzenia.

!!! info "Rejestrować da się tylko przebieg zakończony powodzeniem"

    Przebieg przerwany albo zakończony błędem nie ma kompletnego opisu tego, co się
    wydarzyło. Model bez takiego opisu byłby plikiem nieznanego pochodzenia - czyli
    dokładnie tym, czego rejestr ma unikać.

## Promocja

**Promocja** wskazuje model używany przez projekt do predykcji. Poprzedni trafia do
historii - nie znika.

!!! warning "Geometria musi się zgadzać"

    Modelu detekcji nie da się promować w projekcie z ramkami zorientowanymi i
    odwrotnie. Taki model przewidywałby zupełnie inny rodzaj geometrii, więc próba
    jest odrzucana zamiast dawać bezużyteczne wyniki.

Historia promocji pokazuje, który model był używany w danym okresie - przydaje się przy
wyjaśnianiu, skąd wzięły się starsze predykcje.

## Rodowód

Dla zarejestrowanego modelu można prześledzić łańcuch:

```text
model → przebieg treningu → wersja datasetu → kafelki → adnotacje → autorzy
```

Rodowód odpowiada na pytanie **„czyja praca znalazła się w tym modelu"** - z
dokładnością do konkretnych adnotacji, nie tylko listy uczestników projektu.

!!! info "Liczy się użycie, nie obecność w projekcie"

    W rodowodzie pojawiają się wyłącznie autorzy adnotacji, które **faktycznie trafiły
    do treningu**. Praca odfiltrowana przy budowaniu datasetu albo leżąca w kafelkach
    nieużytych w tej wersji nie jest zaliczana.

Do czego to służy w praktyce:

- ustalenie, czyje adnotacje wpłynęły na zachowanie modelu, gdy myli konkretną klasę;
- rozliczenie wkładu przy pracy zespołowej;
- odtworzenie warunków powstania modelu przy audycie.

## Gdy rodowodu nie da się prześledzić

Łańcuch wymaga, żeby wersja datasetu i jej katalog kafelków nadal istniały. Jeśli
zostały usunięte, aplikacja powie to wprost, zamiast pokazać niepełną listę udającą
kompletną.

!!! warning "Dlatego wersji użytej do treningu nie da się skasować"

    Blokada usuwania opisana w
    [Publikowaniu wersji](../datasets/publikowanie.md) istnieje właśnie po to. Model
    bez możliwego do odtworzenia pochodzenia przestaje być rozliczalny - a w pracy
    GEOINT to zwykle dyskwalifikuje go bardziej niż słabsze metryki.

## Model w projekcie

Promowany model jest używany przez [predykcję](../ai-assistance/predykcja.md) w tym
projekcie. Można też wskazać dowolny plik `.pt` z dysku - rejestr nie ogranicza
predykcji, tylko porządkuje modele własne.
