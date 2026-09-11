"""Polityka budowy derywatu 1x — R1.3 i budżety z §20.

Testy sprawdzają decyzje, nie dekodowanie: dobór pasa, progi pamięci i preflight dysku.
Wszystkie liczby odniesienia pochodzą z E4.
"""

from __future__ import annotations

import pytest

from services.scene_packages.fullres_build_policy import (
    CALIBRATION_ROWS,
    GIB,
    MAX_STRIP_ROWS,
    MEMORY_AVAILABLE_TOO_SMALL,
    MEMORY_HARD_ABORT_BYTES,
    MEMORY_INSTALLED_TOO_SMALL,
    MEMORY_OK,
    MIN_HOST_RAM_BYTES,
    MIN_STRIP_ROWS,
    StripPlanner,
    check_disk,
    check_host_memory,
    estimate_disk_requirement,
    estimated_total_seconds,
    plan_all_strips,
)


# --- budżet pamięci hosta -------------------------------------------------------------


def test_machine_with_enough_ram_is_accepted():
    verdict = check_host_memory(installed_bytes=32 * GIB, available_bytes=20 * GIB)
    assert verdict.ok is True
    assert verdict.reason == MEMORY_OK


def test_small_machine_is_refused_before_anything_starts():
    """E4: podłoga ~2,8 GiB czyni budowę nieosiągalną na małej maszynie."""
    verdict = check_host_memory(installed_bytes=8 * GIB, available_bytes=6 * GIB)
    assert verdict.ok is False
    assert verdict.reason == MEMORY_INSTALLED_TOO_SMALL


def test_installed_ram_alone_is_not_enough():
    """16 GB zainstalowanych nie pomaga, gdy w tej chwili wolne są 3 GB."""
    verdict = check_host_memory(installed_bytes=MIN_HOST_RAM_BYTES, available_bytes=3 * GIB)
    assert verdict.ok is False
    assert verdict.reason == MEMORY_AVAILABLE_TOO_SMALL


# --- preflight dysku ------------------------------------------------------------------


def test_disk_requirement_counts_simultaneous_peak_not_final_size():
    """Liczy się maksymalny stan jednoczesny: surowy + częściowy COG + zapas."""
    requirement = estimate_disk_requirement(
        width=60476, height=43476, band_count=1, itemsize=2, source_bytes=2_610_658_763,
    )
    assert requirement.raw_bytes == 60476 * 43476 * 2
    # Sam COG to ok. 3,8 GiB — samo to niedoszacowałoby wymagania ponad dwukrotnie.
    assert requirement.peak_bytes > requirement.cog_bytes * 2
    assert requirement.peak_bytes == (
        requirement.raw_bytes + requirement.cog_bytes + requirement.margin_bytes
    )


def test_disk_check_rejects_when_free_space_covers_only_the_final_file():
    requirement = estimate_disk_requirement(
        width=60476, height=43476, band_count=1, itemsize=2, source_bytes=2_610_658_763,
    )
    assert check_disk(requirement, free_bytes=requirement.cog_bytes) is False
    assert check_disk(requirement, free_bytes=requirement.peak_bytes) is True


# --- dobór pasów ----------------------------------------------------------------------


def test_first_strip_is_the_conservative_calibration_strip():
    planner = StripPlanner(width=60476, height=43476)
    assert planner.first_rows() == CALIBRATION_ROWS
    assert planner.next_rows() == CALIBRATION_ROWS


def test_strip_grows_when_the_calibration_strip_was_cheap():
    planner = StripPlanner(width=60476, height=43476, floor_bytes=int(2.8 * GIB))
    # Pas kalibracyjny ledwie ruszył część zmienną — jest miejsce na wzrost.
    assert planner.observe(rows=512, peak_rss_bytes=int(3.0 * GIB)) == 512
    nxt = planner.observe(rows=512, peak_rss_bytes=int(3.0 * GIB))
    assert nxt > 512


def test_growth_is_capped_to_avoid_overshooting_the_hard_limit():
    """Zawyżony skok kosztuje cały przebieg, nie jeden pas."""
    planner = StripPlanner(width=60476, height=43476)
    nxt = planner.observe(rows=512, peak_rss_bytes=int(2.81 * GIB))
    assert nxt <= 1024


def test_strip_shrinks_when_measurement_exceeds_the_target():
    planner = StripPlanner(width=63856, height=42336, floor_bytes=int(2.8 * GIB))
    # CHABAROWSK przy 1024 wierszach zużył 4,8 GiB — plan musi zejść niżej.
    nxt = planner.observe(rows=1024, peak_rss_bytes=int(4.8 * GIB))
    assert nxt < 1024


def test_two_band_calibration_accounts_for_row_cost():
    one = StripPlanner(width=47368, height=33976, band_count=1, itemsize=2)
    two = StripPlanner(width=47368, height=33976, band_count=2, itemsize=2)
    assert two.first_rows() < one.first_rows()


def test_failed_strip_is_retried_at_half_height():
    planner = StripPlanner(width=47368, height=33976, band_count=2, itemsize=2)
    assert planner.retry_rows(2048) == 1024
    assert planner.dangerous_rows == [2048]


def test_previous_0304_overshoot_becomes_a_local_retry_not_a_terminal_failure():
    planner = StripPlanner(
        width=47368,
        height=33976,
        band_count=2,
        itemsize=2,
        target_bytes=int(3.9 * GIB),
        hard_limit_bytes=int(4.5 * GIB),
    )
    completed_rows = sum([512, 1024, 1641, 1016, 2032, 1374, 1620, 1584, 1703])

    retry = planner.retry_rows(2217)

    assert retry == 1108
    assert completed_rows == 12506
    assert planner.dangerous_rows == [2217]
    assert planner.cannot_fit(int(4.62 * GIB), rows=2217) is False


def test_planner_never_leaves_the_allowed_range():
    planner = StripPlanner(width=1000, height=100_000)
    assert planner.observe(rows=512, peak_rss_bytes=int(10 * GIB)) >= MIN_STRIP_ROWS
    planner2 = StripPlanner(width=1000, height=100_000)
    assert planner2.observe(rows=4096, peak_rss_bytes=int(2.8 * GIB)) <= MAX_STRIP_ROWS


def test_strip_never_exceeds_image_height():
    planner = StripPlanner(width=1000, height=300)
    assert planner.first_rows() <= 300
    assert planner.observe(rows=100, peak_rss_bytes=int(2.85 * GIB)) <= 300


def test_hard_limit_is_detected():
    planner = StripPlanner(width=1, height=1)
    assert planner.exceeded_hard_limit(MEMORY_HARD_ABORT_BYTES) is True
    assert planner.exceeded_hard_limit(MEMORY_HARD_ABORT_BYTES - 1) is False


def test_smallest_strip_still_over_limit_means_give_up():
    """Bezpieczeństwo pamięci wygrywa z czasem — scena zostaje na 2× (§20)."""
    planner = StripPlanner(width=1, height=1)
    assert planner.cannot_fit(int(5 * GIB), rows=MIN_STRIP_ROWS) is True
    assert planner.cannot_fit(int(5 * GIB), rows=MIN_STRIP_ROWS * 4) is False


def test_observations_are_recorded_for_diagnostics():
    planner = StripPlanner(width=100, height=10_000)
    planner.observe(rows=512, peak_rss_bytes=int(3.0 * GIB))
    planner.observe(rows=700, peak_rss_bytes=int(3.4 * GIB))
    assert [item["rows"] for item in planner.observations] == [512, 700]


# --- podział i prognoza ---------------------------------------------------------------


def test_strips_cover_the_image_exactly_once():
    strips = plan_all_strips(height=1000, rows=300)
    assert strips[0] == (0, 300)
    assert strips[-1] == (900, 1000)
    assert sum(end - start for start, end in strips) == 1000


def test_time_estimate_needs_at_least_one_observation():
    assert estimated_total_seconds([], height=1000) is None
    assert estimated_total_seconds([{"rows": 0, "seconds": 5}], height=1000) is None


def test_time_estimate_extrapolates_from_measured_strips():
    estimate = estimated_total_seconds([{"rows": 500, "seconds": 10.0}], height=5000)
    assert estimate == pytest.approx(100.0)
