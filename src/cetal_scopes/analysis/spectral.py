"""Frequency-domain operations on channels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import detrend as _scipy_detrend
from scipy.signal import get_window

from cetal_scopes.analysis._util import DetrendMode
from cetal_scopes.analysis.results import Spectrum
from cetal_scopes.channel import Channel

__all__ = ["fft", "window_values"]


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
