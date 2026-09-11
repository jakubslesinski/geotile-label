"""Hierarchiczny podglad importu: zrodlo → dostawa → akwizycja → produkt (P2.1).

Plaska tabela pakietow zmusza czytelnika do samodzielnego skladania w glowie, ktore wiersze
naleza do tej samej akwizycji, a ktore sa osobnymi przelotami. Ten modul robi to grupowanie
po stronie backendu, bo klucz akwizycji jest GRAMATYKA DOSTAWCY — mieszka w
`providers/*.py` razem z wiazaniem metadanych i nie ma powodu, zeby powtarzac go w UI.

**Swiadome ograniczenie zakresu.** Sekcja 2.1 roadmapy wymienia dziesiec grup informacji do
pokazania. Wyswietlenie ich wszystkich przy kazdym produkcie zamienia podglad w zrzut danych,
w ktorym nie widac tego, co wymaga decyzji. Dlatego dzielimy je na trzy poziomy:

1. **wiersz** — to, co pozwala przejrzec liste i wychwycic problem: etykieta produktu,
   status, kompletnosc TYLKO gdy niepelna, licznik ostrzezen i konfliktow;
2. **szczegol po rozwinieciu** — to, co objasnia JEDEN produkt: powod automatycznego wyboru,
   czas i GSD ze zrodlem, rozklad assetow wg roli, tresc konfliktow, powiazane archiwum,
   szacunek kosztu przygotowania;
3. **raport importu** (P1.6) — cala reszta: sciezki, rozmiary per plik, identity strength,
   diagnostyka kazdej sceny.

Zasada, ktora rozstrzyga przypadki sporne: **pokazujemy roznice, nie zgodnosc**. `6/6` nie
trafia do wiersza, bo kompletnosc bez brakow nie wymaga uwagi — `4/6` juz tak. Z tego samego
powodu nie ma tu porownania dostawcy zadeklarowanego z wykrytym: w podgladzie nie ma jeszcze
wykrytego (resolver wybiera sie deklaracja zrodla), a rozpoznanie z metadanych nalezy do P0.5.
Pole, ktore zawsze pokazuje zgodnosc, jest gorsze niz brak pola.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from services.scene_packages.archives import ARCHIVE_STATUSES
from services.scene_packages.providers import capella as capella_grammar
from services.scene_packages.providers import iceye as iceye_grammar
from services.scene_packages.providers import worldview as worldview_grammar

#: Gramatyki, ktore potrafia wskazac akwizycje dla sciezki measurement.
_ACQUISITION_GRAMMARS = {
    "iceye": iceye_grammar.binding_key,
    "umbra": iceye_grammar.binding_key,
    "blacksky": iceye_grammar.binding_key,
    "capella": capella_grammar.binding_key,
    "worldview": worldview_grammar.binding_key,
}

#: Grupy rol assetow pokazywane w rozwinietym szczegole. Reszta rol wpada do `auxiliary`,
#: bo dla decyzji uzytkownika nie ma znaczenia, czy plik jest `layout` czy `footprint`.
_ROLE_GROUPS = {
    "measurement": "measurement",
    "product_metadata": "metadata",
    "delivery_metadata": "metadata",
    "rpc": "metadata",
    "tile_manifest": "metadata",
    "browse": "browse",
    "layout": "browse",
}

#: Ile brakujacych czesci wymieniamy z nazwy w szczegole.
MAX_LISTED_MISSING = 10
#: Ile bajtow wyjscia przypada na bajt wejscia panchromatycznego przy pansharpeningu:
#: trzy pasma zamiast jednego, ta sama rozdzielczosc i ta sama glebia.
PANSHARPEN_OUTPUT_RATIO = 3


def _measurement_paths(package: dict[str, Any]) -> list[str]:
    selection = package.get("selection") or {}
    chosen = set(selection.get("identity_asset_ids") or selection.get("asset_ids") or [])
    return [
        str(asset.get("package_relative_path") or asset.get("relative_path") or "")
        for asset in package.get("assets") or []
        if asset.get("asset_id") in chosen
    ]


def _delivery_key(package: dict[str, Any]) -> tuple[str, str]:
    """Dostawa to najwyzszy katalog pakietu; pakiet w korzeniu zrodla jest wlasna dostawa."""
    relative = str(package.get("package_root_relative") or "").strip("/")
    if not relative or relative == ".":
        return ("__root__", "/")
    head = PurePosixPath(relative).parts[0]
    return (head, head)


def _acquisition_key(package: dict[str, Any]) -> str | None:
    grammar = _ACQUISITION_GRAMMARS.get(str(package.get("provider") or ""))
    if grammar is None:
        return None
    for path in _measurement_paths(package):
        key = grammar(path)
        if key.acquisition:
            return key.acquisition
    return None


def _polarization_or_bands(package: dict[str, Any]) -> str | None:
    """Etykieta pasm albo polaryzacji — z gramatyki dostawcy, nie z pola, ktorego nie ma."""
    selection = package.get("selection") or {}
    declared = selection.get("polarization_or_bands") or []
    if declared:
        return "/".join(str(item) for item in declared)
    rgb_bands = selection.get("rgb_bands")
    if rgb_bands:
        return "RGB " + ",".join(str(band) for band in rgb_bands)
    provider = str(package.get("provider") or "")
    parser = {
        "iceye": iceye_grammar.parse_product_name,
        "umbra": iceye_grammar.parse_product_name,
        "blacksky": iceye_grammar.parse_product_name,
        "capella": capella_grammar.parse_product_name,
    }.get(provider)
    if parser is None:
        return None
    for path in _measurement_paths(package):
        polarization = getattr(parser(PurePosixPath(path).name), "polarization", None)
        if polarization:
            return str(polarization)
    return None


def _component_counts(package: dict[str, Any]) -> dict[str, int]:
    selection = package.get("selection") or {}
    components = selection.get("source_components") or {}
    return {name: len(ids or []) for name, ids in sorted(components.items())}


def product_label(package: dict[str, Any]) -> str:
    """Krotka etykieta produktu, np. `GRD VV`, `PAN 6/6`, `MUL 2 + PAN 2`."""
    selection = package.get("selection") or {}
    if package.get("package_kind") == "archive":
        return "ARCHIWUM"
    product_type = str(selection.get("product_type") or "?")
    components = _component_counts(package)
    if len(components) > 1:
        shorthand = {"multispectral": "MUL", "panchromatic": "PAN", "pansharpened": "PSH"}
        return " + ".join(
            f"{shorthand.get(name, name.upper())} {count}"
            for name, count in components.items()
        )
    label = product_type
    qualifier = _polarization_or_bands(package)
    if qualifier:
        label = f"{label} {qualifier}"
    declared = selection.get("declared_parts")
    parts = len(selection.get("asset_ids") or [])
    if declared:
        label = f"{label} {parts}/{declared}"
    elif parts > 1:
        label = f"{label} ×{parts}"
    return label


#: Awaryjne odwzorowanie starej roli, gdy inwentarz nie ma jeszcze `asset_role`.
_LEGACY_ROLE_GROUPS = {"raster_candidate": "measurement", "primary_raster": "measurement",
                       "metadata": "metadata", "archive": "auxiliary"}


def _asset_groups(package: dict[str, Any]) -> dict[str, int]:
    groups = {"measurement": 0, "metadata": 0, "browse": 0, "auxiliary": 0}
    for asset in package.get("assets") or []:
        contract_role = str(asset.get("asset_role") or "")
        if contract_role:
            groups[_ROLE_GROUPS.get(contract_role, "auxiliary")] += 1
        else:
            # Inwentarz sprzed P0.2 (albo z cache) nie ma roli kontraktowej. Zliczenie go
            # jako `auxiliary` pokazywaloby dostawe bez ani jednego assetu measurement.
            groups[_LEGACY_ROLE_GROUPS.get(str(asset.get("role") or ""), "auxiliary")] += 1
    return groups


def _selection_reason(package: dict[str, Any]) -> dict[str, str]:
    """Dlaczego wybrano ten produkt. Kod jest stabilny, komunikat jest dla czlowieka."""
    selection = package.get("selection") or {}
    selected_by = str(selection.get("selected_by") or "resolver")
    if selected_by == "user_override":
        return {
            "code": "user_override",
            "message": "Ręczny wybór plików - poza wariantami rozpoznanymi przez resolver",
        }
    if selected_by == "user":
        return {"code": "user_choice", "message": "Wybór użytkownika spośród wariantów produktu"}
    status = str(selection.get("status") or "")
    if status in ARCHIVE_STATUSES:
        return {"code": "archive", "message": "Archiwum - nie jest sceną do etykietowania"}
    alternatives = selection.get("alternatives") or []
    if status == "decision_required":
        return {
            "code": "needs_decision",
            "message": f"Resolver nie rozstrzygnął: {len(alternatives)} alternatyw(y)",
        }
    if alternatives:
        return {
            "code": "preferred_over_alternatives",
            "message": f"Wybrany spośród {len(alternatives) + 1} wariantów produktu",
        }
    return {"code": "only_candidate", "message": "Jedyny kandydat w tej akwizycji"}


def _preparation_estimate(package: dict[str, Any], free_bytes: int | None) -> dict[str, Any] | None:
    """Szacunek kosztu przygotowania — TYLKO dla produktow, ktore go wymagaja.

    Podstawa sa zadeklarowane rozmiary plikow panchromatycznych: wynik pansharpeningu ma te
    sama rozdzielczosc i glebie, a trzy pasma zamiast jednego. Szacunek jest z natury zgrubny
    i tak jest opisany — dokladna liczba wymagalaby otwarcia rastrow, czego preview nie robi.
    """
    selection = package.get("selection") or {}
    if selection.get("raster_kind") != "derived":
        return None
    pan_ids = set(selection.get("panchromatic_asset_ids") or [])
    if not pan_ids:
        return None
    pan_bytes = sum(
        int(asset.get("size") or 0)
        for asset in package.get("assets") or []
        if asset.get("asset_id") in pan_ids
    )
    estimated = pan_bytes * PANSHARPEN_OUTPUT_RATIO
    return {
        "estimated_output_bytes": estimated,
        "free_bytes": free_bytes,
        "fits": None if free_bytes is None else free_bytes > estimated,
        "method": "pan_bytes_times_band_count",
    }


def _diagnostics(package: dict[str, Any]) -> dict[str, Any]:
    diagnostics = (package.get("selection") or {}).get("diagnostics") or {}
    return {
        "blocking": list(diagnostics.get("errors") or []),
        "warnings": list(diagnostics.get("warnings") or []),
        "metadata_conflicts": len(diagnostics.get("metadata_conflicts") or []),
    }


def _product_node(
    package: dict[str, Any],
    *,
    free_bytes: int | None,
    archives_by_delivery: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    selection = package.get("selection") or {}
    diagnostics = _diagnostics(package)
    delivery = _delivery_key(package)[0]
    return {
        "package_id": package.get("package_id"),
        "decision_uid": package.get("decision_uid"),
        "package_kind": package.get("package_kind") or "delivery",
        "label": product_label(package),
        "status": selection.get("status"),
        "product_type": selection.get("product_type"),
        "raster_kind": selection.get("raster_kind"),
        "part_count": len(selection.get("asset_ids") or []),
        "declared_parts": selection.get("declared_parts"),
        # `6/6` nie trafia do wiersza: kompletnosc bez brakow nie wymaga uwagi.
        "incomplete": selection.get("completeness") == "partial",
        "warning_count": len(diagnostics["warnings"]),
        "blocking_count": len(diagnostics["blocking"]),
        "detail": {
            # Dostawca ZADEKLAROWANY i WYKRYTY nie sa tu porownywane, bo w podgladzie nie ma
            # jeszcze wykrytego: resolver jest wybierany deklaracja zrodla, a rozpoznanie
            # z metadanych (PHR kontra PNEO, sekcja 4.5) nalezy do P0.5 — i to tam parser
            # PHR jest naprawiany. Puste pole bylo by tu zawsze zgodne, czyli bezuzyteczne.
            "provider": package.get("provider"),
            "processing_level": selection.get("processing_level"),
            "polarization_or_bands": _polarization_or_bands(package),
            "completeness": selection.get("completeness"),
            "missing_parts": list(selection.get("missing_parts") or [])[:MAX_LISTED_MISSING],
            "missing_parts_total": len(selection.get("missing_parts") or []),
            "parts_source": "tile_manifest" if selection.get("declared_parts") else None,
            "assets": _asset_groups(package),
            "selection_reason": _selection_reason(package),
            "diagnostics": diagnostics,
            "archive": archives_by_delivery.get(delivery) or [],
            "preparation": _preparation_estimate(package, free_bytes),
            "alternatives": [
                {"label": item.get("label"), "asset_ids": item.get("asset_ids") or []}
                for item in (selection.get("alternatives") or [])
            ],
        },
    }


def _archive_delivery_key(package: dict[str, Any], known_deliveries: set[str]) -> str:
    """Do ktorej dostawy nalezy archiwum.

    Dwie konwencje z korpusu wymagaja dwoch regul. Archiwum Airbusa lezy WEWNATRZ katalogu
    dostawy, wiec wystarczy jego wlasny klucz. Archiwum WV2 lezy w korzeniu zrodla obok
    rozpakowanego katalogu — wtedy dostawe wskazuje katalog najwyzszego poziomu z wnetrza
    archiwum (`delivery_root`). Bez tego kazdy ZIP tworzylby wlasna, pusta dostawe.
    """
    own = _delivery_key(package)[0]
    if own in known_deliveries:
        return own
    delivery_root = str(((package.get("selection") or {}).get("archive") or {}).get("delivery_root") or "")
    if delivery_root in known_deliveries:
        return delivery_root
    return own


def _archive_summary(package: dict[str, Any]) -> dict[str, Any]:
    selection = package.get("selection") or {}
    archive = selection.get("archive") or {}
    return {
        "package_id": package.get("package_id"),
        "name": PurePosixPath(str(package.get("package_root_relative") or "")).name,
        "status": selection.get("status"),
        "extracted_present": archive.get("extracted_present"),
        "extracted_total": archive.get("extracted_total"),
        "compressed_bytes": archive.get("compressed_bytes"),
    }


def build(
    packages: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None = None,
    *,
    free_bytes: int | None = None,
) -> dict[str, Any]:
    """Zbuduj drzewo podgladu z plaskiej listy pakietow."""
    sources_by_id = {
        str(source.get("source_id") or ""): source for source in (sources or [])
    }
    products = [item for item in packages if item.get("package_kind") != "archive"]
    archives = [item for item in packages if item.get("package_kind") == "archive"]

    # Dostawy powstaja WYLACZNIE z pakietow produktowych. Archiwum nie jest dostawa —
    # jest jej druga reprezentacja i dopina sie do juz istniejacej.
    known_deliveries = {_delivery_key(item)[0] for item in products}
    archives_by_delivery: dict[str, list[dict[str, Any]]] = {}
    for package in archives:
        archives_by_delivery.setdefault(
            _archive_delivery_key(package, known_deliveries), []
        ).append(_archive_summary(package))

    tree: dict[str, dict[str, Any]] = {}

    def source_node_for(package: dict[str, Any]) -> dict[str, Any]:
        source_id = str(package.get("source_id") or "")
        return tree.setdefault(source_id, {
            "source_id": source_id,
            "provider": (sources_by_id.get(source_id) or {}).get("provider"),
            "root_path": (sources_by_id.get(source_id) or {}).get("root_path"),
            "deliveries": {},
        })

    for package in products:
        source_node = source_node_for(package)
        delivery_key, delivery_label = _delivery_key(package)
        delivery_node = source_node["deliveries"].setdefault(delivery_key, {
            "delivery_id": delivery_key,
            "label": delivery_label,
            "acquisitions": {},
            "archives": archives_by_delivery.get(delivery_key) or [],
        })
        acquisition_key = _acquisition_key(package) or str(
            (package.get("selection") or {}).get("provider_scene_id")
            or package.get("package_root_relative")
            or package.get("package_id")
        )
        acquisition_node = delivery_node["acquisitions"].setdefault(acquisition_key, {
            "acquisition_id": acquisition_key,
            "products": [],
        })
        acquisition_node["products"].append(
            _product_node(package, free_bytes=free_bytes, archives_by_delivery=archives_by_delivery)
        )

    # Archiwum bez odpowiadajacej dostawy (`archive_only`) jest jedynym sladem po tej
    # dostawie, wiec musi byc widoczne jako wlasny wezel — inaczej zniknie tak samo jak
    # przed P1.3a, tylko o poziom wyzej.
    for package in archives:
        delivery_key = _archive_delivery_key(package, known_deliveries)
        if delivery_key in known_deliveries:
            continue
        source_node = source_node_for(package)
        source_node["deliveries"].setdefault(delivery_key, {
            "delivery_id": delivery_key,
            "label": _delivery_key(package)[1],
            "acquisitions": {},
            "archives": archives_by_delivery.get(delivery_key) or [],
        })

    return {
        "schema_name": "geotile_scene_import_preview_tree",
        "schema_version": 1,
        "free_bytes": free_bytes,
        "sources": [
            {
                **source_node,
                "deliveries": [
                    {
                        **delivery_node,
                        "acquisitions": [
                            delivery_node["acquisitions"][key]
                            for key in sorted(delivery_node["acquisitions"])
                        ],
                    }
                    for _key, delivery_node in sorted(source_node["deliveries"].items())
                ],
            }
            for _source_id, source_node in sorted(tree.items())
        ],
    }


def free_space_bytes(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None
