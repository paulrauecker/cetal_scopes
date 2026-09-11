import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import TimeOffset, estimate_time_offset


def make_channel(volts: np.ndarray, *, dt: float = 1.0, name: str = "CH1") -> Channel:
    return Channel(name=name, volts=np.asarray(volts, dtype=np.float64), t0=0.0, dt=dt)


def fourier_shift(values: np.ndarray, shift: float) -> np.ndarray:
    spectrum = np.fft.rfft(values)
    freqs = np.fft.rfftfreq(values.size)
    return np.fft.irfft(spectrum * np.exp(-2j * np.pi * freqs * shift), n=values.size)


def test_recovers_integer_delay() -> None:
    rng = np.random.default_rng(0)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(np.roll(reference.volts, 37))
    result = estimate_time_offset(reference, signal)
    assert isinstance(result, TimeOffset)
    assert result.offset == pytest.approx(-37.0, abs=0.05)
    assert result.correlation > 0.9
    assert result.inverted is False


def test_recovers_negative_delay() -> None:
    rng = np.random.default_rng(1)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(np.roll(reference.volts, -20))
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(20.0, abs=0.05)


def test_recovers_fractional_delay() -> None:
    rng = np.random.default_rng(2)
    reference = make_channel(rng.standard_normal(4096))
    signal = make_channel(fourier_shift(reference.volts, 12.5))
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(-12.5, abs=0.2)


def test_detects_inversion() -> None:
    rng = np.random.default_rng(3)
    reference = make_channel(rng.standard_normal(2000))
    signal = make_channel(-np.roll(reference.volts, 10))
    result = estimate_time_offset(reference, signal)
    assert result.inverted is True
    assert result.correlation < 0
    assert result.offset == pytest.approx(-10.0, abs=0.05)


def test_mismatched_sample_rates_are_resampled() -> None:
    rng = np.random.default_rng(4)
    dt = 1.0e-9
    reference = make_channel(rng.standard_normal(4096), dt=dt)
    decimated = fourier_shift(reference.volts, 5.5)[::2]
    signal = make_channel(decimated, dt=2.0 * dt)
    result = estimate_time_offset(reference, signal)
    assert result.offset == pytest.approx(-5.5 * dt, abs=2.0 * dt)


def test_max_lag_limits_search() -> None:
    rng = np.random.default_rng(5)
    reference = make_channel(rng.standard_normal(4000))
    signal = make_channel(np.roll(reference.volts, 500))
    result = estimate_time_offset(reference, signal, max_lag=100.0)
    assert -100.0 <= result.offset <= 100.0
    assert result.max_lag == pytest.approx(100.0)


def test_max_lag_must_be_positive() -> None:
    channel = make_channel(np.arange(10.0))
    with pytest.raises(ValueError, match="max_lag must be positive"):
        estimate_time_offset(channel, channel, max_lag=0.0)


def test_too_short_raises() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        estimate_time_offset(make_channel(np.zeros(1)), make_channel(np.zeros(1)))


def test_constant_channel_raises() -> None:
    with pytest.raises(ValueError, match="no signal"):
        estimate_time_offset(make_channel(np.ones(10)), make_channel(np.ones(10)))
