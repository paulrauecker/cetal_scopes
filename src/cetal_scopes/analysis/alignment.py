"""Cross-correlation time alignment between channels."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import numpy as np
from numpy.typing import NDArray
from scipy import signal as _scipy_signal

from cetal_scopes.analysis.results import TimeOffset
from cetal_scopes.analysis.time import resample
from cetal_scopes.channel import Channel
from cetal_scopes.shot import Shot

__all__ = ["align_shot", "estimate_time_offset"]


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
    origins: the comparison runs on the finer of the two sample intervals and
    uses both records in full. Note that the lag is measured **by index**, not
    against the channels' ``t0`` values -- it aligns the two records, not the
    two time axes. To align whole captures, whose origins do differ, use
    :func:`align_shot`, which accounts for that. Each channel is mean-subtracted (and optionally linearly
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

    # Both records are used in full. Truncating them to a common length --
    # which is tempting, since it makes the lag bookkeeping trivial -- throws
    # away the tail of whichever covers more time, and that tail routinely
    # holds the feature: two instruments at different sample rates but the
    # same record length span very different durations.
    a = np.asarray(ref.volts, dtype=np.float64)
    b = np.asarray(sig.volts, dtype=np.float64)
    a = a - a.mean()
    b = b - b.mean()
    if detrend:
        a = _scipy_signal.detrend(a)
        b = _scipy_signal.detrend(b)
    a = _unit_norm(a)
    b = _unit_norm(b)

    if max_lag is None:
        max_lag = 0.5 * (min(a.size, b.size) - 1) * dt
    if max_lag <= 0:
        raise ValueError("max_lag must be positive")

    corr = _scipy_signal.correlate(a, b, mode="full", method="fft")
    # In a 'full' correlation, output index k is a lag of (k - (nb - 1)).
    center = b.size - 1
    limit = min(round(max_lag / dt), center, corr.size - 1 - center)
    window = np.abs(corr[center - limit : center + limit + 1])
    peak = int(np.argmax(window)) + center - limit
    peak_value = float(corr[peak])
    return TimeOffset(
        offset=_refine_peak(corr, peak, dt, center),
        correlation=peak_value,
        inverted=peak_value < 0.0,
        max_lag=limit * dt,
    )


def _default_max_lag(reference: Channel, signal: Channel) -> float:
    """How far to search when the caller did not say.

    :func:`estimate_time_offset` defaults to half the shorter record, measured
    from zero lag. Across instruments that is often not enough: two scopes set
    up with different pretriggers hold the same event at very different
    positions in their records, and the true lag can sit outside the window
    entirely -- the fit then returns a confident-looking number from noise.
    Widening by the difference in recorded origins keeps the physically likely
    lag inside the search.
    """
    span = min(
        (reference.n_samples - 1) * reference.dt,
        (signal.n_samples - 1) * signal.dt,
    )
    return 0.5 * span + abs(reference.t0 - signal.t0)


def _pick_channel(
    capture_channels: Mapping[str, Channel], preferred: str | None
) -> Channel:
    if preferred is not None:
        if preferred not in capture_channels:
            raise KeyError(preferred)
        return capture_channels[preferred]
    return next(iter(capture_channels.values()))


def align_shot(
    shot: Shot,
    *,
    reference: str | None = None,
    channels: Mapping[str, str] | str | None = None,
    max_lag: float | None = None,
    detrend: bool = True,
    apply: bool = True,
) -> dict[str, TimeOffset]:
    """Fit every capture's time offset against the reference capture.

    Where :func:`estimate_time_offset` compares two channels,
    this walks a whole shot: one channel of each capture is correlated against
    one channel of the reference, and the measured offsets are written back
    into the shot so that :meth:`~cetal_scopes.shot.Shot.aligned_channels`
    puts everything on a common axis.

    The returned offsets are inspectable, not magic -- each carries the peak
    correlation and polarity that produced it, so a bad fit (a low correlation,
    an unexpected inversion) is visible rather than silently applied.

    Parameters
    ----------
    shot : Shot
        The shot to align. Needs at least two captures.
    reference : str, optional
        Label whose time axis every other capture is fitted to. Defaults to the
        shot's current :attr:`~cetal_scopes.shot.Shot.reference`.
    channels : mapping or str, optional
        Which channel of each capture to correlate. A single string names the
        same channel in every capture; a mapping gives it per label. Labels not
        covered use their capture's first channel.
    max_lag : float, optional
        Largest lag considered, in seconds. Passed through to
        :func:`estimate_time_offset`.
    detrend : bool, optional
        Remove a linear trend before correlating.
    apply : bool, optional
        Write the fitted offsets into ``shot``. Set ``False`` to measure
        without modifying the shot.

    Returns
    -------
    dict of str to TimeOffset
        One entry per non-reference capture. The reference itself is omitted:
        its offset is zero by definition.

    Raises
    ------
    ValueError
        If the shot has fewer than two captures, or has no reference.
    KeyError
        If a named channel is not present in its capture.

    Examples
    --------
    >>> offsets = align_shot(shot)  # doctest: +SKIP
    >>> {label: round(o.offset * 1e9, 1) for label, o in offsets.items()}  # doctest: +SKIP
    {'m5i': 3.5}
    """
    if len(shot.captures) < 2:
        raise ValueError("aligning a shot needs at least two captures")

    label_ref = reference if reference is not None else shot.reference
    if label_ref is None:
        raise ValueError("shot has no reference capture")
    if label_ref not in shot.captures:
        raise ValueError(f"reference {label_ref!r} is not a capture of this shot")

    if isinstance(channels, str):
        wanted: Mapping[str, str] = dict.fromkeys(shot.captures, channels)
    else:
        wanted = channels or {}

    reference_channel = _pick_channel(
        shot.captures[label_ref].channels, wanted.get(label_ref)
    )

    offsets: dict[str, TimeOffset] = {}
    for label, capture in shot.captures.items():
        if label == label_ref:
            continue
        signal = _pick_channel(capture.channels, wanted.get(label))
        measured = estimate_time_offset(
            reference_channel,
            signal,
            max_lag=max_lag
            if max_lag is not None
            else _default_max_lag(reference_channel, signal),
            detrend=detrend,
        )
        # estimate_time_offset correlates the two records by *index* and never
        # looks at t0, so its lag aligns the arrays, not the time axes. A
        # shot's offset shifts the time axis, so the difference in recorded
        # origins has to be added back -- it is exactly the pretrigger
        # mismatch between two instruments set up differently, and ignoring it
        # silently mis-aligns every shot whose captures do not share a t0.
        corrected = replace(
            measured,
            offset=measured.offset + (reference_channel.t0 - signal.t0),
        )
        offsets[label] = corrected
        if apply:
            shot.set_offset(label, corrected.offset)

    if apply:
        shot.set_reference(label_ref)
        shot.set_offset(label_ref, 0.0)
    return offsets
