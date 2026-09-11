# Publikowanie wersji datasetu

**Cel.** Wyróżnić wersję datasetu, na której warto trenować, i udostępnić ją w
zakładce Trening.

**Kiedy.** Gdy wersja przeszła [audyt](audyt.md) i uznajesz ją za gotową.

## Po co to jest

Warsztat datasetu produkuje wiele wersji - z różnymi podziałami, filtrami i
parametrami. Większość z nich to próby. Publikacja oddziela **materiał roboczy od
tego, na czym faktycznie się trenuje**.

Zakładka Trening pokazuje wyłącznie wersje opublikowane. Dzięki temu nikt nie trenuje
przypadkiem na wersji z eksperymentalnym podziałem.

## Trzy stany

| Stan | Znaczenie |
| --- | --- |
| **robocza** | wersja świeżo wygenerowana; domyślny stan |
| **opublikowana** | wersja przeznaczona do treningu |
| **wycofana** | wersja odsunięta od nowych treningów |

Przejścia są odwracalne - wersję można opublikować, wycofać i cofnąć do roboczej.

## Kroki

1. Otwórz listę wersji datasetu.
2. Wybierz wersję, która przeszła audyt.
3. Wybierz **Opublikuj** i nadaj etykietę publikacji.

**Punkt kontrolny.** Wersja pojawia się na liście datasetów w zakładce Trening.

!!! tip "Etykieta publikacji ma opisywać zawartość, nie kolejność"

    „v3" po miesiącu nic nie mówi. „Porty, podział scenowy, 15% pustych" pozwala
    wybrać właściwą wersję bez otwierania jej konfiguracji.

## Wycofanie

**Wycofaj** odsuwa wersję od nowych treningów, ale **nie usuwa jej** i nie unieważnia
modeli, które na niej powstały. Ich rodowód pozostaje czytelny.

Używaj tego, gdy wersja okazała się wadliwa albo została zastąpiona lepszą, a chcesz
zapobiec przypadkowemu użyciu.

## Usuwanie wersji jest ograniczone

!!! warning "Wersji użytej do treningu nie da się usunąć"

    Jeśli od wersji zależy przebieg treningu albo zarejestrowany model, usunięcie jest
    blokowane. To zabezpieczenie rodowodu: bez datasetu nie dałoby się odtworzyć, na
    czym powstał model, a taki model przestaje być rozliczalny.

    Gdy wersja jest niepotrzebna, ale zablokowana - **wycofaj ją** zamiast usuwać.

## Powiązane

- [Audyt datasetu](audyt.md) - sprawdź przed publikacją
- [Uruchom trening](../training/uruchom-trening.md) - co dzieje się z opublikowaną
  wersją
- [Rejestr modeli](../training/rejestr-modeli.md) - powiązanie modelu z datasetem
