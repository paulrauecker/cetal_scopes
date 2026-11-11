"""Multi-frequency phase-delay measurement on synthetic data."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import Capture
from cetal_scopes.analysis.phase_delay import (
    delay_to_length,
    estimate_frequency,
    fit_delay,
    fit_sine,
    phase_differences,
    wrap_phase,
)

FS = 5e9
N = 50_000


def two_tone_capture(
    f: float,
    delay: float,
    *,
    ppm: float = 2.0,
    noise: float = 0.0,
    rng: np.random.Generator | None = None,
    start_phase: float = 0.0,
    invert: bool = False,
) -> Capture:
    """A on C1, B = A delayed by ``delay`` on C2, at ``f`` shifted by ``ppm``."""
    t = -N / 2 / FS + np.arange(N) / FS
    f_seen = f * (1 + ppm * 1e-6)
    a = 1e-3 * np.cos(2 * np.pi * f_seen * t + start_phase)
    b = 0.7e-3 * np.cos(2 * np.pi * f_seen * (t - delay) + start_phase)
    if invert:
        b = -b
    rows = np.vstack((a, b))
    if noise:
        assert rng is not None
        rows = rows + rng.normal(0, noise, rows.shape)
    return Capture(volts=rows, t0=float(t[0]), dt=1 / FS, channel_names=("C1", "C2"))


def test_wrap_phase_range() -> None:
    assert wrap_phase(np.pi) == pytest.approx(np.pi)
    assert wrap_phase(-np.pi) == pytest.approx(np.pi)
    assert wrap_phase(3 * np.pi / 2) == pytest.approx(-np.pi / 2)


def test_fit_sine_recovers_amplitude_phase_offset() -> None:
    t = np.arange(10_000) / FS
    x = 0.3 * np.cos(2 * np.pi * 1e8 * t + 0.7) + 0.01
    fit = fit_sine(t, x, 1e8)
    assert fit.amplitude == pytest.approx(0.3)
    assert fit.phase == pytest.approx(0.7)
    assert fit.offset == pytest.approx(0.01)
    assert fit.residual_rms < 1e-12


def test_estimate_frequency_through_a_ppm_offset() -> None:
    # 5 ppm at 2 GHz is 10 kHz: 5 cycles of drift across this 500 us record,
    # far outside a bare four-parameter fit's capture range.
    t = np.arange(2_500_000) / FS
    f_true = 2e9 * (1 + 5e-6)
    x = np.cos(2 * np.pi * f_true * t + 1.0)
    assert estimate_frequency(t, x, 2e9) == pytest.approx(f_true, rel=1e-12)


def test_positive_delay_means_b_is_later() -> None:
    capture = two_tone_capture(1e9, 50e-12)
    _, fits = phase_differences(capture, "C1", ["C2"], 1e9)
    dphi = wrap_phase(fits["C1"].phase - fits["C2"].phase)
    # The tone sits 2 ppm above the set frequency (see two_tone_capture).
    assert dphi == pytest.approx(2 * np.pi * 1e9 * (1 + 2e-6) * 50e-12, abs=1e-9)


@pytest.mark.parametrize("delay", [37.5e-12, -37.5e-12, 1.2e-9])
def test_sweep_recovers_known_delay(delay: float) -> None:
    rng = np.random.default_rng(1)
    freqs = np.arange(100e6, 2e9 + 1, 25e6)
    dphi = []
    for i, f in enumerate(freqs):
        # The source's absolute phase changes at every step; it must not matter.
        capture = two_tone_capture(f, delay, noise=2e-5, rng=rng, start_phase=0.37 * i)
        _, fits = phase_differences(capture, "C1", ["C2"], f)
        dphi.append(fits["C1"].phase - fits["C2"].phase)
    result = fit_delay(freqs, dphi)
    assert result.delay == pytest.approx(delay, abs=0.01e-12)
    assert result.delay_stderr < 0.01e-12
    assert abs(result.intercept_deg) < 0.5


def test_polarity_inversion_shows_in_the_intercept() -> None:
    freqs = np.arange(100e6, 2e9 + 1, 100e6)
    dphi = []
    for f in freqs:
        capture = two_tone_capture(f, 20e-12, invert=True)
        _, fits = phase_differences(capture, "C1", ["C2"], f)
        dphi.append(fits["C1"].phase - fits["C2"].phase)
    result = fit_delay(freqs, dphi)
    assert result.delay == pytest.approx(20e-12, abs=1e-15)
    assert abs(result.intercept_deg) == pytest.approx(180.0, abs=1e-6)


def test_fit_delay_input_order_and_validation() -> None:
    freqs = np.array([3e8, 1e8, 2e8, 4e8])
    result = fit_delay(freqs, wrap_phase(2 * np.pi * freqs * 1e-9))
    assert result.delay == pytest.approx(1e-9)
    assert np.all(np.diff(result.frequencies) > 0)
    with pytest.raises(ValueError):
        fit_delay([1.0, 2.0], [0.0, 0.0])


def test_delay_to_length_hf400() -> None:
    # VF 0.85 -> ~3.92 ps/mm.
    assert delay_to_length(3.923e-12) == pytest.approx(1e-3, rel=1e-3)
