import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import (
    Spectrogram,
    Spectrum,
    Tone,
    WaterfallBuffer,
    band_amplitude,
    fft,
    stft,
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


def test_waterfall_buffer_rejects_bad_depth() -> None:
    with pytest.raises(ValueError, match="depth"):
        WaterfallBuffer(0)


def test_waterfall_buffer_fills_rows_in_order() -> None:
    buffer = WaterfallBuffer(3, window="boxcar", detrend=None)
    for freq in (100.0, 100.0, 100.0):
        buffer.push(make_tone(freq=freq))
    assert buffer.n_pushed == 3
    assert buffer.amplitude.shape == (3, 501)
    assert not np.isnan(buffer.amplitude).any()


def test_waterfall_buffer_rolls_when_over_depth() -> None:
    buffer = WaterfallBuffer(2, window="boxcar", detrend=None)
    buffer.push(make_tone(amplitude=1.0, freq=100.0))
    buffer.push(make_tone(amplitude=2.0, freq=100.0))
    buffer.push(make_tone(amplitude=3.0, freq=100.0))
    assert buffer.n_pushed == 3
    # oldest row (amplitude=1.0) has rolled off; last two rows remain.
    assert buffer.amplitude[0].max() == pytest.approx(2.0, rel=1e-2)
    assert buffer.amplitude[1].max() == pytest.approx(3.0, rel=1e-2)


def test_waterfall_buffer_partial_fill_has_nan_rows() -> None:
    buffer = WaterfallBuffer(3, window="boxcar", detrend=None)
    buffer.push(make_tone(freq=100.0))
    assert np.isnan(buffer.amplitude[0]).all()
    assert not np.isnan(buffer.amplitude[-1]).any()


def test_waterfall_buffer_rejects_mismatched_frequency_axis() -> None:
    buffer = WaterfallBuffer(2, window="boxcar", detrend=None)
    buffer.push(make_tone(fs=1000.0, n=1000))
    with pytest.raises(ValueError, match="frequency axis"):
        buffer.push(make_tone(fs=2000.0, n=1000))


def test_waterfall_buffer_to_spectrogram() -> None:
    buffer = WaterfallBuffer(2, window="boxcar", detrend=None)
    buffer.push(make_tone(freq=100.0))
    buffer.push(make_tone(freq=100.0))
    spectrogram = buffer.to_spectrogram()
    assert isinstance(spectrogram, Spectrogram)
    assert spectrogram.n_rows == 2
    assert spectrogram.n_bins == 501


def test_spectrogram_peak_returns_time_freq_amplitude() -> None:
    buffer = WaterfallBuffer(2, window="boxcar", detrend=None)
    buffer.push(make_tone(amplitude=1.0, freq=100.0), t=0.0)
    buffer.push(make_tone(amplitude=5.0, freq=200.0), t=1.0)
    spectrogram = buffer.to_spectrogram()
    peak_time, peak_freq, peak_amplitude = spectrogram.peak()
    assert peak_time == pytest.approx(1.0)
    assert peak_freq == pytest.approx(200.0)
    assert peak_amplitude == pytest.approx(5.0, rel=1e-2)


def test_spectrogram_peak_ignores_nan_rows() -> None:
    buffer = WaterfallBuffer(3, window="boxcar", detrend=None)
    buffer.push(make_tone(amplitude=1.0, freq=100.0), t=0.0)
    spectrogram = buffer.to_spectrogram()
    peak_time, peak_freq, _ = spectrogram.peak()
    assert peak_time == pytest.approx(0.0)
    assert peak_freq == pytest.approx(100.0)


def test_waterfall_buffer_push_accepts_explicit_time() -> None:
    buffer = WaterfallBuffer(2, window="boxcar", detrend=None)
    buffer.push(make_tone(freq=100.0), t=5.0)
    buffer.push(make_tone(freq=100.0), t=7.5)
    spectrogram = buffer.to_spectrogram()
    assert spectrogram.times.tolist() == pytest.approx([5.0, 7.5])


def _make_chirp(*, fs: float = 10000.0, n: int = 10000) -> Channel:
    """A tone that jumps from 200 Hz to 800 Hz halfway through the record."""
    t = np.arange(n) / fs
    half = n // 2
    volts = np.empty(n)
    volts[:half] = np.sin(2 * np.pi * 200.0 * t[:half])
    volts[half:] = np.sin(2 * np.pi * 800.0 * t[half:])
    return Channel(name="CH1", volts=volts, t0=0.0, dt=1.0 / fs)


def test_stft_rejects_bad_segment_samples() -> None:
    channel = make_tone(n=100)
    with pytest.raises(ValueError, match="segment_samples"):
        stft(channel, segment_samples=0)
    with pytest.raises(ValueError, match="segment_samples"):
        stft(channel, segment_samples=101)


def test_stft_rejects_bad_hop_samples() -> None:
    channel = make_tone(n=100)
    with pytest.raises(ValueError, match="hop_samples"):
        stft(channel, segment_samples=50, hop_samples=0)


def test_stft_shape_and_default_hop() -> None:
    channel = make_tone(n=1000, fs=1000.0)
    spectrogram = stft(channel, segment_samples=200, window="boxcar", detrend=None)
    # default hop is segment_samples // 2 = 100; starts at 0, 100, ..., 800 -> 9 rows.
    assert spectrogram.n_rows == 9
    assert spectrogram.n_bins == 101


def test_stft_times_are_segment_center_times_not_wall_clock() -> None:
    channel = make_tone(n=1000, fs=1000.0)
    spectrogram = stft(
        channel, segment_samples=200, hop_samples=200, window="boxcar", detrend=None
    )
    assert spectrogram.times.tolist() == pytest.approx([0.1, 0.3, 0.5, 0.7, 0.9])


def test_stft_resolves_a_frequency_jump_mid_record() -> None:
    channel = _make_chirp()
    spectrogram = stft(
        channel, segment_samples=1000, hop_samples=1000, window="boxcar", detrend=None
    )
    first_peak = spectrogram.freq[int(np.argmax(spectrogram.amplitude[0]))]
    last_peak = spectrogram.freq[int(np.argmax(spectrogram.amplitude[-1]))]
    assert first_peak == pytest.approx(200.0, abs=5.0)
    assert last_peak == pytest.approx(800.0, abs=5.0)
    assert spectrogram.freq.shape == (501,)
    assert spectrogram.times.shape == (10,)
