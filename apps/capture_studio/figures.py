"""Turn a shot plus a view state into Plotly figure specifications.

Figures are built as plain dicts and handed to Plotly.js in the browser, so
nothing here imports plotly: the server does the numerics, the browser does the
drawing.

Every time-domain figure is drawn on the *aligned* axis
(:meth:`~cetal_scopes.shot.Shot.aligned_channels`), so moving a capture's offset
moves its traces.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from itertools import pairwise
from typing import Any, Literal

import numpy as np

from cetal_scopes.analysis import coherence as _coherence
from cetal_scopes.analysis import fft as _fft
from cetal_scopes.analysis import pulse_metrics, stats, stft, vector_at
from cetal_scopes.analysis import resample_onto as _resample_onto
from cetal_scopes.analysis import transfer_function as _transfer_function
from cetal_scopes.antenna import Antenna
from cetal_scopes.capture import Capture
from cetal_scopes.channel import Channel
from cetal_scopes.plotting import decimate_envelope
from cetal_scopes.shot import Shot

__all__ = [
    "LAYOUTS",
    "LayoutMode",
    "coherence_figure",
    "decimate_spectrum",
    "empty_figure",
    "fft_figure",
    "measurement_table",
    "spectrogram_figure",
    "time_figure",
    "to_decibels",
    "transfer_figure",
    "vector_figure",
    "window_samples",
    "xy_figure",
]

LayoutMode = Literal["per-channel", "per-capture", "overlay"]

LAYOUTS: tuple[LayoutMode, ...] = ("per-channel", "per-capture", "overlay")
"""The three ways to arrange the time-domain traces."""

#: A qualitative palette that stays distinguishable in both light and dark
#: themes and for the common forms of colour blindness.
PALETTE = (
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#72B7B2",
    "#B279A2",
    "#EECA3B",
    "#9D755D",
)

_TIME_UNITS = (
    (1.0, "s"),
    (1e-3, "ms"),
    (1e-6, "µs"),
    (1e-9, "ns"),
    (1e-12, "ps"),
)


def time_scale(span: float) -> tuple[float, str]:
    """Pick a readable time unit for a span in seconds."""
    for scale, name in _TIME_UNITS:
        if span >= scale:
            return scale, name
    return 1e-12, "ps"


def _colour(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


#: Height, in CSS pixels, of a figure that is a single set of axes.
#: Every figure declares its own height because several of them share one
#: container: the "Two-channel" panel draws coherence, transfer, XY,
#: spectrogram and the field vector into the same div. ``Plotly.react`` merges
#: layouts, so a figure that omits ``height`` after one that set it inherits
#: the old value and renders taller than the panel, which clips it
#: (``.panel`` is ``overflow: hidden`` for its rounded corners).
PLOT_HEIGHT = 380

#: Height of one row of a stacked subplot grid, before the shared chrome.
ROW_HEIGHT = 190


def stacked_height(n_rows: int) -> int:
    """Height for a figure of ``n_rows`` stacked subplots.

    Linear in the row count so a three-row figure is not three cramped
    slivers, with a floor so a one-row figure still gets a usable panel.
    """
    return max(PLOT_HEIGHT, ROW_HEIGHT * max(n_rows, 1) + 40)


def _base_layout(title: str, **overrides: Any) -> dict[str, Any]:
    """A layout that leaves room for whatever it is given.

    Everything here is about not drawing text on top of other text: the
    legend sits *above* the plotting area rather than below it (where it
    collided with the x-axis title on short panels), every axis carries
    ``automargin`` so long tick labels and axis titles push the margin out
    instead of overlapping, and ``autoexpand`` lets the legend do the same.
    """
    layout: dict[str, Any] = {
        "title": {"text": title, "x": 0, "xanchor": "left", "font": {"size": 13}},
        "margin": {
            "l": 64,
            "r": 24,
            "t": 52 if title else 34,
            "b": 44,
            "autoexpand": True,
        },
        "hovermode": "closest",
        "showlegend": True,
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.0,
            "xanchor": "right",
            "x": 1.0,
            "font": {"size": 11},
        },
        "template": "plotly_white",
        # Redraws keep the pan/zoom the operator set: refreshing after a
        # pipeline tweak should not throw away the window they are looking at.
        "uirevision": "studio",
    }
    for key, value in overrides.items():
        if key.startswith(("xaxis", "yaxis")) and isinstance(value, dict):
            value = {"automargin": True, **value}
        layout[key] = value
    return layout


def empty_figure(message: str) -> dict[str, Any]:
    """A figure that only says why there is nothing to draw."""
    return {
        "data": [],
        "layout": _base_layout(
            "",
            showlegend=False,
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[
                {
                    "text": message,
                    "xref": "paper",
                    "yref": "paper",
                    "x": 0.5,
                    "y": 0.5,
                    "showarrow": False,
                    "font": {"size": 14},
                }
            ],
            # Declared for the same reason every other figure declares it:
            # the empty state replaces a real figure in the same container.
            height=PLOT_HEIGHT,
        ),
    }


def _iter_channels(
    shot: Shot, selection: Sequence[str] | None
) -> Iterator[tuple[str, str, Channel]]:
    """Yield ``(key, label, aligned channel)`` for the selected channels."""
    for key, channel in shot.aligned_channels().items():
        if selection is not None and key not in selection:
            continue
        label = key.split(":", 1)[0]
        yield key, label, channel


def window_samples(
    channel: Channel, t_min: float | None, t_max: float | None
) -> tuple[np.ndarray, np.ndarray]:
    """Return the ``(times, values)`` of ``channel`` inside ``[t_min, t_max]``.

    Index arithmetic rather than a mask: the channel's time axis is derived
    (``t0 + arange(n) * dt``), so the bounds resolve to a slice without
    materialising the whole axis to compare against.

    An empty slice is a legitimate answer -- it means this channel does not
    reach into the requested window, which happens whenever captures with
    different pretriggers are zoomed into one instrument's region.
    """
    n = channel.n_samples
    first = 0 if t_min is None else int(np.ceil((t_min - channel.t0) / channel.dt))
    last = n if t_max is None else int(np.floor((t_max - channel.t0) / channel.dt)) + 1
    first = max(0, min(first, n))
    last = max(first, min(last, n))
    times = channel.t0 + np.arange(first, last, dtype=np.float64) * channel.dt
    return times, np.asarray(channel.volts[first:last], dtype=np.float64)


def time_figure(
    shot: Shot,
    *,
    layout: LayoutMode = "overlay",
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
    max_points: int = 4000,
    t_min: float | None = None,
    t_max: float | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Draw the time-domain traces in one of the three layouts.

    Parameters
    ----------
    shot : Shot
        The shot to draw. Channels are keyed ``"label:channel"``.
    layout : {"per-channel", "per-capture", "overlay"}
        ``per-channel`` gives every channel its own row, ``per-capture`` groups
        each instrument's channels on one row, and ``overlay`` puts everything
        on one axes.
    channels : sequence of str, optional
        Keys to draw. Defaults to all of them.
    processed : dict, optional
        Replacement channels, keyed the same way, from the processing
        pipeline. Keys not present fall back to the recorded channel.
    max_points : int, optional
        Per-trace drawing budget. Traces are reduced with a min/max envelope,
        so narrow transients survive.
    t_min, t_max : float, optional
        Bound the drawn span, in seconds on the aligned axis. Applied
        *before* the budget, so a zoomed-in window spends the whole budget on
        itself: once the window holds no more samples than the budget, every
        sample is drawn and the trace is at full rate.
    revision : str, optional
        Identifies the data behind the figure, usually the shot id. Plotly
        keeps the user's pan and zoom while it is unchanged -- which is what
        lets a zoom re-fetch its own window without the view jumping -- and
        resets the view when it changes, so a new shot is not shown through
        the last one's window.

    Returns
    -------
    dict
        A Plotly figure specification. Its ``layout.meta`` carries the time
        scale in use, the window, and whether any trace was decimated, so the
        browser can convert axis coordinates back to seconds and say what it
        is showing.
    """
    selected = list(_iter_channels(shot, channels))
    if not selected:
        return empty_figure("No channels to display.")

    resolved = [
        (key, label, (processed or {}).get(key, channel))
        for key, label, channel in selected
    ]

    if layout == "per-channel":
        groups = [(key, [(key, label, channel)]) for key, label, channel in resolved]
    elif layout == "per-capture":
        ordered: dict[str, list[tuple[str, str, Channel]]] = {}
        for key, label, channel in resolved:
            ordered.setdefault(label, []).append((key, label, channel))
        groups = list(ordered.items())
    else:
        groups = [("", resolved)]

    # The unit comes from the *whole* record, never from the zoom window: a
    # unit that changed as you zoomed would silently redefine the numbers on
    # the axis mid-gesture, and the browser converts those numbers back to
    # seconds to ask for the next window.
    span = max((channel.n_samples - 1) * channel.dt for _, _, channel in resolved)
    scale, unit = time_scale(span)

    data: list[dict[str, Any]] = []
    figure_layout = _base_layout("")
    n_rows = len(groups)
    order = [item[0] for item in resolved]
    in_window = 0
    drawn = 0

    for row, (group_name, members) in enumerate(groups, start=1):
        axis = "" if row == 1 else str(row)
        for key, _, channel in members:
            index = order.index(key)
            times, volts = window_samples(channel, t_min, t_max)
            in_window = max(in_window, int(times.size))
            t, values = decimate_envelope(times, volts, max_points)
            drawn = max(drawn, int(t.size))
            data.append(
                {
                    "type": "scattergl",
                    "mode": "lines",
                    "name": key,
                    "x": _emit(t / scale, _AXIS_DIGITS),
                    "y": _emit(values),
                    "line": {"color": _colour(index), "width": 1.2},
                    "xaxis": f"x{axis}",
                    "yaxis": f"y{axis}",
                    "hovertemplate": (
                        f"{key}<br>%{{x:.4g}} {unit}<br>%{{y:.5g}} "
                        f"{channel.unit}<extra></extra>"
                    ),
                }
            )
        bottom = row == n_rows
        figure_layout[f"xaxis{axis}"] = {
            "automargin": True,
            "showticklabels": bottom,
            # Only the bottom row is labelled; a title on every row would sit
            # on the row beneath it.
            **({"title": {"text": f"time ({unit})"}} if bottom else {}),
            # Zoom on any subplot moves them all: the point of stacking these
            # is to compare the same instant across instruments.
            **({} if row == 1 else {"matches": "x"}),
        }
        figure_layout[f"yaxis{axis}"] = {
            "automargin": True,
            "title": {"text": _row_title(group_name, members), "standoff": 6},
            "zeroline": True,
        }

    if n_rows > 1:
        figure_layout["grid"] = {
            "rows": n_rows,
            "columns": 1,
            "pattern": "independent",
            "roworder": "top to bottom",
            # Without a gap the rows share an edge, so one row's tick labels
            # land on the next row's traces.
            "ygap": 0.28,
        }
    figure_layout["height"] = stacked_height(n_rows)

    if revision is not None:
        figure_layout["uirevision"] = revision
    figure_layout["meta"] = {
        "time_scale": scale,
        "time_unit": unit,
        "t_min": t_min,
        "t_max": t_max,
        "samples_in_window": in_window,
        "drawn_points": drawn,
        # False means every sample in the window reached the browser.
        "decimated": drawn < in_window,
    }
    return {"data": data, "layout": figure_layout}


def _row_title(group_name: str, members: Sequence[tuple[str, str, Channel]]) -> str:
    """A row label short enough not to crowd the tick labels beside it."""
    unit = _unit_of(members)
    if not group_name:
        return unit
    # "siglent:C1" reads as "C1 (V)" once the row is already that channel's.
    short = group_name.split(":", 1)[-1] if len(members) == 1 else group_name
    return f"{short} ({unit})"


def _emit(values: np.ndarray, digits: int = 6) -> list[float]:
    """Render an array for JSON, rounded to a sane number of digits.

    ``float64`` reprs are about 19 characters each, and a refresh sends tens
    of thousands of them over what may well be a slow link to the bench.
    Rounding to a fixed number of decimals -- chosen from the array's own
    magnitude, so ``digits`` is significant digits -- roughly halves that, and
    the digits dropped are well below both the ADC's resolution and the
    screen's. The full-precision arrays are what every measurement and export
    uses; this is only what gets drawn.
    """
    array = np.asarray(values, dtype=np.float64)
    finite = array[np.isfinite(array)]
    largest = float(np.max(np.abs(finite))) if finite.size else 0.0
    if largest > 0.0:
        decimals = int(digits - 1 - np.floor(np.log10(largest)))
        array = np.round(array, min(max(decimals, -12), 15))
    return array.tolist()


#: Axis values (time, frequency) keep more digits than sample values: a time
#: axis spans microseconds at picosecond resolution, so six digits would
#: quantise it coarser than the sample interval.
_AXIS_DIGITS = 10


def _envelope_at(
    x: np.ndarray, y: np.ndarray, edges: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Keep each bin's minimum and maximum sample, in x order.

    The same idea as :func:`~cetal_scopes.plotting.decimate_envelope`, but with
    the bin edges given rather than uniform, which is what a log-frequency
    axis needs.
    """
    kept: list[int] = []
    for first, last in pairwise(edges):
        start, end = int(first), int(last)
        if end <= start:
            continue
        segment = y[start:end]
        if not np.any(np.isfinite(segment)):
            # An all-gap bin stays a gap: a transfer function is NaN where it
            # is undefined, and joining across that would draw a line through
            # frequencies that have no answer.
            kept.append(start)
            continue
        low = start + int(np.nanargmin(segment))
        high = start + int(np.nanargmax(segment))
        if low == high:
            kept.append(low)
        else:
            kept.extend(sorted((low, high)))
    if not kept:
        return x, y
    index = np.asarray(kept, dtype=np.intp)
    return x[index], y[index]


def decimate_spectrum(
    freq: np.ndarray,
    values: np.ndarray,
    max_points: int,
    *,
    log_x: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a spectrum to at most ``max_points`` samples for drawing.

    A full-rate spectrum is half the record: a 262144-sample capture gives
    131072 points *per channel*, and six of those is an 18 MB JSON response
    that the browser then has to parse and lay out. Nothing on a 1000-pixel
    axis can show it, so the line is binned here instead.

    Each bin keeps its minimum and its maximum, so a narrow spur still reaches
    its true height and the width of the noise floor stays visible -- striding
    would quietly drop both. Bins are geometric when the axis is logarithmic,
    so the decades below the Nyquist frequency keep their detail.

    Analysis always runs on the full spectrum; this only affects what is sent
    to be drawn.
    """
    freq = np.asarray(freq, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    n = freq.size
    if max_points < 8 or n <= max_points:
        return freq, values

    n_bins = max(4, max_points // 2)
    if log_x and freq[0] > 0.0:
        edge_values = np.geomspace(freq[0], freq[-1], n_bins + 1)
        edges = np.searchsorted(freq, edge_values).astype(np.intp)
        edges[0], edges[-1] = 0, n
        edges = np.maximum.accumulate(edges)
    else:
        edges = np.linspace(0, n, n_bins + 1).astype(np.intp)
    return _envelope_at(freq, values, edges)


def _unit_of(members: Sequence[tuple[str, str, Channel]]) -> str:
    units = {channel.unit for _, _, channel in members}
    return units.pop() if len(units) == 1 else "value"


#: A spectrum bin of exactly zero has no dB value, and a record with a flat
#: stretch produces them. Each trace is floored this far below its own peak,
#: which is past anything a real measurement resolves.
DB_FLOOR_RATIO = 1e-10

#: Absolute floor for the converted values. A dead channel -- all zeros, a
#: disconnected input -- would otherwise land thousands of dB down and drag
#: the shared autoscaled y-axis with it, squashing every real trace on the
#: panel into a line.
DB_MIN = -400.0


def to_decibels(values: np.ndarray, *, power: bool) -> np.ndarray:
    """Convert a spectrum to dB relative to one unit of whatever it is in.

    ``power`` selects the factor: a PSD is already a power quantity
    (``10 log10``), an amplitude is not (``20 log10``). Getting this wrong
    halves or doubles every number on the axis, which is why it is a
    parameter rather than a constant.
    """
    array = np.asarray(values, dtype=np.float64)
    peak = float(np.max(array)) if array.size else 0.0
    if peak <= 0.0:
        return np.full(array.shape, DB_MIN, dtype=np.float64)
    factor = 10.0 if power else 20.0
    converted = factor * np.log10(np.maximum(array, peak * DB_FLOOR_RATIO))
    return np.maximum(converted, DB_MIN)


def fft_figure(
    shot: Shot,
    *,
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
    window: str = "hann",
    psd: bool = False,
    log_x: bool = True,
    log_y: bool = True,
    db: bool = False,
    f_min: float | None = None,
    f_max: float | None = None,
    annotate_peak: bool = True,
    max_points: int = 3000,
) -> dict[str, Any]:
    """One-sided spectrum of every selected channel, on shared axes.

    ``f_min`` and ``f_max`` bound the drawn band. They are applied *before*
    the drawing budget, so narrowing to a band spends the whole budget on
    that band rather than showing a binned slice of the full span.

    ``db`` converts to dB relative to one unit of whatever the channels are
    in, and then a logarithmic y-axis would be a log of a log, so ``log_y``
    is ignored.

    ``max_points`` is the per-trace drawing budget; see
    :func:`decimate_spectrum` for what it does and why it is not optional in
    practice. The peak annotation is measured on the full spectrum, before
    any binning, so it reports the real peak rather than a binned one.
    """
    selected = list(_iter_channels(shot, channels))
    if not selected:
        return empty_figure("No channels to transform.")

    data: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    units: set[str] = set()
    for index, (key, _, recorded) in enumerate(selected):
        channel = (processed or {}).get(key, recorded)
        if channel.n_samples < 2:
            continue
        spectrum = _fft(channel, window=window)
        freq = spectrum.freq
        values = spectrum.psd if psd else spectrum.amplitude
        units.add(channel.unit)
        if db:
            values = to_decibels(values, power=psd)

        mask = freq > 0 if log_x else np.ones(freq.shape, dtype=bool)
        if f_min is not None:
            mask &= freq >= f_min
        if f_max is not None:
            mask &= freq <= f_max
        if not mask.any():
            continue

        visible_freq = freq[mask]
        visible_values = values[mask]
        drawn_freq, drawn_values = decimate_spectrum(
            visible_freq, visible_values, max_points, log_x=log_x
        )
        data.append(
            {
                "type": "scattergl",
                "mode": "lines",
                "name": key,
                "x": _emit(drawn_freq, _AXIS_DIGITS),
                "y": _emit(drawn_values),
                "line": {"color": _colour(index), "width": 1.2},
                "hovertemplate": f"{key}<br>%{{x:.5g}} Hz<br>%{{y:.5g}}<extra></extra>",
            }
        )
        if annotate_peak:
            peak_index = int(np.argmax(visible_values))
            annotations.append(
                {
                    "x": float(visible_freq[peak_index]),
                    "y": float(visible_values[peak_index]),
                    "text": f"{visible_freq[peak_index]:.4g} Hz",
                    "showarrow": True,
                    "arrowhead": 2,
                    "arrowsize": 0.7,
                    # One label per channel, and their peaks are often on top
                    # of each other; stack the labels so they stay readable.
                    "ax": 0,
                    "ay": -16 - 15 * len(annotations),
                    "yanchor": "bottom",
                    "font": {"size": 10, "color": _colour(index)},
                    "arrowcolor": _colour(index),
                    "xref": "x",
                    "yref": "y",
                }
            )

    if not data:
        return empty_figure("Nothing in the selected frequency range.")

    return {
        "data": data,
        "layout": _base_layout(
            "",
            xaxis={
                "title": {"text": "frequency (Hz)"},
                "type": "log" if log_x else "linear",
            },
            yaxis={
                "title": {"text": _spectrum_axis_title(units, psd=psd, db=db)},
                # dB is already logarithmic; a log axis on top of it would be
                # a log of a log.
                "type": "log" if (log_y and not db) else "linear",
            },
            annotations=annotations,
            height=PLOT_HEIGHT,
        ),
    }


def _spectrum_axis_title(units: set[str], *, psd: bool, db: bool) -> str:
    """Name the y-axis, including its dB reference when there is one.

    The unit comes from the channels themselves rather than from
    ``Spectrum.amplitude_unit``, which :func:`cetal_scopes.analysis.fft` does
    not currently set from the channel -- so a pipeline that turned volts into
    teslas would otherwise be labelled volts. Channels of different units
    overlaid on one axis have no single unit to name, so none is claimed.
    """
    unit = next(iter(units)) if len(units) == 1 else None
    if db:
        reference = f" re 1 {unit}{'^2/Hz' if psd and unit else ''}" if unit else ""
        return f"dB{reference}"
    if psd:
        return f"PSD ({unit}^2/Hz)" if unit else "PSD"
    return f"amplitude ({unit})" if unit else "amplitude"


def spectrogram_figure(
    shot: Shot,
    key: str,
    *,
    processed: dict[str, Channel] | None = None,
    segment_samples: int | None = None,
    window: str = "hann",
    db: bool = True,
    max_freq_bins: int = 600,
) -> dict[str, Any]:
    """Short-time spectrum of one channel, as a heatmap.

    Raises
    ------
    KeyError
        If ``key`` is not a channel of this shot.
    """
    channels = shot.aligned_channels()
    if key not in channels:
        raise KeyError(key)
    channel = (processed or {}).get(key, channels[key])
    if channel.n_samples < 16:
        return empty_figure("Record too short for a spectrogram.")

    segment = segment_samples or max(16, 2 ** int(np.log2(channel.n_samples / 16)))
    segment = min(segment, channel.n_samples)
    spectrogram = stft(channel, segment_samples=segment, window=window)

    amplitude = np.asarray(spectrogram.amplitude, dtype=np.float64)
    freq = np.asarray(spectrogram.freq, dtype=np.float64)
    # A long record gives thousands of frequency bins, and a heatmap cell
    # smaller than a screen pixel costs a megabyte to say nothing. Bin the
    # frequency axis down by taking each bin's maximum, so a narrow line
    # keeps its height instead of being averaged into the floor.
    if freq.size > max_freq_bins:
        edges = np.linspace(0, freq.size, max_freq_bins + 1).astype(np.intp)
        groups = [
            (int(first), int(last)) for first, last in pairwise(edges) if last > first
        ]
        amplitude = np.stack(
            [amplitude[:, first:last].max(axis=1) for first, last in groups], axis=1
        )
        freq = np.asarray([freq[first:last].mean() for first, last in groups])
    if db:
        floor = float(np.max(amplitude)) * 1e-6 or 1e-18
        z = 20.0 * np.log10(np.maximum(amplitude, floor))
        colorbar_title = "dB"
    else:
        z = amplitude
        colorbar_title = channel.unit

    scale, unit = time_scale(float(spectrogram.times[-1] - spectrogram.times[0]) or 1.0)
    return {
        "data": [
            {
                "type": "heatmap",
                "x": _emit(np.asarray(spectrogram.times) / scale, _AXIS_DIGITS),
                "y": _emit(freq, _AXIS_DIGITS),
                "z": [_emit(row, 4) for row in z.T],
                "colorscale": "Viridis",
                "colorbar": {"title": {"text": colorbar_title}},
                "hovertemplate": (
                    f"%{{x:.4g}} {unit}<br>%{{y:.5g}} Hz<br>%{{z:.4g}}<extra></extra>"
                ),
            }
        ],
        "layout": _base_layout(
            key,
            showlegend=False,
            xaxis={"title": {"text": f"time ({unit})"}},
            yaxis={"title": {"text": "frequency (Hz)"}},
            height=PLOT_HEIGHT,
        ),
    }


def _pair(
    shot: Shot, a: str, b: str, processed: dict[str, Channel] | None
) -> tuple[Channel, Channel]:
    channels = shot.aligned_channels()
    for key in (a, b):
        if key not in channels:
            raise KeyError(key)
    return (
        (processed or {}).get(a, channels[a]),
        (processed or {}).get(b, channels[b]),
    )


def coherence_figure(
    shot: Shot,
    a: str,
    b: str,
    *,
    processed: dict[str, Channel] | None = None,
    segment_samples: int | None = None,
    max_points: int = 3000,
) -> dict[str, Any]:
    """Magnitude-squared coherence between two channels of the shot."""
    left, right = _pair(shot, a, b, processed)
    spectrum = _coherence(left, right, segment_samples=segment_samples)
    freq, values = decimate_spectrum(
        spectrum.freq, spectrum.psd, max_points, log_x=True
    )
    return {
        "data": [
            {
                "type": "scattergl",
                "mode": "lines",
                "name": f"{a} vs {b}",
                "x": _emit(freq, _AXIS_DIGITS),
                "y": _emit(values),
                "line": {"color": _colour(0), "width": 1.2},
                "hovertemplate": "%{x:.5g} Hz<br>%{y:.3f}<extra></extra>",
            }
        ],
        "layout": _base_layout(
            f"coherence: {a} vs {b}",
            showlegend=False,
            xaxis={"title": {"text": "frequency (Hz)"}, "type": "log"},
            yaxis={"title": {"text": "coherence"}, "range": [0, 1.05]},
            height=PLOT_HEIGHT,
        ),
    }


def transfer_figure(
    shot: Shot,
    a: str,
    b: str,
    *,
    processed: dict[str, Channel] | None = None,
    segment_samples: int | None = None,
    max_points: int = 3000,
) -> dict[str, Any]:
    """Gain and phase from ``a`` to ``b``, with the coherence beneath them.

    The coherence row is not decoration: a transfer function has a value at
    every frequency whether or not the two channels are related there, and the
    coherence is what says which of those values to believe.
    """
    left, right = _pair(shot, a, b, processed)
    freq, gain = _transfer_function(left, right, segment_samples=segment_samples)
    spectrum = _coherence(left, right, segment_samples=segment_samples)

    finite = np.isfinite(gain)
    magnitude = np.where(finite, np.abs(gain), np.nan)
    phase = np.where(finite, np.degrees(np.angle(gain)), np.nan)

    def trace(y: np.ndarray, name: str, axis: str, colour: str) -> dict[str, Any]:
        x, values = decimate_spectrum(freq, y, max_points, log_x=True)
        return {
            "type": "scattergl",
            "mode": "lines",
            "name": name,
            "x": _emit(x, _AXIS_DIGITS),
            "y": [None if np.isnan(value) else value for value in _emit(values)],
            "line": {"color": colour, "width": 1.2},
            "xaxis": "x",
            "yaxis": axis,
            "hovertemplate": f"{name}<br>%{{x:.5g}} Hz<br>%{{y:.4g}}<extra></extra>",
        }

    return {
        "data": [
            trace(magnitude, "|H|", "y", _colour(0)),
            trace(phase, "phase (deg)", "y2", _colour(1)),
            trace(spectrum.psd, "coherence", "y3", _colour(2)),
        ],
        "layout": _base_layout(
            f"transfer: {a} -> {b}",
            grid={"rows": 3, "columns": 1, "pattern": "independent"},
            height=stacked_height(3),
            xaxis={"title": {"text": "frequency (Hz)"}, "type": "log"},
            yaxis={"title": {"text": "|H|"}, "type": "log"},
            yaxis2={"title": {"text": "phase (deg)"}, "matches": None},
            yaxis3={"title": {"text": "coherence"}, "range": [0, 1.05]},
        ),
    }


def xy_figure(
    shot: Shot,
    a: str,
    b: str,
    *,
    processed: dict[str, Channel] | None = None,
    max_points: int = 6000,
) -> dict[str, Any]:
    """One channel against another, on a shared time grid.

    The two channels are resampled onto the shot's common grid first, so this
    works across instruments with different sample rates.
    """
    left, right = _pair(shot, a, b, processed)
    grid = shot.common_time(dt=min(left.dt, right.dt))
    x = _resample_onto(left, grid)
    y = _resample_onto(right, grid)

    valid = np.isfinite(x.volts) & np.isfinite(y.volts)
    x_values = x.volts[valid]
    y_values = y.volts[valid]
    if x_values.size == 0:
        return empty_figure("The two channels do not overlap in time.")
    if x_values.size > max_points:
        step = int(np.ceil(x_values.size / max_points))
        x_values = x_values[::step]
        y_values = y_values[::step]

    return {
        "data": [
            {
                "type": "scattergl",
                "mode": "lines",
                "name": f"{b} vs {a}",
                "x": _emit(x_values),
                "y": _emit(y_values),
                "line": {"color": _colour(0), "width": 1.0},
                "hovertemplate": "%{x:.5g}<br>%{y:.5g}<extra></extra>",
            }
        ],
        "layout": _base_layout(
            "",
            showlegend=False,
            xaxis={"title": {"text": f"{a} ({x.unit})"}},
            yaxis={
                "title": {"text": f"{b} ({y.unit})"},
                "scaleanchor": "x",
                "scaleratio": 1,
            },
            # Equal-aspect axes: without a fixed height Plotly satisfies the
            # ratio by growing the plot rather than by narrowing the x range.
            height=PLOT_HEIGHT,
        ),
    }


def measurement_table(
    shot: Shot,
    *,
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
) -> list[dict[str, Any]]:
    """Per-channel statistics and pulse measurements, as table rows."""
    rows: list[dict[str, Any]] = []
    for key, _, recorded in _iter_channels(shot, channels):
        channel = (processed or {}).get(key, recorded)
        if channel.n_samples < 2:
            continue
        summary = stats(channel)
        pulse = pulse_metrics(channel)
        rows.append(
            {
                "channel": key,
                "unit": channel.unit,
                "n_samples": summary.n_samples,
                "mean": summary.mean,
                "rms": summary.rms,
                "peak_to_peak": summary.peak_to_peak,
                "peak_value": summary.peak_value,
                "peak_time": summary.peak_time,
                "base": pulse.base,
                "top": pulse.top,
                "amplitude": pulse.amplitude,
                "rise_time": _finite(pulse.rise_time),
                "fall_time": _finite(pulse.fall_time),
                "width": _finite(pulse.width),
                "fwhm": _finite(pulse.fwhm),
                "overshoot": _finite(pulse.overshoot),
            }
        )
    return rows


def _finite(value: float) -> float | None:
    """``None`` for a measurement the waveform did not support, so JSON is valid."""
    return None if not np.isfinite(value) else float(value)


def vector_figure(
    shot: Shot,
    keys: Sequence[str],
    frequency: float,
    *,
    processed: dict[str, Channel] | None = None,
) -> dict[str, Any]:
    """A phase-correct field vector at ``frequency``, drawn in 3-D.

    Unlike the obvious approach of plotting three FFT magnitudes -- which can
    only ever point into the ``+++`` octant, since magnitudes are
    non-negative -- this uses the complex amplitudes, so a component pointing
    the other way is drawn pointing the other way.

    All three channels must come from the same capture: relative phase is only
    meaningful on a shared timebase.

    Raises
    ------
    ValueError
        If fewer than three channels are given, or they span more than one
        capture.
    KeyError
        If a key is not a channel of this shot.
    """
    names = list(keys)
    if len(names) != 3:
        raise ValueError(f"a field vector needs exactly three channels, got {names!r}")

    labels = {key.split(":", 1)[0] for key in names}
    if len(labels) != 1:
        raise ValueError(
            "the three channels must come from one capture; relative phase is "
            f"only meaningful on a shared timebase, got {sorted(labels)!r}"
        )
    label = labels.pop()
    if label not in shot.captures:
        raise KeyError(label)

    capture = shot.captures[label]
    channel_names = [key.split(":", 1)[1] for key in names]
    if processed:
        # vector_at reads from a Capture, so rebuild one from the processed
        # channels rather than silently analysing the unprocessed record.
        rows = [processed[key].volts for key in names]
        lengths = {len(row) for row in rows}
        if len(lengths) != 1:
            raise ValueError("processing left the three channels different lengths")
        first = processed[names[0]]
        antennas: dict[str, Antenna] = {}
        for name, key in zip(channel_names, names, strict=True):
            antenna = processed[key].antenna
            if antenna is not None:
                antennas[name] = antenna
        capture = Capture(
            volts=np.vstack(rows),
            t0=first.t0,
            dt=first.dt,
            channel_names=tuple(channel_names),
            antennas=antennas,
        )

    vector = vector_at(capture, frequency, channels=tuple(channel_names))
    components = vector.lab_vector

    return {
        "data": [
            {
                "type": "scatter3d",
                "mode": "lines+markers",
                "name": f"{frequency:.4g} Hz",
                "x": [0.0, float(components[0])],
                "y": [0.0, float(components[1])],
                "z": [0.0, float(components[2])],
                "line": {"color": _colour(0), "width": 6},
                "marker": {"size": [2, 6], "color": _colour(0)},
                "hovertemplate": "%{x:.4g}, %{y:.4g}, %{z:.4g}<extra></extra>",
            }
        ],
        "layout": _base_layout(
            f"|v| = {vector.magnitude:.4g} at {frequency:.4g} Hz "
            f"(ellipticity {vector.ellipticity:.2f})",
            showlegend=False,
            scene={
                "xaxis": {"title": {"text": channel_names[0]}},
                "yaxis": {"title": {"text": channel_names[1]}},
                "zaxis": {"title": {"text": channel_names[2]}},
                "aspectmode": "data",
            },
            height=PLOT_HEIGHT + 80,
        ),
    }
