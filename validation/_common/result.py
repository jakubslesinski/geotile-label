"""Wspólny kontrakt wyniku dla wszystkich dowodów walidacyjnych.

Każdy `run.py` zwraca `ValidationResult` i woła `emit()`, który zapisuje
`results/result.json` obok skryptu. Format jest stały, żeby `run_all.py`
mógł zagregować wszystkie dowody do jednej tabeli claim↔wynik do artykułu.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["pass", "fail", "todo"]
Tier = Literal["public", "reported-only", "mixed"]


@dataclass
class ValidationResult:
    id: str                     # np. "v02"
    claim: str                  # ID claimu z claim-evidence-matrix.md, np. "C3"
    title: str                  # krótki opis dowodu
    status: Status = "todo"
    # substrat = na jakich danych dowód działa (nowa konwencja claim×substrat).
    # tier = odtwarzalność: "public" (recenzent rerunuje na benchmarku),
    #        "reported-only" (dane pod licencją: Capella/SAR_test — tylko raport),
    #        "mixed" (część publiczna, część reported-only).
    substrate: list[str] = field(default_factory=list)      # np. ["xView3", "FAIR1M"]
    tier: Tier = "public"
    metrics: dict[str, Any] = field(default_factory=dict)   # liczby do tabeli/rysunku
    artifacts: list[str] = field(default_factory=list)      # ścieżki CSV/PNG w results/
    config: dict[str, Any] = field(default_factory=dict)    # seed, ścieżki, parametry
    notes: str = ""
    timestamp: str = ""
    environment: dict[str, str] = field(default_factory=dict)


def _git_rev(repo: str) -> str:
    """Rewizja gita repozytorium aplikacji.

    Rozróżnia dwa stany, które wcześniej dawały tę samą wartość: `"nogit"` znaczy
    „katalog nie jest repozytorium" (dziś tak jest — stąd `"unknown"` we wszystkich
    wynikach do 2026-09-03), a `"unknown"` — „jest, ale odczytu nie dało się wykonać".
    Tylko to drugie jest problemem do zbadania.
    """
    if not repo or not os.path.isdir(repo):
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return "unknown"
    if out.returncode != 0:
        if "not a git repository" in (out.stderr or "").lower():
            return "nogit"
        return "unknown"
    return out.stdout.strip() or "unknown"


def _app_fingerprint(repo: str) -> str:
    """Odcisk stanu kodu backendu: sha256 po (ścieżka, sha256(treść)) dla `backend/**/*.py`.

    Zastępuje rewizję gita tam, gdzie jej nie ma. Liczony z TREŚCI, nie z czasu modyfikacji,
    więc kopia repozytorium na inną maszynę albo ponowny checkout tego samego stanu dają ten
    sam odcisk. `__pycache__` pominięty — inaczej sam fakt uruchomienia dowodu zmieniałby
    odcisk kolejnego.

    Odcisk PINUJE stan kodu, nie ocenia go: obejmuje też `tests/` i `benchmarks/`, więc
    zmieni się również przy zmianie, która nie dotyka zachowania produktu. To celowe — ma
    odpowiadać na pytanie „ten sam kod?", a nie „to samo zachowanie?".

    Format: `<16 hex>:<liczba plików>` — liczba pozwala odróżnić usunięcie pliku od zmiany treści.
    """
    root = os.path.join(repo, "backend")
    if not repo or not os.path.isdir(root):
        return "unavailable"
    paths: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in files:
            if name.endswith(".py"):
                paths.append(os.path.join(current, name))
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            with open(path, "rb") as fh:
                content = fh.read()
        except OSError:
            return "unavailable"
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return f"{digest.hexdigest()[:16]}:{len(paths)}"


def _app_version(repo: str) -> str:
    """Wersja produktu z konfiguracji Tauri — czytelna dla człowieka, w parze z odciskiem.

    Pusty `repo` musi dać `"unknown"`, a nie ścieżkę względną: `os.path.join("", "frontend", …)`
    rozwiązałoby się względem katalogu roboczego i przy odrobinie pecha wczytało wersję
    z cudzego drzewa. Wynik wyglądałby wtedy na przypisany do stanu kodu, nie będąc nim.
    """
    if not repo or not os.path.isdir(repo):
        return "unknown"
    path = os.path.join(repo, "frontend", "src-tauri", "tauri.conf.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "unknown")
    except Exception:
        return "unknown"


def _app_repo() -> str:
    """Korzeń repozytorium aplikacji.

    Normalnie ustawia go `appenv.bootstrap()`, ale dowody, które nie sięgają do backendu
    (`v06`, `v13` konsumują gotowe przebiegi treningu), nigdy go nie wołają. Bez tej ścieżki
    awaryjnej ich wyniki nie byłyby przypisane do żadnego stanu kodu — a to jest dokładnie
    ta luka, którą B0 zamyka. `appenv.app_repo()` jest czystą funkcją: czyta zmienną
    środowiskową i nic nie ustawia.
    """
    explicit = os.environ.get("APP_REPO")
    if explicit:
        return explicit
    try:
        from . import appenv
    except ImportError:  # uruchomienie spoza pakietu
        return ""
    return str(appenv.app_repo())


def emit(result: ValidationResult, out_dir: str) -> str:
    """Zapisz wynik do <out_dir>/result.json i zwróć ścieżkę."""
    result.timestamp = datetime.now(timezone.utc).isoformat()
    # Wynik nieprzypisany do stanu kodu produktu nadaje się najwyżej do obejrzenia,
    # nie do cytowania — stąd ścieżka awaryjna dla dowodów bez `bootstrap()`.
    app_repo = _app_repo()
    result.environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "app_rev": _git_rev(app_repo),
        "app_fingerprint": _app_fingerprint(app_repo),
        "app_version": _app_version(app_repo),
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_anonymize(asdict(result)), fh, ensure_ascii=False, indent=2)
    return path


def _anonymize(payload):
    """Zamień ścieżki katalogu domowego na zmienne środowiskowe.

    Wyniki są publikowane razem z kodem, a bramka wycieku repozytorium ściga wzorzec
    ``<dysk>:\\Users\\<ktoś>\\``. Jeden dowód (v06) zapisywał w ``config`` bezwzględną
    ścieżkę katalogu danych aplikacji i zapaliłby ją. Zamiast łatać ten dowód osobno,
    czyścimy przy zapisie — dotyczy to każdego pola, każdego dowodu i przyszłych też.
    Podmieniamy na nazwę zmiennej, żeby wartość dalej mówiła, o który katalog chodzi.
    """
    replacements = []
    for var in ("APPDATA", "LOCALAPPDATA", "USERPROFILE"):
        value = os.environ.get(var)
        if value:
            replacements.append((value, "%" + var + "%"))
    # Najdłuższe najpierw: APPDATA leży wewnątrz USERPROFILE.
    replacements.sort(key=lambda item: len(item[0]), reverse=True)

    def clean(node):
        if isinstance(node, str):
            for literal, token in replacements:
                node = node.replace(literal, token)
            return node
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items()}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(payload)


def results_dir(script_file: str) -> str:
    """Katalog results/ obok danego run.py."""
    return os.path.join(os.path.dirname(os.path.abspath(script_file)), "results")
