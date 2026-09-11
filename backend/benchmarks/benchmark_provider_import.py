"""Audyt rzeczywistych paczek dostawcow — TYLKO DO ODCZYTU (DESIGN_DECISIONS.md, scene-import B0b).

Harness skanuje wskazane zrodla dostawcow i zapisuje maszynowo czytelny raport baseline.
Nie modyfikuje zrodla i nie czyta pikseli: caly koszt to `stat()` plus opcjonalne otwarcie
malych sidecarow metadanych. Jest to celowe — root ICEYE z sekcji 4.3 ma okolo 309 GiB, a
audyt ma opisac STRUKTURE dostawy, nie jej tresc.

Trzy wlasnosci, ktorych pilnuje ten harness (bramka B0):

1. **Zrodlo pozostaje nietkniete.** Snapshot `(sciezka, rozmiar, mtime_ns)` liczony przed
   i po skanie musi byc identyczny.
2. **Zmiana w trakcie skanu jest wykrywana.** Jesli snapshoty sie roznia, wynik dostaje status
   `changed_during_scan`. Odtwarza to sytuacje z audytu, w ktorej katalog PAN-only urosl
   z trzech do szesciu plikow podczas odczytu.
3. **Brak probek nie moze udawac sukcesu.** Gdy zadne zrodlo nie jest osiagalne, proces konczy
   sie kodem 2 i statusem `SKIPPED_REAL_DATA` — nigdy cichym zaliczeniem.

Uruchomienie:

    python backend/benchmarks/benchmark_provider_import.py --config scripts/provider-import-paths.json

Sciezki zrodel sa konfiguracja lokalnego srodowiska i NIE moga trafic do repozytorium —
patrz `scripts/provider-import-paths.example.json`. `--anonymize` zastepuje je stabilnymi
skrotami, zeby raport dalo sie przekazac dalej.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from benchmarks.common import add_common_arguments, configure_environment, output_path  # noqa: E402

OPERATION = "provider_import_audit"
SCHEMA_NAME = "geotile_provider_import_audit"
SCHEMA_VERSION = 1

#: Zabezpieczenie przed przypadkowym przejsciem po ogromnym drzewie na udziale sieciowym.
DEFAULT_MAX_ENTRIES = 200_000

STATUS_STABLE = "stable"
STATUS_CHANGED = "changed_during_scan"
STATUS_INCOMPLETE = "incomplete"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"
STATUS_SKIPPED = "SKIPPED_REAL_DATA"


# --- snapshot zrodla ------------------------------------------------------------------


def snapshot_tree(root: Path, *, max_entries: int) -> tuple[dict[str, tuple[int, int]], bool]:
    """Tani snapshot drzewa: `(rozmiar, mtime_ns)` per sciezka wzgledna.

    Zwraca takze flage obciecia. Obciety snapshot nadal wykrywa zmiane w objetej czesci
    drzewa, ale nie moze byc uznany za dowod niezmiennosci calosci — raport to odnotowuje.
    """
    entries: dict[str, tuple[int, int]] = {}
    truncated = False
    for path in root.rglob("*"):
        if len(entries) >= max_entries:
            truncated = True
            break
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            # Plik zniknal albo jest niedostepny — to sama w sobie informacja o zmianie.
            continue
        entries[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return entries, truncated


def diff_snapshots(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> dict[str, Any]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return {
        "added": added[:50],
        "removed": removed[:50],
        "modified": modified[:50],
        "added_count": len(added),
        "removed_count": len(removed),
        "modified_count": len(modified),
        "identical": not (added or removed or modified),
    }


def fingerprint(entries: dict[str, tuple[int, int]]) -> str:
    digest = hashlib.sha256()
    for key in sorted(entries):
        size, mtime = entries[key]
        digest.update(f"{key}|{size}|{mtime}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


# --- audyt pojedynczego zrodla --------------------------------------------------------


def _anonymize(value: str) -> str:
    return "src_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def audit_source(
    name: str,
    root: Path,
    provider: str,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    anonymize: bool = False,
) -> dict[str, Any]:
    """Przeskanuj jedno zrodlo i zwroc rekord raportu."""
    from services.scene_packages.resolvers import scan_source

    record: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "source": _anonymize(str(root)) if anonymize else str(root),
    }

    if not root.exists():
        record.update({"status": STATUS_SKIPPED, "reason": "source path is not reachable"})
        return record

    started = time.perf_counter()
    before, truncated_before = snapshot_tree(root, max_entries=max_entries)
    snapshot_seconds = time.perf_counter() - started

    scan_started = time.perf_counter()
    error: str | None = None
    packages: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    try:
        packages, diagnostics = scan_source(root, provider)
    except Exception as exc:  # noqa: BLE001 — audyt ma raportowac blad, nie przerywac serii
        error = f"{type(exc).__name__}: {exc}"
    scan_seconds = time.perf_counter() - scan_started

    after, truncated_after = snapshot_tree(root, max_entries=max_entries)
    difference = diff_snapshots(before, after)

    if error is not None:
        status = STATUS_ERROR
    elif not difference["identical"]:
        status = STATUS_CHANGED
    elif not packages:
        status = STATUS_UNSUPPORTED
    else:
        status = STATUS_STABLE

    roles: Counter[str] = Counter()
    asset_roles: Counter[str] = Counter()
    products: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    total_bytes = 0
    # Bramka P0.3 wymaga liczby, ktorej nie da sie odtworzyc w CI: spadku konfliktow
    # metadanych z 914 do 0 na rzeczywistym zrodle. Nie parsujemy tu sidecarow (audyt jest
    # tani i read-only), ale liczymy JEGO PRZYCZYNE: ile plikow metadanych resolver zwiazal
    # z wybranym produktem, a ile zostalo bez przypisania. Metadane niezwiazane, mnozone
    # przez liczbe scen, sa dokladnie tym, z czego brala sie liczba 914.
    metadata_total = 0
    metadata_bound = 0
    # Archiwa (P1.3a) sa liczone OSOBNO. Wliczenie ich do `declared_bytes` albo do statusow
    # selekcji zniszczyloby porownywalnosc z baseline'em B0b: 33 archiwa w korpusie to kilka
    # GiB, ktore nie sa produktem do etykietowania, tylko druga reprezentacja tej samej dostawy.
    archive_statuses: Counter[str] = Counter()
    archive_bytes = 0
    archive_missing = 0
    for package in packages:
        if package.get("package_kind") == "archive":
            selection = package.get("selection") or {}
            archive_statuses[str(selection.get("status"))] += 1
            archive_bytes += int((selection.get("archive") or {}).get("compressed_bytes") or 0)
            archive_missing += int((selection.get("archive") or {}).get("missing_count") or 0)
            continue
        package_metadata = 0
        for asset in package.get("assets") or []:
            roles[str(asset.get("role"))] += 1
            asset_roles[str(asset.get("asset_role") or "-")] += 1
            total_bytes += int(asset.get("size") or 0)
            if asset.get("role") == "metadata":
                package_metadata += 1
        selection = package.get("selection") or {}
        products[str(selection.get("product_type"))] += 1
        statuses[str(selection.get("status"))] += 1
        metadata_total += package_metadata
        bound_ids = selection.get("metadata_asset_ids")
        metadata_bound += len(bound_ids) if bound_ids is not None else package_metadata

    record.update(
        {
            "status": status,
            "error": error,
            "packages": len(packages) - sum(
                1 for item in packages if item.get("package_kind") == "archive"
            ),
            "assets_by_role": dict(sorted(roles.items())),
            "assets_by_contract_role": dict(sorted(asset_roles.items())),
            # `excluded_from_selection` to sidecary, ktore PRZESTALY zasilac metadane
            # wybranej sceny — czyli wprost to, z czego brala sie liczba 914 z sekcji 4.3.
            # Nie mylic z ostrzezeniem `metadata_not_bound` resolvera, ktore dotyczy
            # weszego zbioru: sidecarow nienalezacych do zadnego produktu w paczce.
            "metadata_binding": {
                "metadata_assets": metadata_total,
                "bound_to_selection": metadata_bound,
                "excluded_from_selection": metadata_total - metadata_bound,
            },
            "archives": {
                "packages": sum(archive_statuses.values()),
                "by_status": dict(sorted(archive_statuses.items())),
                "compressed_bytes": archive_bytes,
                "files_missing_from_extraction": archive_missing,
            },
            "product_types": dict(sorted(products.items())),
            "selection_statuses": dict(sorted(statuses.items())),
            "declared_bytes": total_bytes,
            "diagnostics": [
                {"level": item.get("level"), "message": item.get("message")}
                for item in (diagnostics or [])
            ][:50],
            "snapshot": {
                "files_before": len(before),
                "files_after": len(after),
                "fingerprint_before": fingerprint(before),
                "fingerprint_after": fingerprint(after),
                "truncated": bool(truncated_before or truncated_after),
                "max_entries": max_entries,
                "difference": difference,
            },
            "timings_seconds": {
                "snapshot": round(snapshot_seconds, 3),
                "scan": round(scan_seconds, 3),
            },
        }
    )
    return record


# --- raport ---------------------------------------------------------------------------


def build_report(records: list[dict[str, Any]], *, anonymized: bool) -> dict[str, Any]:
    from services.performance_metrics import environment_snapshot, utc_now

    reachable = [item for item in records if item.get("status") != STATUS_SKIPPED]
    modified = [
        item
        for item in reachable
        if not ((item.get("snapshot") or {}).get("difference") or {}).get("identical", True)
    ]
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "operation": OPERATION,
        "generated_at": utc_now(),
        "anonymized": anonymized,
        "environment": environment_snapshot(),
        "summary": {
            "sources_declared": len(records),
            "sources_reachable": len(reachable),
            "sources_skipped": len(records) - len(reachable),
            "sources_modified_during_scan": len(modified),
            "overall_status": STATUS_SKIPPED if not reachable else STATUS_STABLE,
        },
        "sources": records,
    }


def load_config(path: Path) -> list[dict[str, str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{path} does not declare a non-empty 'sources' list")
    for item in sources:
        missing = {"name", "provider", "path"} - set(item)
        if missing:
            raise ValueError(f"source entry {item!r} is missing: {sorted(missing)}")
    return sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument(
        "--config",
        required=True,
        help="JSON z lista zrodel (patrz scripts/provider-import-paths.example.json)",
    )
    parser.add_argument(
        "--max-entries",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        help="Gorny limit wpisow snapshotu na zrodlo — ochrona przed ogromnym drzewem",
    )
    parser.add_argument(
        "--anonymize",
        action="store_true",
        help="Zastap sciezki zrodel stabilnymi skrotami, zeby raport dalo sie przekazac dalej",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_environment(args)

    sources = load_config(Path(args.config).expanduser().resolve())
    records = [
        audit_source(
            item["name"],
            Path(item["path"]).expanduser(),
            item["provider"],
            max_entries=args.max_entries,
            anonymize=args.anonymize,
        )
        for item in sources
    ]

    report = build_report(records, anonymized=bool(args.anonymize))
    destination = output_path(args, OPERATION)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    summary = report["summary"]
    for record in records:
        print(
            f"  {record['name']:<16} {record['status']:<20} "
            f"packages={record.get('packages', '-')}"
        )
    print(f"\nraport: {destination}")

    if summary["sources_reachable"] == 0:
        print(
            "BLAD: zadne zrodlo nie bylo osiagalne — audyt NIE zostal wykonany "
            f"({STATUS_SKIPPED}).",
            file=sys.stderr,
        )
        return 2
    if summary["sources_modified_during_scan"]:
        print(
            f"UWAGA: {summary['sources_modified_during_scan']} zrodlo/a zmienilo sie w trakcie "
            "skanu — wynik nie jest stabilnym baseline'em.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
