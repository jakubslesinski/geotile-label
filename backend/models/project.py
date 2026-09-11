import re
import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone


SCHEMA_VERSION = 2
APP_VERSION = os.environ.get("GEOTILE_APP_VERSION", "0.1.4")

ProjectModality = Literal["SAR", "EO", "AERIAL_EO"]
ProjectGeoreferencing = Literal["GEO", "NO_GEO", "SENSOR_GEO"]
AnnotationMode = Literal["bbox", "rotated_bbox", "mask"]
ProjectSourceType = Literal["local_scenes"]
# labeling = analyst working project (exports annotation packages).
# review   = manager merging project (imports packages, terminal — produces datasets).
ProjectRole = Literal["labeling", "review"]


class ProjectProfile(BaseModel):
    modality: ProjectModality = "EO"
    allowed_modalities: list[ProjectModality] = Field(default_factory=lambda: ["EO"])
    georeferencing: ProjectGeoreferencing = "NO_GEO"
    allowed_georeferencing: list[ProjectGeoreferencing] = Field(default_factory=lambda: ["NO_GEO"])
    allow_mixed_scenes: bool = False
    sensors: list[str] = Field(default_factory=list)
    annotation_mode: AnnotationMode = "bbox"
    labeling_author_email: str | None = None
    # Team-workflow role. Default "labeling" so every existing project (and the
    # solo/end-to-end user) keeps its current behaviour with no migration action.
    # Must stay a declared field: _normalize_project_record round-trips the profile
    # through ProjectProfile(**...), which would strip any undeclared key.
    project_role: ProjectRole = "labeling"
    default_preprocessing_profile: str = "eo_rgb_percentile"
    default_split_strategy: str = "scene_split"

    @field_validator("labeling_author_email")
    @classmethod
    def normalize_labeling_author_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", normalized):
            raise ValueError("labeling_author_email must be a valid email address")
        return normalized


def default_project_profile(
    modality: ProjectModality = "EO",
    georeferencing: ProjectGeoreferencing = "NO_GEO",
    sensors: list[str] | None = None,
    annotation_mode: AnnotationMode = "bbox",
) -> ProjectProfile:
    if modality == "SAR":
        preprocessing = "sar_log_percentile"
    elif modality == "AERIAL_EO":
        # Panchromatyczne lotnicze NITF (UInt16). Profil rejestrowany w M2
        # (services/preprocessing_profiles.py); do tego czasu żaden projekt
        # AERIAL_EO nie jest tworzony, więc resolve nie trafia tu w runtime.
        preprocessing = "pan_uint16_percentile"
    else:
        preprocessing = "eo_rgb_percentile"

    # SENSOR_GEO (lotnicze NITF, geometria sensora) nie ma metrycznej siatki
    # mapowej — split idzie po obrazie/scenie, nie przestrzennie.
    if georeferencing == "GEO":
        split_strategy = "spatial_block_split"
    else:
        split_strategy = "image_block_split"

    return ProjectProfile(
        modality=modality,
        allowed_modalities=[modality],
        georeferencing=georeferencing,
        allowed_georeferencing=[georeferencing],
        allow_mixed_scenes=False,
        sensors=sensors or [],
        annotation_mode=annotation_mode,
        default_preprocessing_profile=preprocessing,
        default_split_strategy=split_strategy,
    )


class SceneInfo(BaseModel):
    width: int
    height: int
    channels: int
    dtype: str
    has_geo: bool = False
    crs: str | None = None
    crs_proj4: str | None = None
    transform: list[float] | None = None  # affine [a, b, c, d, e, f]
    bounds: list[float] | None = None  # [west, south, east, north] in EPSG:4326
    gsd_m: float | None = None  # rozdzielczość z rozmiaru piksela transformu (m/px)
    # P0.7: jawny pixel spacing obu osi, liczony z PELNEJ transformacji (z obrotem) i z
    # jednostek CRS. `gsd_m` pozostaje skalarem zgodnym wstecznie; te pola nie traca
    # informacji dla pikseli anizotropowych ani dla SAR, gdzie "GSD" jest mylacym pojeciem.
    pixel_spacing_x_m: float | None = None
    pixel_spacing_y_m: float | None = None
    # Wypelniane wylacznie z metadanych dostawcy — nie zgadujemy geometrii range/azimuth
    # z samej transformacji produktu naziemnie zrzutowanego.
    pixel_spacing_range_m: float | None = None
    pixel_spacing_azimuth_m: float | None = None
    spatial_resolution_m: float | None = None
    gsd_source: str | None = None   # "transform" | "provider"
    gsd_method: str | None = None   # np. "affine_projected", "affine_geographic_wgs84"
    display_min: float | None = None
    display_max: float | None = None
    display_mode: str | None = None
    # P1.7: wynik jednego, ograniczonego odczytu charakterystyki rastra. Pola są
    # opcjonalne, aby istniejące projekty i wirtualne sceny pozostały zgodne.
    characterization_version: int | None = None
    display_profile_version: int | None = None
    characterization_open_count: int | None = None
    color_interpretation: list[str] = Field(default_factory=list)
    data_band_indexes: list[int] = Field(default_factory=list)
    # P1.5: semantyka braku danych. `nodata` to zadeklarowana wartosc rastra, `mask_flags`
    # mowi, skad GDAL bierze maske (wlasna warstwa alpha, nodata, albo „wszystko wazne").
    nodata: float | None = None
    mask_flags: list[str] = Field(default_factory=list)
    native_overviews: bool | None = None
    source_overviews: dict | None = None
    spectral_layout: str | None = None
    spectral_processing: str | None = None
    classification_source: str | None = None
    classification_confidence: float | None = None
    name_metadata: dict | None = None
    display_stats: dict | None = None
    characterization_source_size: int | None = None
    characterization_source_mtime_ns: int | None = None
    file_size: int = 0
    filename: str = ""


class ProjectCreate(BaseModel):
    name: str
    scene_folder: str
    classes_file: str | None = None
    project_location: str | None = None
    profile: ProjectProfile | None = None
    # Wstępne parametry siatki kafli (można zmienić później w Siatce przeglądu). Rozmiar
    # kafla to ważny parametr datasetu, więc definiuje się go już przy zakładaniu projektu.
    tile_size: int = Field(640, ge=128, le=4096)
    buffer: int = Field(0, ge=0)


class Project(BaseModel):
    id: str
    name: str
    schema_version: int = SCHEMA_VERSION
    app_version: str = APP_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scene_folder: str = ""
    project_root: str = ""
    storage_mode: str = "managed"
    created_in_appdata: bool = True
    profile: ProjectProfile = Field(default_factory=default_project_profile)
    scene_count: int = 0
    source_type: ProjectSourceType = "local_scenes"
