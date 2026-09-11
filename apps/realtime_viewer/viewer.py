"""Real-time oscilloscope viewer: live signal and FFT panes.

Downstream application built on :mod:`cetal_scopes`; it contains no reusable
library code. It repeatedly acquires from a scope and redraws a two-pane
matplotlib window -- the signal versus time on top, its amplitude spectrum
below.

Against an SDS6204L over LAN::

    uv run apps/realtime_viewer/viewer.py --address 192.168.5.197 --channels C1 C2

Without hardware, from a synthetic source::

    uv run apps/realtime_viewer/viewer.py --demo --remove-comb
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from cetal_scopes import Capture, Channel, SiglentSDS6204L
from cetal_scopes.analysis import fft, remove_adc_comb
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import DEFAULT_ADDRESS

COMB_PERIOD = 256

#: FFT window names accepted as a plain string by scipy.signal.get_window.
WINDOW_NAMES: tuple[str, ...] = (
    "boxcar",
    "rect",
    "rectangular",
    "triang",
    "blackman",
    "hamming",
    "hann",
    "bartlett",
    "flattop",
    "parzen",
    "bohman",
    "blackmanharris",
    "nuttall",
    "barthann",
    "cosine",
    "exponential",
    "tukey",
    "lanczos",
)

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


def _robust_peak(channel: Channel) -> float:
    """Peak deviation from the median, robust to a few outlier samples."""
    values = np.asarray(channel.volts, dtype=np.float64)
    if values.size == 0:
        return 0.0
    baseline = float(np.median(values))
    return float(np.percentile(np.abs(values - baseline), 99.9))


def auto_range_vdiv(
    scope: Scope,
    channels: Sequence[str],
    *,
    divisions: float = 4.0,
    rounds: int = 2,
) -> Capture:
    """Pick a V/div for each channel that fits the signal.

    Starts from the coarsest step (so a clipped first capture cannot fool the
    estimate) and then sets the most sensitive step that keeps the robust peak
    within ``divisions`` vertical divisions. Returns the last capture.
    """
    capture: Capture | None = None
    for index in range(rounds):
        if index == 0:
            scope.configure(
                {"vertical": {name: {"scale": VDIV_LADDER[-1]} for name in channels}}
            )
        capture = scope.acquire()
        scales = {}
        for name in channels:
            if name in capture:
                scales[name] = {
                    "scale": snap_vdiv(_robust_peak(capture[name]) / divisions)
                }
        scope.configure({"vertical": scales})
    if capture is None:  # pragma: no cover - rounds is at least 1
        raise RuntimeError("auto-range made no acquisition")
    return capture


def _vdiv_arg(value: str) -> float | str:
    """argparse type for ``--vdiv``: a number, or the string ``"auto"``."""
    if value.strip().lower() == "auto":
        return "auto"
    try:
        return float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected a number or 'auto', got {value!r}"
        ) from exc


@dataclass(frozen=True)
class FrameResult:
    """Per-channel summary of one drawn frame."""

    channel: str
    peak_frequency: float
    peak_amplitude: float
    rms: float


class RealtimeViewer:
    """Two-pane live view: signal versus time, and amplitude spectrum.

    Parameters
    ----------
    channels : sequence of str
        Channel names to draw.
    window : str
        FFT window name, any accepted by :func:`scipy.signal.get_window`.
    log_frequency : bool
        Use a logarithmic frequency axis (DC is dropped).
    log_amplitude : bool
        Use a logarithmic amplitude axis; set false for a linear axis.
    frequency_max : float, optional
        Upper frequency limit in Hz; defaults to each capture's Nyquist.
    remove_comb : bool
        Subtract the ADC comb before the FFT.
    comb_period : int
        Comb period in samples passed to
        :func:`cetal_scopes.analysis.remove_adc_comb`.
    amplitude_floor : float
        Smallest amplitude drawn on the log axis (avoids ``log(0)``).
    """

    def __init__(
        self,
        channels: Sequence[str],
        *,
        window: str = "hann",
        log_frequency: bool = True,
        log_amplitude: bool = True,
        frequency_max: float | None = None,
        remove_comb: bool = False,
        comb_period: int = COMB_PERIOD,
        amplitude_floor: float = 1e-9,
    ) -> None:
        if not channels:
            raise ValueError("at least one channel is required")
        self.channels = tuple(channels)
        self.window = window
        self.log_frequency = log_frequency
        self.log_amplitude = log_amplitude
        self.frequency_max = frequency_max
        self.remove_comb = remove_comb
        self.comb_period = comb_period
        self.amplitude_floor = amplitude_floor

        self.figure: Figure
        self.ax_time: Axes
        self.ax_fft: Axes
        self.figure, (self.ax_time, self.ax_fft) = plt.subplots(
            2, 1, figsize=(10.0, 7.0), layout="constrained"
        )
        self.time_lines: dict[str, Line2D] = {}
        self.fft_lines: dict[str, Line2D] = {}
        for name in self.channels:
            (time_line,) = self.ax_time.plot([], [], label=name, lw=1.0)
            self.time_lines[name] = time_line
            (fft_line,) = self.ax_fft.plot([], [], label=name, lw=1.0)
            self.fft_lines[name] = fft_line

        self.ax_time.set_ylabel("signal (V)")
        self.ax_time.grid(True, alpha=0.3)
        self.ax_fft.set_xlabel("frequency (Hz)")
        self.ax_fft.set_ylabel("amplitude (V)")
        self.ax_fft.grid(True, which="both", alpha=0.3)
        self.ax_fft.set_yscale("log" if self.log_amplitude else "linear")
        if self.log_frequency:
            self.ax_fft.set_xscale("log")
        if len(self.channels) > 1:
            self.ax_time.legend(loc="upper right")
            self.ax_fft.legend(loc="upper right")

    def update(self, capture: Capture) -> list[FrameResult]:
        """Redraw both panes from ``capture`` and return per-channel peaks."""
        results: list[FrameResult] = []
        for name in self.channels:
            if name not in capture:
                continue
            channel = capture[name]
            unit, scale = time_unit_for_span(channel.n_samples * channel.dt)
            self.ax_time.set_xlabel(f"time ({unit})")
            self.time_lines[name].set_data(channel.time / scale, channel.volts)

            source = (
                remove_adc_comb(channel, period=self.comb_period)
                if self.remove_comb
                else channel
            )
            spectrum = fft(source, window=self.window)
            peak_index = int(np.argmax(spectrum.amplitude))
            results.append(
                FrameResult(
                    channel=name,
                    peak_frequency=float(spectrum.freq[peak_index]),
                    peak_amplitude=float(spectrum.amplitude[peak_index]),
                    rms=float(np.sqrt(np.mean(channel.volts**2))),
                )
            )

            freq = spectrum.freq
            amplitude = np.maximum(spectrum.amplitude, self.amplitude_floor)
            if self.log_frequency:
                freq = freq[1:]
                amplitude = amplitude[1:]
            self.fft_lines[name].set_data(freq, amplitude)

        self._rescale()
        return results

    def _rescale(self) -> None:
        self.ax_time.relim()
        self.ax_time.autoscale_view()
        if self.frequency_max is not None:
            self.ax_fft.set_xlim(left=None, right=self.frequency_max)
        self.ax_fft.relim()
        self.ax_fft.autoscale_view()
        low, high = self.ax_fft.get_ylim()
        if low <= 0.0 or not math.isfinite(low):
            self.ax_fft.set_ylim(
                self.amplitude_floor, max(high, self.amplitude_floor * 10.0)
            )

    def show(self) -> None:
        """Show the window without blocking."""
        self.figure.show()

    def draw(self) -> None:
        """Schedule a canvas redraw."""
        self.figure.canvas.draw_idle()

    def close(self) -> None:
        """Close the viewer window."""
        plt.close(self.figure)


class DemoScope(Scope):
    """Synthetic free-running source used by ``--demo``.

    Emits a tone per channel plus a deterministic comb pattern so the
    ``--remove-comb`` flag has something to remove.
    """

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
        self._phase = 0.0
        self._comb = 0.02 * np.sin(
            2.0 * np.pi * np.arange(COMB_PERIOD, dtype=np.float64) / COMB_PERIOD
        )

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def configure(self, settings: Mapping[str, object]) -> None:
        """No-op; the demo source is not configurable."""

    def acquire(self) -> Capture:
        """Return one synthetic record and advance the trigger phase."""
        time = np.arange(self.n_samples, dtype=np.float64) * self.dt
        self._phase += 0.37 * self.dt
        phase = np.arange(self.n_samples) % COMB_PERIOD
        rows = []
        for index, _ in enumerate(self.channels):
            frequency = 1.0e6 * float(index + 1)
            tone = 0.1 * np.sin(2.0 * np.pi * frequency * (time + self._phase))
            noise = 0.002 * self._rng.standard_normal(self.n_samples)
            rows.append(tone + noise + self._comb[phase])
        return Capture(
            volts=np.vstack(rows),
            t0=float(self._phase),
            dt=self.dt,
            channel_names=self.channels,
        )

    def close(self) -> None:
        """No-op; safe to call repeatedly."""


def run(
    scope: Scope,
    viewer: RealtimeViewer,
    *,
    interval: float = 0.2,
    frames: int | None = None,
    show: bool = True,
    pause: Callable[[float], None] = plt.pause,
) -> int:
    """Acquire and redraw until ``frames`` are drawn, the window is closed
    or interrupted.

    Returns the number of frames drawn. ``show`` and ``pause`` are injectable
    so the loop can be driven headlessly in tests. The interval is clamped to at
    least one millisecond because ``matplotlib.pyplot.pause(0)`` blocks forever.
    """
    plt.ion()
    if show:
        viewer.show()
    drawn = 0
    try:
        while frames is None or drawn < frames:
            if not plt.fignum_exists(viewer.figure.number):
                break
            capture = scope.acquire()
            viewer.update(capture)
            viewer.draw()
            pause(max(interval, 0.001))
            drawn += 1
    except KeyboardInterrupt:
        pass
    return drawn


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="realtime_viewer",
        description="Live time-domain and FFT viewer for a cetal_scopes driver.",
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS, help="scope IP for the raw socket"
    )
    parser.add_argument(
        "--channels", nargs="+", default=["C1"], help="channels, e.g. C1 C2"
    )
    parser.add_argument(
        "--interval", type=float, default=0.2, help="seconds between frames"
    )
    parser.add_argument(
        "--frames", type=int, default=None, help="stop after N frames (default: run)"
    )
    parser.add_argument(
        "--window",
        choices=WINDOW_NAMES,
        default="hann",
        help="FFT window (default: hann)",
    )
    parser.add_argument(
        "--frequency-max", type=float, default=None, help="FFT upper x-limit in Hz"
    )
    parser.add_argument(
        "--linear-frequency", action="store_true", help="linear FFT frequency axis"
    )
    parser.add_argument(
        "--linear-y", action="store_true", help="linear FFT amplitude axis"
    )
    parser.add_argument(
        "--remove-comb", action="store_true", help="subtract the ADC comb before FFT"
    )
    parser.add_argument(
        "--comb-period", type=int, default=COMB_PERIOD, help="comb period in samples"
    )
    parser.add_argument(
        "--timebase", type=float, default=None, help="horizontal scale in s/div"
    )
    parser.add_argument(
        "--vdiv",
        type=_vdiv_arg,
        default=None,
        metavar="V|auto",
        help=(
            "vertical scale in V/div for every channel, or 'auto' to range once. "
            "Acceptable values are the 1-2-5 ladder "
            f"({', '.join(f'{s:g}' for s in VDIV_LADDER)}); the scope snaps to "
            "the nearest step. Max is 10 V/div at 1 MOhm, 1 V/div at 50 Ohm."
        ),
    )
    parser.add_argument(
        "--impedance",
        choices=("1M", "50"),
        default="1M",
        help="input impedance for every channel (default: 1M; 50 Ohm is fragile)",
    )
    parser.add_argument("--trigger-mode", default="AUTO", help="SINGle, AUTO or NORMal")
    parser.add_argument(
        "--sample-width",
        choices=("BYTE", "WORD"),
        default="WORD",
        help="transfer width",
    )
    parser.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        help="leave the scope running between frames (default: stop per frame)",
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
        sample_width=args.sample_width,
        streaming=args.stream,
        trigger_mode=args.trigger_mode,
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
    """Parse arguments, connect, and run the viewer."""
    args = build_parser().parse_args(argv)
    _warn_if_non_interactive()
    viewer = RealtimeViewer(
        args.channels,
        window=args.window,
        log_frequency=not args.linear_frequency,
        log_amplitude=not args.linear_y,
        frequency_max=args.frequency_max,
        remove_comb=args.remove_comb,
        comb_period=args.comb_period,
    )
    scope = _make_scope(args)
    try:
        with scope:
            if args.timebase is not None:
                scope.configure({"timebase": args.timebase})
            if args.impedance is not None:
                scope.configure(
                    {
                        "vertical": {
                            ch: {"impedance": args.impedance} for ch in args.channels
                        }
                    }
                )
            if args.vdiv == "auto":
                auto_range_vdiv(scope, args.channels)
            elif args.vdiv is not None:
                scale = snap_vdiv(float(args.vdiv))
                if not math.isclose(scale, float(args.vdiv)):
                    print(
                        f"vdiv {args.vdiv:g} snapped to {scale:g} V/div",
                        file=sys.stderr,
                    )
                scope.configure(
                    {"vertical": {ch: {"scale": scale} for ch in args.channels}}
                )
            run(scope, viewer, interval=args.interval, frames=args.frames)
    finally:
        viewer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
