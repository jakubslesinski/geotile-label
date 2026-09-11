# Preflight

Kontrola wykonywana **przed** startem treningu. Sprawdza rzeczy, które inaczej
ujawniłyby się po kilkunastu minutach albo - gorzej - dopiero w wynikach.

## Co jest sprawdzane

**Urządzenie.** Czy runtime GPU jest aktywny i czy karta jest widoczna.

**Pamięć karty.** Czy wybrany rozmiar modelu i batch mają szansę się zmieścić.

**Miejsce na dysku.** Czy starczy na przygotowanie danych i punkty kontrolne.

**Spójność danych.** Czy dataset ma poprawną strukturę, czy podział na zbiory jest
kompletny i czy klasy zgadzają się między konfiguracją a etykietami.

**Etykiety OBB.** W projektach z ramkami zorientowanymi - czy etykiety mają właściwy
format.

**Przepustowość danych.** Ograniczony test odczytu i dekodowania próbek ocenia, czy
trening będzie czekał głównie na dysk, CPU czy GPU. Test obejmuje najwyżej 24 obrazy i
128 MiB danych - nie tworzy pełnego cache i nie modyfikuje opublikowanego datasetu.

## Rekomendacja zasobów

Preflight klasyfikuje profil jako **I/O**, **dekodowanie CPU**, **GPU** albo
**zrównoważony** i proponuje:

- efektywny `batch`;
- liczbę procesów **DataLoader workers**;
- cache obrazów: wyłączony, na dysku lub w RAM.

Rekomendacja jest zapisywana z manifestem, ale **nie zmienia konfiguracji bez Twojej
decyzji**. Użyj **Zastosuj rekomendację**, sprawdź pola i uruchom preflight ponownie.

!!! warning "Cache RAM musi zostawić bezpieczny zapas"

    Preflight uwzględnia rozmiar datasetu, wolną pamięć i procesy workerów. W Windows
    model `spawn` może powielić dane między procesami, dlatego jawne `cache=ram` jest
    blokowane, jeśli po estymacji nie zostanie co najmniej 35% RAM albo 8 GiB zapasu.
    To kontrola startu, nie tylko sugestia.

## Dlaczego kontrola etykiet OBB jest osobno

!!! danger "Zły format etykiet nie zgłasza błędu - daje zły model"

    Framework treningowy wyszukuje etykiety obok obrazów według stałej reguły
    nazewniczej. Gdyby trafił na etykiety prostokątne tam, gdzie oczekiwane są
    zorientowane, **trening przebiegłby normalnie** i zakończył się bez ostrzeżenia -
    tylko model nauczyłby się złych geometrii.

    Dlatego aplikacja przygotowuje dane dla modeli OBB w osobnym katalogu i sprawdza
    format przed startem. To kontrola przeciwko awarii cichej, nie głośnej.

## Szacowany czas

Preflight podaje orientacyjne **pasmo czasu**, nie prognozę.

!!! info "To rząd wielkości, nie obietnica"

    Realny czas zależy od karty, rozmiaru obrazów, liczby kafelków i tego, co jeszcze
    działa na komputerze. Pasmo służy do decyzji „uruchamiam teraz czy na noc", a nie
    do planowania co do minuty.

## Gdy preflight nie przechodzi

**Brak runtime GPU** - zainstaluj [pakiet treningowy](../getting-started/pakiet-gpu.md)
i uruchom aplikację ponownie.

**Za mało pamięci karty** - zmniejsz rozmiar obrazu, ustaw mniejszy batch albo wybierz
mniejszy wariant modelu. Batch `-1` dobiera wartość automatycznie.

**Niebezpieczny cache RAM** - wybierz `disk` lub wyłącz cache, zmniejsz liczbę
workerów albo użyj mniejszego datasetu. Nie obchodź blokady dodatkowym kluczem YAML.

**Niespójne klasy** - sprawdź, czy dataset został wygenerowany po ostatniej zmianie
klas w projekcie.

**Niekompletny podział** - wersja datasetu nie ma wszystkich zbiorów. Wróć do
[budowania datasetu](../datasets/zbuduj-dataset.md).

!!! tip "Preflight nie zastępuje audytu"

    Preflight sprawdza, czy trening **da się uruchomić**. Czy dataset jest **sensowny**
    - bez przecieku przestrzennego i niesprawdzonych obszarów - ocenia
    [audyt](../datasets/audyt.md). Zaliczony preflight nie znaczy, że wynik będzie
    wiarygodny.
