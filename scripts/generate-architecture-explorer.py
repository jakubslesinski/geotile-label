"""Zbuduj `docs/architecture/explorer.html` z modelu tresci i recznego ukladu.

    python scripts/generate-architecture-explorer.py

Wejscie:  docs/architecture/architecture-model.json   (TRESC — generowana)
          docs/architecture/explorer-layout.json      (UKLAD i nawigacja — reczne)
          scripts/templates/explorer.template.html    (szablon: style, skrypt, markup — reczny)
Wyjscie:  docs/architecture/explorer.html   (GENEROWANY)

DLACZEGO WSTRZYKNIECIE, A NIE WCZYTANIE W PRZEGLADARCE. Dokumentacja jest pakowana do
przegladania offline, a `fetch()` na `file://` jest blokowany przez przegladarki. Tresc
musi wiec trafic do pliku w czasie generowania, nie w czasie dzialania.

CO JEST GENEROWANE, A CO NIE. Generowane sa wylacznie bloki danych (P, A, N, relacje,
granice, mapy odnosnikow). Wspolrzedne wezlow, powiazania krzyzowe oraz caly styl i skrypt
pochodza z pliku ukladu i z szablonu — uklad pozostaje decyzja czlowieka.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARCH = REPO / "docs" / "architecture"
MODEL_PATH = ARCH / "architecture-model.json"
LAYOUT_PATH = ARCH / "explorer-layout.json"
TEMPLATE_PATH = REPO / "scripts" / "templates" / "explorer.template.html"
OUT_PATH = ARCH / "explorer.html"

BANNER = (
    "/* ---------------- data (GENEROWANE — nie edytowac recznie) ----------------\n"
    "   Tresc:  docs/architecture/architecture-model.json\n"
    "   Uklad:  docs/architecture/explorer-layout.json\n"
    "   Skrypt: scripts/generate-architecture-explorer.py\n"
    "   ------------------------------------------------------------------------- */"
)


def js(value) -> str:
    """Literal JS. JSON jest podzbiorem JS, wiec wystarczy zwarty dump."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def visible(item: dict) -> bool:
    return "explorer" in (item.get("appears_in") or [])


def refs(item: dict, key: str) -> list:
    """Odnosniki w formacie oczekiwanym przez szablon: [[etykieta, "#"], ...].

    Drugi element jest placeholderem — szablon rozwiazuje adres przez DOCSMAP/SRCMAP
    przy renderowaniu, a odnosniki do kodu zostaja nieaktywne, dopoki nie ustawiono
    REPO/REF (wydanie offline nie zaklada publicznego repozytorium)."""
    return [[label, "#"] for label in item.get(key, [])]


def build_pipeline(model: dict) -> str:
    rows = []
    for key in model["stage_order"]:
        stage = model["stages"][key]
        if not visible(stage):
            continue
        items = [list(i) if isinstance(i, list) else [i, ""] for i in stage["items"]]
        items += [list(i) if isinstance(i, list) else [i, ""] for i in stage.get("items_explorer", [])]
        rec = {
            "id": key,
            "kind": {"input": "Input", "hub": "Canonical hub", "output": "Output"}.get(
                stage.get("kind"), "Stage"
            ),
            "title": stage["title"],
            "summary": stage.get("summary_pl", ""),
            "items": items,
        }
        if stage.get("kind") == "hub":
            rec["hub"] = True
        if stage.get("engine"):
            rec["engine"] = stage["engine"]
        rec["docs"] = refs(stage, "docs")
        rec["source"] = refs(stage, "source")
        rows.append(" " + js(rec))
    return "const P=[\n" + ",\n".join(rows) + "\n];"


def build_architecture(model: dict, layout: dict) -> str:
    rows = []
    for key, comp in model["components"].items():
        if not visible(comp):
            continue
        place = layout["architecture"].get(key)
        if place is None:
            raise SystemExit(f"brak ukladu dla komponentu '{key}' w explorer-layout.json")
        rec = {
            "layer": place["layer"],
            "x": place["x"],
            "y": place["y"],
            "w": place["w"],
            "title": comp["title"],
            "summary": comp.get("summary_pl", ""),
            "parts": list(comp.get("parts", [])) + list(comp.get("parts_explorer", [])),
            "docs": refs(comp, "docs"),
            "source": refs(comp, "source"),
        }
        rows.append(f" {key}:{js(rec)}")
    return "const A={\n" + ",\n".join(rows) + "\n};"


def build_model_nodes(model: dict, layout: dict) -> str:
    rows = []
    for key, entity in model["entities"].items():
        if not visible(entity):
            continue
        place = layout["model"].get(key)
        if place is None:
            raise SystemExit(f"brak ukladu dla encji '{key}' w explorer-layout.json")
        attrs = list(entity["attrs"]) + list(entity.get("attrs_explorer", []))
        head = {"layer": place["layer"]}
        if place.get("sub") or entity.get("substereotype"):
            head["sub"] = place.get("sub") or entity["substereotype"]
        tail = {
            "summary": entity.get("summary_pl", ""),
            "attrs": attrs,
            "docs": refs(entity, "docs"),
            "source": refs(entity, "source"),
        }
        # `x`/`y` musza byc REFERENCJAMI do stalych siatki, nie liczbami — inaczej
        # przestawienie siatki w szablonie przestaloby dzialac.
        inner = js(head)[1:-1]
        rows.append(
            f" {key}:{{{inner},x:{place['col']},y:{place['row']},{js(tail)[1:-1]}}}"
        )
    return "const N={\n" + ",\n".join(rows) + "\n};"


def build_relations(model: dict, field: str, name: str) -> str:
    rows = [
        {"from": r["from"], "to": r["to"], "label": r["label"]}
        for r in model[field]
        if "explorer" in r.get("appears_in", [])
    ]
    return f"const {name}={js(rows)};"


def build_bounds(model: dict, field: str, name: str) -> str:
    rows = [
        {"ids": b["ids"], "label": b["label"]}
        for b in model[field]
        if "explorer" in b.get("appears_in", [])
    ]
    return f"const {name}={js(rows)};"


def replace_single(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(r"^const " + name + r"=.*?;\s*$", re.M)
    if not pattern.search(text):
        raise SystemExit(f"nie znaleziono deklaracji `const {name}=` w szablonie")
    return pattern.sub(lambda _: replacement, text, count=1)


def replace_block(text: str, name: str, opener: str, closer: str, replacement: str) -> str:
    start = text.index(f"const {name}={opener}")
    end = text.index(f"\n{closer};", start) + len(f"\n{closer};")
    return text[:start] + replacement + text[end:]


def build() -> str:
    """Zwroc pelna tresc `explorer.html`.

    Wydzielone z `main()`, zeby bramka mogla wygenerowac wynik w pamieci i porownac go
    z plikiem na dysku. Alternatywa — parsowanie blokow JavaScriptu regexem — okazala sie
    zawodna: cudzyslowowanie kluczy trafialo w srodek napisow zawierajacych `, slowo:`
    (np. summary „Lokalny, offline: SAM…”). Porownanie regenerowanej tresci nie ma tej
    klasy bledow i wychwytuje zarowno nieaktualna generacje, jak i reczna edycje pliku.
    """
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    html = html.replace("/* ---------------- data ---------------- */", BANNER, 1)
    html = replace_block(html, "P", "[", "]", build_pipeline(model))
    html = replace_block(html, "A", "{", "}", build_architecture(model, layout))
    html = replace_block(html, "N", "{", "}", build_model_nodes(model, layout))
    html = replace_single(html, "AREL", build_relations(model, "component_relations", "AREL"))
    html = replace_single(html, "ABOUND", build_bounds(model, "component_boundaries", "ABOUND"))
    html = replace_single(html, "REL", build_relations(model, "entity_relations", "REL"))
    html = replace_single(html, "MBOUND", build_bounds(model, "entity_boundaries", "MBOUND"))
    html = replace_single(html, "TECH", f"const TECH={js(model['tech_strip'])};")
    html = replace_single(html, "DOCSMAP", f"const DOCSMAP={js(model['docs_map'])};")
    html = replace_single(html, "SRCMAP", f"const SRCMAP={js(model['source_map'])};")
    html = replace_single(
        html, "STEREO", f"const STEREO={js(model['stereotypes'])};"
    )
    grid = layout.get("grid_constants", {"MX": 30, "MRX": 420, "MX3": 810, "MW": 300})
    rows = layout.get("grid_rows", {"r1": 70, "r2": 238, "r3": 406, "r4": 574, "r5": 742})
    html = replace_single(
        html,
        "MX",
        "const " + ",".join(f"{k}={v}" for k, v in grid.items()) + f",MR={js(rows)};",
    )
    for name in ("P2E", "P2A", "A2E"):
        links = layout.get("cross_links", {}).get(name)
        if links is not None:
            html = replace_single(html, name, f"const {name}={js(links)};")

    return html


def main() -> int:
    html = build()
    OUT_PATH.write_text(html, encoding="utf-8", newline="\n")
    print(f"{OUT_PATH.name}: {len(html)} B, {len(html.splitlines())} linii")
    return 0


if __name__ == "__main__":
    sys.exit(main())
