"""Buildery korpusu kontraktowego paczek dostawców (B0).

Konwencja: każdy builder dostaje katalog roboczy i tworzy w nim JEDNĄ dostawę, zwracając
`FixturePackage`. Buildery nie sprzątają po sobie — robi to wołający (tmp dir testu).

Nazwy plików odwzorowują rzeczywiste dostawy z sekcji 4 roadmapy, ale wszystkie identyfikatory
są syntetyczne. Sidecary zawierają tylko te pola, których dotyczy kontrakt; nie odtwarzamy
pełnych schematów dostawców, bo test ma pilnować wiązania, a nie parsowania każdego atrybutu.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

STUB = b"x"


@dataclass(frozen=True)
class FixturePackage:
    """Jedna wygenerowana dostawa."""

    root: Path
    provider: str
    description: str
    #: Ścieżki względem `root`, które są prawdziwymi measurement assets dostawy.
    measurement: tuple[str, ...] = ()
    #: Ścieżki, które NIE są measurement (browse, layout, quicklook, preview).
    non_measurement: tuple[str, ...] = ()
    notes: dict[str, object] = field(default_factory=dict)


def _write(root: Path, relative: str, payload: bytes | str = STUB) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_bytes(payload)


# --- ICEYE ----------------------------------------------------------------------------


def build_iceye_legacy_grd(root: Path) -> FixturePackage:
    """Starsza dostawa ICEYE: GeoTIFF + XML obok siebie."""
    base = root / "ICEYE_GRD_SLH_1234567_20240604T101500"
    _write(base, "ICEYE_GRD_SLH_1234567_20240604T101500.tif")
    _write(base, "ICEYE_GRD_SLH_1234567_20240604T101500.xml", "<product><level>GRD</level></product>")
    _write(base, "ICEYE_GRD_SLH_1234567_20240604T101500_QUICKLOOK.png")
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE legacy GRD (GeoTIFF + XML)",
        measurement=("ICEYE_GRD_SLH_1234567_20240604T101500.tif",),
        non_measurement=("ICEYE_GRD_SLH_1234567_20240604T101500_QUICKLOOK.png",),
    )


def build_iceye_cog_geojson(root: Path) -> FixturePackage:
    """Aktualna dostawa ICEYE: GRD COG + sidecar GeoJSON."""
    base = root / "ICEYE_X12_GRD_SM_7654321_20240605T091200"
    stem = "ICEYE_X12_GRD_SM_7654321_20240605T091200"
    _write(base, f"{stem}_GRD.tif")
    _write(
        base,
        f"{stem}.geojson",
        json.dumps(
            {
                "type": "Feature",
                "properties": {
                    "product_level": "GRD",
                    "platform": "ICEYE-X12",
                    "polarization": "VV",
                    "acquisition_start_utc": "2024-06-05T09:12:00Z",
                    "range_spacing_m": 0.25,
                    "azimuth_spacing_m": 0.25,
                },
            }
        ),
    )
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE current GRD COG + GeoJSON",
        measurement=(f"{stem}_GRD.tif",),
    )


def build_iceye_source_root_with_spreadsheet(root: Path) -> FixturePackage:
    """Root ŹRÓDŁA z arkuszem zestawienia i wieloma akwizycjami w podfolderach.

    Odtwarza sekcję 4.3 roadmapy: plik z `ICEYE` w nazwie leżący bezpośrednio w rootcie
    sprawia, że `_looks_like_package_root()` uznaje CAŁY root za jedną paczkę, zamiast
    zejść do podkatalogów i znaleźć dwie odrębne akwizycje.
    """
    base = root / "iceye_delivery"
    _write(base, "Zestawienie_ICEYE_2024.xlsx")
    for stem in (
        "ICEYE_GRD_SLH_1111111_20240604T101500",
        "ICEYE_GRD_SLH_2222222_20240607T045900",
    ):
        _write(base, f"{stem}/{stem}.tif")
        _write(base, f"{stem}/{stem}.xml", "<product><level>GRD</level></product>")
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE source root: spreadsheet + two acquisitions in subfolders",
        notes={"expected_acquisitions": 2},
    )


def build_iceye_grd_suffix_only(root: Path) -> FixturePackage:
    """Nazwa, w ktorej token `GRD` stoi WYLACZNIE na koncu — `..._GRD.tif`.

    Wymieniona wprost w zakresie P0.3. Stary wzorzec resolvera wymagal po `GRD` znaku `_`,
    `-` albo konca napisu, wiec nazwa konczaca sie `_GRD.tif` (po `GRD` stoi kropka) nie byla
    rozpoznawana jako produkt GRD.
    """
    stem = "ICEYE_X7_SLH_9876543_20240610T142200"
    base = root / stem
    _write(base, f"{stem}_GRD.tif")
    _write(base, f"{stem}_GRD.xml", "<product><level>GRD</level></product>")
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE GRD whose product-level token appears only as a name suffix",
        measurement=(f"{stem}_GRD.tif",),
    )


def build_iceye_multi_product_acquisition(root: Path) -> FixturePackage:
    """Jedna akwizycja, trzy poziomy przetworzenia: GRD, VID i SLC w HDF5.

    Odtwarza sekcje 4.3 w miniaturze. Sidecar VID lezy obok sidecara GRD i ma ten sam
    identyfikator zadania — dokladnie ta konfiguracja dala na produkcie GRD
    `product_level=VID`. SLC jako `.h5` sprawdza wymaganie bramki, zeby inwentarz nie
    traktowal kontenera naukowego jak obrazu do etykietowania.
    """
    task = "5555555_20240612T081500"
    base = root / f"ICEYE_ACQ_{task}"
    _write(base, f"ICEYE_X3_GRD_SM_{task}.tif")
    _write(base, f"ICEYE_X3_GRD_SM_{task}.xml", "<product><level>GRD</level></product>")
    _write(base, f"ICEYE_X3_VID_SM_{task}.tif")
    _write(base, f"ICEYE_X3_VID_SM_{task}.xml", "<product><level>VID</level></product>")
    _write(base, f"ICEYE_X3_SLC_SM_{task}.h5")
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE single acquisition delivering GRD, VID and SLC together",
        measurement=(f"ICEYE_X3_GRD_SM_{task}.tif",),
        non_measurement=(f"ICEYE_X3_VID_SM_{task}.tif",),
        notes={"slc_container": f"ICEYE_X3_SLC_SM_{task}.h5"},
    )


def build_iceye_dual_polarization(root: Path) -> FixturePackage:
    """Jedna akwizycja GRD w dwoch polaryzacjach — dwa warianty logiczne, nie jeden wybor."""
    task = "6666666_20240615T033000"
    base = root / f"ICEYE_DUALPOL_{task}"
    for polarization in ("HH", "HV"):
        _write(base, f"ICEYE_X4_GRD_SM_{task}_{polarization}.tif")
        _write(
            base,
            f"ICEYE_X4_GRD_SM_{task}_{polarization}.xml",
            f"<product><level>GRD</level><polarization>{polarization}</polarization></product>",
        )
    return FixturePackage(
        root=base,
        provider="iceye",
        description="ICEYE GRD delivered in two polarizations from one acquisition",
        measurement=(
            f"ICEYE_X4_GRD_SM_{task}_HH.tif",
            f"ICEYE_X4_GRD_SM_{task}_HV.tif",
        ),
        notes={"polarizations": ["HH", "HV"]},
    )


# --- Capella --------------------------------------------------------------------------


def _capella_delivery(root: Path, folder: str, stem: str, product: str) -> None:
    _write(root, f"{folder}/{stem}.tif")
    _write(root, f"{folder}/{stem}_preview.tif")
    _write(
        root,
        f"{folder}/{stem}_extended.json",
        json.dumps({"collect_id": stem, "product_type": product}),
    )


def build_capella_geo(root: Path) -> FixturePackage:
    """Produkt GEO — dominujący w rzeczywistej dostawie, dziś nierozpoznawany."""
    folder = "CAPELLA_C08_SP_GEO_HH_20240604101500"
    stem = folder
    _capella_delivery(root, folder, stem, "GEO")
    return FixturePackage(
        root=root / folder,
        provider="capella",
        description="Capella GEO product",
        measurement=(f"{stem}.tif",),
        non_measurement=(f"{stem}_preview.tif",),
    )


def build_capella_gec(root: Path) -> FixturePackage:
    """Produkt GEC — jedyny obsługiwany dziś przez resolver."""
    folder = "CAPELLA_C08_SP_GEC_HH_20240604101500"
    stem = folder
    _capella_delivery(root, folder, stem, "GEC")
    return FixturePackage(
        root=root / folder,
        provider="capella",
        description="Capella GEC product",
        measurement=(f"{stem}.tif",),
        non_measurement=(f"{stem}_preview.tif",),
    )


def build_capella_two_acquisitions(root: Path) -> FixturePackage:
    """Jeden folder dostawy z DWIEMA akwizycjami — sekcja 4.4 roadmapy."""
    base = root / "CAPELLA_04_06_2024"
    for stem in (
        "CAPELLA_C08_SP_GEO_HH_20240604101500",
        "CAPELLA_C09_SP_GEO_HH_20240604134500",
    ):
        _write(base, f"{stem}.tif")
        _write(base, f"{stem}_preview.tif")
        _write(base, f"{stem}_extended.json", json.dumps({"collect_id": stem, "product_type": "GEO"}))
    return FixturePackage(
        root=base,
        provider="capella",
        description="Capella delivery folder holding two acquisitions",
        notes={"expected_acquisitions": 2},
    )


def build_capella_geo_and_gec(root: Path) -> FixturePackage:
    """Jedna akwizycja dostarczona jako GEO i GEC naraz.

    Sprawdza preferencję produktu: to są DWA produkty jednej akwizycji, a nie dwie sceny,
    więc rozdzielenie akwizycji nie może ich rozbić na osobne paczki.
    """
    base = root / "CAPELLA_C08_20240604101500"
    stem = "CAPELLA_C08_SP_{product}_HH_20240604101500"
    for product in ("GEO", "GEC"):
        name = stem.format(product=product)
        _write(base, f"{name}.tif")
        _write(base, f"{name}_preview.tif")
        _write(base, f"{name}_extended.json", json.dumps({"collect_id": name, "product_type": product}))
    return FixturePackage(
        root=base,
        provider="capella",
        description="Capella acquisition delivered as both GEO and GEC",
        measurement=(
            stem.format(product="GEC") + ".tif",
            stem.format(product="GEO") + ".tif",
        ),
        non_measurement=(
            stem.format(product="GEO") + "_preview.tif",
            stem.format(product="GEC") + "_preview.tif",
        ),
        notes={"expected_acquisitions": 1, "products": ["GEO", "GEC"]},
    )


# --- Airbus ---------------------------------------------------------------------------


#: Szkielet DIMAP-u Airbusa. Nazwy elementow i ich zagniezdzenie odwzorowuja RZECZYWISTE
#: pliki z korpusu (`DIM_PNEO4_*`, `DIM_PHR1B_*`), lacznie z tym, ze `<Raster_Display>` jest
#: RODZENSTWEM `<Data_File>` wewnatrz `<Data_Files>`, a nie jego dzieckiem.
def _dimap(
    *,
    mission: str,
    mission_index: str,
    spectral: str,
    band_ids: list[str],
    files: list[tuple[str, list[str], list[str] | None]],
    product_type: str | None = None,
) -> str:
    blocks = []
    for filename, display, file_bands in files:
        # `Raster_Index` wiaze identyfikator pasma z jego numerem W PLIKU — a nie z pozycja
        # w kolejnosci wyswietlania. Rzeczywisty DIMAP PHR nie ma tego bloku w ogole, wiec
        # fixture tez go nie emituje, gdy `file_bands` jest `None`.
        indexes = "".join(
            f"<Raster_Index><BAND_ID>{band}</BAND_ID><BAND_INDEX>{position}</BAND_INDEX></Raster_Index>"
            for position, band in enumerate(file_bands or [], start=1)
        )
        index_list = f"<Raster_Index_List>{indexes}</Raster_Index_List>" if indexes else ""
        blocks.append(
            "<Data_Files>"
            f'<Data_File tile_R="1" tile_C="1"><DATA_FILE_PATH href="{filename}"></DATA_FILE_PATH></Data_File>'
            "<Raster_Display>"
            f"{index_list}"
            "<Band_Display_Order>"
            f"<RED_CHANNEL>{display[0]}</RED_CHANNEL>"
            f"<GREEN_CHANNEL>{display[1]}</GREEN_CHANNEL>"
            f"<BLUE_CHANNEL>{display[2]}</BLUE_CHANNEL>"
            "</Band_Display_Order>"
            # Wartosci specjalne wystepuja w obu rzeczywistych dostawach; nazwa elementu
            # mowi „count", ale jest to WARTOSC piksela (ustalone pomiarem na rastrach).
            "<Special_Value><SPECIAL_VALUE_TEXT>NODATA</SPECIAL_VALUE_TEXT>"
            "<SPECIAL_VALUE_COUNT>0</SPECIAL_VALUE_COUNT></Special_Value>"
            "<Special_Value><SPECIAL_VALUE_TEXT>SATURATED</SPECIAL_VALUE_TEXT>"
            "<SPECIAL_VALUE_COUNT>255</SPECIAL_VALUE_COUNT></Special_Value>"
            "</Raster_Display>"
            "</Data_Files>"
        )
    bands = "".join(f"<Band_ID>{band}</Band_ID>" for band in band_ids)
    product = f"<PRODUCT_TYPE>{product_type}</PRODUCT_TYPE>" if product_type else ""
    return (
        "<Dimap_Document>"
        "<Metadata_Identification><METADATA_FORMAT>DIMAP</METADATA_FORMAT></Metadata_Identification>"
        "<Dataset_Sources><Source_Identification>"
        f"<MISSION>{mission}</MISSION><MISSION_INDEX>{mission_index}</MISSION_INDEX>"
        "</Source_Identification>"
        "<Source_Information><Time_Range><START>2024-03-01T10:30:00.0Z</START></Time_Range>"
        "<TIME>2024-03-01T10:30:00.0Z</TIME></Source_Information></Dataset_Sources>"
        f"<Product_Settings>{product}<SPECTRAL_PROCESSING>{spectral}</SPECTRAL_PROCESSING>"
        "<PROCESSING_LEVEL>ORTHO</PROCESSING_LEVEL>"
        "<Radiometric_Settings><RADIOMETRIC_PROCESSING>DISPLAY</RADIOMETRIC_PROCESSING>"
        "</Radiometric_Settings></Product_Settings>"
        f"<Raster_Data><Raster_Dimensions><NBANDS>{len(band_ids)}</NBANDS></Raster_Dimensions>"
        f"<Raster_Encoding><NBITS>8</NBITS><DATA_TYPE>INTEGER</DATA_TYPE></Raster_Encoding>"
        f"<Data_Access>{''.join(blocks)}</Data_Access>"
        f"<Raster_Spectral_Record>{bands}</Raster_Spectral_Record></Raster_Data>"
        "</Dimap_Document>"
    )


#: UUID produktu — Airbus nadaje ten sam identyfikator plikowi `DIM_*` i jego rastrom.
_PNEO_UUID = "698037a1-10fa-4211-c245-2bfb3dc1ee9f"
_PHR_UUID = "2a562ee2-09b6-41fc-c1ab-5d6e9fd4c711"


def build_pleiades_neo_dimap(root: Path) -> FixturePackage:
    """Natywna dostawa PNEO PMS-FS: DWA pliki jednego produktu — `_RGB` i `_NED`.

    Nazewnictwo i struktura DIMAP-u odwzorowuja zweryfikowana dostawe
    `dimapV2_pneo_PNEO4_acq20230515`: satelita, znacznik czasu, wariant spektralny, poziom,
    uuid produktu, wariant pasmowy i kafel. `_NED` (NIR / Red Edge / Deep Blue) jest obrazem
    w barwach umownych i NIE moze byc wybrany jako domyslny produkt do etykietowania.
    """
    # Zagniezdzenie jest PLYTSZE niz w dostawie (`<uuid>_NONE_STD_A/IMG_01_PNEO4_PMS-FS/`):
    # nazwy plikow Airbusa maja po 89 znakow, a katalog tymczasowy pytest kolejne ~100, wiec
    # wierne odwzorowanie przekracza windowsowy limit 260 znakow. Kontrakt testu dotyczy nazw
    # plikow i tresci DIMAP-u, nie glebokosci drzewa.
    base = root / "196_pulk"
    volume = "IMG_01"
    stem = f"PNEO4_202305150851506_PMS-FS_ORT_{_PNEO_UUID}"
    rgb = f"IMG_{stem}_RGB_R1C1.TIF"
    ned = f"IMG_{stem}_NED_R1C1.TIF"
    _write(base, "VOL_PNEO.XML", "<Volume/>")
    _write(
        base,
        f"{volume}/DIM_{stem}.XML",
        _dimap(
            mission="PNEO",
            mission_index="4",
            spectral="PMS-FS",
            product_type="STANDARD",
            band_ids=["R", "G", "B", "NIR", "RE", "DB"],
            files=[
                (rgb, ["R", "G", "B"], ["R", "G", "B"]),
                (ned, ["NIR", "RE", "DB"], ["NIR", "RE", "DB"]),
            ],
        ),
    )
    _write(base, f"{volume}/{rgb}")
    _write(base, f"{volume}/{ned}")
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Pleiades Neo PMS-FS delivery with RGB and NED variants",
        measurement=(f"{volume}/{rgb}",),
        non_measurement=(f"{volume}/{ned}",),
        notes={"rgb_bands": [1, 2, 3], "sensor": "PNEO4", "product_uuid": _PNEO_UUID},
    )


def build_pleiades_phr_dimap(root: Path) -> FixturePackage:
    """Dostawa PHR1B — dziesiec takich lezy w rzeczywistym zrodle Airbusa.

    Rozni sie od PNEO trzema rzeczami, ktore lamia dopasowanie po jednym wzorcu nazwy:
    innym porzadkiem tokenow (spektrum PRZED znacznikiem czasu), brakiem wariantu pasmowego
    i pasmami `B0..B3`, gdzie barwy naturalne to `B2/B1/B0` — czyli pasma **3, 2, 1**.
    """
    base = root / "Gwardiejskoje"
    stem = f"PHR1B_PMS_202111010946043_ORT_{_PHR_UUID}"
    raster = f"IMG_{stem}_R1C1.TIF"
    _write(base, "IMG_PHR1B_PMS_001/VOL_PHR.XML", "<Volume/>")
    _write(
        base,
        f"IMG_PHR1B_PMS_001/DIM_{stem}.XML",
        _dimap(
            mission="PHR",
            mission_index="1B",
            spectral="PMS",
            band_ids=["B0", "B1", "B2", "B3"],
            # PHR nie deklaruje `Raster_Index`; numer pasma wynika z kolejnosci `B0..B3`.
            files=[(raster, ["B2", "B1", "B0"], None)],
        ),
    )
    _write(base, f"IMG_PHR1B_PMS_001/{raster}")
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Pleiades PHR1B delivery declared as a Pleiades Neo source",
        measurement=(f"IMG_PHR1B_PMS_001/{raster}",),
        notes={
            "rgb_bands": [3, 2, 1],
            "sensor": "PHR1B",
            "declared_provider": "pleiades_neo",
            "product_uuid": _PHR_UUID,
        },
    )


def build_pleiades_neo_multi_tile(root: Path) -> FixturePackage:
    """Produkt PNEO podzielony na kafle.

    W korpusie NIE MA dostawy Airbusa z wiecej niz jednym kaflem — wszystkie maja `R1C1`
    i `NTILES = 1`. Ten fixture jest wiec jedynym sposobem sprawdzenia, ze czesci jednego
    produktu skladaja sie w jedna scene, i jest jawnie oznaczony jako syntetyczny.
    """
    base = root / "tiled"
    volume = "IMG_01"
    stem = f"PNEO4_202305150851506_PMS-FS_ORT_{_PNEO_UUID}"
    parts = [f"IMG_{stem}_RGB_R1C{index}.TIF" for index in (1, 2)]
    _write(
        base,
        f"{volume}/DIM_{stem}.XML",
        _dimap(
            mission="PNEO",
            mission_index="4",
            spectral="PMS-FS",
            product_type="STANDARD",
            band_ids=["R", "G", "B"],
            files=[(parts[0], ["R", "G", "B"], ["R", "G", "B"])],
        ),
    )
    for name in parts:
        _write(base, f"{volume}/{name}")
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Pleiades Neo product split into two tiles (synthetic)",
        measurement=tuple(f"{volume}/{name}" for name in parts),
        notes={"synthetic": True, "parts": 2},
    )


def build_pleiades_ms_and_pan(root: Path) -> FixturePackage:
    """Dostawa bez gotowego pansharpened: MS-FS i PAN do polaczenia."""
    base = root / "ms_pan_delivery"
    stem = f"PNEO4_202305150851506_ORT_{_PNEO_UUID}"
    _write(base, f"DIM_{stem}.XML", _dimap(
        mission="PNEO", mission_index="4", spectral="MS-FS",
        band_ids=["R", "G", "B"],
        files=[(f"IMG_{stem}_MS-FS_R1C1.TIF", ["R", "G", "B"], ["R", "G", "B"])],
    ))
    _write(base, f"IMG_{stem}_MS-FS_R1C1.TIF")
    _write(base, f"IMG_{stem}_PAN_R1C1.TIF")
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Pleiades Neo delivery requiring pansharpening",
        measurement=(f"IMG_{stem}_MS-FS_R1C1.TIF", f"IMG_{stem}_PAN_R1C1.TIF"),
    )


# --- WorldView ------------------------------------------------------------------------


def _til(parts: list[str]) -> str:
    """Minimalny TIL deklarujący listę części — dokładnie to, czego dotyczy kontrakt."""
    entries = "\n".join(
        f'\tBEGIN_GROUP = TILE_{index}\n\t\tfilename = "{name}";\n\tEND_GROUP = TILE_{index}'
        for index, name in enumerate(parts, start=1)
    )
    return f"BEGIN_GROUP = TILESET\n\tnumTiles = {len(parts)};\n{entries}\nEND_GROUP = TILESET\nEND;\n"


_IMD_8BAND = (
    "BEGIN_GROUP = IMAGE_1\n"
    "\tsatId = \"WV02\";\n"
    "END_GROUP = IMAGE_1\n"
    "BEGIN_GROUP = BAND_C\nEND_GROUP = BAND_C\n"
    "BEGIN_GROUP = BAND_B\nEND_GROUP = BAND_B\n"
    "BEGIN_GROUP = BAND_G\nEND_GROUP = BAND_G\n"
    "BEGIN_GROUP = BAND_R\nEND_GROUP = BAND_R\n"
    "END;\n"
)
_IMD_PAN = "BEGIN_GROUP = IMAGE_1\n\tsatId = \"WV02\";\nEND_GROUP = IMAGE_1\nEND;\n"


def build_worldview_mul_pan(root: Path) -> FixturePackage:
    """Dostawa WV02 MUL+PAN, po dwie części każdy — odpowiednik `014670314010_01_003`."""
    base = root / "014670314010_01_003"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    _write(base, "README.TXT", "delivery readme")
    mul_parts = ["14JUN11_MUL_R1C1.TIF", "14JUN11_MUL_R1C2.TIF"]
    pan_parts = ["14JUN11_PAN_R1C1.TIF", "14JUN11_PAN_R1C2.TIF"]
    for name in mul_parts:
        _write(base, f"014670314010_01/_MUL/{name}")
    for name in pan_parts:
        _write(base, f"014670314010_01/_PAN/{name}")
    _write(base, "014670314010_01/_MUL/14JUN11_MUL.IMD", _IMD_8BAND)
    _write(base, "014670314010_01/_MUL/14JUN11_MUL.TIL", _til(mul_parts))
    _write(base, "014670314010_01/_MUL/14JUN11_MUL.RPB", "satId = \"WV02\";\n")
    _write(base, "014670314010_01/_PAN/14JUN11_PAN.IMD", _IMD_PAN)
    _write(base, "014670314010_01/_PAN/14JUN11_PAN.TIL", _til(pan_parts))
    _write(base, "014670314010_01/LAYOUT.JPG")
    _write(base, "014670314010_01/BROWSE.JPG")
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView-2 MUL+PAN delivery, 2+2 parts",
        measurement=tuple(
            [f"014670314010_01/_MUL/{n}" for n in mul_parts]
            + [f"014670314010_01/_PAN/{n}" for n in pan_parts]
        ),
        non_measurement=("014670314010_01/LAYOUT.JPG", "014670314010_01/BROWSE.JPG"),
    )


def build_worldview_pan_only(root: Path) -> FixturePackage:
    """Dostawa WV02 PAN-only, sześć części zgodnych z TIL — odpowiednik `014679500010_01_003`."""
    base = root / "014679500010_01_003"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    parts = [f"18APR08_PAN_R{r}C{c}.TIF" for r in (1, 2, 3) for c in (1, 2)]
    for name in parts:
        _write(base, f"014679500010_01/_PAN/{name}")
    _write(base, "014679500010_01/_PAN/18APR08_PAN.IMD", _IMD_PAN)
    _write(base, "014679500010_01/_PAN/18APR08_PAN.TIL", _til(parts))
    _write(base, "014679500010_01/_PAN/18APR08_PAN.RPB", "satId = \"WV02\";\n")
    _write(base, "014679500010_01/LAYOUT.JPG")
    _write(base, "014679500010_01/BROWSE.JPG")
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView-2 PAN-only delivery, 6 parts declared by TIL",
        measurement=tuple(f"014679500010_01/_PAN/{n}" for n in parts),
        non_measurement=("014679500010_01/LAYOUT.JPG", "014679500010_01/BROWSE.JPG"),
        notes={"declared_parts": len(parts)},
    )


def build_worldview_incomplete_extract(root: Path) -> FixturePackage:
    """TIL deklaruje sześć części, na dysku są trzy.

    Odtwarza sytuację zaobserwowaną podczas audytu (katalog zmieniał się z 3 do 6 plików
    w trakcie skanu). Kontrakt: niepełna ekstrakcja MUSI być odróżnialna od poprawnego
    PAN-only, a nie prezentowana jako gotowa scena.
    """
    base = root / "014679500010_01_003_incomplete"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    declared = [f"18APR08_PAN_R{r}C{c}.TIF" for r in (1, 2, 3) for c in (1, 2)]
    for name in declared[:3]:
        _write(base, f"014679500010_01/_PAN/{name}")
    _write(base, "014679500010_01/_PAN/18APR08_PAN.IMD", _IMD_PAN)
    _write(base, "014679500010_01/_PAN/18APR08_PAN.TIL", _til(declared))
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView-2 PAN delivery extracted only partially (3 of 6 parts)",
        measurement=tuple(f"014679500010_01/_PAN/{n}" for n in declared[:3]),
        notes={"declared_parts": len(declared), "present_parts": 3},
    )


def build_worldview_til_custom_order(root: Path) -> FixturePackage:
    """TIL deklaruje kolejność części ODWROTNĄ do porządku nazw.

    To jedyny sposób, żeby odróżnić „kolejność z TIL" od „kolejności z `natural_key`" — przy
    zgodnych porządkach oba dają ten sam wynik i test niczego nie mierzy. Bramka P0.6 wymaga
    wprost, żeby kolejność pochodziła z manifestu dostawcy.
    """
    base = root / "014670999010_01_003"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    parts = [f"14JUN11_PAN_R{r}C1.TIF" for r in (1, 2, 3)]
    for name in parts:
        _write(base, f"014670999010_01/_PAN/{name}")
    _write(base, "014670999010_01/_PAN/14JUN11_PAN.IMD", _IMD_PAN)
    _write(base, "014670999010_01/_PAN/14JUN11_PAN.TIL", _til(list(reversed(parts))))
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView PAN delivery whose TIL order is the reverse of the name order",
        measurement=tuple(f"014670999010_01/_PAN/{n}" for n in parts),
        notes={"til_order": list(reversed(parts))},
    )


def build_worldview_mul_only(root: Path) -> FixturePackage:
    """Dostawa wyłącznie multispektralna — zakres P0.6 wymienia ją jako `multispectral view`."""
    base = root / "014670888010_01_003"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    parts = ["14JUN11_MUL_R1C1.TIF", "14JUN11_MUL_R1C2.TIF"]
    for name in parts:
        _write(base, f"014670888010_01/_MUL/{name}")
    _write(base, "014670888010_01/_MUL/14JUN11_MUL.IMD", _IMD_8BAND)
    _write(base, "014670888010_01/_MUL/14JUN11_MUL.TIL", _til(parts))
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView MUL-only delivery",
        measurement=tuple(f"014670888010_01/_MUL/{n}" for n in parts),
    )


def build_worldview_two_products(root: Path) -> FixturePackage:
    """Dwa produkty `P001`/`P002` w jednym korzeniu dostawy.

    Komponent i zamowienie sa takie same. Gdyby resolver grupowal tylko po komponencie albo
    bazowym numerze zamowienia, cztery rastry utworzylby blednie jedna mozaike.
    """
    base = root / "014670777010_01_003"
    _write(base, "DeliveryMetadata.xml", "<delivery/>")
    measurement: list[str] = []
    for product_id, timestamp in (("P001", "14JUN11102144"), ("P002", "14JUN11103144")):
        directory = f"014670777010_01/014670777010_01_{product_id}_PAN"
        parts = [
            f"{timestamp}-P2AS_R1C1-014670777010_01_{product_id}.TIF",
            f"{timestamp}-P2AS_R1C2-014670777010_01_{product_id}.TIF",
        ]
        for name in parts:
            relative = f"{directory}/{name}"
            _write(base, relative)
            measurement.append(relative)
        stem = f"{timestamp}-P2AS-014670777010_01_{product_id}"
        _write(base, f"{directory}/{stem}.IMD", _IMD_PAN)
        _write(base, f"{directory}/{stem}.TIL", _til(parts))
    return FixturePackage(
        root=base,
        provider="worldview",
        description="Two WorldView products in one delivery root",
        measurement=tuple(measurement),
        notes={"expected_scenes": 2, "parts_per_scene": 2},
    )


def build_flattened_generic_rasters(root: Path) -> FixturePackage:
    """Spłaszczone rastry pochodne — mają pozostać osobnymi scenami `generic`.

    Układ spotykany w dostawach przetworzonych poza aplikacją: rastry rozrzucone po
    podfolderach lokalizacji, bez manifestu paczki.

    Ostatni warunek bramki P0.6. Nazwy niosą tokeny WorldView (`MUL`, `PAN`), więc gdyby
    gramatyka komponentów wyciekła poza resolver WorldView, sceny zostałyby scalone.
    """
    base = root / "flat_delivery"
    for name in ("lokalizacja_A/obraz_MUL.tif", "lokalizacja_B/obraz_PAN.tif", "obraz_C.tif"):
        _write(base, name)
    return FixturePackage(
        root=base,
        provider="generic",
        description="Flattened derived rasters that must stay separate generic scenes",
        measurement=("lokalizacja_A/obraz_MUL.tif", "lokalizacja_B/obraz_PAN.tif", "obraz_C.tif"),
        notes={"expected_scenes": 3},
    )


# --- Archiwa (P1.3a) ------------------------------------------------------------------


def _zip(target: Path, entries: dict[str, bytes | str]) -> None:
    """Zapisz archiwum o zadanej zawartosci. Rozmiary wpisow sa istotne dla porownania."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def build_worldview_archive_duplicate(root: Path) -> FixturePackage:
    """ZIP obok kompletnie rozpakowanej dostawy — uklad `\\\\...\\WV2` z sekcji 4.6.

    Wpisy archiwum niosa WLASNY katalog dostawy, wiec rozpakowana kopia lezy bezposrednio w
    katalogu, w ktorym stoi ZIP. To pierwsza z dwoch konwencji, ktore musi obsluzyc
    `archives.match_extracted()`.
    """
    base = root / "WV2"
    delivery = "014670314010_01_003"
    files = {
        f"{delivery}/_PAN/14JUN11_PAN_R1C1.TIF": b"raster-a",
        f"{delivery}/_PAN/14JUN11_PAN_R1C2.TIF": b"raster-b",
        f"{delivery}/_PAN/14JUN11_PAN.IMD": "bandId = \"P\";",
    }
    for name, payload in files.items():
        _write(base, name, payload)
    _zip(base / "EPWApan_014670314010_0.zip", files)
    return FixturePackage(
        root=base,
        provider="worldview",
        description="WorldView delivery duplicated by a sibling ZIP",
        measurement=(
            f"{delivery}/_PAN/14JUN11_PAN_R1C1.TIF",
            f"{delivery}/_PAN/14JUN11_PAN_R1C2.TIF",
        ),
        notes={"archive_relative": "EPWApan_014670314010_0.zip", "archive_entries": 3},
    )


def build_airbus_archive_incomplete(root: Path) -> FixturePackage:
    """Archiwum, ktorego rozpakowana kopia jest NIEPELNA — realny przypadek z sekcji 4.5.

    Uklad odwzorowuje jedyna paczke PNEO w korpusie: katalog `foo/` obok `foo.zip` zawiera
    tylko czesc plikow, a brakuje w nim wlasnie rastra. Druga z dwoch obslugiwanych konwencji.
    """
    base = root / "196_pulk"
    stem = "dimapV2_pneo_PNEO4_acq20230515_dele2bbb8ea"
    entries = {
        f"{stem}_STD_A/VOL_PNEO.XML": "<volume/>",
        f"{stem}_STD_A/IMG_01_PNEO4_PMS-FS/DIM_PNEO4.XML": "<dimap/>",
        f"{stem}_STD_A/IMG_01_PNEO4_PMS-FS/IMG_PNEO4_RGB_R1C1.TIF": b"r" * 512,
    }
    _zip(base / f"{stem}.zip", entries)
    # Na dysku jest wylacznie plik wolumenu — dwa pozostale nigdy sie nie rozpakowaly.
    _write(base / stem, f"{stem}_STD_A/VOL_PNEO.XML", "<volume/>")
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Airbus delivery whose extracted copy is missing the raster",
        notes={
            "archive_relative": f"{stem}.zip",
            "archive_entries": 3,
            "extracted_present": 1,
            "missing_raster": f"{stem}_STD_A/IMG_01_PNEO4_PMS-FS/IMG_PNEO4_RGB_R1C1.TIF",
        },
    )


def build_airbus_archive_only(root: Path) -> FixturePackage:
    """Dostawa istniejaca WYLACZNIE w archiwum — dzis znika ze skanu bez diagnostyki."""
    base = root / "airbus_source"
    stem = "dimapV2_PHR1A_acq20210917_deldbd15137"
    _zip(base / "Gwardiejskoje" / f"{stem}.zip", {
        f"IMG_PHR1A_PMS_001/DIM_PHR1A.XML": "<dimap/>",
        f"IMG_PHR1A_PMS_001/IMG_PHR1A_R1C1.TIF": b"r" * 256,
    })
    return FixturePackage(
        root=base,
        provider="pleiades_neo",
        description="Airbus delivery available only as an archive",
        notes={"archive_relative": f"Gwardiejskoje/{stem}.zip", "archive_entries": 2},
    )


def build_archive_with_unsafe_entries(root: Path) -> FixturePackage:
    """Archiwum z nazwami wychodzacymi poza katalog docelowy.

    Sprawdzane JUZ przy indeksowaniu, mimo ze P1.3a niczego nie rozpakowuje: nazwa wpisu nigdy
    nie ma prawa zamienic sie w sciezke na dysku, nawet przy porownaniu z rozpakowana kopia.
    """
    base = root / "unsafe_source"
    _zip(base / "delivery" / "suspicious.zip", {
        "../../etc/passwd": "nope",
        "/absolute/path.tif": b"nope",
        "IMG/legit_R1C1.TIF": b"r" * 64,
    })
    return FixturePackage(
        root=base,
        provider="worldview",
        description="Archive containing entries that escape the extraction root",
        notes={"archive_relative": "delivery/suspicious.zip", "safe_entries": 1, "unsafe_entries": 2},
    )


def build_readme_only_directory(root: Path) -> FixturePackage:
    """Katalog z samym README i rastrem w podfolderze.

    Dokumentuje ustalenie 17.2.2: token `README` w sygnaturze WorldView sprawia, że taki
    katalog jest uznawany za root paczki WorldView, mimo braku jakiegokolwiek artefaktu
    swoistego dla dostawcy.
    """
    base = root / "jakis_katalog_z_readme"
    _write(base, "README.md", "to nie jest dostawa WorldView")
    _write(base, "podfolder/obraz.tif")
    return FixturePackage(
        root=base,
        provider="worldview",
        description="Directory whose only WorldView-looking artifact is a README",
    )


# --- Geometria (P0.7) -----------------------------------------------------------------


def build_rotated_transform_raster(
    root: Path,
    *,
    spacing: float = 0.25,
    rotation_deg: float = 79.0,
    crs: str = "EPSG:32633",
    name: str = "rotated.tif",
) -> Path:
    """Mały GeoTIFF z OBRÓCONĄ geotransformacją i znanym pixel spacing.

    To jedyny builder tworzący prawdziwy raster, bo kontrakt dotyczy tu wartości, nie nazw.
    Przy obrocie `θ` człon `a` transformacji wynosi `spacing·cos(θ)`, więc odczyt oparty na
    samym `abs(a)` zaniża spacing tym mocniej, im większy obrót — to mechanizm z sekcji 17.2.1.
    """
    import math

    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    theta = math.radians(rotation_deg)
    transform = (
        Affine.translation(500_000.0, 5_600_000.0)
        * Affine.rotation(rotation_deg)
        * Affine.scale(spacing, -spacing)
    )
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=8,
        height=8,
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(np.zeros((8, 8), dtype="uint8"), 1)
    return path
