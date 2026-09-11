from __future__ import annotations

from pathlib import Path

import pytest

from services.scene_packages.fullres_cog_builder import (
    FULLRES_COG_NAME,
    _copy_strip_into_bsq,
    _raw_vrt_xml,
    _whole_decode_is_safe,
    cleanup_legacy_fullres,
    fullres_dir,
    fullres_state_path,
    published_fullres_cog,
)
from services.scene_packages.fullres_derivative import COG_PROFILE_VERSION
from services.scene_packages.fullres_publication import (
    STATE_ACTIVE,
    PublicationRecord,
    write_publication_record,
)


def _piece(path: Path, bands: list[list[int]]) -> None:
    path.write_bytes(bytes(value for band in bands for value in band))


def test_multiband_strips_are_placed_in_full_image_bsq_planes(tmp_path: Path):
    # 4x6, two byte-sized bands, three independently decoded two-row strips.
    raw = tmp_path / "fullres.raw"
    with raw.open("w+b") as handle:
        handle.truncate(4 * 6 * 2)
        for y0, values in (
            (0, ([11] * 8, [101] * 8)),
            (2, ([22] * 8, [102] * 8)),
            (4, ([33] * 8, [103] * 8)),
        ):
            piece = tmp_path / f"piece-{y0}.raw"
            _piece(piece, list(values))
            _copy_strip_into_bsq(
                handle,
                piece,
                width=4,
                height=6,
                band_count=2,
                itemsize=1,
                y0=y0,
                y1=y0 + 2,
            )
    data = raw.read_bytes()
    assert data[:24] == bytes([11] * 8 + [22] * 8 + [33] * 8)
    assert data[24:] == bytes([101] * 8 + [102] * 8 + [103] * 8)


def test_strip_size_is_exactly_validated(tmp_path: Path):
    raw = tmp_path / "fullres.raw"
    piece = tmp_path / "short.raw"
    piece.write_bytes(b"\x00" * 15)
    with raw.open("w+b") as handle:
        handle.truncate(32)
        with pytest.raises(RuntimeError, match="decoded_strip_size_mismatch"):
            _copy_strip_into_bsq(
                handle,
                piece,
                width=4,
                height=4,
                band_count=2,
                itemsize=1,
                y0=0,
                y1=2,
            )


def test_raw_vrt_preserves_gray_alpha_and_nodata():
    xml = _raw_vrt_xml(
        raw_name="fullres.raw",
        width=4,
        height=6,
        band_count=2,
        dtype="UInt16",
        itemsize=2,
        crs_wkt="EPSG:4326",
        geotransform=[1, 2, 0, 3, 0, -2],
        color_interpretation=["gray", "alpha"],
        nodata_values=[0, None],
    )
    assert "<ImageOffset>0</ImageOffset>" in xml
    assert "<ImageOffset>48</ImageOffset>" in xml
    assert "<ColorInterp>Gray</ColorInterp>" in xml
    assert "<ColorInterp>Alpha</ColorInterp>" in xml
    assert "<NoDataValue>0</NoDataValue>" in xml


def test_bare_or_v1_cog_is_never_published(tmp_path: Path):
    directory = fullres_dir(tmp_path, "scene", "variant")
    directory.mkdir(parents=True)
    cog = directory / FULLRES_COG_NAME
    cog.write_bytes(b"legacy")
    assert published_fullres_cog(
        tmp_path, "scene", "variant", source_fingerprint="fp"
    ) is None
    assert cleanup_legacy_fullres(tmp_path, "scene", "variant") == [FULLRES_COG_NAME]


def test_active_v2_record_requires_matching_fingerprint_and_variant(tmp_path: Path):
    directory = fullres_dir(tmp_path, "scene", "variant")
    directory.mkdir(parents=True)
    cog = directory / FULLRES_COG_NAME
    cog.write_bytes(b"valid")
    write_publication_record(
        fullres_state_path(tmp_path, "scene", "variant"),
        PublicationRecord(
            state=STATE_ACTIVE,
            payload={
                "source_fingerprint": "fp",
                "variant_id": "variant",
                "cog_profile_version": COG_PROFILE_VERSION,
            },
        ),
    )
    assert published_fullres_cog(
        tmp_path, "scene", "variant", source_fingerprint="fp"
    ) == cog
    assert published_fullres_cog(
        tmp_path, "scene", "variant", source_fingerprint="other"
    ) is None


def test_whole_decode_is_reserved_for_one_band_high_memory_hosts():
    safe, limit = _whole_decode_is_safe(
        installed_bytes=128 * 1024**3,
        available_bytes=100 * 1024**3,
        band_count=1,
        scene_profile={"whole_decode_measured_peak_bytes": int(12.65 * 1024**3)},
    )
    assert safe is True
    assert limit <= 16 * 1024**3

    multiband, _ = _whole_decode_is_safe(
        installed_bytes=128 * 1024**3,
        available_bytes=100 * 1024**3,
        band_count=2,
        scene_profile={},
    )
    assert multiband is False


def test_whole_decode_keeps_system_reserve_and_measured_headroom():
    low_available, _ = _whole_decode_is_safe(
        installed_bytes=128 * 1024**3,
        available_bytes=18 * 1024**3,
        band_count=1,
        scene_profile={"whole_decode_measured_peak_bytes": int(12.65 * 1024**3)},
    )
    underestimated, _ = _whole_decode_is_safe(
        installed_bytes=128 * 1024**3,
        available_bytes=100 * 1024**3,
        band_count=1,
        scene_profile={"whole_decode_measured_peak_bytes": int(15.9 * 1024**3)},
    )
    assert low_available is False
    assert underestimated is False
