"""Zero-phase filtering and smoothing."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import (
    bandpass,
    bandstop,
    fft,
    highpass,
    lowpass,
    moving_average,
    savgol,
)

DT = 1e-6  # 1 MS/s, Nyquist 500 kHz
N = 4096


def make_channel(*tones: float, amplitude: float = 1.0, dt: float = DT) -> Channel:
    t = np.arange(N, dtype=np.float64) * dt
    values = sum(amplitude * np.sin(2.0 * np.pi * f * t) for f in tones)
    return Channel(name="C1", volts=np.asarray(values, dtype=np.float64), t0=0.0, dt=dt)


def amplitude_at(channel: Channel, frequency: float) -> float:
    spectrum = fft(channel, window="hann")
    index = int(np.argmin(np.abs(spectrum.freq - frequency)))
    return float(spectrum.amplitude[index])


def test_lowpass_keeps_the_low_tone_and_removes_the_high_one() -> None:
    channel = make_channel(1e3, 2e5)
    filtered = lowpass(channel, cutoff=1e4)

    assert amplitude_at(filtered, 1e3) == pytest.approx(1.0, rel=0.05)
    assert amplitude_at(filtered, 2e5) < 0.01


def test_highpass_keeps_the_high_tone() -> None:
    channel = make_channel(1e3, 2e5)
    filtered = highpass(channel, cutoff=1e4)

    assert amplitude_at(filtered, 2e5) == pytest.approx(1.0, rel=0.05)
    assert amplitude_at(filtered, 1e3) < 0.01


def test_bandpass_keeps_only_the_middle_tone() -> None:
    channel = make_channel(1e3, 5e4, 2e5)
    filtered = bandpass(channel, low=3e4, high=7e4)

    assert amplitude_at(filtered, 5e4) == pytest.approx(1.0, rel=0.05)
    assert amplitude_at(filtered, 1e3) < 0.01
    assert amplitude_at(filtered, 2e5) < 0.05


def test_bandstop_removes_only_the_middle_tone() -> None:
    channel = make_channel(1e3, 5e4, 2e5)
    filtered = bandstop(channel, low=3e4, high=7e4)

    assert amplitude_at(filtered, 5e4) < 0.05
    assert amplitude_at(filtered, 1e3) == pytest.approx(1.0, rel=0.05)
    assert amplitude_at(filtered, 2e5) == pytest.approx(1.0, rel=0.05)


def test_filtering_is_zero_phase() -> None:
    # A causal filter would delay the pulse; a zero-phase one must not move it.
    t = np.arange(N, dtype=np.float64) * DT
    centre = t[N // 2]
    pulse = np.exp(-0.5 * ((t - centre) / (50 * DT)) ** 2)
    channel = Channel(name="C1", volts=pulse, t0=0.0, dt=DT)

    filtered = lowpass(channel, cutoff=2e4)
    assert int(np.argmax(filtered.volts)) == pytest.approx(N // 2, abs=1)


def test_filters_preserve_the_time_axis_and_drop_raw() -> None:
    channel = Channel(
        name="C1",
        volts=np.sin(np.arange(N) * 0.01),
        t0=-1e-3,
        dt=DT,
        raw=np.arange(N, dtype=np.int16),
        unit="V",
    )
    filtered = lowpass(channel, cutoff=1e4)

    assert filtered.t0 == channel.t0
    assert filtered.dt == channel.dt
    assert filtered.n_samples == channel.n_samples
    assert filtered.raw is None  # samples no longer map to ADC codes
    assert filtered.unit == "V"


@pytest.mark.parametrize("cutoff", [0.0, -1.0, 5e5, 1e9])
def test_a_cutoff_outside_the_band_is_rejected(cutoff: float) -> None:
    with pytest.raises(ValueError, match="cutoff must be in"):
        lowpass(make_channel(1e3), cutoff=cutoff)


def test_an_inverted_band_is_rejected() -> None:
    with pytest.raises(ValueError, match="expected low < high"):
        bandpass(make_channel(1e3), low=1e5, high=1e4)


def test_a_bad_order_is_rejected() -> None:
    with pytest.raises(ValueError, match="order must be at least 1"):
        lowpass(make_channel(1e3), cutoff=1e4, order=0)


def test_a_short_record_is_rejected_with_a_useful_message() -> None:
    short = Channel(name="C1", volts=np.zeros(10), t0=0.0, dt=DT)
    with pytest.raises(ValueError, match="too few for a zero-phase filter"):
        lowpass(short, cutoff=1e4)


def test_moving_average_smooths_noise() -> None:
    rng = np.random.default_rng(0)
    values = np.ones(N) + rng.standard_normal(N)
    channel = Channel(name="C1", volts=values, t0=0.0, dt=DT)

    smoothed = moving_average(channel, window_s=64 * DT)
    assert smoothed.n_samples == N
    assert float(np.std(smoothed.volts)) < float(np.std(values)) / 4
    assert float(np.mean(smoothed.volts)) == pytest.approx(1.0, abs=0.05)


def test_moving_average_rejects_a_sub_sample_window() -> None:
    with pytest.raises(ValueError, match="widen it to at least two samples"):
        moving_average(make_channel(1e3), window_s=DT / 10)


def test_moving_average_rejects_a_window_longer_than_the_record() -> None:
    with pytest.raises(ValueError, match="not shorter than"):
        moving_average(make_channel(1e3), window_s=N * DT)


def test_savgol_preserves_a_peak_better_than_a_boxcar() -> None:
    t = np.arange(N, dtype=np.float64) * DT
    centre = t[N // 2]
    pulse = np.exp(-0.5 * ((t - centre) / (20 * DT)) ** 2)
    channel = Channel(name="C1", volts=pulse, t0=0.0, dt=DT)
    window = 41 * DT

    boxcar = float(np.max(moving_average(channel, window_s=window).volts))
    fitted = float(np.max(savgol(channel, window_s=window).volts))

    assert fitted > boxcar
    assert fitted == pytest.approx(1.0, rel=0.02)


def test_savgol_rejects_a_window_too_narrow_for_the_polynomial() -> None:
    with pytest.raises(ValueError, match="cannot support a degree-3 fit"):
        savgol(make_channel(1e3), window_s=2 * DT)


def test_savgol_rejects_a_window_longer_than_the_record() -> None:
    with pytest.raises(ValueError, match="exceeds the"):
        savgol(make_channel(1e3), window_s=2 * N * DT)
