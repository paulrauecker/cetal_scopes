"""Frequency-domain operations on channels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import detrend as _scipy_detrend
from scipy.signal import get_window

from cetal_scopes.analysis._util import DetrendMode
from cetal_scopes.analysis.results import Spectrum, Tone
from cetal_scopes.channel import Channel

__all__ = ["band_amplitude", "fft", "tone_amplitude", "window_values"]


def window_values(channel: Channel, *, window: str = "hann") -> NDArray[np.float64]:
    """Return the window coefficients for a channel's length.

    Parameters
    ----------
    channel : Channel
        Channel whose length sets the window size.
    window : str
        Any name accepted by :func:`scipy.signal.get_window`, or ``"boxcar"``
        for no window.

    Returns
    -------
    numpy.ndarray
        Window coefficients, length ``channel.n_samples``.
    """
    return np.asarray(
        get_window(window, channel.n_samples, fftbins=True), dtype=np.float64
    )


def fft(
    channel: Channel,
    *,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Spectrum:
    """One-sided amplitude/PSD spectrum of a channel.

    Parameters
    ----------
    channel : Channel
        Source channel; its uniform ``dt`` sets the sample rate.
    window : str
        Window applied before the transform (see :func:`window_values`).
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    Spectrum
        Frequency axis, one-sided peak amplitude and one-sided PSD.

    Raises
    ------
    ValueError
        If the channel is empty.
    """
    values = channel.volts.astype(np.float64)
    n_samples = values.size
    if n_samples == 0:
        raise ValueError("cannot transform an empty channel")
    if detrend is not None:
        values = _scipy_detrend(values, type=detrend)

    weights = window_values(channel, window=window)
    spectrum = np.fft.rfft(values * weights)
    freq = np.fft.rfftfreq(n_samples, d=channel.dt)

    amplitude = np.abs(spectrum) / weights.sum()
    psd = np.abs(spectrum) ** 2 * channel.dt / np.sum(weights**2)

    interior = slice(1, freq.size - (1 if n_samples % 2 == 0 else 0))
    amplitude[interior] *= 2.0
    psd[interior] *= 2.0

    return Spectrum(
        freq=np.asarray(freq, dtype=np.float64),
        amplitude=np.asarray(amplitude, dtype=np.float64),
        psd=np.asarray(psd, dtype=np.float64),
        window=window,
    )


def band_amplitude(
    channel: Channel,
    *,
    low: float,
    high: float,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> float:
    """Return the largest spectral amplitude within ``[low, high]`` Hz.

    This is the right way to measure a tone whose exact frequency is unknown:
    the global FFT peak can be dominated by instrument spurs outside the band.

    Parameters
    ----------
    channel : Channel
        Source channel.
    low, high : float
        Band edges in Hz.
    window : str
        Window applied before the transform.
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    float
        Peak amplitude in volts, or ``0.0`` when the band is empty.
    """
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    spectrum = fft(channel, window=window, detrend=detrend)
    mask = (spectrum.freq >= low) & (spectrum.freq <= high)
    if not mask.any():
        return 0.0
    return float(spectrum.amplitude[mask].max())


def tone_amplitude(
    channel: Channel,
    frequency: float,
    *,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Tone:
    """Coherently detect a tone at a known frequency.

    The signal is projected onto a complex reference ``exp(-j 2 pi f t)`` and
    averaged, which acts as a very narrow band-pass filter. Unlike an FFT peak,
    this is immune to instrument spurs and broadband noise at other
    frequencies, and it recovers the phase relative to the channel's time axis.

    Parameters
    ----------
    channel : Channel
        Source channel.
    frequency : float
        Reference frequency in Hz.
    window : str
        Window applied before the projection.
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    Tone
        Detected amplitude (volts) and phase (radians).

    Raises
    ------
    ValueError
        If the channel is empty.
    """
    values = channel.volts.astype(np.float64)
    if values.size == 0:
        raise ValueError("cannot detect a tone in an empty channel")
    if detrend is not None:
        values = _scipy_detrend(values, type=detrend)
    weights = window_values(channel, window=window)
    reference = np.exp(-2j * np.pi * frequency * channel.time)
    projection = np.sum(values * weights * reference) / weights.sum()
    amplitude = abs(projection) * (1.0 if frequency == 0.0 else 2.0)
    return Tone(
        frequency=frequency,
        amplitude=float(amplitude),
        phase=float(np.angle(projection)),
    )
