"""Generuj zrodla d2 trzech rysunkow architektury z modelu tresci.

    python scripts/generate-architecture-d2.py

Wejscie:  docs/architecture/architecture-model.json
Wyjscie:  docs/architecture/src/rys1-data-model.d2
          docs/architecture/src/rys2-architecture.d2
          docs/architecture/src/rys3-pipeline.d2

ROLA TYCH PLIKOW. To NIE sa zrodla rysunkow artykulu — uklad rysunkow jest reczny i zyje
w Figmie. Pliki d2 sa **podgladem tresci do recenzji**: pozwalaja zobaczyc i zdiffowac to,
co model twierdzi, zanim ktokolwiek dotknie Figmy. Rys. 3 dostaje tu swoje pierwsze
tekstowe zrodlo w historii projektu.

Emitowana jest wylacznie tresc oznaczona `appears_in` dla danego rysunku — dokladnie ta,
ktora ma sie na nim znalezc. Pola `attrs_explorer` / `items_explorer` sa POMIJANE, bo
naleza do explorera (DESIGN_DECISIONS.md, architecture-diagrams).

Render (na maszynie z zainstalowanym d2):
    d2 --theme 0 --pad 24 docs/architecture/src/rys1-data-model.d2 rys1.svg
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODEL_PATH = REPO / "docs" / "architecture" / "architecture-model.json"
OUT_DIR = REPO / "docs" / "architecture" / "src"

HEADER = (
    "# PLIK GENEROWANY — nie edytowac recznie.\n"
    "# Zrodlo tresci: docs/architecture/architecture-model.json\n"
    "# Generator:     scripts/generate-architecture-d2.py\n"
    "# Uklad rysunkow artykulu jest RECZNY i zyje w Figmie; ten plik sluzy recenzji tresci.\n"
)


def q(text: str) -> str:
    """Zacytuj etykiete d2. Cudzyslowy w tresci nie wystepuja, ale nie zakladamy tego."""
    return '"' + str(text).replace('"', "'") + '"'


def visible(item: dict, figure: str) -> bool:
    return figure in (item.get("appears_in") or [])


def stereotype_label(model: dict, entity: dict) -> str | None:
    base = model["stereotypes"].get(entity.get("stereotype") or "")
    if not base:
        return None
    sub = entity.get("substereotype")
    return base if not sub else f"{base[:-1]} · {sub}»"


def emit_rys1(model: dict) -> str:
    out = [HEADER, "# Rys. 1 — kanoniczny model geospatial datasetu (UML class)", "direction: down", ""]
    for key, entity in model["entities"].items():
        if not visible(entity, "rys1"):
            continue
        out.append(f"{entity['title']}: {{")
        out.append("  shape: class")
        stereo = stereotype_label(model, entity)
        if stereo:
            out.append(f"  {q(stereo)}: {q('')}")
        for attr in entity["attrs"]:
            # `nazwa: typ` gdy atrybut niesie dwuczlonowy opis, inaczej sam `nazwa: string`
            if ": " in attr:
                name, value = attr.split(": ", 1)
                out.append(f"  {q(name)}: {q(value)}")
            else:
                out.append(f"  {q(attr)}: string")
        out.append("}")
        out.append("")
    out.append("# relacje")
    for rel in model["entity_relations"]:
        if not visible(rel, "rys1"):
            continue
        out.append(f"{rel['from']} -> {rel['to']}: {q(rel['label'])}")
    out.append("")
    out.append("# granice warstw (w d2 tylko komentarz — grupowanie robi uklad w Figmie)")
    for bound in model["entity_boundaries"]:
        if not visible(bound, "rys1"):
            continue
        out.append(f"#   {bound['label']}: {', '.join(bound['ids'])}")
    return "\n".join(out) + "\n"


def emit_rys2(model: dict) -> str:
    out = [HEADER, "# Rys. 2 — architektura C4-style (kontenery i komponenty)", "direction: down", ""]
    boundaries = [b for b in model["component_boundaries"] if visible(b, "rys2")]
    grouped = {cid for b in boundaries for cid in b["ids"]}

    for key, comp in model["components"].items():
        if not visible(comp, "rys2") or key in grouped:
            continue
        out.append(_component_block(key, comp, indent=""))
        out.append("")

    for bound in boundaries:
        out.append(f"{_ident(bound['label'])}: {q(bound['label'])} {{")
        for cid in bound["ids"]:
            comp = model["components"].get(cid)
            if not comp or not visible(comp, "rys2"):
                continue
            out.append(_component_block(cid, comp, indent="  "))
        out.append("}")
        out.append("")

    place = {}
    for bound in boundaries:
        for cid in bound["ids"]:
            place[cid] = f"{_ident(bound['label'])}.{cid}"

    out.append("# przeplywy")
    for rel in model["component_relations"]:
        if not visible(rel, "rys2"):
            continue
        src = place.get(rel["from"], rel["from"])
        dst = place.get(rel["to"], rel["to"])
        out.append(f"{src} -> {dst}: {q(rel['label'])}")
    return "\n".join(out) + "\n"


def _ident(label: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in label]
    ident = "".join(keep)
    while "__" in ident:
        ident = ident.replace("__", "_")
    return ident.strip("_")


def _fence(body: str) -> str:
    """Ogrodzenie bloku `|md`, dluzsze niz najdluzszy ciag `|` w tresci.

    d2 zamyka blok na pierwszym ciagu pionowych kresek rownym otwierajacemu, wiec
    `|` W ETYKIECIE ROZWALA PLIK: `- affine | gcp_tps` w komponencie SceneGeoModel
    urywalo blok w polowie i `rys2-architecture.d2` sie nie kompilowal. Bramka tego
    nie widziala, bo porownuje teksty, a nie kompiluje d2 — wyszlo przy renderze.
    """
    longest = max((len(m) for m in re.findall(r"\|+", body)), default=0)
    return "|" * (longest + 1)


def _component_block(key: str, comp: dict, indent: str) -> str:
    body = [f"{indent}  ### {comp['title']}"]
    if comp.get("tag"):
        body.append(f"{indent}  _[{comp['tag']}]_")
    for part in comp.get("parts", []):
        body.append(f"{indent}  - {part}")
    text = "\n".join(body)
    fence = _fence(text)
    return "\n".join([f"{indent}{key}: {fence}md", text, f"{indent}{fence}"])


def emit_rys3(model: dict) -> str:
    out = [HEADER, "# Rys. 3 — architektura funkcjonalna i potok danych", "direction: right", ""]
    order = [s for s in model["stage_order"] if visible(model["stages"][s], "rys3")]
    for key in order:
        stage = model["stages"][key]
        out.append(f"{key}: {q(stage['title'])} {{")
        if stage.get("kind") == "hub":
            out.append("  style.fill: \"#eef0ff\"")
        for item in stage["items"]:
            label, detail = (item + ["", ""])[:2] if isinstance(item, list) else (item, "")
            text = label if not detail else f"{label} — {detail}"
            out.append(f"  {_ident(label)}: {q(text)}")
        if stage.get("engine"):
            out.append(f"  # silnik: {' · '.join(stage['engine'])}")
        out.append("}")
        out.append("")
    out.append("# kolejnosc etapow")
    for left, right in zip(order, order[1:]):
        out.append(f"{left} -> {right}")
    out.append("")
    out.append(f"# wspolny stos technologiczny: {' · '.join(model['tech_strip'])}")
    return "\n".join(out) + "\n"


def main() -> int:
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, emitter in (
        ("rys1-data-model.d2", emit_rys1),
        ("rys2-architecture.d2", emit_rys2),
        ("rys3-pipeline.d2", emit_rys3),
    ):
        path = OUT_DIR / name
        text = emitter(model)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append((name, len(text.splitlines())))
    for name, lines in written:
        print(f"{name:26s} {lines:4d} linii")
    return 0


if __name__ == "__main__":
    sys.exit(main())
