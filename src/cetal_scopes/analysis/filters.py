"""Zero-phase digital filtering of channels.

Every filter here is applied forwards and backwards
(:func:`scipy.signal.sosfiltfilt`), so it has no phase response at all. That
matters downstream: a causal filter would shift features in time by a
frequency-dependent amount and corrupt exactly the timing measurements that
:mod:`cetal_scopes.analysis.alignment` and the pulse metrics depend on. The
price is that the filter is non-causal and the effective order is doubled.

Second-order sections (``output="sos"``) rather than transfer-function
coefficients, which lose numerical conditioning badly at the high orders and
narrow relative bandwidths a digitizer record invites.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, savgol_filter, sosfiltfilt

from cetal_scopes.analysis._util import with_volts
from cetal_scopes.channel import Channel

__all__ = [
    "bandpass",
    "bandstop",
    "highpass",
    "lowpass",
    "moving_average",
    "savgol",
]

#: ``sosfiltfilt`` extends the signal by ``3 * (2 * n_sections + 1)`` samples at
#: each end, and refuses a record shorter than that.
_PADDING_PER_SECTION = 6


def _sample_rate(channel: Channel) -> float:
    if channel.dt <= 0:  # pragma: no cover - Capture rejects this
        raise ValueError(f"channel dt must be positive, got {channel.dt!r}")
    return 1.0 / channel.dt


def _check_order(order: int) -> int:
    if order < 1:
        raise ValueError(f"order must be at least 1, got {order!r}")
    return int(order)


def _check_corner(channel: Channel, name: str, value: float) -> float:
    nyquist = _sample_rate(channel) / 2.0
    if not 0.0 < value < nyquist:
        raise ValueError(
            f"{name} must be in (0, {nyquist:g}) Hz for this channel's "
            f"sample rate, got {value!r}"
        )
    return float(value)


def _apply(channel: Channel, sos: np.ndarray) -> Channel:
    n_sections = sos.shape[0]
    minimum = _PADDING_PER_SECTION * n_sections + 4
    if channel.n_samples <= minimum:
        raise ValueError(
            f"channel has {channel.n_samples} samples, too few for a "
            f"zero-phase filter of {n_sections} sections (needs more than "
            f"{minimum}); lower the order or use a longer record"
        )
    values = np.asarray(channel.volts, dtype=np.float64)
    filtered = np.asarray(sosfiltfilt(sos, values), dtype=np.float64)
    return with_volts(channel, filtered)


def lowpass(channel: Channel, *, cutoff: float, order: int = 4) -> Channel:
    """Zero-phase Butterworth low-pass.

    Parameters
    ----------
    channel : Channel
        Source channel.
    cutoff : float
        -3 dB corner in hertz, below Nyquist.
    order : int, optional
        Butterworth order of the one-way filter; the zero-phase pass doubles
        the effective roll-off.

    Returns
    -------
    Channel
        Filtered channel; ``raw`` codes are dropped.

    Raises
    ------
    ValueError
        If ``cutoff`` is not below Nyquist, or the record is too short.
    """
    corner = _check_corner(channel, "cutoff", cutoff)
    sos = butter(
        _check_order(order),
        corner,
        btype="lowpass",
        fs=_sample_rate(channel),
        output="sos",
    )
    return _apply(channel, np.asarray(sos))


def highpass(channel: Channel, *, cutoff: float, order: int = 4) -> Channel:
    """Zero-phase Butterworth high-pass. See :func:`lowpass` for the parameters."""
    corner = _check_corner(channel, "cutoff", cutoff)
    sos = butter(
        _check_order(order),
        corner,
        btype="highpass",
        fs=_sample_rate(channel),
        output="sos",
    )
    return _apply(channel, np.asarray(sos))


def bandpass(channel: Channel, *, low: float, high: float, order: int = 4) -> Channel:
    """Zero-phase Butterworth band-pass between ``low`` and ``high`` hertz.

    Raises
    ------
    ValueError
        If the band is not ``0 < low < high < nyquist``, or the record is too
        short for the resulting filter.
    """
    low_corner = _check_corner(channel, "low", low)
    high_corner = _check_corner(channel, "high", high)
    if low_corner >= high_corner:
        raise ValueError(f"expected low < high, got {low!r} and {high!r}")
    sos = butter(
        _check_order(order),
        (low_corner, high_corner),
        btype="bandpass",
        fs=_sample_rate(channel),
        output="sos",
    )
    return _apply(channel, np.asarray(sos))


def bandstop(channel: Channel, *, low: float, high: float, order: int = 4) -> Channel:
    """Zero-phase Butterworth band-stop. See :func:`bandpass` for the parameters."""
    low_corner = _check_corner(channel, "low", low)
    high_corner = _check_corner(channel, "high", high)
    if low_corner >= high_corner:
        raise ValueError(f"expected low < high, got {low!r} and {high!r}")
    sos = butter(
        _check_order(order),
        (low_corner, high_corner),
        btype="bandstop",
        fs=_sample_rate(channel),
        output="sos",
    )
    return _apply(channel, np.asarray(sos))


def moving_average(channel: Channel, *, window_s: float) -> Channel:
    """Centred boxcar average over ``window_s`` seconds.

    A crude low-pass with a ``sinc`` response, but it is the smoother people
    reach for when they want an obvious, explainable operation. The window is
    rounded to an odd number of samples so it stays centred, and the record is
    edge-padded so the output keeps its length.

    Raises
    ------
    ValueError
        If ``window_s`` is not positive or spans fewer than two samples.
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be positive, got {window_s!r}")
    n = round(window_s / channel.dt)
    if n < 2:
        raise ValueError(
            f"window_s {window_s!r} spans {n} samples at dt={channel.dt:g}; "
            "widen it to at least two samples"
        )
    if n % 2 == 0:
        n += 1
    if n >= channel.n_samples:
        raise ValueError(
            f"window of {n} samples is not shorter than the {channel.n_samples}"
            "-sample record"
        )

    values = np.asarray(channel.volts, dtype=np.float64)
    half = n // 2
    padded = np.pad(values, half, mode="edge")
    kernel = np.full(n, 1.0 / n, dtype=np.float64)
    smoothed = np.convolve(padded, kernel, mode="valid")
    return with_volts(channel, np.asarray(smoothed, dtype=np.float64))


def savgol(channel: Channel, *, window_s: float, polyorder: int = 3) -> Channel:
    """Savitzky-Golay smoothing over ``window_s`` seconds.

    Preferred over :func:`moving_average` when peak height and width must
    survive the smoothing: fitting a local polynomial preserves them far better
    than a boxcar, which flattens narrow features.

    Raises
    ------
    ValueError
        If the window spans too few samples for ``polyorder``.
    """
    if window_s <= 0:
        raise ValueError(f"window_s must be positive, got {window_s!r}")
    if polyorder < 1:
        raise ValueError(f"polyorder must be at least 1, got {polyorder!r}")
    n = round(window_s / channel.dt)
    if n % 2 == 0:
        n += 1
    if n <= polyorder:
        raise ValueError(
            f"window_s {window_s!r} spans {n} samples, which cannot support a "
            f"degree-{polyorder} fit; widen it or lower polyorder"
        )
    if n > channel.n_samples:
        raise ValueError(
            f"window of {n} samples exceeds the {channel.n_samples}-sample record"
        )

    values = np.asarray(channel.volts, dtype=np.float64)
    smoothed = savgol_filter(values, window_length=n, polyorder=polyorder)
    return with_volts(channel, np.asarray(smoothed, dtype=np.float64))
