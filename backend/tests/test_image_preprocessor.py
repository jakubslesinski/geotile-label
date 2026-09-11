import numpy as np
import pytest

from services.image_preprocessor import _uint8_percentiles, apply_display_params


@pytest.mark.parametrize(
    ("low_pct", "high_pct"),
    [(0.0, 100.0), (2.0, 98.0), (2.37, 91.61), (50.0, 50.0)],
)
def test_uint8_histogram_percentiles_match_numpy_linear_method(
    low_pct: float, high_pct: float
):
    rng = np.random.default_rng(42)
    image = rng.integers(0, 256, size=(137, 113, 3), dtype=np.uint8)

    actual = _uint8_percentiles(image, low_pct, high_pct)
    expected = np.percentile(
        image.astype(np.float32), [low_pct, high_pct], method="linear"
    )

    assert actual == pytest.approx(expected, abs=1e-9)


def test_fast_uint8_stretch_matches_previous_numpy_implementation():
    rng = np.random.default_rng(7)
    image = rng.integers(0, 256, size=(640, 640, 3), dtype=np.uint8)
    low_val, high_val = np.percentile(
        image.astype(np.float32), [2.0, 98.0], method="linear"
    )
    expected = np.clip(
        (image.astype(np.float32) - low_val) / (high_val - low_val) * 255.0,
        0.0,
        255.0,
    ).astype(np.uint8)

    actual = apply_display_params(image, stretch_low=2.0, stretch_high=98.0)

    np.testing.assert_array_equal(actual, expected)
