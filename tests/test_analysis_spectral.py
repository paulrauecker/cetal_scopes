import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import (
    Spectrum,
    Tone,
    band_amplitude,
    fft,
    tone_amplitude,
    window_values,
)


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


def test_tone_amplitude_recovers_amplitude_and_phase() -> None:
    fs, n, freq, phi = 10000.0, 10000, 500.0, 0.7
    t = np.arange(n) / fs
    channel = Channel(
        name="CH1",
        volts=2.0 * np.cos(2 * np.pi * freq * t + phi),
        t0=0.0,
        dt=1.0 / fs,
    )
    tone = tone_amplitude(channel, freq, window="boxcar", detrend=None)
    assert isinstance(tone, Tone)
    assert tone.amplitude == pytest.approx(2.0, rel=1e-3)
    assert tone.phase == pytest.approx(phi, abs=1e-3)
    assert tone.phase_deg == pytest.approx(np.degrees(phi), abs=0.1)


def test_tone_amplitude_rejects_other_tones() -> None:
    fs, n, freq = 10000.0, 10000, 500.0
    t = np.arange(n) / fs
    channel = Channel(
        name="CH1",
        volts=0.05 * np.cos(2 * np.pi * freq * t)
        + 5.0 * np.cos(2 * np.pi * 1250.0 * t),
        t0=0.0,
        dt=1.0 / fs,
    )
    tone = tone_amplitude(channel, freq, window="boxcar", detrend=None)
    assert tone.amplitude == pytest.approx(0.05, rel=1e-2)


def test_tone_amplitude_dc() -> None:
    channel = Channel(name="CH1", volts=np.full(100, 3.0), t0=0.0, dt=1.0)
    tone = tone_amplitude(channel, 0.0, window="boxcar", detrend=None)
    assert tone.amplitude == pytest.approx(3.0)


def test_tone_amplitude_empty_raises() -> None:
    channel = Channel(name="CH1", volts=np.zeros(0), t0=0.0, dt=1.0)
    with pytest.raises(ValueError, match="empty channel"):
        tone_amplitude(channel, 500.0)


def test_band_amplitude_picks_tone_in_band() -> None:
    channel = make_tone(amplitude=2.0, freq=100.0)
    assert band_amplitude(channel, low=90.0, high=110.0) == pytest.approx(2.0, rel=0.02)
    assert band_amplitude(channel, low=400.0, high=500.0) < 0.05


def test_band_amplitude_empty_band_returns_zero() -> None:
    assert band_amplitude(make_tone(), low=2000.0, high=3000.0) == 0.0


def test_band_amplitude_bad_range() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        band_amplitude(make_tone(), low=500.0, high=100.0)
