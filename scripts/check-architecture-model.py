"""Bramka dryfu: czy konsumenci diagramow zgadzaja sie z modelem tresci.

    python scripts/check-architecture-model.py
    python scripts/check-architecture-model.py --pending rys2,rys3

Sprawdza trzy rzeczy:
  1. SPOJNOSC MODELU — `appears_in` z dozwolonej listy, relacje i granice wskazujace
     istniejace identyfikatory, kompletny `stage_order`, zywe sciezki `source_map`
     i `docs_map`.
  2. EXPLORER — bloki danych w `explorer.html` odpowiadaja modelowi. Wychwytuje
     recznа edycje pliku generowanego zamiast modelu.
  3. RYSUNKI — teksty w eksportach SVG pokrywaja etykiety oznaczone `rys1`/`rys2`/`rys3`.

TRZY REGULY, BEZ KTORYCH BRAMKA KLAMIE. Wynikaja z bledow popelnionych przy jej pisaniu
(DESIGN_DECISIONS.md, architecture-diagrams):

  a) Porownanie NIECZULE NA WIELKOSC LITER — Figma stosuje wersaliki jako styl, wiec
     `Canonical / annotation layer` renderuje sie jako `CANONICAL / ANNOTATION LAYER`.
  b) Porownywanie CALYCH etykiet, nigdy podciagow — `derived_scenes` w `Project storage`
     to legalna tresc Rys. 2, a nie wyciek atrybutu o tej samej nazwie z explorera.
  c) Tresc oznaczona wylacznie dla explorera NIE MOZE trafic na rysunki i odwrotnie —
     dlatego kazdy artefakt porownuje sie tylko z tym, co dla niego oznaczone.

Rysunki zyja w OSOBNYM repozytorium artykulu, ktorego nie musi byc na maszynie budujacej
wydanie. Gdy katalog `figures` jest nieobecny, sprawdzenia 1 i 2 i tak sie wykonuja, a
rysunki sa POMIJANE — nie zglaszane jako blad. Sciezke wskazuje `GEOTILE_PAPER_ROOT`.

Bramka generujaca falszywe alarmy jest gorsza od jej braku: zostanie wylaczona przy
pierwszym. Dlatego `--pending` pozwala jawnie wymienic rysunki, ktore czekaja jeszcze na
aktualizacje w Figmie — sa raportowane, ale nie przewracaja bramki.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Repozytorium artykulu jest OSOBNE i nie musi istniec na maszynie budujacej wydanie.
# Sciezka nie jest zaszyta na sztywno: `GEOTILE_PAPER_ROOT` pozwala ja wskazac, a domyslnie
# szukamy katalogu obok repozytorium aplikacji.
PAPER = Path(os.environ.get("GEOTILE_PAPER_ROOT") or (REPO.parent / "geotile-label-paper"))
FIGURES = PAPER / "figures"
ARCH = REPO / "docs" / "architecture"
VALID_TARGETS = {"rys1", "rys2", "rys3", "explorer"}

FIGURE_FILES = {
    "rys1": "Rys.1 - Model geospatial datasetu (EN, 2026-09).svg",
    "rys2": "Rys.2 - C4-style container & component architecture (EN, 2026-09).svg",
    "rys3": "Rys.3 - Functional architecture & pipeline (EN, 2026-09).svg",
}


def norm(text: str) -> str:
    """Regula (a): jedna spacja, bez marginesow, bez wielkosci liter."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def load_model() -> dict:
    return json.loads((ARCH / "architecture-model.json").read_text(encoding="utf-8"))


# --- 1. spojnosc modelu ---------------------------------------------------------------

def check_model(model: dict) -> list[str]:
    errors: list[str] = []
    ent, comp, stg = set(model["entities"]), set(model["components"]), set(model["stages"])

    for kind, coll in (("entity", model["entities"]), ("component", model["components"]),
                       ("stage", model["stages"])):
        for key, item in coll.items():
            targets = set(item.get("appears_in") or [])
            if not targets:
                errors.append(f"{kind} {key}: brak appears_in")
            elif targets - VALID_TARGETS:
                errors.append(f"{kind} {key}: nieznane appears_in {sorted(targets - VALID_TARGETS)}")
            if not item.get("title"):
                errors.append(f"{kind} {key}: brak title")
            for label in item.get("docs", []):
                if label not in model["docs_map"]:
                    errors.append(f"{kind} {key}: docs '{label}' spoza docs_map")
            for label in item.get("source", []):
                if label not in model["source_map"]:
                    errors.append(f"{kind} {key}: source '{label}' spoza source_map")

    for rel in model["entity_relations"]:
        for side in ("from", "to"):
            if rel[side] not in ent:
                errors.append(f"entity_relations: nieznana encja {rel[side]}")
    for rel in model["component_relations"]:
        for side in ("from", "to"):
            if rel[side] not in comp:
                errors.append(f"component_relations: nieznany komponent {rel[side]}")
    for bound in model["entity_boundaries"]:
        errors += [f"entity_boundaries '{bound['label']}': nieznana encja {i}"
                   for i in bound["ids"] if i not in ent]
    for bound in model["component_boundaries"]:
        errors += [f"component_boundaries '{bound['label']}': nieznany komponent {i}"
                   for i in bound["ids"] if i not in comp]
    if set(model["stage_order"]) != stg:
        errors.append("stage_order nie pokrywa dokladnie zbioru etapow")

    for label, rel in model["source_map"].items():
        if not (REPO / rel).is_file():
            errors.append(f"source_map '{label}': brak pliku {rel}")
    for label, rel in model["docs_map"].items():
        if not (REPO / "docs" / rel.replace(".html", ".md")).is_file():
            errors.append(f"docs_map '{label}': brak strony {rel}")
    return errors


# --- 2. explorer -----------------------------------------------------------------------

def check_explorer(model: dict) -> list[str]:
    """Porownaj `explorer.html` z wynikiem swiezej generacji z modelu.

    Nie parsujemy JavaScriptu. Pierwsza wersja probowala regexem zamienic bloki `A`/`N`
    na JSON i cudzyslowowanie kluczy trafialo w SRODEK napisow zawierajacych `, slowo:`
    — np. summary „Lokalny, offline: SAM, DINO, YOLO”. Regeneracja i porownanie tekstu
    nie ma tej klasy bledow i wychwytuje jednoczesnie nieaktualna generacje oraz reczna
    edycje pliku, ktory jest oznaczony jako generowany.
    """
    del model  # generator sam czyta model — sygnatura zachowana dla symetrii z reszta
    path = ARCH / "explorer.html"
    if not path.is_file():
        return ["brak explorer.html — uruchom scripts/generate-architecture-explorer.py"]

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import importlib
        generator = importlib.import_module("generate-architecture-explorer".replace("-", "_"))
    except ModuleNotFoundError:
        spec_path = REPO / "scripts" / "generate-architecture-explorer.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("gen_explorer", spec_path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)

    expected = generator.build()
    actual = path.read_text(encoding="utf-8")
    if expected == actual:
        return []
    exp_lines, act_lines = expected.splitlines(), actual.splitlines()
    if len(exp_lines) != len(act_lines):
        return [f"explorer.html rozni sie od regeneracji: {len(act_lines)} linii "
                f"wobec {len(exp_lines)} — uruchom generator"]
    for index, (want, got) in enumerate(zip(exp_lines, act_lines), start=1):
        if want != got:
            return [f"explorer.html rozni sie od regeneracji od linii {index} "
                    f"— uruchom generator albo popraw model"]
    return ["explorer.html rozni sie od regeneracji (znaki konca linii?)"]


# --- 3. rysunki ------------------------------------------------------------------------

def figure_labels(model: dict, target: str) -> set[str]:
    want: set[str] = set()
    if target == "rys1":
        for entity in model["entities"].values():
            if target not in entity["appears_in"]:
                continue
            want.add(entity["title"])
            stereo = model["stereotypes"].get(entity.get("stereotype") or "")
            if stereo:
                sub = entity.get("substereotype")
                want.add(stereo if not sub else f"{stereo[:-1]} · {sub}»")
            want |= {"+ " + a for a in entity["attrs"]}
        want |= {b["label"] for b in model["entity_boundaries"] if target in b["appears_in"]}
        want |= {r["label"] for r in model["entity_relations"] if target in r["appears_in"]}
    elif target == "rys2":
        for comp in model["components"].values():
            if target not in comp["appears_in"]:
                continue
            want.add(comp["title"])
            if comp.get("tag"):
                want.add(f"[{comp['tag']}]")
            want |= set(comp.get("parts", []))
        want |= {b["label"] for b in model["component_boundaries"] if target in b["appears_in"]}
        want |= {r["label"] for r in model["component_relations"] if target in r["appears_in"]}
    else:
        for stage in model["stages"].values():
            if target not in stage["appears_in"]:
                continue
            want.add(stage["title"])
            for item in stage["items"]:
                label, detail = (item[0], item[1] if len(item) > 1 else "") if isinstance(item, list) else (item, "")
                want.add(label)
                if detail:
                    want.add(detail)
        want |= set(model["tech_strip"])
    return {w for w in want if w}


def svg_texts(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    out = set()
    for match in re.finditer(r"<text[^>]*>(.*?)</text>", text, re.S):
        inner = re.sub(r"<[^>]+>", "", match.group(1))
        value = html.unescape(inner).strip()
        if value:
            out.add(value)
    return out


def check_figure(model: dict, target: str) -> tuple[list[str], bool]:
    """Zwroc (problemy, czy_sprawdzono). `False` znaczy „nie bylo czego sprawdzic"."""
    path = FIGURES / FIGURE_FILES[target]
    if not FIGURES.is_dir():
        return [], False        # repozytorium artykulu nieobecne — patrz POWOD_POMINIECIA
    if not path.is_file():
        return [f"brak eksportu {path.name} w {FIGURES}"], True
    found = {norm(x) for x in svg_texts(path)}
    missing = sorted(w for w in figure_labels(model, target) if norm(w) not in found)
    return [f"{target}: brak na rysunku — {m}" for m in missing], True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending", default="",
                        help="rysunki czekajace na aktualizacje w Figmie, np. rys2,rys3")
    args = parser.parse_args()
    pending = {p.strip() for p in args.pending.split(",") if p.strip()}

    model = load_model()
    failed = False

    for name, errors in (("model", check_model(model)), ("explorer", check_explorer(model))):
        print(f"[{'OK  ' if not errors else 'BLAD'}] {name}: {len(errors)} problemow")
        for err in errors:
            print("        ", err)
        failed |= bool(errors)

    if not FIGURES.is_dir():
        print(f"[POMIN] rys1/rys2/rys3: brak katalogu {FIGURES}")
        print("         Repozytorium artykulu jest osobne i nie jest wymagane do zbudowania")
        print("         wydania. Ustaw GEOTILE_PAPER_ROOT, zeby sprawdzic takze rysunki.")
        return 1 if failed else 0

    for target in ("rys1", "rys2", "rys3"):
        errors, checked = check_figure(model, target)
        if target in pending:
            print(f"[CZEKA] {target}: {len(errors)} roznic — do wykonania w etapie 5")
            for err in errors[:12]:
                print("        ", err)
            if len(errors) > 12:
                print(f"         … i {len(errors) - 12} wiecej")
            continue
        print(f"[{'OK  ' if not errors else 'BLAD'}] {target}: {len(errors)} problemow")
        for err in errors:
            print("        ", err)
        failed |= bool(errors)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
