"""Acquisition and figure building for the B-dot web app.

This module has **no Dash imports** so it can be tested headlessly. The Dash
layout and callbacks live in :mod:`bdot_web`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from cetal_scopes import Antenna, Capture, Channel, Scope, TransferFunction
from cetal_scopes.analysis import b_field, b_field_rate, b_magnitude

__all__ = [
    "AxisReading",
    "ProbeSession",
    "ScopeConfig",
    "ShotResult",
    "analyse_capture",
    "band_for_channel",
    "decimate",
    "nominal_transfer_function",
    "time_unit_for_span",
    "trigger_level_warning",
    "trigger_message",
]

#: Orientation labels applied to channels in order.
DEFAULT_LABELS: tuple[str, ...] = ("X", "Y", "Z", "W")

_TIME_UNITS: tuple[tuple[str, float], ...] = (
    ("s", 1.0),
    ("ms", 1e-3),
    ("\u00b5s", 1e-6),
    ("ns", 1e-9),
    ("ps", 1e-12),
)


def time_unit_for_span(span: float) -> tuple[str, float]:
    """Return a readable ``(unit, seconds_per_unit)`` for a span in seconds."""
    for unit, scale in _TIME_UNITS:
        if span >= scale:
            return unit, scale
    return _TIME_UNITS[-1]


def decimate(
    time: np.ndarray, values: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Stride a trace down to at most ``max_points`` for drawing.

    Peaks are computed on the full record, so this only affects what is drawn.
    """
    if max_points <= 0 or values.size <= max_points:
        return time, values
    step = int(np.ceil(values.size / max_points))
    return time[::step], values[::step]


def nominal_transfer_function(
    sensitivity: float, *, f_min: float, f_max: float
) -> TransferFunction:
    """A flat, real placeholder transfer function ``V/(T/s)`` on ``[f_min, f_max]``.

    ``f_min`` matters: the field functions integrate with ``1/f``, so including
    arbitrarily low frequencies lets low-frequency noise dominate ``B``.
    """
    if not math.isfinite(sensitivity) or sensitivity <= 0.0:
        raise ValueError(f"sensitivity must be positive, got {sensitivity!r}")
    if not (0.0 < f_min < f_max):
        raise ValueError(f"expected 0 < f_min < f_max, got {f_min!r}, {f_max!r}")
    return TransferFunction(
        freq=[f_min, f_max],
        gain=[complex(sensitivity), complex(sensitivity)],
        unit="V/(T/s)",
    )


def band_for_channel(
    channel: Channel,
    *,
    f_min: float | None = None,
    f_max: float | None = None,
) -> tuple[float, float]:
    """Pick a calibration band from a record's bin spacing and Nyquist.

    ``f_min`` defaults to ten times the DFT bin spacing ``df = 1 / (n * dt)``,
    which keeps the ``1/f`` integration from amplifying the lowest bins;
    ``f_max`` defaults to Nyquist.
    """
    nyquist = 0.5 / channel.dt
    df = 1.0 / (channel.n_samples * channel.dt)
    low = 10.0 * df if f_min is None else float(f_min)
    high = nyquist if f_max is None else float(f_max)
    low = min(low, 0.5 * high)
    return low, high


def trigger_level_warning(requested: float, actual: float) -> str | None:
    """Return a warning when the scope clamped the trigger level.

    The SDS6204L clamps the edge-trigger level to about ``+/-4.5 * V/div`` of the
    source channel, so a level above the on-screen range is pinned near the
    noise floor. Returns ``None`` when the requested level was applied.
    """
    tolerance = max(1.0e-9, 0.05 * abs(requested))
    if abs(actual - requested) <= tolerance:
        return None
    return (
        f"trigger level {requested:g} V clamped to {actual:g} V by the vertical "
        "range (about +/-4.5 V/div); use a coarser vdiv or a vertical offset"
    )


@dataclass(frozen=True)
class AxisReading:
    """Peak values measured on one channel during a shot."""

    channel: str
    label: str
    peak_rate: float
    peak_field: float


@dataclass(frozen=True)
class ShotResult:
    """Summary of one analysed shot."""

    axis: tuple[AxisReading, ...]
    magnitude_peak: float
    band: tuple[float, float]
    n_samples: int
    dt: float


def analyse_capture(
    capture: Capture,
    channels: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    sensitivity: float = 1.0,
    fmin: float | None = None,
    fmax: float | None = None,
    max_points: int = 20000,
) -> tuple[go.Figure, ShotResult]:
    """Calibrate ``capture`` and build an interactive two-pane figure.

    Returns the plotly figure and a :class:`ShotResult` summary.
    """
    present = [name for name in channels if name in capture]
    if not present:
        raise ValueError("capture has none of the requested channels")
    axis_labels = (
        tuple(labels) if labels is not None else DEFAULT_LABELS[: len(channels)]
    )
    if len(axis_labels) != len(channels):
        raise ValueError("labels must match channels one-to-one")

    low, high = band_for_channel(capture[present[0]], f_min=fmin, f_max=fmax)
    transfer = nominal_transfer_function(sensitivity, f_min=low, f_max=high)
    antennas = {
        name: Antenna(name=label, kind="b-dot", transfer_function=transfer)
        for name, label in zip(channels, axis_labels)
        if name in present
    }
    annotated = capture.with_antennas(antennas)

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        subplot_titles=("probe response dB/dt", "integrated field B"),
    )
    fields = []
    readings: list[AxisReading] = []
    span = capture[present[0]].n_samples * capture[present[0]].dt
    for name in present:
        channel = annotated[name]
        label = antennas[name].name
        rate = b_field_rate(channel, outside="zero", detrend=False)
        field = b_field(channel, outside="zero", detrend=False)
        fields.append(field)

        unit, scale = time_unit_for_span(channel.n_samples * channel.dt)
        rate_time, rate_values = decimate(channel.time / scale, rate.volts, max_points)
        field_time, field_values = decimate(
            channel.time / scale, field.volts, max_points
        )
        figure.add_trace(
            go.Scattergl(
                x=rate_time,
                y=rate_values,
                name=label,
                mode="lines",
                legendgroup=label,
                hovertemplate=f"{label} %{{y:.3e}} T/s<extra></extra>",
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scattergl(
                x=field_time,
                y=field_values,
                name=label,
                mode="lines",
                legendgroup=label,
                showlegend=False,
                hovertemplate=f"{label} %{{y:.3e}} T<extra></extra>",
            ),
            row=2,
            col=1,
        )
        readings.append(
            AxisReading(
                channel=name,
                label=label,
                peak_rate=float(np.max(np.abs(rate.volts))),
                peak_field=float(np.max(np.abs(field.volts))),
            )
        )

    magnitude_peak = 0.0
    if fields:
        magnitude = b_magnitude(*fields)
        unit, scale = time_unit_for_span(magnitude.n_samples * magnitude.dt)
        mag_time, mag_values = decimate(
            magnitude.time / scale, magnitude.volts, max_points
        )
        magnitude_peak = float(np.max(np.abs(magnitude.volts)))
        figure.add_trace(
            go.Scattergl(
                x=mag_time,
                y=mag_values,
                name="|B|",
                mode="lines",
                line={"color": "black", "dash": "dash", "width": 2},
                hovertemplate="|B| %{y:.3e} T<extra></extra>",
            ),
            row=2,
            col=1,
        )

    unit, _ = time_unit_for_span(span)
    figure.update_xaxes(title_text=f"time ({unit})", row=2, col=1)
    figure.update_yaxes(title_text="dB/dt (T/s)", row=1, col=1)
    figure.update_yaxes(title_text="B (T)", row=2, col=1)
    figure.update_layout(
        template="plotly_white",
        height=760,
        margin={"l": 70, "r": 20, "t": 70, "b": 45},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 1.0, "xanchor": "right"},
        title=(
            f"shot |B| peak = {magnitude_peak:.3e} T  "
            f"(nominal {sensitivity:g} V/(T/s), band {low:.3g}-{high:.3g} Hz)"
        ),
        uirevision="keepview",
    )
    result = ShotResult(
        axis=tuple(readings),
        magnitude_peak=magnitude_peak,
        band=(low, high),
        n_samples=capture[present[0]].n_samples,
        dt=capture[present[0]].dt,
    )
    return figure, result


def empty_figure(message: str = "armed - waiting for a shot") -> go.Figure:
    """A blank two-pane figure used before the first shot."""
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.09,
        subplot_titles=("probe response dB/dt", "integrated field B"),
    )
    figure.update_layout(
        template="plotly_white",
        height=760,
        margin={"l": 70, "r": 20, "t": 70, "b": 45},
        title=message,
        hovermode="x unified",
    )
    figure.update_xaxes(title_text="time", row=2, col=1)
    figure.update_yaxes(title_text="dB/dt (T/s)", row=1, col=1)
    figure.update_yaxes(title_text="B (T)", row=2, col=1)
    return figure


def trigger_message(
    shot: int,
    capture: Capture,
    result: ShotResult,
    trigger_channel: str | None,
) -> str:
    """Console/log line for a captured shot (timestamp + peak amplitudes)."""
    parts = [f"[{time.strftime('%H:%M:%S')}] trigger: captured shot {shot}"]
    if trigger_channel is not None and trigger_channel in capture:
        peak = float(np.max(np.abs(capture[trigger_channel].volts)))
        parts.append(f"{trigger_channel} peak={peak:.3e} V")
    parts.append(f"|B|peak={result.magnitude_peak:.3e} T")
    return "  ".join(parts)


@dataclass
class ScopeConfig:
    """Scope/session settings mirrored by the web form."""

    address: str
    channels: tuple[str, ...] = ("C1", "C2", "C3")
    labels: tuple[str, ...] | None = None
    sensitivity: float = 1.0
    fmin: float | None = None
    fmax: float | None = None
    trigger_channel: str | None = None
    trigger_level: float = 0.0
    trigger_slope: str = "RISing"
    trigger_timeout: float = 5.0
    timebase: float | None = None
    vdiv: float | None = None
    impedance: str = "1M"
    max_points: int = 20000
    demo: bool = False


class ProbeSession:
    """Server-side state: the connected scope, config, log and last figure."""

    def __init__(
        self,
        config: ScopeConfig,
        *,
        scope_factory: Callable[[ScopeConfig], Scope] | None = None,
    ) -> None:
        self.config = config
        self._scope_factory = scope_factory or default_scope_factory
        self.scope: Scope | None = None
        self.shot = 0
        self.log: list[str] = []
        self.last_figure: go.Figure | None = None
        self.last_result: ShotResult | None = None

    def disconnect(self) -> None:
        """Close the scope, if connected."""
        if self.scope is not None:
            self.scope.close()
            self.scope = None

    def arm(self) -> str:
        """(Re)connect and apply the current configuration."""
        self.disconnect()
        self.scope = self._scope_factory(self.config)
        self.scope.connect()
        self._apply(self.config)
        status = f"armed on {self.config.address} (trigger {self._trigger_channel()})"
        warning = self._trigger_warning()
        if warning is not None:
            status += f" — WARNING: {warning}"
            self.log.insert(0, f"WARNING: {warning}")
            del self.log[50:]
        return status

    def _trigger_warning(self) -> str | None:
        if self.config.demo or self.scope is None:
            return None
        read_level = cast(
            "Callable[[], float] | None", getattr(self.scope, "trigger_level", None)
        )
        if read_level is None:
            return None
        return trigger_level_warning(self.config.trigger_level, read_level())

    def _trigger_channel(self) -> str:
        return self.config.trigger_channel or self.config.channels[0]

    def _apply(self, config: ScopeConfig) -> None:
        scope = self.scope
        if scope is None:  # pragma: no cover - callers connect first
            raise RuntimeError("scope is not connected")
        if config.timebase is not None:
            scope.configure({"timebase": config.timebase})
        scope.configure(
            {
                "vertical": {
                    name: {"impedance": config.impedance} for name in config.channels
                }
            }
        )
        if config.vdiv is not None:
            scope.configure(
                {"vertical": {name: {"scale": config.vdiv} for name in config.channels}}
            )
        if not config.demo:
            scope.configure(
                {
                    "trigger_mode": "SINGle",
                    "trigger": {
                        "source": self._trigger_channel(),
                        "level": config.trigger_level,
                        "slope": config.trigger_slope,
                    },
                }
            )

    def capture_once(self) -> tuple[go.Figure, ShotResult] | None:
        """Acquire one shot and build its figure, or ``None`` on trigger timeout."""
        if self.scope is None:
            self.arm()
        if self.scope is None:  # pragma: no cover - arm raises otherwise
            raise RuntimeError("scope is not connected")
        try:
            capture = self.scope.acquire()
        except TimeoutError as exc:
            if "did not complete" not in str(exc):
                raise
            return None
        self.shot += 1
        figure, result = analyse_capture(
            capture,
            self.config.channels,
            labels=self.config.labels,
            sensitivity=self.config.sensitivity,
            fmin=self.config.fmin,
            fmax=self.config.fmax,
            max_points=self.config.max_points,
        )
        self.last_figure = figure
        self.last_result = result
        self.log.insert(
            0,
            trigger_message(self.shot, capture, result, self._trigger_channel()),
        )
        del self.log[50:]
        return figure, result


def default_scope_factory(config: ScopeConfig) -> Scope:
    """Build either the demo source or a real SDS6204L from ``config``."""
    if config.demo:
        return DemoScope(channels=config.channels)
    from cetal_scopes import SiglentSDS6204L

    return SiglentSDS6204L(
        address=config.address,
        channels=config.channels,
        trigger_mode="SINGle",
        acquire_timeout=config.trigger_timeout,
    )


class DemoScope(Scope):
    """Synthetic B-dot source: a bipolar pulse per channel, per shot."""

    def __init__(
        self,
        *,
        channels: Sequence[str] = ("C1",),
        n_samples: int = 8192,
        dt: float = 1e-8,
        seed: int = 0,
    ) -> None:
        self.channels = tuple(channels)
        self.n_samples = n_samples
        self.dt = dt
        self._rng = np.random.default_rng(seed)

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def configure(self, settings: Mapping[str, object]) -> None:
        """No-op; the demo source is not configurable."""

    def acquire(self) -> Capture:
        """Return a synthetic derivative-of-Gaussian pulse per channel."""
        time = np.arange(self.n_samples, dtype=np.float64) * self.dt
        center = 0.5 * (self.n_samples - 1) * self.dt
        width = 0.08 * self.n_samples * self.dt
        rows = []
        for index, _ in enumerate(self.channels):
            amplitude = 1.0e-3 * float(index + 1)
            envelope = amplitude * np.exp(-(((time - center) / width) ** 2))
            derivative = -2.0 * (time - center) / width**2 * envelope
            noise = 1.0e-6 * self._rng.standard_normal(self.n_samples)
            rows.append(derivative + noise)
        return Capture(
            volts=np.vstack(rows),
            t0=0.0,
            dt=self.dt,
            channel_names=self.channels,
        )

    def close(self) -> None:
        """No-op; safe to call repeatedly."""
