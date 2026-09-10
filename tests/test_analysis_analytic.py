import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import (
    analytic_signal,
    envelope,
    instantaneous_frequency,
    instantaneous_phase,
)


def make_tone(
    *, freq: float = 200.0, fs: float = 1000.0, n: int = 2000
) -> tuple[Channel, np.ndarray]:
    t = np.arange(n) / fs
    channel = Channel(
        name="CH1",
        volts=np.cos(2 * np.pi * freq * t),
        t0=0.0,
        dt=1.0 / fs,
    )
    return channel, t


def test_analytic_signal_shape_and_dtype() -> None:
    channel, _ = make_tone()
    result = analytic_signal(channel)
    assert result.shape == (channel.n_samples,)
    assert np.iscomplexobj(result)


def test_envelope_recovers_am_modulation() -> None:
    fs, n = 1000.0, 2000
    t = np.arange(n) / fs
    carrier = np.cos(2 * np.pi * 200.0 * t)
    modulation = 1.0 + 0.5 * np.cos(2 * np.pi * 10.0 * t)
    channel = Channel(name="CH1", volts=modulation * carrier, t0=0.0, dt=1.0 / fs)
    result = envelope(channel)
    assert result.raw is None
    np.testing.assert_allclose(result.volts[200:-200], modulation[200:-200], atol=0.05)


def test_instantaneous_phase_is_unwrapped() -> None:
    channel, _ = make_tone()
    phase = instantaneous_phase(channel)
    assert phase.shape == (channel.n_samples,)
    assert phase[100] < phase[101]


def test_instantaneous_frequency_tracks_carrier() -> None:
    channel, _ = make_tone(freq=200.0)
    freq = instantaneous_frequency(channel)
    np.testing.assert_allclose(freq[200:-200], 200.0, atol=1.0)


def test_instantaneous_frequency_single_sample() -> None:
    channel = Channel(name="CH1", volts=np.array([1.0]), t0=0.0, dt=1.0)
    freq = instantaneous_frequency(channel)
    np.testing.assert_array_equal(freq, [0.0])


def test_envelope_preserves_timebase() -> None:
    channel, _ = make_tone()
    result = envelope(channel)
    assert result.t0 == pytest.approx(channel.t0)
    assert result.dt == pytest.approx(channel.dt)
