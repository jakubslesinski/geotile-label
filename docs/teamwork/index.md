# Praca zespołowa

Workflow zespołowy rozdziela **adnotowanie pełnych scen** od **budowania datasetu**.
Analitycy przekazują lekkie paczki adnotacji, manager scala je w projekcie zbiorczym i
prowadzi kontrolę jakości.

Nie ma kont ani logowania. Wszystko opiera się na roli projektu i adresie autora
wpisanym w jego profilu.

## Zacznij tutaj

<div class="grid cards" markdown>

-   :material-account-edit: **Jestem analitykiem**

    ---

    Eksport paczki, obieg poprawek po recenzji.

    [Workflow analityka](workflow-analityka.md)

-   :material-account-tie: **Jestem managerem**

    ---

    Projekt zbiorczy, import paczek, werdykty, dataset.

    [Szybki start managera](szybki-start-managera.md)

-   :material-shield-account: **Jak to działa**

    ---

    Role, własność adnotacji i zakres podmiany.

    [Role projektu](role-projektu.md)

</div>

## Pętla w skrócie

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

## Trzy zasady, na których to stoi

**Podmiana, nie doklejanie.** Import zastępuje adnotacje danego właściciela w danej
scenie w całości. Dzięki temu propagują się nie tylko nowe obiekty, ale też **poprawki
i usunięcia**.

**Werdykt na poziomie sceny.** Manager ocenia pracę, nie każdy obiekt z osobna - przy
tysiącach adnotacji nic innego nie jest wykonalne.

**Recenzja bez geometrii.** Paczka wracająca do analityka niesie wyłącznie werdykty i
komentarze, więc import recenzji nie może niczego popsuć w jego scenach.

## Zanim rozdasz pracę

1. **Uzgodnij klasy** - ten sam `classes.json` u wszystkich. Nazwa klasy jest kluczem
   dopasowania.
2. **Uzgodnij adresy autorów** - literówka tworzy w projekcie zbiorczym drugiego
   analityka.
3. **Uzgodnij profil projektu** - modalność, georeferencję i tryb adnotacji.
4. **Uzgodnij przygotowanie scen** - rozjazd widoków roboczych wychodzi dopiero przy
   imporcie, czyli po wykonaniu pracy.

!!! danger "Własność chroni przed pomyłką, nie przed złą wolą"

    Adres autora to tekst wpisany ręcznie, a paczki nie są podpisane. To świadomy
    wybór dla narzędzia offline w zespole, który sobie ufa - ale nie buduj na tym
    rozliczalności formalnej. Szczegóły w [Rolach projektu](role-projektu.md).
