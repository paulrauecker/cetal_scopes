"""Two-channel spectral analysis.

Everything here compares a pair of channels, which is what makes a
multi-instrument shot worth taking: the interesting question is rarely what one
record contains but how two of them relate. The pair may come from different
captures at different sample rates and time origins -- they are brought onto a
common grid first, the same way
:func:`~cetal_scopes.analysis.alignment.estimate_time_offset` handles it.

Estimates are Welch-averaged over overlapping segments. That is not a detail:
a single-segment coherence is identically 1 at every frequency, because one
observation of a ratio has no scatter to measure. Averaging is what makes the
number mean anything.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.signal import csd, welch

from cetal_scopes.analysis._util import DetrendMode
from cetal_scopes.analysis.results import Spectrum
from cetal_scopes.analysis.time import resample
from cetal_scopes.channel import Channel

__all__ = ["coherence", "cross_spectrum", "transfer_function"]


def _detrend_arg(detrend: DetrendMode | None) -> Any:
    """SciPy spells "no detrending" as ``False``; its stubs only admit ``str``."""
    return detrend if detrend is not None else False


def _common_grid(
    a: Channel, b: Channel
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """Put both channels on the finer sample interval and a common length.

    Absolute time origins are deliberately *not* reconciled here: a cross
    spectrum is a function of lag, and forcing an origin would silently bake
    in whatever offset the two instruments happened to have. Align the shot
    first if the absolute phase is meant to be physical.
    """
    dt = min(a.dt, b.dt)
    left = a if a.dt == dt else resample(a, dt=dt)
    right = b if b.dt == dt else resample(b, dt=dt)
    n = min(left.n_samples, right.n_samples)
    if n < 2:
        raise ValueError("both channels need at least two samples")
    return (
        np.asarray(left.volts[:n], dtype=np.float64),
        np.asarray(right.volts[:n], dtype=np.float64),
        dt,
    )


def _segment_length(n: int, segment_samples: int | None) -> int:
    if segment_samples is None:
        # Eight half-overlapping segments: enough averaging for the estimate
        # to be meaningful, without throwing away frequency resolution.
        return max(16, min(n, 2 ** int(np.floor(np.log2(max(n // 8, 16))))))
    if not 2 <= segment_samples <= n:
        raise ValueError(
            f"segment_samples must be in [2, {n}], got {segment_samples!r}"
        )
    return segment_samples


def cross_spectrum(
    a: Channel,
    b: Channel,
    *,
    segment_samples: int | None = None,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> tuple[NDArray[np.float64], NDArray[np.complex128]]:
    """Welch-averaged cross power spectral density of ``a`` and ``b``.

    Parameters
    ----------
    a, b : Channel
        The two channels. They may come from different captures; the finer
        sample interval is used and the longer record is truncated.
    segment_samples : int, optional
        Averaging segment length. Defaults to roughly an eighth of the record.
    window : str, optional
        Window applied to each segment.
    detrend : {"constant", "linear", None}, optional
        Trend removed from each segment.

    Returns
    -------
    freq : ndarray
        One-sided frequency axis in hertz.
    cross : ndarray of complex
        ``Sxy``. Its magnitude is the shared power, its argument the phase of
        ``b`` relative to ``a``.

    Raises
    ------
    ValueError
        If either channel is too short.
    """
    left, right, dt = _common_grid(a, b)
    nperseg = _segment_length(left.size, segment_samples)
    freq, cross = csd(
        left,
        right,
        fs=1.0 / dt,
        window=window,
        nperseg=nperseg,
        detrend=_detrend_arg(detrend),
    )
    return (
        np.asarray(freq, dtype=np.float64),
        np.asarray(cross, dtype=np.complex128),
    )


def coherence(
    a: Channel,
    b: Channel,
    *,
    segment_samples: int | None = None,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Spectrum:
    """Magnitude-squared coherence between ``a`` and ``b``.

    How much of ``b`` is linearly explained by ``a``, per frequency, in
    ``[0, 1]``. This is the honest companion to
    :func:`transfer_function`: a transfer function has a value at every
    frequency whether or not the two channels have anything to do with each
    other there, and coherence is what says which of those values to believe.

    Returns
    -------
    Spectrum
        ``amplitude`` carries the coherence and ``psd`` its square; the units
        are dimensionless.

    See Also
    --------
    transfer_function : the gain whose trustworthiness this measures.
    """
    left, right, dt = _common_grid(a, b)
    nperseg = _segment_length(left.size, segment_samples)
    kwargs = {
        "fs": 1.0 / dt,
        "window": window,
        "nperseg": nperseg,
        "detrend": _detrend_arg(detrend),
    }
    freq, pxx = welch(left, **kwargs)
    _, pyy = welch(right, **kwargs)
    _, pxy = csd(left, right, **kwargs)

    denominator = np.asarray(pxx, dtype=np.float64) * np.asarray(pyy, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        values = np.abs(np.asarray(pxy)) ** 2 / denominator
    values = np.where(denominator > 0.0, values, 0.0)
    values = np.clip(values, 0.0, 1.0)

    magnitude = np.sqrt(values)
    return Spectrum(
        freq=np.asarray(freq, dtype=np.float64),
        amplitude=np.asarray(magnitude, dtype=np.float64),
        psd=np.asarray(values, dtype=np.float64),
        window=window,
    )


def transfer_function(
    a: Channel,
    b: Channel,
    *,
    segment_samples: int | None = None,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> tuple[NDArray[np.float64], NDArray[np.complex128]]:
    """Complex gain from ``a`` to ``b``, estimated as ``Sxy / Sxx``.

    The ``H1`` estimator: it is unbiased when the noise is on the output
    (``b``) and biased low when it is on the input (``a``), which is the usual
    situation when ``a`` is a drive or reference signal.

    Parameters
    ----------
    a : Channel
        Input or reference channel.
    b : Channel
        Output channel.
    segment_samples, window, detrend
        As for :func:`cross_spectrum`.

    Returns
    -------
    freq : ndarray
        One-sided frequency axis in hertz.
    gain : ndarray of complex
        ``b / a`` per frequency. Bins where ``a`` carries no power come back
        as ``nan``, rather than as a huge number divided out of noise -- pair
        this with :func:`coherence` to see which bins to trust.
    """
    left, right, dt = _common_grid(a, b)
    nperseg = _segment_length(left.size, segment_samples)
    kwargs = {
        "fs": 1.0 / dt,
        "window": window,
        "nperseg": nperseg,
        "detrend": _detrend_arg(detrend),
    }
    freq, pxx = welch(left, **kwargs)
    _, pxy = csd(left, right, **kwargs)

    power = np.asarray(pxx, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        gain = np.asarray(pxy, dtype=np.complex128) / power
    gain = np.where(power > 0.0, gain, np.nan + 0j)
    return np.asarray(freq, dtype=np.float64), np.asarray(gain, dtype=np.complex128)
