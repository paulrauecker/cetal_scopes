"""Cross-correlation time alignment between channels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy import signal as _scipy_signal

from cetal_scopes.analysis.results import TimeOffset
from cetal_scopes.analysis.time import resample
from cetal_scopes.channel import Channel

__all__ = ["estimate_time_offset"]


def _unit_norm(values: NDArray[np.float64]) -> NDArray[np.float64]:
    norm = float(np.linalg.norm(values))
    if norm == 0.0:
        raise ValueError("channel has no signal (zero variance)")
    return values / norm


def _refine_peak(corr: NDArray[np.float64], peak: int, dt: float, center: int) -> float:
    if peak <= 0 or peak >= corr.size - 1:
        return float((peak - center) * dt)
    left = abs(float(corr[peak - 1]))
    middle = abs(float(corr[peak]))
    right = abs(float(corr[peak + 1]))
    denominator = left - 2.0 * middle + right
    fraction = 0.0 if denominator == 0.0 else 0.5 * (left - right) / denominator
    fraction = float(np.clip(fraction, -0.5, 0.5))
    return float((peak + fraction - center) * dt)


def estimate_time_offset(
    reference: Channel,
    signal: Channel,
    *,
    max_lag: float | None = None,
    detrend: bool = True,
) -> TimeOffset:
    """Estimate the lag between two channels by cross-correlation.

    The returned :class:`~cetal_scopes.analysis.results.TimeOffset` carries the
    seconds to add to ``signal``'s time axis to align it with ``reference``. The
    channels do not need matching sample rates, sample counts or absolute time
    origins: both are handled by index, on the finer of the two sample
    intervals. Each channel is mean-subtracted (and optionally linearly
    detrended) and normalized, so the peak correlation is unitless.

    Parameters
    ----------
    reference : Channel
        Channel whose time axis is the alignment target.
    signal : Channel
        Channel to be shifted onto ``reference``.
    max_lag : float, optional
        Largest lag considered, in seconds. Defaults to half the shorter
        record. Must be positive.
    detrend : bool, optional
        Remove a linear trend before correlating. Defaults to ``True``.

    Returns
    -------
    TimeOffset
        The measured offset, normalized peak correlation and polarity.
    """
    if reference.n_samples < 2 or signal.n_samples < 2:
        raise ValueError("both channels need at least two samples")

    dt = min(reference.dt, signal.dt)
    ref = reference if reference.dt == dt else resample(reference, dt=dt)
    sig = signal if signal.dt == dt else resample(signal, dt=dt)

    n = min(ref.n_samples, sig.n_samples)
    a = np.asarray(ref.volts[:n], dtype=np.float64)
    b = np.asarray(sig.volts[:n], dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    if detrend:
        a = _scipy_signal.detrend(a)
        b = _scipy_signal.detrend(b)
    a = _unit_norm(a)
    b = _unit_norm(b)

    if max_lag is None:
        max_lag = 0.5 * (n - 1) * dt
    if max_lag <= 0:
        raise ValueError("max_lag must be positive")
    limit = min(round(max_lag / dt), n - 1)

    corr = _scipy_signal.correlate(a, b, mode="full", method="fft")
    center = n - 1
    window = np.abs(corr[center - limit : center + limit + 1])
    peak = int(np.argmax(window)) + center - limit
    peak_value = float(corr[peak])
    return TimeOffset(
        offset=_refine_peak(corr, peak, dt, center),
        correlation=peak_value,
        inverted=peak_value < 0.0,
        max_lag=limit * dt,
    )
