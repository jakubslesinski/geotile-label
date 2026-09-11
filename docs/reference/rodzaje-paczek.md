# Rodzaje paczek wymiany

Aplikacja tworzy cztery różne paczki ZIP. Wyglądają podobnie, ale robią zupełnie co
innego - pomylenie ich jest najczęstszym źródłem nieporozumień w pracy zespołowej.

| Paczka | Kierunek | Do czego służy | Niesie geometrię |
| --- | --- | --- | --- |
| **Backup projektu** | - | odtworzenie całego projektu | tak |
| **Paczka adnotacji** | analityk → manager | przekazanie labeli bez kafelków | tak |
| **Paczka recenzji** | manager → analityk | werdykty i komentarze | **nie** |
| **Dataset ZIP** | - | trening, audyt, archiwizacja | tak (etykiety) |

## Backup a paczka adnotacji

!!! warning "To nie są zamienniki"

    **Backup odtwarza cały projekt jako nowy.** Paczka adnotacji **dołącza pracę do
    istniejącego** projektu. Użycie backupu tam, gdzie potrzebna była paczka
    adnotacji, tworzy drugi, równoległy projekt zamiast scalić pracę.

Backup zabiera konfigurację, klasy, źródła, adnotacje i produkty pochodne. Paczka
adnotacji niesie tylko to, co analityk narysował, plus dane pozwalające dopasować to
do scen po stronie managera.

## Paczka recenzji nie zawiera adnotacji

Recenzja jedzie w drugą stronę i **nie przenosi geometrii**. Zawiera werdykty na
poziomie sceny wraz z komentarzem, kto i kiedy je wystawił.

Skutek praktyczny: import recenzji **nie zmienia ani jednej adnotacji** u analityka.
Mówi mu tylko, które sceny wracają do poprawy i dlaczego. Poprawki analityk wprowadza
sam, a następnie odsyła zwykłą paczkę adnotacji.

!!! info "Werdykt dotyczy sceny, nie pojedynczego obiektu"

    Przy tysiącach adnotacji ocenianie każdej z osobna jest nierealne, dlatego
    werdykt obejmuje całą scenę. Szczegóły przekazuje się komentarzem.

## Zakres i łańcuch zastępowania

Paczka adnotacji niesie **zakres** - parę „scena × właściciel". Import podmienia
adnotacje w tym zakresie **hurtowo**, dzięki czemu propagują się nie tylko nowe
obiekty, ale też poprawki i usunięcia.

Kolejna paczka od tego samego analityka na tę samą scenę powinna wskazywać poprzednią
jako zastąpioną. Bez tego łańcucha aplikacja **odrzuca** import z błędem, zamiast
zgadywać, która wersja jest nowsza.

!!! warning "Starsze paczki tylko dokładają"

    Paczki utworzone przed wprowadzeniem zakresu potrafią jedynie dodawać adnotacje -
    nie zaktualizują ani nie usuną istniejących. Różnice są wtedy raportowane jako
    niezastosowane. Jeśli poprawki nie docierają do managera, sprawdź najpierw wersję
    paczki.

## Rundy recenzji

Werdykt odnosi się do konkretnej rundy. Gdy analityk odeśle poprawioną pracę, werdykt
z poprzedniej rundy staje się **nieaktualny** i jest zliczany osobno, zamiast
udawać ocenę bieżącej wersji.

Pełny obieg opisuje [Role projektu](../teamwork/role-projektu.md).

## Dataset ZIP

Produkt końcowy: obrazy kafelków, etykiety w wybranym formacie, manifesty, sumy
kontrolne i pochodzenie. Nie służy do wymiany pracy w zespole - jest wejściem do
treningu i materiałem archiwalnym.

Zawartość zależy od wybranego formatu eksportu; zestawienie znajduje się w rozdziale
[Eksport](../export/index.md).
