"""Trwaly raport importu scen (DESIGN_DECISIONS.md, scene-import P1.6).

Do tej pory po imporcie zostawal wylacznie zestaw licznikow (`added`, `updated`, `blocked`,
`failed`) plus lista OSTATNICH 20 pozycji w pliku joba. Przy dostawie liczonej w setkach scen
oznaczalo to, ze przyczyny wiekszosci niepowodzen znikaly — a bramka M2 wymaga, zeby raport
pozwalal odtworzyc przyczyne KAZDEJ sceny `blocked` albo `failed`.

Raport jest budowany przyrostowo w trakcie katalogowania, a po fazach tozsamosci i piramid
uzupelniany o ich wynik i zapisywany ponownie pod tym samym `scan_id`. Dzieki temu jeden plik
opisuje caly import, a nie jego pierwsza faze.

Anonimizacja jest wariantem ODCZYTU, nie zapisu: raport na dysku zawiera pelne sciezki, bo
sluzy diagnozie u siebie. Dopiero kopia do przekazania dalej zamienia sciezki i nazwy plikow
na stabilne skroty — stabilne, wiec ta sama scena ma ten sam token w calym raporcie i mozna
o niej rozmawiac bez ujawniania struktury katalogow.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_SCHEMA_NAME = "geotile_scene_import_report"
REPORT_SCHEMA_VERSION = 1

#: Klucze, ktorych wartosci sa sciezkami albo nazwami plikow i znikaja przy anonimizacji.
_PATH_KEYS = frozenset({
    "root_path",
    "filename",
    "package_root_relative",
    "relative_path",
    "path",
    "source_path",
    "log_name",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reports_dir(project_dir_path: Path) -> Path:
    path = project_dir_path / "import_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ImportReportBuilder:
    """Zbiera wynik importu scena po scenie i sklada go w jeden dokument."""

    def __init__(self, scan_id: str | None = None) -> None:
        self.scan_id = scan_id or uuid.uuid4().hex
        self.created_at = _utc_now()
        self.scenes: list[dict[str, Any]] = []
        self.sources: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.counters: dict[str, int] = {}
        self.phase_seconds: dict[str, float] = {}
        self.io_bytes: dict[str, Any] = {}
        self.scan_cache: list[dict[str, Any]] = []
        self.cancelled = False

    # --- zbieranie ---------------------------------------------------------------------

    def record_scene(self, entry: dict[str, Any]) -> None:
        self.scenes.append(entry)
        # Lista bledow jest PELNA. Skrocenie do ostatnich 20 nalezy do UI postepu, nie do
        # raportu — inaczej przyczyna wczesnych niepowodzen ginie w duzym imporcie.
        if entry.get("status") in {"error", "blocked"}:
            self.errors.append({
                "scene_id": entry.get("scene_id"),
                "filename": entry.get("filename"),
                "status": entry.get("status"),
                "message": entry.get("error") or entry.get("blocked_reason"),
                "diagnostics": entry.get("diagnostics") or {},
            })

    def record_sources(self, sources: list[dict[str, Any]]) -> None:
        self.sources = [
            {
                "source_id": source.get("source_id"),
                "provider": source.get("provider"),
                "root_path": source.get("root_path"),
            }
            for source in sources or []
        ]

    def annotate_scene(self, scene_id: str, **fields: Any) -> None:
        """Uzupelnij wpis sceny po fazie, ktora konczy sie pozniej niz katalogowanie."""
        for entry in self.scenes:
            if entry.get("scene_id") == scene_id:
                entry.update(fields)
                return

    # --- skladanie ---------------------------------------------------------------------

    def _rollup_by_source(self) -> list[dict[str, Any]]:
        rollup: dict[str, dict[str, Any]] = {}
        for entry in self.scenes:
            source_id = str(entry.get("source_id") or "")
            bucket = rollup.setdefault(source_id, {
                "source_id": source_id,
                "scenes": 0,
                "by_status": {},
                "by_product_type": {},
                "by_asset_role": {},
                "incomplete": 0,
            })
            bucket["scenes"] += 1
            status = str(entry.get("status") or "unknown")
            bucket["by_status"][status] = bucket["by_status"].get(status, 0) + 1
            product = str(entry.get("product_type") or "unknown")
            bucket["by_product_type"][product] = bucket["by_product_type"].get(product, 0) + 1
            for role, count in (entry.get("assets_by_role") or {}).items():
                bucket["by_asset_role"][role] = bucket["by_asset_role"].get(role, 0) + int(count)
            if entry.get("completeness") == "partial":
                bucket["incomplete"] += 1
        for bucket in rollup.values():
            for key in ("by_status", "by_product_type", "by_asset_role"):
                bucket[key] = dict(sorted(bucket[key].items()))
        return [rollup[key] for key in sorted(rollup)]

    def build(self) -> dict[str, Any]:
        return {
            "schema_name": REPORT_SCHEMA_NAME,
            "schema_version": REPORT_SCHEMA_VERSION,
            "scan_id": self.scan_id,
            "created_at": self.created_at,
            "updated_at": _utc_now(),
            "cancelled": self.cancelled,
            **self.counters,
            "phase_seconds": dict(sorted(self.phase_seconds.items())),
            "io_bytes": self.io_bytes,
            "scan_cache": self.scan_cache,
            "sources": self.sources,
            "by_source": self._rollup_by_source(),
            "scenes": self.scenes,
            "errors": self.errors,
        }

    def save(self, project_dir_path: Path) -> Path:
        report = self.build()
        target = reports_dir(project_dir_path) / f"{self.scan_id}.json"
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return target


def scene_entry(
    *,
    scene_id: str,
    filename: str,
    package: dict[str, Any],
    selection: dict[str, Any],
    status: str,
    elapsed_ms: int,
    error: str | None = None,
) -> dict[str, Any]:
    """Zbuduj wpis jednej sceny z tego, co juz wiadomo w chwili katalogowania."""
    assets = package.get("assets") or []
    by_role: dict[str, int] = {}
    by_contract_role: dict[str, int] = {}
    for asset in assets:
        role = str(asset.get("role") or "unknown")
        by_role[role] = by_role.get(role, 0) + 1
        contract_role = str(asset.get("asset_role") or "-")
        by_contract_role[contract_role] = by_contract_role.get(contract_role, 0) + 1
    diagnostics = selection.get("diagnostics") or {}
    return {
        "scene_id": scene_id,
        "filename": filename,
        "source_id": package.get("source_id"),
        "package_id": package.get("package_id"),
        "package_root_relative": package.get("package_root_relative"),
        "provider": package.get("provider"),
        "decision_uid": package.get("decision_uid"),
        "provider_scene_id": selection.get("provider_scene_id"),
        "product_type": selection.get("product_type"),
        "processing_level": selection.get("processing_level"),
        "raster_kind": selection.get("raster_kind"),
        "selection_status": selection.get("status"),
        "selected_by": selection.get("selected_by") or "resolver",
        "completeness": selection.get("completeness"),
        "declared_parts": selection.get("declared_parts"),
        "missing_parts": len(selection.get("missing_parts") or []),
        "asset_count": len(assets),
        "assets_by_role": dict(sorted(by_role.items())),
        "assets_by_contract_role": dict(sorted(by_contract_role.items())),
        "diagnostics": {
            "warnings": list(diagnostics.get("warnings") or []),
            "errors": list(diagnostics.get("errors") or []),
            "metadata_conflicts": list(diagnostics.get("metadata_conflicts") or []),
        },
        "status": status,
        "error": error,
        "elapsed_ms": elapsed_ms,
    }


def manifest_annotations(manifest: dict[str, Any] | None, scene: dict[str, Any] | None) -> dict[str, Any]:
    """Pola, ktore znane sa dopiero po zapisaniu sceny albo po fazach tla."""
    manifest = manifest or {}
    scene = scene or {}
    identity = manifest.get("source_identity") or {}
    working = manifest.get("working_view") or {}
    radiometry = manifest.get("radiometry") or {}
    return {
        "identity_status": identity.get("status"),
        "identity_strength": identity.get("identity_strength"),
        "identity_method": identity.get("identity_method"),
        "metadata_status": manifest.get("metadata_status"),
        "working_view": {
            "raster_kind": working.get("raster_kind"),
            "preparation_status": working.get("preparation_status"),
            "variant_id": working.get("variant_id"),
            "mosaic_geometry": working.get("mosaic_geometry"),
            "locked": bool(working.get("locked")),
        },
        "overview_status": scene.get("overview_status"),
        "overview_type": scene.get("overview_type"),
        "radiometry": {
            "quantity": radiometry.get("quantity"),
            "calibration_state": radiometry.get("calibration_state"),
            "units": radiometry.get("units"),
        },
    }


# --- wariant do przekazania dalej --------------------------------------------------------


def _token(value: str) -> str:
    return "path_" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def anonymize(report: Any) -> Any:
    """Kopia raportu bez sciezek i nazw plikow.

    Skroty sa STABILNE: ta sama sciezka daje ten sam token w calym raporcie, wiec zaleznosci
    miedzy scenami pozostaja czytelne. Liczby, statusy i kody diagnostyki nie sa ruszane —
    to one sa trescia raportu.
    """
    if isinstance(report, dict):
        result: dict[str, Any] = {}
        for key, value in report.items():
            if key in _PATH_KEYS and isinstance(value, str) and value:
                result[key] = _token(value)
            else:
                result[key] = anonymize(value)
        return result
    if isinstance(report, list):
        return [anonymize(item) for item in report]
    return report


def list_reports(project_dir_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Naglowki zapisanych raportow, od najnowszego."""
    directory = project_dir_path / "import_reports"
    if not directory.is_dir():
        return []
    entries: list[tuple[tuple[str, str, int, str], dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mtime_ns = path.stat().st_mtime_ns
        except (OSError, ValueError):
            continue
        header = {
            "scan_id": payload.get("scan_id") or path.stem,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "schema_version": payload.get("schema_version"),
            "added": payload.get("added"),
            "updated": payload.get("updated"),
            "blocked": payload.get("blocked"),
            "failed": payload.get("failed"),
            "archived": payload.get("archived"),
            "missing": payload.get("missing"),
            "cancelled": payload.get("cancelled"),
            "scene_count": len(payload.get("scenes") or []),
            "error_count": len(payload.get("errors") or []),
        }
        # FAT/SMB and some Windows configurations expose a timestamp resolution too coarse
        # to order two reports created in the same tick.  Report timestamps are ISO-8601 UTC,
        # so lexical order is chronological; mtime and scan_id are deterministic fallbacks.
        order_key = (
            str(header["created_at"] or ""),
            str(header["updated_at"] or ""),
            int(mtime_ns),
            str(header["scan_id"] or ""),
        )
        entries.append((order_key, header))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [header for _key, header in entries[:max(0, limit)]]


def load_report(project_dir_path: Path, scan_id: str) -> dict[str, Any] | None:
    path = project_dir_path / "import_reports" / f"{scan_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
