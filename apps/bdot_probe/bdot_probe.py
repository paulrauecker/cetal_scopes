"""Live B-dot probe display: ``dB/dt`` and integrated ``B`` for a triad.

Downstream application built on :mod:`cetal_scopes`; it contains no reusable
library code. It acquires repeatedly from a scope, assumes a flat nominal
sensitivity for each B-dot, and draws two live panes: the probe response
``dB/dt`` on top and the integrated field ``B`` (plus ``|B|``) below. Peak
values are latched so a moved magnet is easy to see; press ``r`` to reset the
latch, ``q`` to quit.

A B-dot measures the field's *time derivative*, so a stationary magnet reads
zero -- move or rotate the magnet to induce a signal.

By default the app runs in **triggered single-shot** mode: it arms an edge
trigger on the first channel, waits for the magnet pulse to cross the trigger
level, fetches that one record and **holds it on screen for analysis**. Press
space (or `n`) to re-arm for another shot; every trigger is printed to the
console. Set `--continuous` to re-arm automatically, or `--trigger-mode AUTO`
for the old free-running behaviour. For the fastest, cleanest shots use a short
`--timebase` (e.g. `0.005` s/div); at a seconds-long timebase each record is
slow to fetch.

Against an SDS6204L over LAN::

    uv run apps/bdot_probe/bdot_probe.py --address 192.168.5.197 --channels C1 C2 C3

Without hardware, from a synthetic source::

    uv run apps/bdot_probe/bdot_probe.py --demo
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.backend_bases import KeyEvent
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from cetal_scopes import Antenna, Capture, Channel, SiglentSDS6204L, TransferFunction
from cetal_scopes.analysis import b_field, b_field_rate, b_magnitude
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import DEFAULT_ADDRESS

#: Default sensor sensitivity, volts per (tesla/second). Nominal only -- replace
#: with the probe's measured value once you have one.
DEFAULT_SENSITIVITY = 1.0

#: Orientation labels applied to ``--channels`` in order.
DEFAULT_LABELS: tuple[str, ...] = ("X", "Y", "Z", "W")

#: SDS6204L vertical-scale ladder (V/div), 1-2-5 sequence.
VDIV_LADDER: tuple[float, ...] = (
    0.0005,
    0.001,
    0.002,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
)

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


def snap_vdiv(value: float) -> float:
    """Snap ``value`` up to the next valid V/div step (clamped to the ends)."""
    for step in VDIV_LADDER:
        if step >= value:
            return step
    return VDIV_LADDER[-1]


def decimate(
    time: np.ndarray, values: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Stride a trace down to at most ``max_points`` for drawing.

    Peaks are computed on the full record, so this only affects what is
    plotted, not what is measured.
    """
    if max_points <= 0 or values.size <= max_points:
        return time, values
    step = int(np.ceil(values.size / max_points))
    return time[::step], values[::step]


def nominal_transfer_function(
    sensitivity: float, *, f_min: float, f_max: float
) -> TransferFunction:
    """A flat, real transfer function ``V/(T/s)`` over ``[f_min, f_max]``.

    This is a placeholder for a real probe calibration: every frequency in the
    band is scaled by the same ``sensitivity`` and no phase is applied.
    ``f_min`` matters: the field functions integrate with ``1/f``, so including
    arbitrarily low frequencies lets low-frequency noise dominate ``B``. Use a
    low edge that reflects the probe's real low-frequency cutoff (or the
    record's frequency resolution).
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
    """Pick a calibration band from a record's frequency resolution/Nyquist.

    ``f_min`` defaults to ten times the DFT bin spacing ``df = 1 / (n * dt)``,
    which keeps the ``1/f`` integration from amplifying the lowest, least
    trustworthy bins; ``f_max`` defaults to Nyquist.
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
    source channel, so a level above the on-screen range is silently pinned near
    the noise floor. Returns ``None`` when the requested level was applied.
    """
    tolerance = max(1.0e-9, 0.05 * abs(requested))
    if abs(actual - requested) <= tolerance:
        return None
    return (
        f"trigger level {requested:g} V clamped to {actual:g} V by the vertical "
        "range (about +/-4.5 V/div); use a coarser --vdiv or a vertical offset"
    )


@dataclass(frozen=True)
class AxisReading:
    """Peak values measured on one channel during a frame."""

    channel: str
    label: str
    peak_rate: float
    peak_field: float


@dataclass(frozen=True)
class FrameResult:
    """Summary of one drawn frame."""

    axis: tuple[AxisReading, ...]
    magnitude_peak: float


class BdotProbe:
    """Two-pane live view of ``dB/dt`` (top) and ``B`` and ``|B|`` (bottom)."""

    def __init__(
        self,
        channels: Sequence[str],
        *,
        labels: Sequence[str] | None = None,
        sensitivity: float = DEFAULT_SENSITIVITY,
        max_points: int = 20000,
        fmin: float | None = None,
        fmax: float | None = None,
    ) -> None:
        if not channels:
            raise ValueError("at least one channel is required")
        self.channels = tuple(channels)
        self.labels = (
            tuple(labels) if labels is not None else DEFAULT_LABELS[: len(channels)]
        )
        if len(self.labels) != len(self.channels):
            raise ValueError("labels must match channels one-to-one")
        self.sensitivity = sensitivity
        self.max_points = max_points
        self.fmin = fmin
        self.fmax = fmax
        self.band: tuple[float, float] | None = None
        self.status = "armed"
        self.arm_requested = False

        self.figure: Figure
        self.ax_rate: Axes
        self.ax_field: Axes
        self.figure, (self.ax_rate, self.ax_field) = plt.subplots(
            2, 1, figsize=(10.0, 7.0), layout="constrained"
        )
        self.rate_lines: dict[str, Line2D] = {}
        self.field_lines: dict[str, Line2D] = {}
        for name, label in zip(self.channels, self.labels):
            (rate_line,) = self.ax_rate.plot([], [], label=label, lw=1.0)
            self.rate_lines[name] = rate_line
            (field_line,) = self.ax_field.plot([], [], label=label, lw=1.0)
            self.field_lines[name] = field_line
        (self.magnitude_line,) = self.ax_field.plot(
            [], [], label="|B|", lw=1.8, color="black", linestyle="--"
        )

        self.ax_rate.set_ylabel("dB/dt (T/s)")
        self.ax_field.set_ylabel("B (T)")
        for axis in (self.ax_rate, self.ax_field):
            axis.set_xlabel("time (s)")
            axis.grid(True, alpha=0.3)
            axis.legend(loc="upper right")

        self.peak_rate: dict[str, float] = {name: 0.0 for name in self.channels}
        self.peak_field: dict[str, float] = {name: 0.0 for name in self.channels}
        self.peak_magnitude = 0.0
        self._update_title()

        self._key_cid = self.figure.canvas.mpl_connect("key_press_event", self._on_key)

    def update(self, capture: Capture) -> FrameResult:
        """Recalibrate ``capture`` and redraw both panes."""
        present = [name for name in self.channels if name in capture]
        if present:
            low, high = band_for_channel(
                capture[present[0]], f_min=self.fmin, f_max=self.fmax
            )
            self.band = (low, high)
            transfer = nominal_transfer_function(
                self.sensitivity, f_min=low, f_max=high
            )
        else:
            transfer = nominal_transfer_function(
                self.sensitivity, f_min=1.0, f_max=1.0e12
            )
        antennas = {
            name: Antenna(
                name=label,
                kind="b-dot",
                transfer_function=transfer,
            )
            for name, label in zip(self.channels, self.labels)
            if name in present
        }
        annotated = capture.with_antennas(antennas)

        fields = []
        readings: list[AxisReading] = []
        for name in present:
            channel = annotated[name]
            rate = b_field_rate(channel, outside="zero", detrend=False)
            field = b_field(channel, outside="zero", detrend=False)
            fields.append(field)

            unit, scale = time_unit_for_span(channel.n_samples * channel.dt)
            self.ax_rate.set_xlabel(f"time ({unit})")
            self.ax_field.set_xlabel(f"time ({unit})")
            rate_time, rate_values = decimate(
                channel.time / scale, rate.volts, self.max_points
            )
            field_time, field_values = decimate(
                channel.time / scale, field.volts, self.max_points
            )
            self.rate_lines[name].set_data(rate_time, rate_values)
            self.field_lines[name].set_data(field_time, field_values)

            peak_rate = float(np.max(np.abs(rate.volts)))
            peak_field = float(np.max(np.abs(field.volts)))
            self.peak_rate[name] = max(self.peak_rate[name], peak_rate)
            self.peak_field[name] = max(self.peak_field[name], peak_field)
            readings.append(
                AxisReading(
                    channel=name,
                    label=antennas[name].name,
                    peak_rate=peak_rate,
                    peak_field=peak_field,
                )
            )

        magnitude_peak = 0.0
        if fields:
            magnitude = b_magnitude(*fields)
            unit, scale = time_unit_for_span(magnitude.n_samples * magnitude.dt)
            mag_time, mag_values = decimate(
                magnitude.time / scale, magnitude.volts, self.max_points
            )
            self.magnitude_line.set_data(mag_time, mag_values)
            magnitude_peak = float(np.max(np.abs(magnitude.volts)))
            self.peak_magnitude = max(self.peak_magnitude, magnitude_peak)

        self.status = "shot"
        self._rescale()
        self._update_title()
        return FrameResult(axis=tuple(readings), magnitude_peak=magnitude_peak)

    def set_status(self, status: str) -> None:
        """Set the status line shown in the window title."""
        self.status = status
        self._update_title()

    def reset(self) -> None:
        """Clear the latched peak values."""
        self.peak_rate = {name: 0.0 for name in self.channels}
        self.peak_field = {name: 0.0 for name in self.channels}
        self.peak_magnitude = 0.0
        self._update_title()

    def _update_title(self) -> None:
        band = (
            ""
            if self.band is None
            else f"  band {self.band[0]:.3g}-{self.band[1]:.3g} Hz"
        )
        self.figure.suptitle(
            f"[{self.status}] latched peak |B| = {self.peak_magnitude:.3e} T   "
            f"(nominal sensitivity {self.sensitivity:g} V/(T/s){band}; "
            "space=arm, r=reset, q=quit)"
        )

    def _rescale(self) -> None:
        for axis in (self.ax_rate, self.ax_field):
            axis.relim()
            axis.autoscale_view()

    def _on_key(self, event: KeyEvent) -> None:
        if event.key == "r":
            self.reset()
        elif event.key in (" ", "n"):
            self.arm_requested = True
        elif event.key in ("q", "escape"):
            plt.close(self.figure)

    def show(self) -> None:
        """Show the window without blocking."""
        self.figure.show()

    def draw(self) -> None:
        """Schedule a canvas redraw."""
        self.figure.canvas.draw_idle()

    def close(self) -> None:
        """Close the probe window."""
        plt.close(self.figure)


class DemoScope(Scope):
    """Synthetic B-dot source: a bipolar pulse per channel, per frame."""

    def __init__(
        self,
        *,
        channels: Sequence[str] = ("C1",),
        n_samples: int = 4096,
        dt: float = 1e-7,
        seed: int = 0,
    ) -> None:
        self.channels = tuple(channels)
        self.n_samples = n_samples
        self.dt = dt
        self._rng = np.random.default_rng(seed)
        self._frame = 0

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def configure(self, settings: Mapping[str, object]) -> None:
        """No-op; the demo source is not configurable."""

    def acquire(self) -> Capture:
        """Return one synthetic derivative-of-Gaussian pulse per channel."""
        time = np.arange(self.n_samples, dtype=np.float64) * self.dt
        center = 0.5 * (self.n_samples - 1) * self.dt
        width = 0.08 * self.n_samples * self.dt
        self._frame += 1
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


def trigger_message(
    shot: int,
    capture: Capture,
    result: FrameResult,
    trigger_channel: str | None,
) -> str:
    """Format the console line logged for a captured shot.

    Includes the trigger channel's raw peak (volts, the quantity that crossed
    the level) and the integrated peak ``|B|``, so a real pulse can be told
    from a noise trigger.
    """
    parts = [f"[{time.strftime('%H:%M:%S')}] trigger: captured shot {shot}"]
    if trigger_channel is not None and trigger_channel in capture:
        peak = float(np.max(np.abs(capture[trigger_channel].volts)))
        parts.append(f"{trigger_channel} peak={peak:.3e} V")
    parts.append(f"|B|peak={result.magnitude_peak:.3e} T")
    return "  ".join(parts)


def run(
    scope: Scope,
    probe: BdotProbe,
    *,
    interval: float = 0.2,
    frames: int | None = None,
    hold: bool = True,
    trigger_channel: str | None = None,
    show: bool = True,
    pause: Callable[[float], None] = plt.pause,
) -> int:
    """Acquire and redraw until the window closes or interrupted.

    With ``hold`` (the default) the loop captures **one** shot, keeps it on
    screen for analysis, and waits for the user to press space (or ``n``)
    before re-arming; ``hold=False`` re-arms continuously. ``frames`` limits
    the number of captured shots; once reached, a holding loop keeps the window
    open while a non-holding loop returns.

    In triggered single-shot mode ``scope.acquire`` blocks until the trigger
    fires or its timeout expires; a timeout is treated as "no shot yet", so the
    loop keeps polling and the window stays responsive. Each shot is announced
    on stdout with ``trigger_channel``'s raw peak and the integrated ``|B|``.
    ``show`` and ``pause`` are injectable so the loop can run headlessly in
    tests. The interval is clamped to at least one millisecond because
    ``matplotlib.pyplot.pause(0)`` blocks forever.
    """
    plt.ion()
    if show:
        probe.show()
    drawn = 0
    armed = True
    try:
        while plt.fignum_exists(probe.figure.number):
            if armed and (frames is None or drawn < frames):
                try:
                    capture = scope.acquire()
                except TimeoutError as exc:
                    if "did not complete" not in str(exc):
                        raise
                    probe.set_status("armed - waiting for trigger")
                    probe.draw()
                    pause(max(interval, 0.001))
                    continue
                result = probe.update(capture)
                drawn += 1
                print(
                    trigger_message(drawn, capture, result, trigger_channel),
                    flush=True,
                )
                probe.draw()
                if frames is not None and drawn >= frames:
                    if not hold:
                        break
                    armed = False
                    probe.set_status("done - q to quit")
                elif hold:
                    armed = False
                    probe.set_status("shot - press space to arm")
                else:
                    pause(max(interval, 0.001))
                continue

            if not armed:
                probe.arm_requested = False
                while (
                    plt.fignum_exists(probe.figure.number) and not probe.arm_requested
                ):
                    pause(max(interval, 0.001))
                if frames is not None and drawn >= frames:
                    continue
                armed = True
                continue

            pause(max(interval, 0.001))
    except KeyboardInterrupt:
        pass
    return drawn


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="bdot_probe",
        description="Live dB/dt and integrated B from a B-dot triad.",
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS, help="scope IP for the raw socket"
    )
    parser.add_argument(
        "--channels",
        nargs="+",
        default=["C1", "C2", "C3"],
        help="channels, e.g. C1 C2 C3",
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        default=None,
        help="orientation label per channel (default: X Y Z)",
    )
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=DEFAULT_SENSITIVITY,
        help="nominal flat sensitivity in V per (T/s)",
    )
    parser.add_argument(
        "--fmin",
        type=float,
        default=None,
        help="calibration low band edge in Hz (default: 10x the DFT bin spacing)",
    )
    parser.add_argument(
        "--fmax",
        type=float,
        default=None,
        help="calibration high band edge in Hz (default: Nyquist)",
    )
    parser.add_argument(
        "--interval", type=float, default=0.2, help="seconds between frames"
    )
    parser.add_argument(
        "--frames", type=int, default=None, help="stop after N captured shots"
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="re-arm immediately after each shot instead of holding for analysis",
    )
    parser.add_argument(
        "--timebase", type=float, default=None, help="horizontal scale in s/div"
    )
    parser.add_argument(
        "--vdiv",
        type=float,
        default=None,
        metavar="V",
        help="vertical scale in V/div for every channel (snapped to the 1-2-5 ladder)",
    )
    parser.add_argument(
        "--impedance",
        choices=("1M", "50"),
        default="1M",
        help="input impedance for every channel (default: 1M)",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=20000,
        help="maximum points drawn per trace (peaks use the full record)",
    )
    parser.add_argument(
        "--trigger-mode",
        choices=("SINGle", "AUTO", "NORMal"),
        default="SINGle",
        help="sweep mode: one triggered shot, free-running, or normal (default: SINGle)",
    )
    parser.add_argument(
        "--trigger-channel",
        default=None,
        help="edge-trigger source (default: first channel)",
    )
    parser.add_argument(
        "--trigger-level",
        type=float,
        default=0.0,
        help="edge-trigger level in volts (default: 0)",
    )
    parser.add_argument(
        "--trigger-slope",
        choices=("RISing", "FALLing"),
        default="RISing",
        help="edge-trigger slope (default: RISing)",
    )
    parser.add_argument(
        "--trigger-timeout",
        type=float,
        default=5.0,
        help="seconds to wait for a trigger before repolling (default: 5)",
    )
    parser.add_argument(
        "--demo", action="store_true", help="run against a synthetic source"
    )
    return parser


def _make_scope(args: argparse.Namespace) -> Scope:
    if args.demo:
        return DemoScope(channels=tuple(args.channels))
    return SiglentSDS6204L(
        address=args.address,
        channels=tuple(args.channels),
        trigger_mode=args.trigger_mode,
        acquire_timeout=args.trigger_timeout,
    )


_NON_INTERACTIVE_BACKENDS = frozenset(
    {"agg", "cairo", "pdf", "pgf", "ps", "svg", "template"}
)


def _warn_if_non_interactive() -> None:
    backend = matplotlib.get_backend()
    if backend.lower() in _NON_INTERACTIVE_BACKENDS:
        print(
            f"warning: matplotlib is using the non-interactive {backend!r} "
            "backend, so no window will open. For a GUI, run inside the dev "
            "shell (Tk is provided there) or set MPLBACKEND to an interactive "
            "backend.",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, connect, and run the probe."""
    args = build_parser().parse_args(argv)
    _warn_if_non_interactive()
    probe = BdotProbe(
        args.channels,
        labels=args.labels,
        sensitivity=args.sensitivity,
        max_points=args.max_points,
        fmin=args.fmin,
        fmax=args.fmax,
    )
    scope = _make_scope(args)
    try:
        with scope:
            if args.timebase is not None:
                scope.configure({"timebase": args.timebase})
            scope.configure(
                {
                    "vertical": {
                        ch: {"impedance": args.impedance} for ch in args.channels
                    }
                }
            )
            if args.vdiv is not None:
                scale = snap_vdiv(float(args.vdiv))
                if not math.isclose(scale, float(args.vdiv)):
                    print(
                        f"vdiv {args.vdiv:g} snapped to {scale:g} V/div",
                        file=sys.stderr,
                    )
                scope.configure(
                    {"vertical": {ch: {"scale": scale} for ch in args.channels}}
                )
            trigger_channel = args.trigger_channel or args.channels[0]
            if args.trigger_mode in ("SINGle", "NORMal"):
                scope.configure(
                    {
                        "trigger_mode": args.trigger_mode,
                        "trigger": {
                            "source": trigger_channel,
                            "level": args.trigger_level,
                            "slope": args.trigger_slope,
                        },
                    }
                )
                read_level = cast(
                    "Callable[[], float] | None",
                    getattr(scope, "trigger_level", None),
                )
                if read_level is not None:
                    note = trigger_level_warning(args.trigger_level, read_level())
                    if note is not None:
                        print(f"warning: {note}", file=sys.stderr)
            run(
                scope,
                probe,
                interval=args.interval,
                frames=args.frames,
                hold=not args.continuous,
                trigger_channel=trigger_channel,
            )
    finally:
        probe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
