"""Two-channel spectral comparison."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import coherence, cross_spectrum, transfer_function

DT = 1e-6
N = 8192


def make(values: np.ndarray, *, name: str = "C1", dt: float = DT) -> Channel:
    return Channel(name=name, volts=np.asarray(values, dtype=np.float64), t0=0.0, dt=dt)


def bin_at(freq: np.ndarray, target: float) -> int:
    return int(np.argmin(np.abs(freq - target)))


def test_coherence_is_high_for_a_scaled_copy() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal(N)
    channel_a = make(a)
    channel_b = make(2.0 * a, name="C2")

    result = coherence(channel_a, channel_b)
    assert float(np.median(result.amplitude)) == pytest.approx(1.0, abs=0.01)


def test_coherence_is_low_for_independent_noise() -> None:
    rng = np.random.default_rng(1)
    result = coherence(
        make(rng.standard_normal(N)), make(rng.standard_normal(N), name="C2")
    )
    assert float(np.median(result.amplitude)) < 0.7


def test_coherence_is_bounded() -> None:
    rng = np.random.default_rng(2)
    result = coherence(
        make(rng.standard_normal(N)), make(rng.standard_normal(N), name="C2")
    )
    assert result.amplitude.min() >= 0.0
    assert result.amplitude.max() <= 1.0
    np.testing.assert_allclose(result.psd, result.amplitude**2, atol=1e-12)


def test_coherence_separates_a_shared_tone_from_independent_noise() -> None:
    rng = np.random.default_rng(3)
    t = np.arange(N) * DT
    tone = np.sin(2.0 * np.pi * 5e4 * t)
    a = tone + 0.5 * rng.standard_normal(N)
    b = tone + 0.5 * rng.standard_normal(N)

    result = coherence(make(a), make(b, name="C2"))
    shared = bin_at(result.freq, 5e4)
    elsewhere = bin_at(result.freq, 2e5)

    assert result.amplitude[shared] > 0.9
    assert result.amplitude[elsewhere] < result.amplitude[shared]


def test_transfer_function_recovers_a_flat_gain() -> None:
    rng = np.random.default_rng(4)
    a = rng.standard_normal(N)
    freq, gain = transfer_function(make(a), make(3.0 * a, name="C2"))

    finite = gain[np.isfinite(gain)]
    assert float(np.median(np.abs(finite))) == pytest.approx(3.0, rel=0.02)
    assert float(np.median(np.abs(np.angle(finite)))) < 0.05
    assert freq[0] == 0.0


def test_transfer_function_sees_an_inversion_as_a_pi_phase() -> None:
    rng = np.random.default_rng(5)
    a = rng.standard_normal(N)
    _, gain = transfer_function(make(a), make(-a, name="C2"))

    finite = gain[np.isfinite(gain)]
    assert float(np.median(np.abs(np.angle(finite)))) == pytest.approx(np.pi, rel=0.02)


def test_cross_spectrum_peaks_at_the_shared_tone() -> None:
    t = np.arange(N) * DT
    tone = np.sin(2.0 * np.pi * 5e4 * t)
    freq, cross = cross_spectrum(make(tone), make(tone, name="C2"))

    assert freq[int(np.argmax(np.abs(cross)))] == pytest.approx(5e4, rel=0.02)


def test_channels_with_different_sample_rates_are_put_on_a_common_grid() -> None:
    t_fast = np.arange(N) * DT
    fast = make(np.sin(2.0 * np.pi * 5e4 * t_fast))

    t_slow = np.arange(N // 2) * (2 * DT)
    slow = make(np.sin(2.0 * np.pi * 5e4 * t_slow), name="C2", dt=2 * DT)

    result = coherence(fast, slow)
    assert result.amplitude[bin_at(result.freq, 5e4)] > 0.9


def test_a_too_short_channel_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        coherence(make(np.zeros(1)), make(np.zeros(1), name="C2"))


def test_an_out_of_range_segment_length_is_rejected() -> None:
    rng = np.random.default_rng(6)
    a = make(rng.standard_normal(256))
    b = make(rng.standard_normal(256), name="C2")

    with pytest.raises(ValueError, match="segment_samples must be in"):
        coherence(a, b, segment_samples=10_000)


def test_a_silent_input_gives_nan_gain_rather_than_a_huge_number() -> None:
    rng = np.random.default_rng(7)
    quiet = make(np.zeros(N))
    loud = make(rng.standard_normal(N), name="C2")

    _, gain = transfer_function(quiet, loud)
    assert np.isnan(gain).all()
