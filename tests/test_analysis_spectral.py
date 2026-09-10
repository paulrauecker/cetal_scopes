import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import Spectrum, fft, window_values


def make_tone(
    *, amplitude: float = 2.0, freq: float = 100.0, fs: float = 1000.0, n: int = 1000
) -> Channel:
    t = np.arange(n) / fs
    return Channel(
        name="CH1",
        volts=(amplitude * np.sin(2 * np.pi * freq * t)),
        t0=0.0,
        dt=1.0 / fs,
    )


def test_window_values_shape_and_taper() -> None:
    weights = window_values(make_tone(), window="hann")
    assert weights.shape == (1000,)
    assert weights[0] == pytest.approx(0.0)
    assert weights.max() == pytest.approx(1.0)


def test_fft_recovers_tone_amplitude_and_frequency() -> None:
    spectrum = fft(make_tone(), window="boxcar", detrend=None)
    freq, amplitude = spectrum.peak()
    assert freq == pytest.approx(100.0)
    assert amplitude == pytest.approx(2.0)


def test_spectrum_metadata() -> None:
    spectrum = fft(make_tone(), window="boxcar", detrend=None)
    assert isinstance(spectrum, Spectrum)
    assert spectrum.n_bins == 501
    assert spectrum.df == pytest.approx(1.0)
    assert spectrum.f_nyquist == pytest.approx(500.0)


def test_fft_windowed_tone_still_peaks_correctly() -> None:
    spectrum = fft(make_tone(), window="hann")
    freq, amplitude = spectrum.peak()
    assert freq == pytest.approx(100.0)
    assert amplitude == pytest.approx(2.0, rel=0.05)


def test_fft_psd_peaks_at_same_frequency() -> None:
    spectrum = fft(make_tone(), window="hann")
    assert int(np.argmax(spectrum.psd)) == int(np.argmax(spectrum.amplitude))
    assert np.all(spectrum.psd >= 0.0)


def test_fft_odd_length_does_not_double_nyquist() -> None:
    channel = make_tone(freq=100.0, fs=999.0, n=999)
    spectrum = fft(channel, window="boxcar", detrend=None)
    assert spectrum.n_bins == 500
    freq, amplitude = spectrum.peak()
    assert freq == pytest.approx(100.0)
    assert amplitude == pytest.approx(2.0, rel=1e-2)


def test_spectrum_single_bin_df_is_zero() -> None:
    spectrum = Spectrum(freq=np.zeros(1), amplitude=np.zeros(1), psd=np.zeros(1))
    assert spectrum.df == 0.0
    assert spectrum.f_nyquist == 0.0


def test_fft_empty_channel_raises() -> None:
    channel = Channel(name="CH1", volts=np.zeros(0), t0=0.0, dt=1.0)
    with pytest.raises(ValueError, match="empty channel"):
        fft(channel)
