#!/usr/bin/env python3
"""Zlozenie wynikow E4 w jeden rekord decyzyjny.

Czyta wszystkie `e4_*.json` z katalogu przebiegu i wypisuje `summary.md` plus
`e4_summary.json` w formie, ktora wchodzi wprost do tabeli z sekcji 16.1 planu
i do rejestru z sekcji 22.

Swiadomie NIE wybiera zwyciezcy. E4 ma dostarczyc liczby i werdykty bramek;
wybor strategii nalezy do D1, ktore potrzebuje jeszcze wynikow E3 i E5.
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


def _gib(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return "—"
    return f"{value / 1024**3:.2f}"


def _row(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result", {})
    gate_r = record.get("resource_gate") or {}
    gate_c = record.get("correctness_gate") or {}
    decode = result.get("decode") or {}
    cog = result.get("cog") or {}
    strategy = result.get("strategy", "?")
    if strategy == "opj_regions":
        strategy = f"opj_regions({decode.get('regions')})"
    return {
        "source_id": record.get("source_id"),
        "strategy": strategy,
        "compression": (cog.get("creation_options") or [""])[0].replace("COMPRESS=", "") or None,
        "decode_seconds": decode.get("elapsed_seconds"),
        "cog_seconds": cog.get("elapsed_seconds"),
        "total_seconds": result.get("total_seconds") or gate_r.get("total_seconds"),
        "total_minutes": gate_r.get("total_minutes"),
        "time_verdict": gate_r.get("time_verdict") or gate_r.get("skipped"),
        "peak_rss_gib": gate_r.get("peak_rss_gib"),
        "rss_within_limit": gate_r.get("rss_within_limit"),
        "cog_bytes": cog.get("final_bytes"),
        "size_ratio": gate_r.get("size_ratio"),
        "size_within_limit": gate_r.get("size_within_limit"),
        "rss_limit_gib": round((gate_r.get("rss_limit_bytes") or 0) / 1024**3, 2) or None,
        "rss_margin_gib": (
            round((gate_r["rss_limit_bytes"] - gate_r["peak_rss_bytes"]) / 1024**3, 2)
            if gate_r.get("rss_limit_bytes") and gate_r.get("peak_rss_bytes")
            else None
        ),
        "e4c_passed": gate_c.get("passed"),
        "e4c_mismatches": gate_c.get("mismatch_count"),
        "e4c_extra_bands": (gate_c.get("extra_bands") or {}).get("mismatch_count"),
        "cog_validator": (gate_c.get("cog_validator") or {}).get("is_cog"),
        "overviews": gate_c.get("internal_overviews"),
        "extrapolated_minutes": result.get("extrapolated_minutes"),
    }


#: Ponizej tego zapasu wynik jest w granicach szumu miedzy przebiegami (zmierzony
#: rozrzut dekodu: 23%), wiec "mieści sie w limicie" jest wtedy rzutem moneta, a nie
#: spelniona bramka. Raportujemy to jawnie, zeby nikt nie odczytal 3,99/4,00 jako PASS.
RSS_MARGIN_MIN_GIB = 0.25


def _verdict_line(row: dict[str, Any]) -> str:
    if row["extrapolated_minutes"]:
        return f"ODRZUCONA — {row['extrapolated_minutes']} min (ekstrapolacja)"
    parts = []
    if row["time_verdict"]:
        parts.append(f"czas: {row['time_verdict']}")
    if row["rss_within_limit"] is not None:
        if not row["rss_within_limit"]:
            parts.append("RAM: PRZEKROCZONA")
        elif (row["rss_margin_gib"] or 0) < RSS_MARGIN_MIN_GIB:
            parts.append(f"RAM: NA STYK (zapas {row['rss_margin_gib']} GiB)")
        else:
            parts.append(f"RAM: OK (zapas {row['rss_margin_gib']} GiB)")
    if row["size_within_limit"] is not None:
        parts.append("rozmiar: " + ("OK" if row["size_within_limit"] else "PRZEKROCZONY"))
    if row["e4c_passed"] is not None:
        parts.append("E4-C: " + ("PASS" if row["e4c_passed"] else "FAIL"))
    return " | ".join(parts) or "—"


def build_summary(run_dir: Path) -> dict[str, Any]:
    records = []
    for path in sorted(run_dir.glob("e4_*.json")):
        if path.name in {"e4_summary.json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("schema_name") != "geotile_jp2_cog_construction":
            continue
        records.append((path.name, payload))

    rows = [dict(_row(payload), record_file=name) for name, payload in records]
    rows.sort(key=lambda item: (str(item["source_id"]), str(item["strategy"])))

    passing = [
        row for row in rows
        if row["e4c_passed"] and row["rss_within_limit"] and row["size_within_limit"]
        and row["time_verdict"] in {"spelniona", "warunkowa"}
        and (row["rss_margin_gib"] or 0) >= RSS_MARGIN_MIN_GIB
    ]
    marginal = [
        row for row in rows
        if row["e4c_passed"] and row["rss_within_limit"]
        and 0 <= (row["rss_margin_gib"] or 0) < RSS_MARGIN_MIN_GIB
    ]
    return {
        "schema_name": "geotile_jp2_cog_construction_summary",
        "schema_version": 1,
        "stage": "E4",
        "run_dir": str(run_dir),
        "record_count": len(rows),
        "rows": rows,
        "configurations_passing_all_gates": [
            {"source_id": row["source_id"], "strategy": row["strategy"],
             "total_minutes": row["total_minutes"], "peak_rss_gib": row["peak_rss_gib"],
             "rss_margin_gib": row["rss_margin_gib"], "time_verdict": row["time_verdict"]}
            for row in passing
        ],
        "configurations_within_noise_of_rss_limit": [
            {"source_id": row["source_id"], "strategy": row["strategy"],
             "peak_rss_gib": row["peak_rss_gib"], "rss_margin_gib": row["rss_margin_gib"]}
            for row in marginal
        ],
        "rss_margin_threshold_gib": RSS_MARGIN_MIN_GIB,
        "note": "E4 nie wybiera zwyciezcy; wybor nalezy do D1 po E3 i E5.",
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# E4 — budowa i walidacja pelnorozdzielczego COG",
        "",
        f"Katalog przebiegu: `{summary['run_dir']}`  ",
        f"Rekordow: {summary['record_count']}",
        "",
        "## Wyniki",
        "",
        "| scena | strategia | kompr. | dekod [s] | COG [s] | razem [min] | peak RSS [GiB] | rozmiar | werdykt bramek |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary["rows"]:
        total = row["total_minutes"]
        lines.append(
            f"| {row['source_id']} | {row['strategy']} | {row['compression'] or '—'} "
            f"| {row['decode_seconds'] if row['decode_seconds'] is not None else '—'} "
            f"| {row['cog_seconds'] if row['cog_seconds'] is not None else '—'} "
            f"| {total if total is not None else '—'} "
            f"| {row['peak_rss_gib'] if row['peak_rss_gib'] is not None else '—'} "
            f"| {('x' + str(row['size_ratio'])) if row['size_ratio'] else '—'} "
            f"| {_verdict_line(row)} |"
        )
    lines += [
        "",
        "## Konfiguracje spelniajace wszystkie bramki",
        "",
    ]
    if summary["configurations_passing_all_gates"]:
        for item in summary["configurations_passing_all_gates"]:
            lines.append(
                f"- **{item['source_id']} / {item['strategy']}** — {item['total_minutes']} min "
                f"({item['time_verdict']}), peak RSS {item['peak_rss_gib']} GiB"
            )
    else:
        lines.append("- brak")
    if summary.get("configurations_within_noise_of_rss_limit"):
        lines += ["", "## Odrzucone jako wynik w granicach szumu", "",
                  f"Zapas ponizej {summary['rss_margin_threshold_gib']} GiB do limitu RAM. "
                  "Formalnie mieszcza sie, ale zmierzony rozrzut miedzy przebiegami (23% na "
                  "czasie dekodu) jest wiekszy niz ten zapas.", ""]
        for item in summary["configurations_within_noise_of_rss_limit"]:
            lines.append(
                f"- {item['source_id']} / {item['strategy']} — {item['peak_rss_gib']} GiB "
                f"(zapas {item['rss_margin_gib']} GiB)"
            )
    lines += ["", f"> {summary['note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    summary = build_summary(run_dir)
    _write_json_atomic(run_dir / "e4_summary.json", summary)
    markdown = render_markdown(summary)
    (run_dir / "summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"zapisano: {run_dir / 'e4_summary.json'} i {run_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
