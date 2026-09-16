"""Pulse measurements and peak finding."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import find_peaks, pulse_metrics

DT = 1e-9
N = 2000


def step(rise_samples: int, *, base: float = 0.0, top: float = 1.0) -> Channel:
    """A ramped step: flat at ``base``, linear rise, flat at ``top``."""
    values = np.full(N, top, dtype=np.float64)
    values[: N // 4] = base
    ramp = np.linspace(base, top, rise_samples, endpoint=False)
    values[N // 4 : N // 4 + rise_samples] = ramp
    return Channel(name="C1", volts=values, t0=0.0, dt=DT)


def pulse(width_samples: int, *, amplitude: float = 1.0) -> Channel:
    values = np.zeros(N, dtype=np.float64)
    start = N // 2 - width_samples // 2
    values[start : start + width_samples] = amplitude
    return Channel(name="C1", volts=values, t0=0.0, dt=DT)


def test_rise_time_measures_the_10_to_90_percent_transition() -> None:
    metrics = pulse_metrics(step(100))

    # 80% of a 100-sample linear ramp.
    assert metrics.rise_time == pytest.approx(80 * DT, rel=0.05)
    assert metrics.base == pytest.approx(0.0, abs=0.02)
    assert metrics.top == pytest.approx(1.0, abs=0.02)
    assert metrics.amplitude == pytest.approx(1.0, abs=0.05)


def test_the_reference_fractions_are_configurable() -> None:
    metrics = pulse_metrics(step(100), low=0.2, high=0.8)
    assert metrics.rise_time == pytest.approx(60 * DT, rel=0.05)


def test_ringing_does_not_shorten_the_rise_time() -> None:
    # A min/max amplitude would be inflated by the overshoot, pushing the 10%
    # and 90% levels apart and reading a longer rise; the histogram levels
    # must ignore it.
    clean = step(100)
    ringing = step(100)
    values = ringing.volts.copy()
    settle = slice(N // 4 + 100, N // 4 + 160)
    t = np.arange(60)
    values[settle] = 1.0 + 0.3 * np.exp(-t / 15) * np.cos(2 * np.pi * t / 12)
    noisy = Channel(name="C1", volts=values, t0=0.0, dt=DT)

    assert pulse_metrics(noisy).rise_time == pytest.approx(
        pulse_metrics(clean).rise_time, rel=0.1
    )
    assert pulse_metrics(noisy).overshoot > 0.2


def test_width_and_fwhm_of_a_rectangular_pulse() -> None:
    metrics = pulse_metrics(pulse(400))

    assert metrics.width == pytest.approx(400 * DT, rel=0.02)
    assert metrics.fwhm == pytest.approx(400 * DT, rel=0.02)
    assert metrics.peak_value == pytest.approx(1.0)


def test_fall_time_is_measured_after_the_peak() -> None:
    metrics = pulse_metrics(pulse(400))
    assert metrics.fall_time == pytest.approx(0.0, abs=2 * DT)


def test_a_flat_trace_reports_nan_rather_than_a_fabricated_edge() -> None:
    flat = Channel(name="C1", volts=np.full(N, 0.25), t0=0.0, dt=DT)
    metrics = pulse_metrics(flat)

    assert metrics.amplitude == pytest.approx(0.0)
    assert np.isnan(metrics.rise_time)
    assert np.isnan(metrics.fall_time)
    assert np.isnan(metrics.width)
    assert metrics.peak_value == pytest.approx(0.25)


def test_a_rising_edge_alone_has_no_fall_time() -> None:
    metrics = pulse_metrics(step(100))
    assert np.isnan(metrics.fall_time)
    assert np.isnan(metrics.width)


def test_undershoot_is_reported() -> None:
    values = np.zeros(N, dtype=np.float64)
    values[: N // 4] = 0.0
    values[N // 4 : N // 4 + 40] = -0.25
    values[N // 4 + 40 :] = 1.0
    metrics = pulse_metrics(Channel(name="C1", volts=values, t0=0.0, dt=DT))

    assert metrics.undershoot > 0.2


def test_bad_reference_fractions_are_rejected() -> None:
    with pytest.raises(ValueError, match="expected 0 < low < high < 1"):
        pulse_metrics(step(100), low=0.9, high=0.1)


def test_a_one_sample_channel_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        pulse_metrics(Channel(name="C1", volts=np.zeros(1), t0=0.0, dt=DT))


# ---------------------------------------------------------------------------
# find_peaks
# ---------------------------------------------------------------------------


def three_pulses() -> Channel:
    t = np.arange(N, dtype=np.float64)
    values = np.zeros(N, dtype=np.float64)
    for centre, amplitude in ((400, 1.0), (900, 0.4), (1500, 0.8)):
        values += amplitude * np.exp(-0.5 * ((t - centre) / 20.0) ** 2)
    return Channel(name="C1", volts=values, t0=0.0, dt=DT)


def test_find_peaks_locates_every_pulse() -> None:
    peaks = find_peaks(three_pulses(), prominence=0.1)

    assert [peak.index for peak in peaks] == [400, 900, 1500]
    assert [round(peak.value, 2) for peak in peaks] == [1.0, 0.4, 0.8]
    assert all(peak.prominence > 0.1 for peak in peaks)


def test_peak_times_use_the_channel_axis() -> None:
    channel = Channel(name="C1", volts=three_pulses().volts, t0=-1e-6, dt=DT)
    peaks = find_peaks(channel, prominence=0.1)
    assert peaks[0].time == pytest.approx(-1e-6 + 400 * DT)


def test_peak_width_is_in_seconds() -> None:
    peaks = find_peaks(three_pulses(), prominence=0.1)
    # A Gaussian of sigma 20 samples has a FWHM of about 47 samples.
    assert peaks[0].width == pytest.approx(47 * DT, rel=0.1)


def test_a_height_threshold_filters_small_peaks() -> None:
    peaks = find_peaks(three_pulses(), height=0.5)
    assert [peak.index for peak in peaks] == [400, 1500]


def test_distance_is_given_in_seconds() -> None:
    peaks = find_peaks(three_pulses(), prominence=0.1, distance=700 * DT)
    assert len(peaks) == 2


def test_a_sub_sample_distance_is_rejected() -> None:
    with pytest.raises(ValueError, match="shorter than one sample"):
        find_peaks(three_pulses(), distance=DT / 10)


def test_absolute_search_finds_negative_excursions() -> None:
    channel = Channel(name="C1", volts=-three_pulses().volts, t0=0.0, dt=DT)

    # A plain search finds the flat gaps *between* the inverted pulses, which
    # are the only local maxima of a downward-going trace.
    plain = find_peaks(channel, prominence=0.1)
    assert all(abs(peak.value) < 1e-6 for peak in plain)

    peaks = find_peaks(channel, prominence=0.1, absolute=True)
    assert [peak.index for peak in peaks] == [400, 900, 1500]
    assert all(peak.value < 0 for peak in peaks)


def test_a_tiny_channel_has_no_peaks() -> None:
    assert find_peaks(Channel(name="C1", volts=np.zeros(2), t0=0.0, dt=DT)) == []


def test_a_flat_channel_has_no_peaks() -> None:
    flat = Channel(name="C1", volts=np.zeros(N), t0=0.0, dt=DT)
    assert find_peaks(flat, prominence=0.1) == []
