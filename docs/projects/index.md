# Projekty

Projekt przechowuje profil, źródła scen, klasy, adnotacje, ustawienia przeglądu oraz
historię wersji datasetów. **Sceny źródłowe nie są do niego kopiowane** - projekt
trzyma tylko odwołania do nich.

## Zadania

<div class="grid cards" markdown>

-   :material-folder-plus: **Zakładam projekt**

    ---

    Znaczenie pól profilu i konsekwencje wyborów.

    [Utwórz projekt](utworz-projekt.md)

-   :material-tag-multiple: **Ustawiam klasy**

    ---

    Import z pliku, format i uzgodnienie w zespole.

    [Klasy](klasy.md)

-   :material-link-variant: **Zgubiły się sceny**

    ---

    Ponowne wskazanie plików po przeniesieniu danych.

    [Relinkuj źródło](relinkuj-zrodlo.md)

-   :material-archive: **Zabezpieczam pracę**

    ---

    Backup ZIP, odtwarzanie i przenoszenie na inny komputer.

    [Backup i przenoszenie](backup.md)

</div>

## Co jest w projekcie, a co poza nim

| W projekcie | Poza projektem |
| --- | --- |
| profil, klasy, ustawienia | pliki źródłowe scen |
| adnotacje | metadane dostawcy |
| katalogi kafelków i wersje datasetów | wagi modeli bazowych |
| widoki robocze i przebiegi treningu | |

Ten podział decyduje o wszystkim, co dotyczy przenoszenia pracy: **backup zabiera to,
czego nie da się odtworzyć**, a sceny trzeba mieć osobno.

Pełne zestawienie plików znajduje się w
[Lokalizacjach danych](../reference/lokalizacje-danych.md).

## Trwałe decyzje

Większość ustawień można zmienić w trakcie pracy. Trzy rzeczy są jednak trudne albo
niemożliwe do cofnięcia:

**Tryb adnotacji** - decyduje, jakie architektury da się trenować i jak wyglądają
eksporty. Ustal go z zespołem przed startem.

**Modalność** - jest wspólna dla wszystkich źródeł, więc materiału optycznego i
radarowego nie da się połączyć w jednym projekcie.

**Wariant sceny** - po pierwszej adnotacji aplikacja blokuje zmianę produktu
roboczego. Powód opisują
[Produkty pochodne](../input-data/produkty-pochodne.md).

## Rola projektu

Projekt można prowadzić jako indywidualny albo jako zbiorczy, w którym scala się pracę
zespołu i wystawia werdykty. Ustawienie to opisuje
[Role projektu](../teamwork/role-projektu.md).
