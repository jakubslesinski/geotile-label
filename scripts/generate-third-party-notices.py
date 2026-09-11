"""Zbuduj THIRD_PARTY_NOTICES.md z metadanych, ktore i tak leza na dysku.

Swiadomie NIE wymaga `pip-licenses`, `license-checker` ani `cargo-license`. Kazdy
z trzech ekosystemow niesie licencje we wlasnych metadanych, wiec dokladamy tylko
odczyt i formatowanie:

  Rust   - `cargo metadata --format-version 1`, pole `license` kazdego pakietu,
  npm    - `frontend/package-lock.json`, pole `license` kazdego wpisu,
  Python - `*.dist-info/METADATA` (`License-Expression`, `License`, klasyfikatory).

Uzycie:

    python scripts/generate-third-party-notices.py
    python scripts/generate-third-party-notices.py --python-env <sciezka-do-runtime>
    python scripts/generate-third-party-notices.py --skip-rust      # bez sieci

WAZNE: plik wynikowy nie moze niesc sciezki katalogu domowego ani nazwy stacji, bo
zapali bramke wyciekow. Dlatego zapisujemy WARIANT runtime'u i wersje pakietow,
nigdy sciezke, z ktorej je odczytano.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "THIRD_PARTY_NOTICES.md"
LOCKFILE = REPO / "frontend" / "package-lock.json"
CARGO_MANIFEST = REPO / "frontend" / "src-tauri" / "Cargo.toml"

# Licencje, ktore trzeba wypunktowac osobno: recenzent i dzial prawny patrza wlasnie
# na nie, a nie na dwiescie linijek "MIT OR Apache-2.0".
#
# Silne copyleft rozciaga sie na cale dzielo i to ono decyduje o licencji aplikacji.
# Slabe konczy sie na plikach samej biblioteki. Mieszanie ich w jednej tabeli mylilo:
# `certifi` (MPL-2.0) trafialo pod naglowek "dlatego aplikacja jest na AGPL".
STRONG_COPYLEFT = re.compile(r"\bA?GPL[-\s]?[0-9v]|\bSSPL\b|\bOSL\b|\bEUPL\b", re.IGNORECASE)
WEAK_COPYLEFT = re.compile(r"\b(?:LGPL|MPL|CDDL|EPL)\b", re.IGNORECASE)
# Licencje, ktore nie sa zatwierdzone przez OSI albo roznicuja prawa odbiorcow.
NON_OSI = re.compile(r"SEE LICENSE|UNLICENSED|proprietary", re.IGNORECASE)


def _fail(message: str) -> None:
    print("BLAD: " + message, file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------- Rust
def collect_rust() -> list[tuple[str, str, str]]:
    try:
        raw = subprocess.run(
            ["cargo", "metadata", "--format-version", "1"],
            cwd=CARGO_MANIFEST.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
    except FileNotFoundError:
        _fail("nie znaleziono `cargo` w PATH (uzyj --skip-rust)")
    except subprocess.CalledProcessError as exc:
        _fail("`cargo metadata` zakonczylo sie kodem %d:\n%s" % (exc.returncode, exc.stderr[:400]))

    rows = []
    for pkg in json.loads(raw)["packages"]:
        if pkg["name"] == "geotile-label-desktop":
            continue  # to my, nie strona trzecia
        license_ = pkg.get("license")
        if not license_ and pkg.get("license_file"):
            license_ = "plik licencji w pakiecie: %s" % pkg["license_file"]
        rows.append((pkg["name"], pkg["version"], license_ or "(not declared)"))
    return sorted(set(rows))


# ---------------------------------------------------------------------------- npm
def collect_npm() -> list[tuple[str, str, str]]:
    if not LOCKFILE.is_file():
        _fail("brak %s" % LOCKFILE.name)
    lock = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    rows = []
    for path, data in lock.get("packages", {}).items():
        if not path:
            continue  # korzen projektu
        name = path.split("node_modules/")[-1]
        license_ = data.get("license")
        if isinstance(license_, list):
            license_ = " OR ".join(str(x) for x in license_)
        rows.append((name, data.get("version", "?"), license_ or "(not declared)"))
    return sorted(set(rows))


# ------------------------------------------------------------------------- Python
def _license_from_metadata(text: str) -> str | None:
    # `License-Expression` to wyrazenie SPDX i bywa dlugie z powodu — torch deklaruje
    # szesc licencji naraz. Limit dlugosci obowiazuje tylko `License`, bo to pole
    # wolnotekstowe i czesc pakietow wkleja do niego caly tekst licencji.
    match = re.search(r"^License-Expression:\s*(.+)$", text, re.M)
    if match:
        return match.group(1).strip()
    match = re.search(r"^License:\s*(.+)$", text, re.M)
    if match and len(match.group(1).strip()) < 80:
        return match.group(1).strip()
    classifiers = re.findall(r"^Classifier:\s*License ::\s*(.+)$", text, re.M)
    if classifiers:
        # "OSI Approved :: BSD License" niesie jedna uzyteczna informacje na koncu.
        cleaned = [c.strip().replace("OSI Approved :: ", "") for c in classifiers]
        return "; ".join(cleaned)
    return None


def collect_python(site_packages: Path) -> list[tuple[str, str, str]]:
    if not site_packages.is_dir():
        _fail("katalog site-packages nie istnieje; podaj --python-env")

    rows = []
    for dist in sorted(site_packages.iterdir()):
        if not dist.name.endswith(".dist-info"):
            continue
        metadata = dist / "METADATA"
        name = version = None
        license_ = None
        if metadata.is_file():
            text = metadata.read_text(encoding="utf-8", errors="replace")
            name = (re.search(r"^Name:\s*(.+)$", text, re.M) or [None, None])[1]
            version = (re.search(r"^Version:\s*(.+)$", text, re.M) or [None, None])[1]
            license_ = _license_from_metadata(text)
        if not name:
            name, _, version = dist.name[: -len(".dist-info")].partition("-")
        if not license_:
            # Ostatnia szansa: pelny tekst licencji dolaczony do dystrybucji.
            license_ = _license_from_license_files(dist)
        rows.append((name, version or "?", license_ or "(not declared)"))
    return sorted(set(rows))


def _license_from_license_files(dist: Path) -> str | None:
    """Rozpoznaj licencje po pierwszej linii dolaczonego tekstu.

    Potrzebne dla dystrybucji, ktore nie deklaruja licencji w METADATA — np. fork
    CLIP-a instalowany z gita, ktory niesie pelny tekst AGPL i nic poza nim.
    """
    for candidate in (dist / "licenses", dist):
        if not candidate.is_dir():
            continue
        for path in sorted(candidate.glob("LICENSE*")):
            if not path.is_file():
                continue
            head = path.read_text(encoding="utf-8", errors="replace")[:400].upper()
            # Etykiety ida do angielskiego raportu, wiec sa po angielsku.
            if "AFFERO GENERAL PUBLIC LICENSE" in head:
                return "AGPL-3.0 (full text bundled, not declared in metadata)"
            if "APACHE LICENSE" in head:
                return "Apache-2.0 (full text bundled, not declared in metadata)"
            if "MIT LICENSE" in head:
                return "MIT (full text bundled, not declared in metadata)"
            if "BSD" in head:
                return "BSD (full text bundled, not declared in metadata)"
    return None


# ------------------------------------------------------------------- kod wendorowany
# Repozytoria kopiowane do `resources/backend/vendor/` przez prepare-tauri-backend.ps1.
# Klucz to nazwa katalogu klonu; wartosc to (nazwa w raporcie, licencja, plik z trescia).
VENDORED_DINO = {
    "dinov2_repo": ("facebookresearch/dinov2", "Apache-2.0", "LICENSE"),
    "dinov3_repo": ("facebookresearch/dinov3", "DINOv3 License (Meta, not OSI)", "LICENSE.md"),
}
VENDOR_SOURCE = REPO / "data" / "models" / "dino"


def collect_vendored() -> list[tuple[str, str, str]]:
    """Kod zrodlowy pakowany do instalatora poza menedzerami pakietow."""
    rows = []
    for directory, (name, license_, license_file) in sorted(VENDORED_DINO.items()):
        path = VENDOR_SOURCE / directory
        if not path.is_dir():
            continue
        # `fetch-dino-repos.ps1` zapisuje SOURCE.txt przed usunieciem `.git`; bez niego
        # nie da sie powiedziec, ktora rewizja trafila do instalatora.
        version = "unknown revision"
        try:
            for line in (path / "SOURCE.txt").read_text(encoding="utf-8").splitlines():
                if line.startswith("commit:"):
                    version = line.split(":", 1)[1].strip()[:12]
                    break
        except OSError:
            pass
        if not (path / license_file).is_file():
            _fail("brak pliku licencji %s w %s" % (license_file, path))
        rows.append((name, version, license_))
    return rows


# ------------------------------------------------------------------------- raport
def table(rows: list[tuple[str, str, str]]) -> str:
    out = ["| Package | Version | License |", "| --- | --- | --- |"]
    for name, version, license_ in rows:
        out.append("| `%s` | %s | %s |" % (name, version, license_.replace("|", "/")))
    return "\n".join(out)


def flagged(rows, pattern):
    return [r for r in rows if pattern.search(r[2])]


def build_report(rust, npm, python, vendored, runtime_variant: str) -> str:
    everything = rust + npm + python + vendored
    strong = flagged(everything, STRONG_COPYLEFT)
    weak = flagged(everything, WEAK_COPYLEFT)
    non_osi = flagged(everything, NON_OSI)

    parts = []
    parts.append("# Third-party notices\n")
    parts.append(
        "GeoTile Label is distributed under the GNU Affero General Public License v3.0\n"
        "(see [`LICENSE`](LICENSE)). This file lists the third-party components shipped with\n"
        "the application, together with the license each of them declares. Where a bundled\n"
        "component carries terms that AGPL-3.0 cannot absorb, it is called out first.\n"
    )
    parts.append(
        "Generated by `scripts/generate-third-party-notices.py` on %s. It is derived from\n"
        "metadata that already ships with the dependencies — `cargo metadata` for Rust,\n"
        "`frontend/package-lock.json` for npm and `*.dist-info/METADATA` for Python — so it can\n"
        "be regenerated on any machine without installing extra tooling. Regenerate it whenever\n"
        "dependencies change.\n" % date.today().isoformat()
    )

    if vendored:
        parts.append("\n## Field-of-use restrictions — read this first\n")
        parts.append(
            "One bundled component restricts **who may use the software**, not merely how it may\n"
            "be redistributed. That is a different kind of obligation from copyleft and it is not\n"
            "something an AGPL-3.0 release can absorb, so it is stated before anything else.\n"
        )
        parts.append(table(vendored))
        parts.append(
            "\nThese repositories hold the backbone architecture used by **Dataset analysis**\n"
            "(class similarity, suspicious labels, near-duplicates, train/validation leakage).\n"
            "They are copied into `resources/backend/vendor/dino` so the feature works offline;\n"
            "the model weights are not shipped and are supplied by the user.\n"
        )
        parts.append(
            "\n`facebookresearch/dinov3` is covered by the **DINOv3 License**, which is not an\n"
            "OSI-approved license. Redistribution is permitted royalty-free, but section 1.b of\n"
            "that agreement requires a copy of it to travel with the code and binds every\n"
            "downstream recipient, and it prohibits use for, among others, **military or warfare\n"
            "purposes, espionage, nuclear applications and activities subject to ITAR**. The full\n"
            "text ships in `resources/backend/vendor/dino/dinov3_repo/LICENSE.md`.\n"
        )
        parts.append(
            "\nConsequence for the release as a whole: the statement that GeoTile Label is\n"
            "distributed under AGPL-3.0 describes the application and every other component, but\n"
            "not this one. `facebookresearch/dinov2` carries no such restriction — it is\n"
            "Apache-2.0 — so an installer built without the DINOv3 repository is free of this\n"
            "condition and loses only the DINOv3-SAT variant.\n"
        )
    parts.append("\n## Copyleft and non-permissive components\n")
    parts.append(
        "These are the entries that decide what the whole work may be distributed under, so\n"
        "they are listed separately rather than buried in the full tables below.\n"
    )
    if strong:
        parts.append(
            "\n### Strong copyleft — this is what sets the license of the whole work\n\n"
            "Every one of these arrives through the YOLO stack. They are the reason GeoTile Label\n"
            "is released under AGPL-3.0 rather than a permissive license: they are imported\n"
            "directly by training, prediction, YOLO export and the model registry, so they are not\n"
            "an optional add-on that could be kept outside the boundary of the work.\n"
        )
        parts.append(table(strong))
    if weak:
        parts.append(
            "\n### Weak copyleft — file-level, compatible with an AGPL-3.0 release\n\n"
            "MPL-2.0 and LGPL oblige publication of changes made to those files, not of the\n"
            "surrounding work. Where a component offers a choice of licenses, the permissive\n"
            "option (MIT or Apache-2.0) is the one taken.\n"
        )
        parts.append(table(weak))
    parts.append("\n### Non-OSI and unresolved declarations\n")
    if non_osi:
        parts.append(
            "\n**Each of these needs a human decision before a release.** A dependency whose license\n"
            "forbids sublicensing, or grants rights depending on who the recipient is, cannot be\n"
            "redistributed as part of an AGPL-3.0 work.\n"
        )
        parts.append(table(non_osi))
    else:
        parts.append(
            "\nNone. This is re-checked on every regeneration, because such a license can arrive\n"
            "through a semver range without any code change: `react-apexcharts` switched to a\n"
            "revenue-gated dual license in 1.8.0, and a `^1.6.0` range had silently resolved to it.\n"
            "It is therefore pinned to the exact version 1.7.0, its last MIT release.\n"
        )

    parts.append("\n## Python — backend runtime bundled in the installer\n")
    parts.append(
        "%d distributions, taken from the packed backend runtime (`%s` variant). The installer\n"
        "ships this runtime, so these are distributed with the application.\n"
        "\n"
        "The versions below are the ones present in the runtime this file was generated from.\n"
        "That runtime is built by `scripts/build-backend-env.ps1`, which resolves the newest\n"
        "compatible releases rather than a pinned set, so the exact composition of a given\n"
        "build is recorded separately in [`runtime-lock.txt`](runtime-lock.txt) — conda packages\n"
        "with their full URLs and pip packages with their versions. Read that file when you need\n"
        "to know precisely what a released installer contains; `environment.yml` describes the\n"
        "development environment and does not govern this build.\n" % (len(python), runtime_variant)
    )
    parts.append(table(python))

    parts.append("\n## npm — frontend bundled into the shipped JavaScript\n")
    parts.append(
        "%d packages resolved by `frontend/package-lock.json`. Build-time-only packages are\n"
        "included: they are part of the toolchain a reviewer reproduces, even when their code\n"
        "does not reach the bundle.\n" % len(npm)
    )
    parts.append(table(npm))

    parts.append("\n## Rust — desktop shell (Tauri)\n")
    parts.append(
        "%d crates in the dependency graph of the desktop shell, excluding the application\n"
        "crate itself.\n" % len(rust)
    )
    parts.append(table(rust))

    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_env = Path(os.environ.get("APPDATA", "")) / "GeoTileLabel" / "runtime" / "backend-env"
    parser.add_argument("--python-env", type=Path, default=default_env,
                        help="katalog runtime backendu (domyslnie spakowany runtime aplikacji)")
    parser.add_argument("--runtime-variant", default="analyst",
                        help="wariant runtime'u wpisywany do raportu (analyst albo studio)")
    parser.add_argument("--skip-rust", action="store_true",
                        help="pomin sekcje Rust (cargo metadata potrafi siegac do sieci)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    site_packages = args.python_env / "Lib" / "site-packages"
    if not site_packages.is_dir():
        site_packages = args.python_env  # ktos podal juz site-packages

    rust = [] if args.skip_rust else collect_rust()
    npm = collect_npm()
    python = collect_python(site_packages)
    vendored = collect_vendored()

    report = build_report(rust, npm, python, vendored, args.runtime_variant)
    args.output.write_text(report, encoding="utf-8", newline="\n")

    print("Rust  : %4d skrzynek" % len(rust))
    print("npm   : %4d pakietow" % len(npm))
    print("Python: %4d dystrybucji" % len(python))
    print("vendor: %4d repozytoriow" % len(vendored))
    print("zapisano %s (%d B)" % (args.output.name, len(report.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
