"""Korpus kontraktowy paczek dostawców (DESIGN_DECISIONS.md, scene-import B0).

Fixture są GENEROWANE w locie, nie kopiowane ze źródeł dostawcy. Powód jest podwójny:

1. dane dostawców nie mogą trafić do repozytorium ani do CI (sekcja 14 roadmapy),
2. kontrakt, który testujemy, dotyczy STRUKTURY dostawy — nazw, ról, sidecarów i relacji
   part↔manifest — a nie zawartości pikseli.

Dlatego większość plików to puste stuby: resolvery i `build_inventory()` patrzą wyłącznie na
nazwę, rozszerzenie i `stat()`. Prawdziwe GeoTIFF-y powstają tylko tam, gdzie test dotyczy
geometrii (`build_rotated_transform_raster`), bo tam treść ma znaczenie.

Każdy builder zwraca `FixturePackage` z korzeniem drzewa i krótkim opisem tego, co dostawa
reprezentuje. Oczekiwania kontraktowe celowo NIE są tutaj — należą do testów, żeby jeden
fixture mógł obsłużyć kilka różnych bramek.
"""

from .builders import (
    FixturePackage,
    build_airbus_archive_incomplete,
    build_airbus_archive_only,
    build_archive_with_unsafe_entries,
    build_worldview_archive_duplicate,
    build_flattened_generic_rasters,
    build_capella_gec,
    build_capella_geo,
    build_capella_geo_and_gec,
    build_capella_two_acquisitions,
    build_iceye_cog_geojson,
    build_iceye_dual_polarization,
    build_iceye_grd_suffix_only,
    build_iceye_multi_product_acquisition,
    build_iceye_legacy_grd,
    build_iceye_source_root_with_spreadsheet,
    build_pleiades_neo_dimap,
    build_pleiades_neo_multi_tile,
    build_pleiades_ms_and_pan,
    build_pleiades_phr_dimap,
    build_readme_only_directory,
    build_rotated_transform_raster,
    build_worldview_incomplete_extract,
    build_worldview_mul_pan,
    build_worldview_pan_only,
    build_worldview_til_custom_order,
    build_worldview_mul_only,
    build_worldview_two_products,
)

__all__ = [
    "FixturePackage",
    "build_airbus_archive_incomplete",
    "build_airbus_archive_only",
    "build_archive_with_unsafe_entries",
    "build_worldview_archive_duplicate",
    "build_flattened_generic_rasters",
    "build_capella_gec",
    "build_capella_geo",
    "build_capella_geo_and_gec",
    "build_capella_two_acquisitions",
    "build_iceye_cog_geojson",
    "build_iceye_dual_polarization",
    "build_iceye_grd_suffix_only",
    "build_iceye_multi_product_acquisition",
    "build_iceye_legacy_grd",
    "build_iceye_source_root_with_spreadsheet",
    "build_pleiades_neo_dimap",
    "build_pleiades_neo_multi_tile",
    "build_pleiades_ms_and_pan",
    "build_pleiades_phr_dimap",
    "build_readme_only_directory",
    "build_rotated_transform_raster",
    "build_worldview_incomplete_extract",
    "build_worldview_mul_pan",
    "build_worldview_pan_only",
    "build_worldview_til_custom_order",
    "build_worldview_mul_only",
    "build_worldview_two_products",
]
