# Adnotowanie

Adnotacje powstają **na pełnej scenie** i stanowią kanoniczne źródło etykiet. Etykiety
w kafelkach są produktem pochodnym, wyliczanym przy budowaniu datasetu.

!!! info "Nie ma przycisku zapisu"

    Praca zapisuje się na bieżąco do pliku sceny w folderze projektu.

## Narzędzia

Narzędzie wybiera się klawiszem i pozostaje aktywne do zmiany.

| Narzędzie | Skrót | Do czego |
| --- | --- | --- |
| Przesuwanie / wybór | ++v++ | poruszanie się po scenie, zaznaczanie |
| Rysowanie adnotacji | ++d++ | tworzenie nowych ramek |
| Zaznaczanie wielu | ++a++ | operacje na grupie adnotacji |
| Pomiar | ++m++ | odległości na scenie |
| Malowanie klasą | ++p++ | szybkie nadawanie klasy kolejnym obiektom |

Pełna lista w [Skrótach klawiaturowych](../reference/skroty.md).

## Ramki osiowe i zorientowane

Typ geometrii wynika z profilu projektu i nie zmienia się w trakcie pracy.

**Ramka osiowa** ma boki równoległe do osi obrazu. Prostsza i szybsza w rysowaniu.

**Ramka zorientowana** jest obrócona do kształtu obiektu i zachowuje **kierunek
przodu**. Ma sens tam, gdzie orientacja niesie informację - statki, samoloty, pojazdy.

!!! tip "Kierunek przodu to nie kosmetyka"

    Przy ramkach zorientowanych orientacja jest zapisywana jako azymut względem
    północy rzeczywistej i trafia do atrybutów obliczonych oraz do eksportów.
    Niedbałe ustawienie przodu psuje dane, choć na mapie wygląda poprawnie.

## Praca na scenie

- przesuwanie, skalowanie i obracanie istniejących ramek;
- kopiowanie ramki przez przeciągnięcie jej środka z wciśniętym ++c++;
- wymiary boków pokazywane podczas rysowania na scenach `GEO`;
- wybór adnotacji z mapy i z listy, przybliżenie dwukrotnym kliknięciem;
- zmiana klasy wybranego obiektu wprost z listy (zakładka **Rysowanie**) - ikona obok
  kosza rozwija pod nazwą listę klas; wybór z listy od razu zmienia klasę;
- usuwanie klawiszem ++delete++ poza trybem rysowania.

Do scen `GEO` można włączyć podkład mapowy. Dla scen rastrowych **panel wyświetlania**
pozwala regulować rozciągnięcie tonalne wprost na **histogramie** (dwa uchwyty percentyli,
presety `p2–98`, `1–99`, `μ±2σ`, `μ±3σ`, pełny zakres, skala log/liniowa, a dla SAR dodatkowo
`SAR 1–99,8%`) oraz - w sekcji „Zaawansowane" - jasność, kontrast i gammę. Dla scen
wielopasmowych histogram można przełączyć między **pasmami** (osobna krzywa dla R, G i B)
a **jasnością** postrzeganą; przełącznik zmienia tylko wykres, nie obraz.

**Zakres statystyk** decyduje, z jakich pikseli liczone są progi rozciągnięcia:

- **Scena** (domyślnie) - progi z histogramu całej sceny. Sąsiednie kafelki i ten sam obiekt
  w różnych miejscach sceny mają identyczną jasność.
- **Widok** - progi z bieżącego widoku mapy, przeliczane po każdym przesunięciu lub zoomie
  (jak „Updated canvas" w QGIS). Przydaje się w SAR, gdy scena łączy ciemną wodę i jasną
  zabudowę: oglądany fragment dostaje pełny kontrast, a wszystkie jego kafelki te same progi,
  więc nie powstają szwy. Kłódka **zamraża** progi bieżącego widoku, a znacznik w rogu mapy
  przypomina, że rozciągnięcie pochodzi z widoku. Wybór zakresu jest zapamiętywany dla
  projektu.

Sceny SAR otwierają się z nastawą `1–99,8%` i neutralną jasnością, kontrastem i gammą - górny
próg chroni jasne cele (pojazdy, zabudowę, statki) przed wypaleniem do bieli. Ustawienia
wyświetlania **nie zmieniają danych** - wpływają tylko na to, co widzisz; dataset i narzędzia
AI korzystają z własnego przetwarzania.

## Współrzędne (tylko sceny `GEO`)

Na scenach z georeferencją okno labelowania pokazuje współrzędne i pozwala się nimi
posługiwać. Funkcje są dostępne wyłącznie dla scen `GEO` - na scenach bez georeferencji
się nie pojawiają.

- **Odczyt kursora** - w lewym dolnym rogu mapy małe okienko na bieżąco pokazuje pozycję
  kursora w **MGRS** oraz w stopniach dziesiętnych (`szer., dł.`).
- **PPM → kopiuj współrzędne** - prawy przycisk myszy na scenie otwiera menu z trzema
  formatami do schowka:
    - **Kopiuj MGRS** - sam odnośnik MGRS (np. `34U EC 00833 86587`);
    - **Kopiuj szer., dł.** - stopnie dziesiętne;
    - **Kopiuj Współrzędne+ID** - MGRS + `szer., dł.` + identyfikator sceny w jednej linii.
- **Idź do współrzędnych** - w tym samym okienku wpisujesz **MGRS albo `szer., dł.`**
  (format wykrywany automatycznie) i skaczesz do punktu; trafione miejsce jest chwilowo
  podświetlane.

!!! tip "Po co to analitykowi"

    Gdy nie masz pewności co do obiektu w konkretnym miejscu, skopiuj **Współrzędne+ID**
    i wklej managerowi - dostaje MGRS, stopnie i identyfikator sceny naraz, więc od razu
    trafia w to samo miejsce przez „Idź do współrzędnych".

!!! info "Zabezpieczenie przed błędnym skokiem"

    Współrzędne spoza obszaru sceny są zwykle pomyłką, więc „Idź do" najpierw ostrzega i
    nie przeskakuje daleko poza scenę. Jeśli naprawdę chcesz tam trafić, potwierdź
    przyciskiem **Pokaż mimo to**. Błędny format jest sygnalizowany od razu.

## Atrybuty obliczane automatycznie

Dla każdej adnotacji na scenie z georeferencją wyliczane są wymiary, pole,
proporcje i azymut. Liczone są w metrach niezależnie od układu współrzędnych sceny,
więc wartości z różnych dostawców są porównywalne.

## Kontrola postępu

Na dużych scenach nie da się zapamiętać, co już przejrzano. Służy do tego
[siatka przeglądu](siatka-przegladu.md) - pozwala odnotować sprawdzone fragmenty,
także te bez obiektów.

## Zasady jakości

- obrysowuj obiekt **ściśle**, bez zapasu marginesu;
- trzymaj się jednej interpretacji klasy w całym projekcie;
- obiekty częściowo widoczne na krawędzi sceny oznaczaj zgodnie z ustaleniem zespołu -
  i konsekwentnie;
- gdy nie masz pewności co do klasy, lepiej zostawić obiekt nieoznaczony i zgłosić
  wątpliwość niż zgadywać.

!!! warning "Niekonsekwencja szkodzi bardziej niż braki"

    Model uczy się z tego, co dostanie. Ten sam obiekt raz oznaczony, raz pominięty
    jest dla niego sprzecznym sygnałem - gorszym niż konsekwentne pomijanie całej
    kategorii.

## Narzędzia AI

Predykcja, SAM i dopasowanie wzorca tworzą **propozycje**, a nie adnotacje.
Propozycja staje się adnotacją dopiero po akceptacji. Patrz
[Narzędzia AI](../ai-assistance/index.md).
