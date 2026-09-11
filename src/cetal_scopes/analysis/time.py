"""Time-domain operations on channels."""

from __future__ import annotations

import numpy as np
from scipy.signal import detrend as _scipy_detrend

from cetal_scopes.analysis._util import DetrendMode, with_volts
from cetal_scopes.channel import Channel

__all__ = ["detrend", "gate", "remove_adc_comb", "resample", "subtract_baseline"]


def gate(
    channel: Channel,
    *,
    t_start: float | None = None,
    t_end: float | None = None,
) -> Channel:
    """Select the samples whose time falls in ``[t_start, t_end]``.

    Parameters
    ----------
    channel : Channel
        Source channel.
    t_start : float, optional
        Inclusive lower bound in seconds. Defaults to the first sample.
    t_end : float, optional
        Inclusive upper bound in seconds. Defaults to the last sample.

    Returns
    -------
    Channel
        A view over the selected samples with an adjusted ``t0``.
    """
    t0 = channel.t0
    dt = channel.dt
    n_samples = channel.n_samples
    i0 = 0 if t_start is None else max(0, int(np.ceil((t_start - t0) / dt)))
    i1 = (
        n_samples
        if t_end is None
        else min(n_samples, int(np.floor((t_end - t0) / dt)) + 1)
    )
    if i1 < i0:
        raise ValueError("t_end selects no samples before t_start")
    return Channel(
        name=channel.name,
        volts=channel.volts[i0:i1],
        t0=t0 + i0 * dt,
        dt=dt,
        raw=None if channel.raw is None else channel.raw[i0:i1],
    )


def subtract_baseline(
    channel: Channel,
    *,
    t_start: float | None = None,
    t_end: float | None = None,
    mode: str = "mean",
) -> Channel:
    """Subtract a constant baseline measured over a (pre-trigger) window.

    Parameters
    ----------
    channel : Channel
        Source channel.
    t_start, t_end : float, optional
        Baseline window in seconds. With neither given the whole channel is
        used.
    mode : {"mean", "median"}
        Estimator for the baseline level.

    Returns
    -------
    Channel
        Baseline-subtracted channel (raw codes dropped).
    """
    region = (
        gate(channel, t_start=t_start, t_end=t_end)
        if t_start is not None or t_end is not None
        else channel
    )
    if mode == "mean":
        level = float(np.mean(region.volts))
    elif mode == "median":
        level = float(np.median(region.volts))
    else:
        raise ValueError(f"mode must be 'mean' or 'median', got {mode!r}")
    return with_volts(channel, channel.volts - level)


def remove_adc_comb(channel: Channel, *, period: int = 256) -> Channel:
    """Remove a periodic ADC interleave comb from the channel.

    The SDS6204L's 16-bit acquisition path adds a deterministic pattern with a
    period of ``256`` samples, producing spurs at every multiple of
    ``fs / 256`` (about 39.06 MHz at 10 GS/s), with ``fs / 8``, ``fs / 4`` and
    ``fs / 2`` among the strongest. Subtracting the mean of each phase
    (``index % period``) removes exactly that pattern and is gentler than
    notching the spectrum: the overall DC level is preserved, so only the comb
    is removed.

    Parameters
    ----------
    channel : Channel
        Source channel.
    period : int
        Pattern period in samples. Defaults to ``256``. The pattern is locked to
        the instrument's internal ADC clock, so at decimated sample rates the
        period in the recorded stream scales with ``fs`` (e.g. 512 at 5 GS/s).

    Returns
    -------
    Channel
        Comb-corrected channel (raw codes dropped).
    """
    if period < 2:
        raise ValueError("period must be at least 2")
    volts = channel.volts.astype(np.float64)
    if volts.size < period:
        raise ValueError("channel is shorter than one comb period")
    phase = np.arange(volts.size) % period
    phase_means = np.array(
        [volts[phase == p].mean() for p in range(period)], dtype=np.float64
    )
    return with_volts(channel, volts - phase_means[phase] + volts.mean())


def detrend(channel: Channel, *, type: DetrendMode = "linear") -> Channel:
    """Remove a constant or linear trend from the channel.

    Parameters
    ----------
    channel : Channel
        Source channel.
    type : {"linear", "constant"}
        Trend order, passed to :func:`scipy.signal.detrend`.

    Returns
    -------
    Channel
        Detrended channel (raw codes dropped).
    """
    if type not in ("linear", "constant"):
        raise ValueError(f"type must be 'linear' or 'constant', got {type!r}")
    return with_volts(channel, _scipy_detrend(channel.volts, type=type))


def resample(
    channel: Channel,
    *,
    dt: float | None = None,
    n: int | None = None,
) -> Channel:
    """Linearly interpolate the channel onto a new uniform time grid.

    Exactly one of ``dt`` or ``n`` must be given. The new grid starts at the
    original ``t0``.

    Parameters
    ----------
    channel : Channel
        Source channel.
    dt : float, optional
        Target sample interval in seconds.
    n : int, optional
        Target number of samples, spanning the original duration.

    Returns
    -------
    Channel
        Resampled channel (raw codes dropped).
    """
    if (dt is None) == (n is None):
        raise ValueError("give exactly one of dt or n")
    last = channel.t0 + (channel.n_samples - 1) * channel.dt
    if n is not None:
        if n < 1:
            raise ValueError("n must be positive")
        new_dt = (last - channel.t0) / (n - 1) if n > 1 else channel.dt
        new_time = np.linspace(channel.t0, last, n)
    else:
        if dt is None or dt <= 0:
            raise ValueError("dt must be positive")
        new_dt = dt
        new_n = max(1, round((last - channel.t0) / dt) + 1)
        new_time = channel.t0 + np.arange(new_n, dtype=np.float64) * new_dt
    volts = np.interp(new_time, channel.time, channel.volts)
    return Channel(
        name=channel.name,
        volts=volts.astype(np.float64),
        t0=channel.t0,
        dt=new_dt,
    )
