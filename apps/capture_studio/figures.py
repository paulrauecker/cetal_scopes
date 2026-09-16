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
    "empty_figure",
    "fft_figure",
    "measurement_table",
    "spectrogram_figure",
    "time_figure",
    "transfer_figure",
    "vector_figure",
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


def _base_layout(title: str, **overrides: Any) -> dict[str, Any]:
    layout: dict[str, Any] = {
        "title": {"text": title},
        "margin": {"l": 70, "r": 20, "t": 44, "b": 52},
        "hovermode": "closest",
        "showlegend": True,
        "legend": {"orientation": "h", "y": -0.18},
        "template": "plotly_white",
    }
    layout.update(overrides)
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


def time_figure(
    shot: Shot,
    *,
    layout: LayoutMode = "overlay",
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
    max_points: int = 4000,
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

    Returns
    -------
    dict
        A Plotly figure specification.
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

    span = max((channel.n_samples - 1) * channel.dt for _, _, channel in resolved)
    scale, unit = time_scale(span)

    data: list[dict[str, Any]] = []
    figure_layout = _base_layout("")
    n_rows = len(groups)

    for row, (group_name, members) in enumerate(groups, start=1):
        axis = "" if row == 1 else str(row)
        for key, _, channel in members:
            index = [item[0] for item in resolved].index(key)
            t, values = decimate_envelope(channel.time, channel.volts, max_points)
            data.append(
                {
                    "type": "scattergl",
                    "mode": "lines",
                    "name": key,
                    "x": (t / scale).tolist(),
                    "y": values.tolist(),
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
            "title": {"text": f"time ({unit})"} if bottom else None,
            "showticklabels": bottom,
            # Zoom on any subplot moves them all: the point of stacking these
            # is to compare the same instant across instruments.
            **({} if row == 1 else {"matches": "x"}),
        }
        figure_layout[f"yaxis{axis}"] = {
            "title": {"text": group_name or _unit_of(members)},
            "zeroline": True,
        }

    if n_rows > 1:
        figure_layout["grid"] = {
            "rows": n_rows,
            "columns": 1,
            "pattern": "independent",
            "roworder": "top to bottom",
        }
        figure_layout["height"] = max(280, 170 * n_rows)
    return {"data": data, "layout": figure_layout}


def _unit_of(members: Sequence[tuple[str, str, Channel]]) -> str:
    units = {channel.unit for _, _, channel in members}
    return units.pop() if len(units) == 1 else "value"


def fft_figure(
    shot: Shot,
    *,
    channels: Sequence[str] | None = None,
    processed: dict[str, Channel] | None = None,
    window: str = "hann",
    psd: bool = False,
    log_x: bool = True,
    log_y: bool = True,
    f_min: float | None = None,
    f_max: float | None = None,
    annotate_peak: bool = True,
) -> dict[str, Any]:
    """One-sided spectrum of every selected channel, on shared axes."""
    selected = list(_iter_channels(shot, channels))
    if not selected:
        return empty_figure("No channels to transform.")

    data: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for index, (key, _, recorded) in enumerate(selected):
        channel = (processed or {}).get(key, recorded)
        if channel.n_samples < 2:
            continue
        spectrum = _fft(channel, window=window)
        freq = spectrum.freq
        values = spectrum.psd if psd else spectrum.amplitude

        mask = freq > 0 if log_x else np.ones(freq.shape, dtype=bool)
        if f_min is not None:
            mask &= freq >= f_min
        if f_max is not None:
            mask &= freq <= f_max
        if not mask.any():
            continue

        data.append(
            {
                "type": "scattergl",
                "mode": "lines",
                "name": key,
                "x": freq[mask].tolist(),
                "y": values[mask].tolist(),
                "line": {"color": _colour(index), "width": 1.2},
                "hovertemplate": f"{key}<br>%{{x:.5g}} Hz<br>%{{y:.5g}}<extra></extra>",
            }
        )
        if annotate_peak:
            peak_index = int(np.argmax(values[mask]))
            annotations.append(
                {
                    "x": float(freq[mask][peak_index]),
                    "y": float(values[mask][peak_index]),
                    "text": f"{freq[mask][peak_index]:.4g} Hz",
                    "showarrow": True,
                    "arrowhead": 2,
                    "arrowsize": 0.7,
                    "font": {"size": 10, "color": _colour(index)},
                    "arrowcolor": _colour(index),
                    "xref": "x",
                    "yref": "y",
                }
            )

    if not data:
        return empty_figure("Nothing in the selected frequency range.")

    unit = "PSD" if psd else "amplitude"
    return {
        "data": data,
        "layout": _base_layout(
            "",
            xaxis={
                "title": {"text": "frequency (Hz)"},
                "type": "log" if log_x else "linear",
            },
            yaxis={"title": {"text": unit}, "type": "log" if log_y else "linear"},
            annotations=annotations,
        ),
    }


def spectrogram_figure(
    shot: Shot,
    key: str,
    *,
    processed: dict[str, Channel] | None = None,
    segment_samples: int | None = None,
    window: str = "hann",
    db: bool = True,
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
                "x": (np.asarray(spectrogram.times) / scale).tolist(),
                "y": np.asarray(spectrogram.freq).tolist(),
                "z": z.T.tolist(),
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
) -> dict[str, Any]:
    """Magnitude-squared coherence between two channels of the shot."""
    left, right = _pair(shot, a, b, processed)
    spectrum = _coherence(left, right, segment_samples=segment_samples)
    return {
        "data": [
            {
                "type": "scattergl",
                "mode": "lines",
                "name": f"{a} vs {b}",
                "x": spectrum.freq.tolist(),
                "y": spectrum.psd.tolist(),
                "line": {"color": _colour(0), "width": 1.2},
                "hovertemplate": "%{x:.5g} Hz<br>%{y:.3f}<extra></extra>",
            }
        ],
        "layout": _base_layout(
            f"coherence: {a} vs {b}",
            showlegend=False,
            xaxis={"title": {"text": "frequency (Hz)"}, "type": "log"},
            yaxis={"title": {"text": "coherence"}, "range": [0, 1.05]},
        ),
    }


def transfer_figure(
    shot: Shot,
    a: str,
    b: str,
    *,
    processed: dict[str, Channel] | None = None,
    segment_samples: int | None = None,
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
        return {
            "type": "scattergl",
            "mode": "lines",
            "name": name,
            "x": freq.tolist(),
            "y": [None if np.isnan(value) else float(value) for value in y],
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
            height=520,
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
                "x": x_values.tolist(),
                "y": y_values.tolist(),
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
            height=460,
        ),
    }
