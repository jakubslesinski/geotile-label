"""Derywat pelnej rozdzielczosci (COG) — kwalifikacja i kontrakt zadania.

Realizuje R1.0 i R1.2 (DESIGN_DECISIONS.md, jp2-fullres).

Kwalifikacja
------------
Konwersji podlegaja WYLACZNIE problematyczne sceny `direct` generic JP2. Nie kwalifikuja
sie mozaiki, GeoTIFF/NITF/SAR ani male JP2, ktorych poziomy natywne wystarczaja
do wyswietlania — dla nich derywat kosztowalby ~20 minut i kilka GB, nie dajac nic.

Predykat jest wyprowadzony z TEGO SAMEGO kontraktu, ktory ogranicza zoom (R0.1/R0.2):
scena potrzebuje derywatu dokladnie wtedy, gdy aktywna sciezka odczytu wymaga dekodu JP2
i nie siega poziomu 1x. Dzieki temu nie da sie doprowadzic do stanu, w ktorym zoom jest
ograniczony, a nikt nie buduje derywatu — albo odwrotnie.

Kontrakt zadania
----------------
`SCENE_PREPARATION` nie nadaje sie do ponownego uzycia: jego payload wymaga dokladnie
trzech pasm RGB i uruchamia pansharpening. Stad osobny typ zadania.

Payload jest NIEZMIENNY i niesie wszystko, co pozwala pozniej stwierdzic, czy wynik jest
jeszcze aktualny. Sprawdzenie przed aktywacja jest obowiazkowe: relink albo podmiana
zrodla w trakcie dwudziestominutowej budowy nie moze opublikowac COG-a zbudowanego
z nieaktualnych pikseli.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.scene_display_contract import (
    DISPLAY_KIND_PREVIEW_GEOTIFF,
    SOURCE_KIND_JP2,
    classify_display_assets,
)

#: Wersja profilu COG. Podniesienie wymusza leniwa odbudowe derywatow (R1.1) — nie masowa
#: przy otwarciu projektu, tylko per scena przy pierwszym uzyciu.
COG_PROFILE_VERSION = 2

#: Parametry zapisu wybrane pomiarem w E4 (§12 planu). `BLOCKSIZE` zostaje 512 do czasu
#: bramki „profile freeze" — wariant 256 jest szybszy w odczycie, ale ramie C1 w E5
#: mierzono na 512 i zmiana wymaga ponownego pomiaru, nie decyzji tutaj.
COG_PROFILE = {
    "compression": "ZSTD",
    "predictor": 2,
    "blocksize": 512,
    "overviews": "AUTO",
    "resampling": "AVERAGE",
}

DISQUALIFIED_NOT_DIRECT = "raster_kind_is_not_direct"
DISQUALIFIED_NO_JP2_DECODE = "display_path_does_not_decode_jp2"
DISQUALIFIED_ALREADY_FULL_RESOLUTION = "one_x_is_already_servable"
QUALIFIED = "qualified"


@dataclass(frozen=True)
class Qualification:
    qualifies: bool
    reason: str
    finest_display_factor: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "qualifies": self.qualifies,
            "reason": self.reason,
            "finest_display_factor": self.finest_display_factor,
        }


def qualifies_for_fullres_derivative(
    *,
    source_path: Path | None,
    display_path: Path | None,
    raster_kind: str | None,
    scene_info: dict[str, Any] | None,
    overview_factors: list[int] | None = None,
) -> Qualification:
    """Czy ta scena wymaga pelnorozdzielczego derywatu (R1.0)."""
    # `virtual_mosaic` ma wlasna sciezke przygotowania; wciagniecie go tutaj
    # oznaczaloby budowanie derywatu dla czegos, co juz jest przygotowane.
    if raster_kind != "direct":
        return Qualification(False, DISQUALIFIED_NOT_DIRECT)

    assets = classify_display_assets(
        source_path=source_path,
        display_path=display_path,
        raster_kind=raster_kind,
        scene_info=scene_info,
        overview_factors=overview_factors,
    )
    preview_only = (
        assets.source_asset_kind == SOURCE_KIND_JP2
        and assets.display_asset_kind == DISPLAY_KIND_PREVIEW_GEOTIFF
    )
    if not assets.display_requires_jp2_decode and not preview_only:
        # Albo zrodlo nie jest JP2, albo aktywny pelnorozdzielczy derywat juz istnieje.
        return Qualification(False, DISQUALIFIED_NO_JP2_DECODE)
    if assets.finest_display_factor <= 1:
        # Male JP2 z uzytecznymi poziomami natywnymi obsluguja 1x wystarczajaco szybko.
        return Qualification(False, DISQUALIFIED_ALREADY_FULL_RESOLUTION)
    return Qualification(True, QUALIFIED, assets.finest_display_factor)


def build_job_payload(
    *,
    scene_id: str,
    source_fingerprint: str | None,
    variant_id: str | None,
    source_revision: str | None,
) -> dict[str, Any]:
    """Niezmienny payload zadania (R1.2).

    Wszystkie pola sluza jednemu celowi: zeby po dwudziestu minutach dalo sie stwierdzic,
    czy wynik odnosi sie jeszcze do tego samego zrodla i tego samego profilu.
    """
    return {
        "scene_id": scene_id,
        "source_fingerprint": source_fingerprint,
        "variant_id": variant_id,
        "cog_profile_version": COG_PROFILE_VERSION,
        "expected_source_revision": source_revision,
    }


def dedupe_key(project_id: str, scene_id: str) -> str:
    return f"scene-fullres:{project_id}:{scene_id}"


@dataclass(frozen=True)
class ActivationCheck:
    may_activate: bool
    reason: str
    details: dict[str, Any]


ACTIVATION_OK = "current"
ACTIVATION_SOURCE_CHANGED = "source_fingerprint_changed"
ACTIVATION_VARIANT_CHANGED = "working_variant_changed"
ACTIVATION_PROFILE_CHANGED = "cog_profile_version_changed"
ACTIVATION_REVISION_CHANGED = "source_revision_changed"


def may_activate(
    payload: dict[str, Any],
    *,
    source_fingerprint: str | None,
    variant_id: str | None,
    source_revision: str | None,
) -> ActivationCheck:
    """Czy zbudowany derywat wolno aktywowac (R1.2, R1.4).

    Wolane BEZPOSREDNIO przed przelaczeniem, nie na starcie zadania. Miedzy startem
    a koncem budowy mieszcza sie dwadziescia minut, w ktorych scena mogla zostac
    zrelinkowana albo zrodlo podmienione — a wtedy gotowy plik opisuje piksele, ktorych
    juz nie ma.
    """
    details = {
        "expected": {
            "source_fingerprint": payload.get("source_fingerprint"),
            "variant_id": payload.get("variant_id"),
            "cog_profile_version": payload.get("cog_profile_version"),
            "expected_source_revision": payload.get("expected_source_revision"),
        },
        "actual": {
            "source_fingerprint": source_fingerprint,
            "variant_id": variant_id,
            "cog_profile_version": COG_PROFILE_VERSION,
            "expected_source_revision": source_revision,
        },
    }
    if payload.get("source_fingerprint") != source_fingerprint:
        return ActivationCheck(False, ACTIVATION_SOURCE_CHANGED, details)
    if payload.get("variant_id") != variant_id:
        return ActivationCheck(False, ACTIVATION_VARIANT_CHANGED, details)
    if int(payload.get("cog_profile_version") or 0) != COG_PROFILE_VERSION:
        return ActivationCheck(False, ACTIVATION_PROFILE_CHANGED, details)
    if payload.get("expected_source_revision") != source_revision:
        return ActivationCheck(False, ACTIVATION_REVISION_CHANGED, details)
    return ActivationCheck(True, ACTIVATION_OK, details)
