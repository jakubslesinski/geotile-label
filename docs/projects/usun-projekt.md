# Usuń projekt

**Cel.** Trwale usunąć projekt z aplikacji.

!!! danger "Operacji nie można cofnąć"

    Przed usunięciem utwórz [backup](backup.md). Jest lekki i zajmuje chwilę, a jest
    jedyną drogą powrotu.

## Co zostanie usunięte

- konfiguracja i profil projektu;
- **adnotacje**;
- katalogi kafelków;
- wygenerowane datasety i przebiegi treningu.

## Co zostanie nietknięte

**Pliki źródłowe scen.** Leżą poza projektem i aplikacja ich nie usuwa. Usunięcie
projektu nie kasuje zobrazowań dostawcy.

## Kroki

1. W widoku projektów kliknij ikonę kosza przy projekcie.
2. Przeczytaj okno potwierdzenia - wymienia, co zostanie skasowane.
3. Potwierdź.

## Zanim usuniesz

Zadaj sobie trzy pytania:

1. Czy praca z tego projektu została **przekazana albo wyeksportowana**? Jeśli nie,
   adnotacje przepadną razem z projektem.
2. Czy istnieje **model wytrenowany** na tym projekcie? Usunięcie kasuje przebiegi
   treningu, a wraz z nimi rodowód modelu - nie będzie już wiadomo, na jakich danych
   powstał.
3. Czy ktoś inny **nie korzysta z tego samego folderu**? Projekt może leżeć na dysku
   współdzielonym.

!!! tip "Alternatywa dla usuwania"

    Jeśli chodzi tylko o uporządkowanie listy, rozważ przeniesienie folderu projektu w
    inne miejsce zamiast kasowania. Projekt zniknie z listy, a dane zostaną - można go
    później otworzyć przez **Importuj folder projektu**.
