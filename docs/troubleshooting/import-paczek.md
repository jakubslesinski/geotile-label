# Import paczki nie przechodzi

Import paczki adnotacji odrzuca część scen albo całą paczkę. Każda przyczyna ma inne
rozwiązanie.

!!! info "Odrzucenie sceny nie psuje reszty importu"

    Problem z jedną sceną blokuje tylko ją. Pozostałe stosują się normalnie.

## Brak sceny

Paczka zawiera scenę, której nie ma w projekcie docelowym.

**Rozwiązanie.** Zaimportuj brakującą scenę do projektu zbiorczego, a potem powtórz
import paczki.

## Inny widok roboczy

Ta sama scena źródłowa, ale przygotowana w innej siatce roboczej niż w projekcie
docelowym.

**Rozwiązanie.** Przygotuj scenę w tej samej siatce przed importem.

!!! warning "To nie jest niejednoznaczność do rozstrzygnięcia"

    Adnotacje są zapisane w pikselach konkretnego widoku roboczego. Dopasowanie ich na
    siłę do innej siatki przesunęłoby obiekty względem terenu. Dlatego aplikacja to
    zgłasza, zamiast zgadywać. Patrz
    [Produkty pochodne](../input-data/produkty-pochodne.md).

## Dopasowanie tylko po identyfikatorze dostawcy

Sceny są dopasowywane po stabilnym identyfikatorze, sumie kontrolnej pliku, a w
ostateczności po kontrolowanym zestawie metadanych. Dopasowanie oparte wyłącznie na
identyfikatorze dostawcy **wymaga ręcznego potwierdzenia** i nie stosuje się
automatycznie.

## Brakująca klasa

Paczka zawiera klasę nieznaną w projekcie docelowym.

**Rozwiązanie.** Zgódź się na jej utworzenie podczas importu albo dodaj ją wcześniej.

!!! danger "Bez zgody scena jest pomijana w całości"

    To zabezpieczenie. Import podmienia adnotacje właściciela w scenie w całości -
    gdyby część obiektów odpadła z powodu brakującej klasy, podmiana **skasowałaby
    resztę** i zostawiła niepełny zestaw. Lepiej pominąć scenę i zgłosić problem, niż
    po cichu okroić dane.

Trwałym rozwiązaniem jest uzgodnienie jednego pliku klas w zespole. Patrz
[Klasy](../projects/klasy.md).

## Dwie paczki tego samego autora na tę samą scenę

Bez łańcucha zastępowania import jest **odrzucany z błędem**.

**Rozwiązanie.** Wybierz najnowszą paczkę.

!!! info "Dlaczego aplikacja nie wybiera sama"

    Data pliku nie mówi, która paczka zawiera nowszą pracę - mogła zostać skopiowana
    albo odtworzona z backupu. Zgadywanie groziłoby cofnięciem poprawek, więc
    aplikacja woli zapytać.

## Zmienione adnotacje nie wchodzą

Ten sam obiekt z inną treścią nie zostaje zaktualizowany, a różnice są raportowane
jako niezastosowane.

**Przyczyna.** Paczka pochodzi sprzed wprowadzenia zakresu. Takie paczki potrafią
wyłącznie **dodawać** - nie zaktualizują ani nie usuną istniejących adnotacji.

**Rozwiązanie.** Poproś o ponowny eksport z aktualnej wersji aplikacji.

## Gdzie szukać szczegółów

Raport importu i migawka danych sprzed importu zostają w folderze projektu, w
`import_reports\`. Import jest transakcyjny - nieudany nie zostawia stanu pośredniego.
