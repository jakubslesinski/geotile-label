# Skróty klawiaturowe

Pełna lista skrótów widoku labelowania. Skróty nie działają, gdy kursor znajduje się
w polu tekstowym.

## Narzędzia mapy

Narzędzie pozostaje aktywne do czasu wybrania innego.

| Skrót | Narzędzie | Do czego służy |
| --- | --- | --- |
| ++v++ | Przesuwanie / wybór | przesuwanie mapy i zaznaczanie pojedynczych adnotacji |
| ++d++ | Rysowanie adnotacji | włącza i wyłącza tryb rysowania |
| ++a++ | Zaznaczanie wielu | zaznaczenie prostokątem grupy adnotacji |
| ++m++ | Pomiar | pomiar odległości na scenie |
| ++p++ | Malowanie klasą | nadawanie klasy kolejnym adnotacjom bez otwierania listy |

## Klasy

| Skrót | Działanie |
| --- | --- |
| ++1++ – ++9++ | wybór klasy z przypisanym numerem |
| ++k++ | otwarcie wyszukiwarki klas |
| ++n++ | przełączenie trybu negatywnego |

!!! note "Numery działają tylko dla klas, które je mają"

    Klawisze ++1++–++9++ wybierają klasę o przypisanym numerze skrótu. Klasa bez
    przypisanego numeru jest dostępna wyłącznie przez listę albo wyszukiwarkę
    (++k++). Przypisanie numerów opisuje [Klasy](../projects/klasy.md).

## Adnotacje

| Skrót | Działanie |
| --- | --- |
| ++c++ + przeciągnięcie środka ramki | utworzenie kopii adnotacji |
| ++delete++ albo ++backspace++ | usunięcie zaznaczonej adnotacji poza trybem rysowania |
| ++ctrl+z++ | cofnięcie ostatniej adnotacji |
| ++esc++ | anulowanie bieżącej operacji |

## Siatka przeglądu

| Skrót | Działanie |
| --- | --- |
| ++s++ | pokazanie lub ukrycie siatki |
| ++r++ | oznaczenie komórki pod kursorem jako sprawdzonej |
| ++shift+r++ | oznaczenie wszystkich widocznych komórek jako sprawdzonych |
| ++e++ | wykluczenie komórki pod kursorem |
| ++shift+e++ | wykluczenie wszystkich widocznych komórek |

Znaczenie stanów komórek opisuje
[Siatka przeglądu](../annotation/siatka-przegladu.md).

!!! tip "Skrót ++s++ pokazuje tę siatkę, która jest w danej chwili istotna"

    Zanim siatka powstanie, ++s++ przełącza podgląd konfiguracji. Gdy już istnieje -
    właściwą siatkę przeglądu. Obie nigdy nie są widoczne naraz.

## Warstwy i wygląd sceny

| Skrót | Działanie |
| --- | --- |
| ++z++ | pokazanie lub ukrycie warstwy rastra źródłowego |
| ++bracket-left++ | zmniejszenie krycia sceny o 10% |
| ++bracket-right++ | zwiększenie krycia sceny o 10% |
| ++ctrl+bracket-left++ | zmniejszenie gammy |
| ++ctrl+bracket-right++ | zwiększenie gammy |

Ukrycie rastra (++z++) pozwala porównać adnotacje z podkładem mapowym na scenach
z georeferencją.

!!! warning "++ctrl+z++ to cofnięcie, nie warstwa"

    Samo ++z++ przełącza warstwę rastra, a ++ctrl+z++ cofa ostatnią adnotację. To
    dwie różne akcje na tym samym klawiszu.
