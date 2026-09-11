# Pierwsze kroki

Ten rozdział prowadzi od instalacji do pierwszej zapisanej adnotacji i pokazuje, gdzie
zacząć w zależności od roli.

## Wybierz ścieżkę

<div class="grid cards" markdown>

-   :material-download: **Instalacja**

    ---

    Wymagania, instalator i pierwsze uruchomienie.

    [Zainstaluj aplikację](instalacja.md)

-   :material-account-edit: **Adnotuję sceny**

    ---

    Od utworzenia projektu do pierwszej adnotacji, w kilkanaście minut.

    [Szybki start analityka](szybki-start-analityka.md)

-   :material-account-tie: **Scalam pracę zespołu**

    ---

    Projekt zbiorczy, import paczek i budowanie datasetu.

    [Szybki start managera](../teamwork/szybki-start-managera.md)

-   :material-graph: **Chcę zobaczyć całość**

    ---

    Pełna droga danych od sceny źródłowej do wersji datasetu.

    [Od sceny do datasetu](od-sceny-do-datasetu.md)

</div>

## Co działa od razu po instalacji

Instalacja bazowa obejmuje **labelowanie, narzędzia AI i predykcję**. Wszystko działa
na procesorze - karta graficzna nie jest potrzebna.

Osobnego [pakietu treningowego GPU](pakiet-gpu.md) wymaga wyłącznie **trenowanie
modeli**. Bez niego zakładka Trening jest widoczna, ale architektury pozostają
niedostępne wraz z podanym powodem.

!!! info "Dane źródłowe zostają tam, gdzie są"

    Aplikacja czyta sceny tylko do odczytu i nie kopiuje ich do siebie. W folderze
    projektu zapisuje konfigurację, adnotacje i produkty pochodne. Szczegóły w
    [Lokalizacjach danych](../reference/lokalizacje-danych.md).

## Zanim zaczniesz

Przygotuj:

- **sceny** w postaci, w jakiej dostarczył je dostawca - nie rozpakowuj ich do
  wspólnego folderu i nie zmieniaj nazw plików;
- **plik klas** `classes.json`, jeśli zespół już go ustalił;
- **folder na projekty** - może być na dysku roboczym albo współdzielonym.

!!! warning "Nie konwertuj paczek przed importem"

    Aplikacja rozpoznaje produkty dostawców i sama przygotowuje widok roboczy, gdy
    jest potrzebny. Ręczna konwersja zwykle utrudnia rozpoznanie i zrywa łańcuch
    pochodzenia danych. Patrz
    [Domyślne produkty dostawców](../reference/produkty-dostawcow.md).

## Język interfejsu i pomoc

Język przełącza się w **Ustawieniach**. Tę dokumentację otwiera przycisk **Pomoc**
oraz klawisz ++f1++.
