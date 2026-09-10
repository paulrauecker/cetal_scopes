"""Analytic-signal (Hilbert) operations on channels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import detrend as _scipy_detrend
from scipy.signal import hilbert

from cetal_scopes.analysis._util import DetrendMode, with_volts
from cetal_scopes.channel import Channel

__all__ = [
    "analytic_signal",
    "envelope",
    "instantaneous_frequency",
    "instantaneous_phase",
]


def analytic_signal(
    channel: Channel, *, detrend: DetrendMode | None = "constant"
) -> NDArray[np.complex128]:
    """Return the complex analytic signal of a channel via the Hilbert transform.

    The instantaneous amplitude, phase and frequency are the magnitude,
    (unwrapped) angle and phase derivative of this signal. Edge samples are
    unreliable because the transform assumes a periodic signal.

    Parameters
    ----------
    channel : Channel
        Source channel.
    detrend : {"constant", "linear", None}
        Trend removed before the transform.

    Returns
    -------
    numpy.ndarray
        Complex analytic signal, same length as the channel.
    """
    values = channel.volts.astype(np.float64)
    if detrend is not None:
        values = _scipy_detrend(values, type=detrend)
    return np.asarray(hilbert(values), dtype=np.complex128)


def envelope(channel: Channel, *, detrend: DetrendMode | None = "constant") -> Channel:
    """Return the instantaneous amplitude (Hilbert envelope) as a channel."""
    magnitude = np.abs(analytic_signal(channel, detrend=detrend))
    return with_volts(channel, magnitude)


def instantaneous_phase(
    channel: Channel, *, detrend: DetrendMode | None = "constant"
) -> NDArray[np.float64]:
    """Return the unwrapped instantaneous phase in radians."""
    phase = np.unwrap(np.angle(analytic_signal(channel, detrend=detrend)))
    return np.asarray(phase, dtype=np.float64)


def instantaneous_frequency(
    channel: Channel, *, detrend: DetrendMode | None = "constant"
) -> NDArray[np.float64]:
    """Return the instantaneous frequency in Hz (d phase / dt / 2pi)."""
    phase = instantaneous_phase(channel, detrend=detrend)
    if phase.size < 2:
        return np.zeros_like(phase)
    return np.gradient(phase, channel.dt) / (2.0 * np.pi)
