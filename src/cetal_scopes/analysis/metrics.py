"""Scalar metrics extracted from channels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import find_peaks as _scipy_find_peaks
from scipy.signal import peak_prominences, peak_widths

from cetal_scopes.analysis.results import ChannelStats, Peak, PulseMetrics
from cetal_scopes.channel import Channel

__all__ = ["find_peaks", "pulse_metrics", "stats"]


def stats(channel: Channel) -> ChannelStats:
    """Compute descriptive time-domain statistics for a channel.

    ``peak_value``/``peak_time`` refer to the sample with the largest absolute
    amplitude.

    Parameters
    ----------
    channel : Channel
        Source channel.

    Returns
    -------
    ChannelStats
        The computed statistics; all fields are ``nan`` for an empty channel.
    """
    values = channel.volts.astype(np.float64)
    if values.size == 0:
        nan = float("nan")
        return ChannelStats(
            name=channel.name,
            n_samples=0,
            mean=nan,
            rms=nan,
            std=nan,
            minimum=nan,
            maximum=nan,
            peak_to_peak=nan,
            peak_value=nan,
            peak_time=nan,
        )
    peak_index = int(np.argmax(np.abs(values)))
    return ChannelStats(
        name=channel.name,
        n_samples=int(values.size),
        mean=float(np.mean(values)),
        rms=float(np.sqrt(np.mean(values**2))),
        std=float(np.std(values)),
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        peak_to_peak=float(np.max(values) - np.min(values)),
        peak_value=float(values[peak_index]),
        peak_time=float(channel.time[peak_index]),
    )


def find_peaks(
    channel: Channel,
    *,
    height: float | None = None,
    prominence: float | None = None,
    distance: float | None = None,
    absolute: bool = False,
) -> list[Peak]:
    """Locate local maxima in a channel.

    Parameters
    ----------
    channel : Channel
        Source channel.
    height : float, optional
        Minimum sample value for a peak.
    prominence : float, optional
        Minimum prominence. Usually the more useful of the two thresholds:
        height alone admits every ripple riding on a large feature, while
        prominence asks how far a peak stands out from its own surroundings.
    distance : float, optional
        Minimum separation between peaks, **in seconds** (the library's unit),
        not in samples as :func:`scipy.signal.find_peaks` takes it.
    absolute : bool, optional
        Search ``abs(volts)``, so negative-going features count as peaks too.

    Returns
    -------
    list of Peak
        Peaks in time order, each with its prominence and half-prominence
        width in seconds.

    Raises
    ------
    ValueError
        If ``distance`` is positive but shorter than one sample.
    """
    values = np.asarray(channel.volts, dtype=np.float64)
    if values.size < 3:
        return []
    search: NDArray[np.float64] = np.abs(values) if absolute else values

    distance_samples: float | None = None
    if distance is not None:
        distance_samples = distance / channel.dt
        if distance_samples < 1.0:
            raise ValueError(
                f"distance {distance!r} s is shorter than one sample ({channel.dt:g} s)"
            )

    indices, _ = _scipy_find_peaks(
        search,
        height=height,
        prominence=prominence,
        distance=distance_samples,
    )
    if indices.size == 0:
        return []

    prominences = peak_prominences(search, indices)[0]
    widths = peak_widths(search, indices, rel_height=0.5)[0]
    times = channel.time
    return [
        Peak(
            index=int(index),
            time=float(times[index]),
            value=float(values[index]),
            prominence=float(prominences[position]),
            width=float(widths[position] * channel.dt),
        )
        for position, index in enumerate(indices)
    ]


def _settled_level(values: NDArray[np.float64], *, upper: bool) -> float:
    """Histogram-mode level of the waveform's upper or lower half.

    A scope measures ``top`` and ``base`` this way rather than with min/max so
    that overshoot and ringing do not inflate the amplitude -- which is what
    makes a naive 10-90% rise time read short.
    """
    low = float(np.min(values))
    high = float(np.max(values))
    if high == low:
        return high
    midpoint = 0.5 * (low + high)
    half = values[values >= midpoint] if upper else values[values <= midpoint]
    if half.size == 0:  # pragma: no cover - midpoint always selects something
        return high if upper else low
    counts, edges = np.histogram(half, bins=min(64, max(8, half.size // 8)))
    peak_bin = int(np.argmax(counts))
    return float(0.5 * (edges[peak_bin] + edges[peak_bin + 1]))


def _first_crossing(
    values: NDArray[np.float64], level: float, start: int, *, rising: bool
) -> float | None:
    """Sub-sample index where ``values`` first crosses ``level`` from ``start``."""
    if rising:
        candidates = np.nonzero(
            (values[start:-1] < level) & (values[start + 1 :] >= level)
        )[0]
    else:
        candidates = np.nonzero(
            (values[start:-1] > level) & (values[start + 1 :] <= level)
        )[0]
    if candidates.size == 0:
        return None
    index = int(candidates[0]) + start
    span = values[index + 1] - values[index]
    if span == 0:  # pragma: no cover - a crossing implies a nonzero step
        return float(index)
    return index + (level - values[index]) / span


def pulse_metrics(
    channel: Channel, *, low: float = 0.1, high: float = 0.9
) -> PulseMetrics:
    """Measure the edges and width of a pulse, the way a scope's meters do.

    Levels are taken from the histogram modes of the settled parts of the
    waveform, so ringing does not shorten the rise time. Any measurement the
    waveform does not support -- a rise time with no rising edge, a width with
    no crossing -- comes back ``nan`` rather than as a fabricated number.

    Parameters
    ----------
    channel : Channel
        Source channel.
    low, high : float, optional
        Reference fractions of the amplitude for the edge timings. The
        defaults give the conventional 10-90% rise and fall times; ``0.2`` and
        ``0.8`` are the other common choice.

    Returns
    -------
    PulseMetrics
        The measured levels, edge timings and widths.

    Raises
    ------
    ValueError
        If the channel has fewer than two samples, or the fractions are not
        ``0 < low < high < 1``.
    """
    if not 0.0 < low < high < 1.0:
        raise ValueError(f"expected 0 < low < high < 1, got {low!r} and {high!r}")
    values = np.asarray(channel.volts, dtype=np.float64)
    if values.size < 2:
        raise ValueError("pulse metrics need at least two samples")

    nan = float("nan")
    base = _settled_level(values, upper=False)
    top = _settled_level(values, upper=True)
    amplitude = top - base
    times = channel.time
    peak_index = int(np.argmax(np.abs(values)))

    if amplitude <= 0.0:
        # A flat trace has levels but no edges; say so instead of guessing.
        return PulseMetrics(
            name=channel.name,
            base=base,
            top=top,
            amplitude=amplitude,
            low_reference=nan,
            high_reference=nan,
            rise_time=nan,
            fall_time=nan,
            width=nan,
            fwhm=nan,
            overshoot=nan,
            undershoot=nan,
            peak_time=float(times[peak_index]),
            peak_value=float(values[peak_index]),
        )

    low_reference = base + low * amplitude
    high_reference = base + high * amplitude
    midpoint = base + 0.5 * amplitude

    rise_start = _first_crossing(values, low_reference, 0, rising=True)
    rise_end = (
        None
        if rise_start is None
        else _first_crossing(values, high_reference, int(rise_start), rising=True)
    )
    rise_time = (
        nan
        if rise_start is None or rise_end is None
        else (rise_end - rise_start) * channel.dt
    )

    fall_start = _first_crossing(values, high_reference, peak_index, rising=False)
    fall_end = (
        None
        if fall_start is None
        else _first_crossing(values, low_reference, int(fall_start), rising=False)
    )
    fall_time = (
        nan
        if fall_start is None or fall_end is None
        else (fall_end - fall_start) * channel.dt
    )

    mid_rise = _first_crossing(values, midpoint, 0, rising=True)
    mid_fall = (
        None
        if mid_rise is None
        else _first_crossing(values, midpoint, int(mid_rise), rising=False)
    )
    width = (
        nan
        if mid_rise is None or mid_fall is None
        else (mid_fall - mid_rise) * channel.dt
    )

    half_level = base + 0.5 * (float(np.max(values)) - base)
    half_rise = _first_crossing(values, half_level, 0, rising=True)
    half_fall = (
        None
        if half_rise is None
        else _first_crossing(values, half_level, int(half_rise), rising=False)
    )
    fwhm = (
        nan
        if half_rise is None or half_fall is None
        else (half_fall - half_rise) * channel.dt
    )

    overshoot = max(0.0, float(np.max(values)) - top) / amplitude
    undershoot = max(0.0, base - float(np.min(values))) / amplitude

    return PulseMetrics(
        name=channel.name,
        base=base,
        top=top,
        amplitude=amplitude,
        low_reference=low_reference,
        high_reference=high_reference,
        rise_time=rise_time,
        fall_time=fall_time,
        width=width,
        fwhm=fwhm,
        overshoot=overshoot,
        undershoot=undershoot,
        peak_time=float(times[peak_index]),
        peak_value=float(values[peak_index]),
    )
