"""Wersjonowany kontrakt grafu dostawa–akwizycja–produkt–asset (P0.1, P0.2).

Dotychczasowy model utozsamia PAKIET z FOLDEREM. Sekcja 4 roadmapy pokazuje, ze to zalozenie
nie utrzymuje sie na rzeczywistych dostawach: root ICEYE z arkuszem zestawienia scala 432
rastry w jedna "scene", jeden folder Capella miesci dwie akwizycje, a dostawa WorldView ma
dwa komponenty (MUL i PAN) tego samego produktu.

Ten modul definiuje slownictwo i identyfikatory dla modelu, ktory tego zalozenia nie robi.
Jest CELOWO wolny od wejscia/wyjscia i od GDAL — dzieki temu identyfikatory i role da sie
testowac deterministycznie, a resolvery dostawcow moga go uzywac stopniowo.

Stan wdrozenia: struktury i UID-y sa gotowe; przepiecie discovery na graf odbywa sie
etapami za flaga `GEOTILE_SCENE_PACKAGE_GRAPH_V2` (patrz `roles.py` i `base.py`).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = 2

FLAG_GRAPH_V2 = "GEOTILE_SCENE_PACKAGE_GRAPH_V2"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def graph_v2_enabled() -> bool:
    """Czy kontrakt v2 jest wlaczony. Domyslnie NIE — zmiana jest odwracalna flaga."""
    return str(os.environ.get(FLAG_GRAPH_V2) or "").strip().lower() in _TRUE_VALUES


# --- Role assetow (P0.2) --------------------------------------------------------------
#
# Dotychczas asset mial jedna z DWOCH rol: `raster_candidate` albo `metadata`, wylacznie na
# podstawie rozszerzenia pliku. Skutkiem bylo m.in. to, ze `BROWSE.JPG`, `LAYOUT.JPG`
# i `<produkt>_preview.tif` trafialy do puli kandydatow na obraz do etykietowania.

ROLE_MEASUREMENT = "measurement"
ROLE_PRODUCT_METADATA = "product_metadata"
ROLE_DELIVERY_METADATA = "delivery_metadata"
ROLE_RPC = "rpc"
ROLE_TILE_MANIFEST = "tile_manifest"
ROLE_BROWSE = "browse"
ROLE_LAYOUT = "layout"
ROLE_FOOTPRINT = "footprint"
ROLE_AUXILIARY = "auxiliary"
ROLE_ARCHIVE = "archive"
ROLE_UNKNOWN = "unknown"

ASSET_ROLES = (
    ROLE_MEASUREMENT,
    ROLE_PRODUCT_METADATA,
    ROLE_DELIVERY_METADATA,
    ROLE_RPC,
    ROLE_TILE_MANIFEST,
    ROLE_BROWSE,
    ROLE_LAYOUT,
    ROLE_FOOTPRINT,
    ROLE_AUXILIARY,
    ROLE_ARCHIVE,
    ROLE_UNKNOWN,
)

#: Role, ktore moga zostac wybrane jako obraz do etykietowania. Wszystko poza ta lista jest
#: materialem pomocniczym i nie moze trafic do wyboru measurement.
SELECTABLE_ROLES = frozenset({ROLE_MEASUREMENT})


# --- Identyfikatory -------------------------------------------------------------------
#
# Wymagania z sekcji 7/P0.1: identyfikator nie moze zalezec wylacznie od sciezki wzglednej,
# a `decision_uid` musi zawierac `source_id`, zeby dwa zrodla o tej samej strukturze nie
# dzielily decyzji uzytkownika. UID musi tez byc odporny na kolejnosc plikow i na systemowa
# wielkosc liter.

_WHITESPACE = re.compile(r"\s+")


def normalize_token(value: Any) -> str:
    """Znormalizuj skladnik identyfikatora: bez ogonow, bez wielkosci liter, bez wielospacji."""
    text = _WHITESPACE.sub(" ", str(value if value is not None else "").strip())
    return text.casefold()


def _uid(prefix: str, parts: "list[Any] | tuple[Any, ...]") -> str:
    payload = "|".join(normalize_token(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def delivery_uid(provider: str, delivery_id: str) -> str:
    return _uid("dlv", [provider, delivery_id])


def acquisition_uid(delivery: str, acquisition_id: str, acquisition_datetime: Any = None) -> str:
    return _uid("acq", [delivery, acquisition_id, acquisition_datetime or ""])


def product_uid(
    acquisition: str,
    product_type: str,
    band_or_polarization: "list[str] | tuple[str, ...] | str | None" = None,
    product_id: Any = None,
) -> str:
    """UID produktu. Zestaw pasm/polaryzacji jest SORTOWANY — kolejnosc nie moze zmieniac UID."""
    if band_or_polarization is None:
        bands: list[str] = []
    elif isinstance(band_or_polarization, str):
        bands = [band_or_polarization]
    else:
        bands = list(band_or_polarization)
    return _uid(
        "prd",
        [acquisition, product_type, ",".join(sorted(normalize_token(b) for b in bands)), product_id or ""],
    )


def decision_uid(source_id: str, product: str) -> str:
    """Klucz decyzji uzytkownika.

    Zawiera `source_id`, bo dwa rozne zrodla moga miec identyczna strukture katalogow —
    bez tego skladnika decyzja podjeta w jednym zrodle wyciekalaby do drugiego.
    """
    return _uid("dec", [source_id, product])


# --- Struktury grafu ------------------------------------------------------------------


@dataclass(frozen=True)
class AssetRef:
    """Jeden plik dostawy wraz z rola i przynaleznoscia."""

    asset_id: str
    relative_path: str
    role: str
    size: int | None = None
    mtime_ns: int | None = None
    part_id: str | None = None
    component: str | None = None  # np. "multispectral" / "panchromatic"
    format: str | None = None

    @property
    def is_selectable(self) -> bool:
        return self.role in SELECTABLE_ROLES


@dataclass(frozen=True)
class PackageDiagnostic:
    """Rozpoznana sytuacja wymagajaca uwagi. `blocking` decyduje, czy blokuje `ready`."""

    code: str
    message: str
    level: str = "warning"
    blocking: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "level": self.level,
            "blocking": self.blocking,
            **({"context": self.context} if self.context else {}),
        }


@dataclass(frozen=True)
class ProductCandidate:
    """Jeden produkt jednej akwizycji, wraz z assetami wg roli."""

    product_uid: str
    product_type: str
    assets: tuple[AssetRef, ...] = ()
    processing_level: str | None = None
    spectral_layout: str | None = None
    completeness: str | None = None  # "complete" | "partial" | "unknown"
    declared_parts: int | None = None
    diagnostics: tuple[PackageDiagnostic, ...] = ()

    def by_role(self, role: str) -> tuple[AssetRef, ...]:
        return tuple(asset for asset in self.assets if asset.role == role)

    @property
    def measurement_assets(self) -> tuple[AssetRef, ...]:
        return self.by_role(ROLE_MEASUREMENT)

    @property
    def is_blocked(self) -> bool:
        return any(item.blocking for item in self.diagnostics)


@dataclass(frozen=True)
class AcquisitionCandidate:
    acquisition_uid: str
    acquisition_id: str
    acquisition_datetime: str | None = None
    sensor: str | None = None
    products: tuple[ProductCandidate, ...] = ()


@dataclass(frozen=True)
class DeliveryCandidate:
    delivery_uid: str
    delivery_id: str
    provider_declared: str
    provider_detected: str | None = None
    detection_confidence: float | None = None
    detection_evidence: tuple[str, ...] = ()
    acquisitions: tuple[AcquisitionCandidate, ...] = ()
    diagnostics: tuple[PackageDiagnostic, ...] = ()

    @property
    def provider_mismatch(self) -> bool:
        """Czy dostawca zadeklarowany rozni sie od wykrytego (sekcja 3.4)."""
        return bool(
            self.provider_detected
            and normalize_token(self.provider_detected) != normalize_token(self.provider_declared)
        )


@dataclass(frozen=True)
class ProductSelection:
    """Wybor produktu przekazywany do warstwy API i UI."""

    decision_uid: str
    product_uid: str
    product_type: str
    status: str  # "ready" | "prepare_required" | "decision_required" | "invalid"
    measurement_asset_ids: tuple[str, ...] = ()
    metadata_asset_ids: tuple[str, ...] = ()
    auxiliary_asset_ids: tuple[str, ...] = ()
    source_components: dict[str, tuple[str, ...]] = field(default_factory=dict)
    completeness: str | None = None
    selected_by: str = "resolver"
    diagnostics: tuple[PackageDiagnostic, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "decision_uid": self.decision_uid,
            "product_uid": self.product_uid,
            "product_type": self.product_type,
            "status": self.status,
            "measurement_asset_ids": list(self.measurement_asset_ids),
            "metadata_asset_ids": list(self.metadata_asset_ids),
            "auxiliary_asset_ids": list(self.auxiliary_asset_ids),
            "source_components": {k: list(v) for k, v in self.source_components.items()},
            "completeness": self.completeness,
            "selected_by": self.selected_by,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }
