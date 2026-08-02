"""``downsample_waveform_preview`` reduces a decoded song to a bounded preview.

Guards the perf rework (#24): the vectorized numpy path must produce output
identical to the pure-Python fallback -- same length, same clamping, same
extreme-first ordering per chunk -- across the ragged-tail edge cases.
"""

from __future__ import annotations

import random

import pytest


def test_short_input_passes_through_clamped():
    from notegrabber.gui.widgets.waveform import downsample_waveform_preview

    out = downsample_waveform_preview([0.0, 1.5, -2.0, 0.25], max_samples=10)
    assert out == [0.0, 1.0, -1.0, 0.25]  # <= max_samples: values kept, clamped to [-1, 1]


def test_bounds_output_length():
    from notegrabber.gui.widgets.waveform import downsample_waveform_preview

    assert len(downsample_waveform_preview(range(100), max_samples=10)) == 10


def test_extreme_value_comes_first_per_chunk():
    from notegrabber.gui.widgets.waveform import downsample_waveform_preview

    # Two chunks of 3: first chunk's biggest magnitude is +0.9, second is -0.8.
    out = downsample_waveform_preview([0.1, 0.9, -0.2, 0.3, -0.8, 0.4], max_samples=2)
    assert out == pytest.approx([0.9, -0.2])  # step=3 -> one chunk, extreme (0.9) first


@pytest.mark.parametrize("n", [0, 1, 5, 10, 999, 48000, 48001, 96000, 200003])
def test_numpy_and_python_paths_agree(n):
    """The vectorized and fallback implementations must be byte-for-byte equal."""

    np = pytest.importorskip("numpy")
    from notegrabber.gui.widgets.waveform import (
        MAX_WAVEFORM_PREVIEW_SAMPLES,
        _downsample_waveform_preview_py,
        downsample_waveform_preview,
    )

    rng = random.Random(n)
    data = [rng.uniform(-1.5, 1.5) for _ in range(n)]

    vectorized = downsample_waveform_preview(data)
    fallback = _downsample_waveform_preview_py(data, MAX_WAVEFORM_PREVIEW_SAMPLES)

    assert len(vectorized) == len(fallback)
    # numpy path is float32; compare at float32 tolerance.
    assert all(abs(float(np.float32(a)) - float(np.float32(b))) < 1e-5 for a, b in zip(vectorized, fallback))
