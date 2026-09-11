# Role projektu i własność adnotacji

Praca zespołowa opiera się na dwóch pojęciach: **roli projektu**, która decyduje o
dostępnych operacjach, i **właścicielu adnotacji**, który decyduje o tym, czego dotyka
import.

## Rola projektu

Rolę wybiera się **raz, przy zakładaniu projektu**. Nie ma kont ani logowania.

| Rola | Kto | Może |
| --- | --- | --- |
| **Labelowanie** (domyślna) | analityk | eksportować paczki adnotacji, importować recenzje |
| **Recenzja** | manager | importować paczki adnotacji, wystawiać werdykty, eksportować recenzje |

Projekt recenzji jest **końcem drogi**: jego produktem jest dataset, a nie kolejna
paczka adnotacji - dlatego eksport paczki adnotacji jest w nim wyłączony.

!!! info "Niedostępne przyciski są wygaszone, nie ukryte"

    Zawsze z podanym powodem. Brak funkcji ma być zrozumiały, a nie wyglądać na
    usterkę.

Projekty założone przed wprowadzeniem ról otwierają się jako **Labelowanie** i działają
bez zmian. Praca jednoosobowa nie wymaga niczego konfigurować.

## Właściciel adnotacji

Każda adnotacja utworzona lokalnie dostaje adres z profilu projektu. To pole jest
**właścicielem** i decyduje, czego dotknie import paczki.

Osobne pole zachowuje **pierwotnego autora**, nawet jeśli własność później się zmieni.
Dzięki temu przekazanie pracy nie zaciera tego, kto ją wykonał.

!!! danger "Własność jest kooperacyjna, nie egzekwowana"

    Adres właściciela to tekst wpisany ręcznie w ustawieniach projektu, a paczki nie są
    podpisane. Nikt nie weryfikuje, czy analityk wpisał własny adres.

    Mechanizm chroni **przed pomyłką, nie przed złą wolą**. Dla narzędzia offline w
    zespole, który sobie ufa, to świadomy wybór - ale nie traktuj go jako
    zabezpieczenia i nie buduj na nim rozliczalności formalnej.

## Dlaczego właściciel jest ważny przy imporcie

Import paczki **podmienia adnotacje danego właściciela w danej scenie w całości**.
Wchodzą poprawki, znikają obiekty usunięte przez analityka.

Adnotacje innych właścicieli - drugiego analityka albo samego managera - pozostają
nietknięte. To dlatego zakres paczki obejmuje parę „scena × właściciel", a nie samą
scenę.

!!! tip "Uzgodnijcie adresy przed startem"

    Analityk, który wpisze inny adres niż zwykle, stworzy w projekcie zbiorczym
    „drugiego autora". Jego poprawki nie podmienią wcześniejszej pracy, tylko dołożą
    się obok niej. To najczęstsza przyczyna zdublowanych obiektów po imporcie.

## Pętla pracy

```text
analityk                     manager
   │                            │
   ├─ labeluje sceny            │
   ├─ eksportuje paczkę ───────►│
   │                            ├─ importuje, decyduje per scena
   │                            ├─ kontroluje kompletność
   │◄─────── eksportuje recenzję┤  (werdykty, bez geometrii)
   ├─ poprawia oznaczone sceny  │
   └─ eksportuje nową paczkę ──►│
                                └─ buduje katalog i dataset
```

Analitycy **nie generują kafelków ani datasetów** - to zadanie projektu recenzji.

## Powiązane

- [Workflow analityka](workflow-analityka.md)
- [Szybki start managera](szybki-start-managera.md)
- [Rodzaje paczek](../reference/rodzaje-paczek.md)
