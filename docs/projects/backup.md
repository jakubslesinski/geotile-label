# Backup i przenoszenie projektu

**Cel.** Zabezpieczyć pracę albo przenieść projekt na inny komputer.

**Kiedy.** Przed usunięciem projektu, przed większą zmianą konfiguracji oraz przy
przekazywaniu pracy na inne stanowisko.

## Utwórz backup

1. Otwórz projekt.
2. Wybierz **Backup projektu**.
3. Aplikacja uruchomi zadanie `project_backup`. Możesz zamknąć panel i kontynuować
   pracę.
4. Po ukończeniu otwórz [Zadania w tle](../reference/zadania-w-tle.md), wybierz
   **Pobierz** i wskaż miejsce zapisania ZIP.

Backup jest tworzony strumieniowo i publikowany dopiero po poprawnym zamknięciu
archiwum. Anulowane lub nieudane zadanie nie udostępnia częściowego ZIP jako gotowego
backupu.

**Co zawiera.** Konfigurację, profil, klasy, listę scen, adnotacje i metadane
kafelków.

**Czego nie zawiera.** Plików źródłowych scen ani wygenerowanych datasetów.

!!! info "Backup jest lekki z założenia"

    Sceny potrafią ważyć dziesiątki gigabajtów i zwykle są dostępne na dysku
    współdzielonym. Backup niesie to, czego nie da się odtworzyć - Twoją pracę - a nie
    dane, które i tak masz.

## Odtwórz z backupu

1. W widoku projektów wybierz **Importuj backup ZIP**.
2. Wskaż plik ZIP.
3. Wskaż folder z oryginalnymi scenami.

Trzeci krok jest konieczny właśnie dlatego, że backup nie niesie scen. Bez wskazania
źródeł projekt odtworzy się z adnotacjami, ale sceny będą miały status **brak
źródła**.

!!! warning "Backup tworzy nowy projekt, nie scala pracy"

    Import backupu odtwarza **cały projekt jako osobny**. Do dołączenia pracy
    analityka do projektu zbiorczego służy paczka adnotacji - to inna operacja i nie
    zastępują się nawzajem. Patrz
    [Rodzaje paczek](../reference/rodzaje-paczek.md).

## Otwórz istniejący folder projektu

Opcja **Importuj folder projektu** otwiera projekt utworzony wcześniej - na przykład
skopiowany z innego stanowiska albo pochodzący ze starszej wersji aplikacji.

Po imporcie wskaż ponownie folder scen. Brakujące sceny znajdziesz w raporcie.

## Migracja ze starszych wersji

Przy pierwszym otwarciu projektu z wersji `0.1.3` lub `0.1.4` aplikacja migruje
metadane. Przed zmianą zapisuje lekki snapshot JSON w `.migration_backups`.

Migracja **nie kopiuje scen ani datasetów** i **nie zmienia identyfikatorów** scen ani
adnotacji - dzięki temu wcześniej wyeksportowane paczki nadal pasują do projektu.

## Przeniesienie na inny komputer

Dwie drogi, zależnie od tego, czy masz dostęp do tego samego folderu projektu.

**Przez backup ZIP** - gdy przenosisz się między stanowiskami: utwórz backup,
przenieś plik, zaimportuj i wskaż sceny.

**Przez skopiowanie folderu projektu** - gdy projekt leży na dysku przenośnym albo
współdzielonym: skopiuj cały folder i użyj **Importuj folder projektu**.

!!! warning "W obu przypadkach potrzebujesz tych samych scen"

    Folder projektu nie zawiera zobrazowań. Na nowym stanowisku podłącz ten sam
    nośnik albo wskaż kopię źródeł, a następnie
    [zrelinkuj źródła](relinkuj-zrodlo.md). Adnotacje przetrwają - są zapisane w
    projekcie, nie przy scenach.

## Powiązane

- [Lokalizacje danych](../reference/lokalizacje-danych.md) - co dokładnie leży w
  folderze projektu
- [Usuń projekt](usun-projekt.md) - operacja nieodwracalna, zrób backup wcześniej
