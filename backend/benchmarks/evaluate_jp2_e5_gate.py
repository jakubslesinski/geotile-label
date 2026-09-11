#!/usr/bin/env python3
"""Bramka E5 eksperymentu JP2 (DESIGN_DECISIONS.md, jp2-fullres).

Runner `benchmark_jp2_fullres_strategies.py` produkuje rekordy ramion wspolne dla
E2, E3 i E5, ale jego `summarize` ma dedykowana logike tylko dla E2 (pary A0/A1)
i E3. Progi E5 — viewport, pan, cieply powrot, RSS — nie sa tam egzekwowane.
Ten modul je liczy, czytajac te same pliki ramion; NIE wykonuje wlasnych pomiarow,
zeby C1 pozostalo mierzone dokladnie tym kodem co B1 (kontrakt z sekcji 6.1).

Wybor zakresu cache jest jawna decyzja: progi z sekcji 13 sa progami **p95**, a p95
istnieje tylko w zakresie `handle_warm` (10 powtorzen sladu). Zakres
`renderer_fresh_handle_probe` ma z zalozenia jeden pomiar, wiec nie da sie z niego
policzyc p95 — raportujemy go osobno jako pojedyncza obserwacje, bo to on odpowiada
zachowaniu aplikacji otwierajacej uchwyt na kazdy kafel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(_BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS_DIR))

from benchmark_jp2_fullres_strategies import _write_json_atomic  # noqa: E402

SCHEMA_NAME = "geotile_jp2_e5_gate"
SCHEMA_VERSION = 1

#: Progi z sekcji 13 planu.
GATE_VIEWPORT_P95_MS = 1000.0
GATE_PAN_P95_MS = 500.0
GATE_WARM_RETURN_P95_MS = 200.0
GATE_RSS_ABSOLUTE_BYTES = 1 * 1024**3
GATE_RSS_HOST_FRACTION = 0.10

WARM = "handle_warm"
COLD_PROBE = "renderer_fresh_handle_probe"

#: Workloady, ktore skladaja sie na interaktywne wyswietlanie — z nich liczymy RSS.
INTERACTIVE_WORKLOADS = {"W3", "W4", "W5", "W6", "W7"}


def _host_rss_limit() -> tuple[int, int | None]:
    try:
        import psutil

        total = int(psutil.virtual_memory().total)
    except Exception:
        return GATE_RSS_ABSOLUTE_BYTES, None
    return min(GATE_RSS_ABSOLUTE_BYTES, int(total * GATE_RSS_HOST_FRACTION)), total


def _pick(arm: dict[str, Any], workload: str, scope: str) -> dict[str, Any] | None:
    for item in arm.get("workloads") or []:
        if item.get("workload") == workload and item.get("cache_scope") == scope:
            return item
    return None


def _p95(arm: dict[str, Any], workload: str, scope: str = WARM) -> float | None:
    entry = _pick(arm, workload, scope)
    if not entry:
        return None
    stats = (entry.get("details") or {}).get("stats") or {}
    value = stats.get("p95_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _pan_p95(arm: dict[str, Any]) -> float | None:
    """Pan liczony JAK W E3: p95 pojedynczego viewportu, nie calego sladu.

    `W6.details.stats` opisuje czas przejscia pieciu viewportow razem, a
    `per_viewport_stats` — jednego kroku. Bramka E3 Codexa uzywa tej drugiej wartosci,
    wiec E5 musi robic tak samo; inaczej kolumny B1 i C1 w tabeli 16.1 opisywalyby
    rozne wielkosci i porownanie byloby bez sensu.
    """
    entry = _pick(arm, "W6", WARM)
    details = (entry or {}).get("details") or {}
    stats = details.get("per_viewport_stats") or details.get("stats") or {}
    value = stats.get("p95_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _initial_persistent_viewport(arm: dict[str, Any]) -> float | None:
    """Pierwszy viewport na trwalym uchwycie — ta sama definicja co w E3."""
    entry = _pick(arm, "W4", WARM)
    warmups = ((entry or {}).get("details") or {}).get("warmup_ms") or []
    return float(warmups[0]) if warmups else None


def _single_ms(arm: dict[str, Any], workload: str, scope: str) -> float | None:
    entry = _pick(arm, workload, scope)
    if not entry:
        return None
    stats = (entry.get("details") or {}).get("stats") or {}
    value = stats.get("p50_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _peak_rss_delta(arm: dict[str, Any]) -> int | None:
    values = [
        int(item["resource"]["peak_rss_delta_bytes"])
        for item in arm.get("workloads") or []
        if item.get("workload") in INTERACTIVE_WORKLOADS
        and item.get("cache_scope") == WARM
        and isinstance((item.get("resource") or {}).get("peak_rss_delta_bytes"), (int, float))
    ]
    return max(values) if values else None


def _signature(arm: dict[str, Any], workload: str) -> Any:
    entry = _pick(arm, workload, WARM)
    return ((entry or {}).get("details") or {}).get("signature")


def _stable_pixels(arm: dict[str, Any], workload: str) -> bool | None:
    entry = _pick(arm, workload, WARM)
    value = ((entry or {}).get("details") or {}).get("stable_pixels")
    return bool(value) if value is not None else None


def evaluate_arm(arm: dict[str, Any], rss_limit: int) -> dict[str, Any]:
    viewport = _p95(arm, "W4")
    pan = _pan_p95(arm)
    warm_return = _p95(arm, "W7")
    metatile = _p95(arm, "W5")
    rss = _peak_rss_delta(arm)
    correctness = arm.get("correctness") or {}

    # "Wyniki sa stabilne dla osobnych kafli i metakafla" (sekcja 13).
    #
    # NIE wolno tego sprawdzac przez porownanie podpisow W4 i W5 miedzy soba: W4
    # haszuje szesnascie kafli 256x256 w kolejnosci odczytu, a W5 jeden blok
    # 1024x1024. To sa rozne uklady bajtow tych samych pikseli, wiec podpisy nie moga
    # byc rowne — sprawdzone: A1 i B1 z E3 maja dokladnie taka sama "niezgodnosc".
    #
    # Stabilnosc kazdej sciezki z osobna niesie flaga `stable_pixels` (runner powtarza
    # slad i porownuje wynik), a rownowaznosc miedzy sciezkami wynika z tego, ze obie
    # zgadzaja sie z referencja A0 przez `correctness.passed`.
    stable_tiles = _stable_pixels(arm, "W4")
    stable_metatile = _stable_pixels(arm, "W5")
    stable_paths = bool(stable_tiles) and bool(stable_metatile)

    checks = {
        "viewport_p95_ms": {
            "value": viewport, "limit": GATE_VIEWPORT_P95_MS,
            "passed": viewport is not None and viewport <= GATE_VIEWPORT_P95_MS,
        },
        "adjacent_pan_p95_ms": {
            "value": pan, "limit": GATE_PAN_P95_MS,
            "passed": pan is not None and pan <= GATE_PAN_P95_MS,
        },
        "warm_return_p95_ms": {
            "value": warm_return, "limit": GATE_WARM_RETURN_P95_MS,
            "passed": warm_return is not None and warm_return <= GATE_WARM_RETURN_P95_MS,
        },
        "peak_rss_delta_bytes": {
            "value": rss, "limit": rss_limit,
            "passed": rss is not None and rss <= rss_limit,
        },
        "source_pixels_preserved": {
            "value": bool(correctness.get("passed")),
            "passed": bool(correctness.get("passed")),
        },
        "stable_pixels_tiles_and_metatile": {
            "value": {"W4": stable_tiles, "W5": stable_metatile},
            "passed": stable_paths,
        },
        "source_unchanged": {
            "value": bool(correctness.get("source_unchanged")),
            "passed": bool(correctness.get("source_unchanged")),
        },
    }
    return {
        "source_id": arm.get("source_id"),
        "strategy": arm.get("strategy"),
        "threads": ((arm.get("configuration") or {}).get("threads")),
        "asset_path": arm.get("asset_path"),
        "runtime_label": (arm.get("environment") or {}).get("runtime_label"),
        "metatile_p95_ms": metatile,
        "initial_persistent_viewport_ms": _initial_persistent_viewport(arm),
        "pan_trace_p95_ms": _p95(arm, "W6"),
        # Dowod tozsamosci pikseli miedzy ramionami — nie bramka, ale najmocniejsza
        # przeslanka dla D1: COG zwraca to samo, co odczyt JP2 przez oba dekodery.
        "signatures": {"W4": _signature(arm, "W4"), "W5": _signature(arm, "W5")},
        "checks": checks,
        "passed": all(item["passed"] for item in checks.values()),
        # Zakres aplikacyjny raportowany osobno: jeden pomiar, brak p95.
        "renderer_fresh_handle_probe_ms": {
            "viewport_W4": _single_ms(arm, "W4", COLD_PROBE),
            "pan_W6": _single_ms(arm, "W6", COLD_PROBE),
            "warm_return_W7": _single_ms(arm, "W7", COLD_PROBE),
        },
    }


def load_arms(run_dir: Path) -> list[dict[str, Any]]:
    arms_dir = run_dir / "arms"
    paths = sorted(arms_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"Brak plikow ramion w {arms_dir}")
    arms = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    manifests = {arm.get("manifest_id") for arm in arms}
    if len(manifests) != 1:
        raise RuntimeError(f"Ramiona uzywaja roznych manifestow: {manifests}")
    return arms


def comparison_rows(e3_dir: Path | None, source_ids: set[str]) -> list[dict[str, Any]]:
    """Odniesienie do A1/B1 z E3 — do tabeli 16.1, nie do bramki E5.

    Pomiary E3 i E5 powstaly w osobnych przebiegach, wiec stan cache systemu byl inny.
    Zestawienie sluzy rzedowi wielkosci i tak jest opisane, a nie jako pomiar
    roznicowy.
    """
    if not e3_dir or not e3_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted((e3_dir / "arms").glob("*_t1_*.json")):
        try:
            arm = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if arm.get("source_id") not in source_ids:
            continue
        rows.append({
            "source_id": arm.get("source_id"),
            "strategy": arm.get("strategy"),
            "viewport_p95_ms": _p95(arm, "W4"),
            "adjacent_pan_p95_ms": _pan_p95(arm),
            "initial_persistent_viewport_ms": _initial_persistent_viewport(arm),
            "warm_return_p95_ms": _p95(arm, "W7"),
            "peak_rss_delta_bytes": _peak_rss_delta(arm),
            "renderer_fresh_handle_viewport_ms": _single_ms(arm, "W4", COLD_PROBE),
            "signatures": {"W4": _signature(arm, "W4"), "W5": _signature(arm, "W5")},
        })
    return rows


def cross_strategy_identity(core: list[dict[str, Any]], e3_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Czy C1 zwraca te same piksele co A1 i B1 na tych samych workloadach.

    To nie jest bramka E5 — poprawnosc wzgledem zamrozonej referencji A0 niesie
    `correctness.passed`. Jest to natomiast najmocniejsza przeslanka dla D1: jesli
    podpisy sa identyczne, wybor miedzy sterownikiem a derywatem przestaje byc
    pytaniem o wierność obrazu i zostaje pytaniem o koszt.
    """
    result: list[dict[str, Any]] = []
    for row in core:
        for other in e3_rows:
            if other["source_id"] != row["source_id"]:
                continue
            matches = {
                workload: (
                    bool(row["signatures"].get(workload))
                    and row["signatures"].get(workload) == other["signatures"].get(workload)
                )
                for workload in ("W4", "W5")
            }
            result.append({
                "source_id": row["source_id"],
                "compared_with": other["strategy"],
                "matches": matches,
                "identical": all(matches.values()),
            })
    return result


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "tak" if value else "nie"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}".replace(",", " ")
    if isinstance(value, int):
        return f"{value:,}{suffix}".replace(",", " ")
    return str(value)


def render_markdown(summary: dict[str, Any]) -> str:
    limit_gib = summary["rss_limit_bytes"] / 1024**3
    lines = [
        "# E5 — benchmark runtime C1 (pełnorozdzielczy COG)",
        "",
        f"Katalog: `{summary['run_dir']}`  ",
        f"Ramion: {summary['arm_count']}  ",
        f"Limit RSS bramki: `min(1 GiB, 10% RAM)` = **{limit_gib:.2f} GiB**",
        "",
        "Progi z §13 są progami **p95**, a p95 istnieje tylko w zakresie `handle_warm`",
        "(10 powtórzeń śladu). Zakres `renderer_fresh_handle_probe` ma z założenia jeden",
        "pomiar i jest raportowany osobno — to on odpowiada aplikacji otwierającej uchwyt",
        "na każdy kafel.",
        "",
        "## Bramka E5 (rdzeń t1)",
        "",
        "| scena | viewport p95 [ms] | pan p95 [ms] | ciepły powrót p95 [ms] | ΔRSS | piksele | stabilność | wynik |",
        "| --- | ---: | ---: | ---: | ---: | :-: | :-: | :-: |",
    ]
    for row in summary["core_arms"]:
        checks = row["checks"]
        lines.append(
            f"| {row['source_id']} "
            f"| {_fmt(checks['viewport_p95_ms']['value'])} "
            f"| {_fmt(checks['adjacent_pan_p95_ms']['value'])} "
            f"| {_fmt(checks['warm_return_p95_ms']['value'])} "
            f"| {_fmt((checks['peak_rss_delta_bytes']['value'] or 0) / 1024**2)} MiB "
            f"| {'✓' if checks['source_pixels_preserved']['passed'] else '✗'} "
            f"| {'✓' if checks['stable_pixels_tiles_and_metatile']['passed'] else '✗'} "
            f"| {'**PASS**' if row['passed'] else '**FAIL**'} |"
        )
    lines += ["", f"Progi: viewport ≤ {GATE_VIEWPORT_P95_MS:.0f} ms, pan ≤ {GATE_PAN_P95_MS:.0f} ms, "
              f"ciepły powrót ≤ {GATE_WARM_RETURN_P95_MS:.0f} ms.", ""]

    if summary["scaling_arms"]:
        lines += ["## Skalowanie wątków", "",
                  "| scena | wątki | viewport p95 | pan p95 | ciepły powrót p95 |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for row in summary["scaling_arms"]:
            checks = row["checks"]
            lines.append(
                f"| {row['source_id']} | {row['threads']} "
                f"| {_fmt(checks['viewport_p95_ms']['value'])} "
                f"| {_fmt(checks['adjacent_pan_p95_ms']['value'])} "
                f"| {_fmt(checks['warm_return_p95_ms']['value'])} |"
            )
        lines.append("")

    lines += ["## Zakres aplikacyjny (świeży uchwyt na kafel, 1 pomiar)", "",
              "| scena | viewport [ms] | pan [ms] | ciepły powrót [ms] |",
              "| --- | ---: | ---: | ---: |"]
    for row in summary["core_arms"]:
        probe = row["renderer_fresh_handle_probe_ms"]
        lines.append(
            f"| {row['source_id']} | {_fmt(probe['viewport_W4'])} "
            f"| {_fmt(probe['pan_W6'])} | {_fmt(probe['warm_return_W7'])} |"
        )
    lines.append("")

    if summary["e3_comparison"]:
        lines += ["## Odniesienie do E3 (A1/B1, t1)", "",
                  "Osobne przebiegi, inny stan cache systemu — rząd wielkości, nie pomiar różnicowy.",
                  "",
                  "| scena | ramię | pierwszy viewport | viewport p95 | pan p95 | ciepły powrót p95 | świeży uchwyt viewport |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in summary["e3_comparison"]:
            lines.append(
                f"| {row['source_id']} | {row['strategy']} "
                f"| {_fmt(row['initial_persistent_viewport_ms'])} "
                f"| {_fmt(row['viewport_p95_ms'])} | {_fmt(row['adjacent_pan_p95_ms'])} "
                f"| {_fmt(row['warm_return_p95_ms'])} "
                f"| {_fmt(row['renderer_fresh_handle_viewport_ms'])} |"
            )
        for row in summary["core_arms"]:
            checks = row["checks"]
            lines.append(
                f"| {row['source_id']} | **C1** "
                f"| {_fmt(row['initial_persistent_viewport_ms'])} "
                f"| {_fmt(checks['viewport_p95_ms']['value'])} "
                f"| {_fmt(checks['adjacent_pan_p95_ms']['value'])} "
                f"| {_fmt(checks['warm_return_p95_ms']['value'])} "
                f"| {_fmt(row['renderer_fresh_handle_probe_ms']['viewport_W4'])} |"
            )
        lines.append("")

    if summary.get("cross_strategy_pixel_identity"):
        lines += ["## Tożsamość pikseli między ramionami", "",
                  "Nie jest to bramka E5 (poprawność wobec A0 niesie `correctness.passed`),",
                  "ale przesądza, że wybór między sterownikiem a derywatem jest pytaniem",
                  "o koszt, a nie o wierność obrazu.", "",
                  "| scena | C1 vs | W4 | W5 |", "| --- | --- | :-: | :-: |"]
        for item in summary["cross_strategy_pixel_identity"]:
            lines.append(
                f"| {item['source_id']} | {item['compared_with']} "
                f"| {'✓' if item['matches']['W4'] else '✗'} "
                f"| {'✓' if item['matches']['W5'] else '✗'} |"
            )
        lines.append("")

    lines += [f"## Wynik: {'**BRAMKA E5 SPEŁNIONA**' if summary['passed'] else '**BRAMKA E5 NIESPEŁNIONA**'}", ""]
    if not summary["passed"]:
        for row in summary["core_arms"]:
            for name, check in row["checks"].items():
                if not check["passed"]:
                    lines.append(f"- {row['source_id']}: `{name}` = {_fmt(check['value'])} "
                                 f"(limit {_fmt(check.get('limit'))})")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--e3-dir", type=Path,
                        default=Path("benchmark-results/jp2-fullres/e3_20260901T181927Z"),
                        help="Katalog E3 do zestawienia odniesienia; pomijany, jesli nie istnieje")
    args = parser.parse_args(argv)

    run_dir = args.run_dir.expanduser().resolve(strict=True)
    arms = load_arms(run_dir)
    rss_limit, host_total = _host_rss_limit()

    evaluated = [evaluate_arm(arm, rss_limit) for arm in arms]
    core = [row for row in evaluated if row["threads"] == 1]
    scaling = sorted(
        (row for row in evaluated if row["threads"] != 1),
        key=lambda row: (str(row["source_id"]), int(row["threads"] or 0)),
    )
    source_ids = {str(row["source_id"]) for row in evaluated}
    e3_rows = comparison_rows(args.e3_dir, source_ids)

    summary = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "stage": "E5",
        "run_dir": str(run_dir),
        "arm_count": len(arms),
        "manifest_id": arms[0].get("manifest_id"),
        "rss_limit_bytes": rss_limit,
        "host_total_ram_bytes": host_total,
        "thresholds": {
            "viewport_p95_ms": GATE_VIEWPORT_P95_MS,
            "adjacent_pan_p95_ms": GATE_PAN_P95_MS,
            "warm_return_p95_ms": GATE_WARM_RETURN_P95_MS,
            "peak_rss_delta_bytes": rss_limit,
            "scope": WARM,
        },
        "core_arms": sorted(core, key=lambda row: str(row["source_id"])),
        "scaling_arms": scaling,
        "e3_comparison": e3_rows,
        "cross_strategy_pixel_identity": cross_strategy_identity(
            sorted(core, key=lambda row: str(row["source_id"])), e3_rows
        ),
        # Bramka rozstrzyga sie na rdzeniu t1: to on niesie kontrole poprawnosci.
        # Ramiona wielowatkowe sa danymi o skalowaniu, nie osobnymi bramkami.
        "passed": bool(core) and all(row["passed"] for row in core),
    }

    _write_json_atomic(run_dir / "e5_gate.json", summary)
    markdown = render_markdown(summary)
    (run_dir / "summary.md").write_text(markdown, encoding="utf-8")

    # Na konsole idzie tylko wersja ASCII. Konsola laboratoryjnego runtime jest w
    # cp1250 i wypisanie pelnego markdownu (ze znakami "Δ", "≤", "✓")
    # wywracalo caly krok bramki, mimo ze plik zapisal sie poprawnie w UTF-8.
    print(f"E5 / limit RSS {summary['rss_limit_bytes'] / 1024**3:.2f} GiB / "
          f"zakres {summary['thresholds']['scope']}")
    for row in summary["core_arms"]:
        checks = row["checks"]
        rss = checks["peak_rss_delta_bytes"]["value"]
        print(f"  {row['source_id']:22s} viewport {_fmt(checks['viewport_p95_ms']['value']):>10s} ms | "
              f"pan {_fmt(checks['adjacent_pan_p95_ms']['value']):>10s} ms | "
              f"powrot {_fmt(checks['warm_return_p95_ms']['value']):>10s} ms | "
              f"RSS {(rss or 0) / 1024**2:8.1f} MiB | "
              f"{'PASS' if row['passed'] else 'FAIL'}")
        for name, check in checks.items():
            if not check["passed"]:
                print(f"      niespelnione: {name} = {_fmt(check['value'])} "
                      f"(limit {_fmt(check.get('limit'))})")
    print(f"WYNIK: {'BRAMKA E5 SPELNIONA' if summary['passed'] else 'BRAMKA E5 NIESPELNIONA'}")
    print(f"zapisano: {run_dir / 'e5_gate.json'} i {run_dir / 'summary.md'}")
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
