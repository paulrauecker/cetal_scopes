"""RF waterfall (spectrogram): frequency vs. time, color = amplitude.

Downstream application built on :mod:`cetal_scopes`; it contains no reusable
library code. Repeatedly acquires a single channel from a scope, computes its
spectrum via :func:`cetal_scopes.analysis.fft`, and stacks spectra over time
into a scrolling waterfall image via
:class:`cetal_scopes.analysis.WaterfallBuffer`.

Three modes, selected with ``--mode``:

- ``dynamic`` (default): a live, continuously-scrolling waterfall in a
  matplotlib window -- a fixed-depth rolling buffer of the most recent
  ``--depth`` sweeps.
- ``record``: acquires exactly ``--sweeps`` **separate** records (optionally
  gated by an edge trigger, the same model as ``apps/bdot_probe``), then
  renders one static waterfall image and optionally saves it and/or a CSV.
- ``transient``: for a single non-repeating event (e.g. an EMP pulse) that
  only triggers once. Acquires **one** triggered record sized to hold the
  whole event, then slices *that one record* into overlapping
  ``--stft-window``-long segments and FFTs each (a short-time Fourier
  transform, via :func:`cetal_scopes.analysis.stft`) to show how the pulse's
  spectral content evolves during the event itself -- unlike ``record``,
  which stacks independent trigger events, not slices of one.

Known hardware caveat: the SDS6204L's 16-bit acquisition path carries a
deterministic ADC-interleave comb (period 256 samples -- see
:func:`cetal_scopes.analysis.remove_adc_comb`). Pass ``--remove-comb`` if comb
spurs are visible as horizontal stripes in the waterfall.

Live waterfall against an SDS6204L over LAN::

    uv run apps/rf_waterfall/rf_waterfall.py --address 192.168.5.197 --channel C1

Fixed recording, triggered, saved to disk::

    uv run apps/rf_waterfall/rf_waterfall.py --mode record --sweeps 200 \\
        --trigger-level 0.01 --output-plot waterfall.png --output-csv waterfall.csv

A single triggered transient (e.g. an EMP event), sliced into a spectrogram::

    uv run apps/rf_waterfall/rf_waterfall.py --mode transient \\
        --timebase 0.001 --trigger-level 0.05 --stft-window 2e-5 \\
        --output-plot emp.png

Without hardware, from a synthetic chirping source::

    uv run apps/rf_waterfall/rf_waterfall.py --demo
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from typing import Literal, cast

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.image import AxesImage

from cetal_scopes import Capture, Channel, SiglentSDS6204L
from cetal_scopes.analysis import (
    Spectrogram,
    Spectrum,
    WaterfallBuffer,
    remove_adc_comb,
    stft,
)
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import DEFAULT_ADDRESS

#: Trend orders accepted by :func:`cetal_scopes.analysis.fft`.
DetrendMode = Literal["constant", "linear"]

COMB_PERIOD = 256
DEFAULT_CHANNEL = "C1"
DEFAULT_DEPTH = 200
DEFAULT_WINDOW = "hann"
DEFAULT_INTERVAL = 0.2
DEFAULT_CMAP = "viridis"
DEFAULT_DB_FLOOR = -120.0


def trigger_level_warning(requested: float, actual: float) -> str | None:
    """Return a warning when the scope clamped the trigger level.

    The SDS6204L clamps the edge-trigger level to about ``+/-4.5 * V/div`` of
    the source channel, so a level above the on-screen range is silently
    pinned near the noise floor. Returns ``None`` when the requested level
    was applied.
    """
    tolerance = max(1.0e-9, 0.05 * abs(requested))
    if abs(actual - requested) <= tolerance:
        return None
    return (
        f"trigger level {requested:g} V clamped to {actual:g} V by the vertical "
        "range (about +/-4.5 V/div); use a coarser --vdiv or a vertical offset"
    )


class WaterfallViewer:
    """Live-updating waterfall: an ``imshow`` of a :class:`WaterfallBuffer`.

    Parameters
    ----------
    depth : int
        Number of rows (most recent sweeps) kept and shown.
    window : str
        FFT window name, any accepted by :func:`scipy.signal.get_window`.
    detrend : {"constant", "linear", None}
        Trend removed before each row's transform.
    db_scale : bool
        Show amplitude in dB (``20*log10``) instead of linear volts.
    db_floor : float
        Smallest value shown on the dB scale; also the reference floor
        amplitude is clamped to before taking the log.
    cmap : str
        Matplotlib colormap name.
    freq_max : float, optional
        Upper frequency limit in Hz shown; defaults to each row's Nyquist.
    remove_comb : bool
        Subtract the ADC comb before each row's FFT.
    comb_period : int
        Comb period in samples passed to
        :func:`cetal_scopes.analysis.remove_adc_comb`.
    """

    def __init__(
        self,
        *,
        depth: int = DEFAULT_DEPTH,
        window: str = DEFAULT_WINDOW,
        detrend: DetrendMode | None = "constant",
        db_scale: bool = True,
        db_floor: float = DEFAULT_DB_FLOOR,
        cmap: str = DEFAULT_CMAP,
        freq_max: float | None = None,
        remove_comb: bool = False,
        comb_period: int = COMB_PERIOD,
    ) -> None:
        self.depth = depth
        self.db_scale = db_scale
        self.db_floor = db_floor
        self.freq_max = freq_max
        self.remove_comb = remove_comb
        self.comb_period = comb_period
        self._buffer = WaterfallBuffer(depth, window=window, detrend=detrend)

        self.figure: Figure
        self.ax: Axes
        self.figure, self.ax = plt.subplots(figsize=(9.0, 6.0), layout="constrained")
        placeholder = np.full((depth, 2), np.nan)
        self.image: AxesImage = self.ax.imshow(
            placeholder,
            origin="lower",
            aspect="auto",
            cmap=cmap,
            extent=(0.0, 1.0, 0.0, float(depth)),
        )
        self.colorbar = self.figure.colorbar(
            self.image,
            ax=self.ax,
            label="amplitude (dB)" if db_scale else "amplitude (V)",
        )
        self.ax.set_xlabel("frequency (Hz)")
        self.ax.set_ylabel("sweep (most recent at top)")

    def update(self, capture: Capture, channel: str) -> Spectrum:
        """Push ``capture[channel]`` into the buffer and redraw the image."""
        source = capture[channel]
        if self.remove_comb:
            source = remove_adc_comb(source, period=self.comb_period)
        spectrum = self._buffer.push(source)
        self._redraw()
        return spectrum

    def to_spectrogram(self) -> Spectrogram:
        """Return the viewer's current buffer contents as a :class:`Spectrogram`."""
        return self._buffer.to_spectrogram()

    def _redraw(self) -> None:
        freq = self._buffer.freq
        grid = self._buffer.amplitude
        if self.freq_max is not None and freq.size:
            mask = freq <= self.freq_max
            freq = freq[mask]
            grid = grid[:, mask]
        if self.db_scale:
            floor_linear = 10.0 ** (self.db_floor / 20.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                grid = 20.0 * np.log10(np.maximum(grid, floor_linear))
            grid = np.clip(grid, self.db_floor, None)
        masked = np.ma.masked_invalid(grid)
        self.image.set_data(masked)
        if freq.size:
            self.image.set_extent(
                (float(freq[0]), float(freq[-1]), 0.0, float(self.depth))
            )
        if masked.count() > 0:
            self.image.set_clim(float(masked.min()), float(masked.max()))

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
    """Synthetic source: a chirping tone plus a fixed tone and noise.

    The chirp sweeps linearly from ``f_start`` to ``f_end`` and back over
    ``sweep_period_frames`` acquisitions, so a live waterfall visibly shows
    motion -- a static-frequency demo tone would hide bugs in the scrolling
    axis.
    """

    def __init__(
        self,
        *,
        channel: str = DEFAULT_CHANNEL,
        sample_rate: float = 1.0e8,
        n_samples: int = 4096,
        f_start: float = 5.0e6,
        f_end: float = 30.0e6,
        sweep_period_frames: int = 50,
        noise: float = 0.005,
        seed: int = 0,
    ) -> None:
        self.channel = channel
        self._dt = 1.0 / sample_rate
        self.n_samples = n_samples
        self.f_start = f_start
        self.f_end = f_end
        self.sweep_period_frames = max(2, sweep_period_frames)
        self.noise = noise
        self._rng = np.random.default_rng(seed)
        self._frame = 0

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def configure(self, settings: Mapping[str, object]) -> None:
        """No-op; the demo source runs at a fixed sample rate/record length."""

    def acquire(self) -> Capture:
        """Return one synthetic record and advance the chirp phase."""
        t = np.arange(self.n_samples, dtype=np.float64) * self._dt
        period = self.sweep_period_frames
        # Triangle wave in [0, 1] over `period` frames, so the chirp sweeps
        # up and back down instead of jumping at each period boundary.
        cycle = (self._frame % period) / period
        phase = 2.0 * cycle if cycle < 0.5 else 2.0 - 2.0 * cycle
        freq = self.f_start + (self.f_end - self.f_start) * phase
        self._frame += 1

        chirp = 0.05 * np.sin(2.0 * np.pi * freq * t)
        fixed_tone = 0.03 * np.sin(2.0 * np.pi * (0.5 * self.f_start) * t)
        noise = self.noise * self._rng.standard_normal(self.n_samples)
        volts = chirp + fixed_tone + noise
        return Capture(
            volts=volts[np.newaxis, :],
            t0=0.0,
            dt=self._dt,
            channel_names=(self.channel,),
        )

    def close(self) -> None:
        """No-op; safe to call repeatedly."""


def run(
    scope: Scope,
    viewer: WaterfallViewer,
    *,
    channel: str,
    interval: float = DEFAULT_INTERVAL,
    frames: int | None = None,
    show: bool = True,
    pause: Callable[[float], None] = plt.pause,
) -> int:
    """Acquire and redraw until ``frames`` are drawn, the window is closed, or interrupted.

    In a triggered sweep mode (``SINGle``/``NORMal``) ``scope.acquire()``
    blocks until it fires and raises ``TimeoutError`` on each polling
    timeout; per :class:`SiglentSDS6204L`'s convention that's "no trigger
    yet", not a failure, so it's swallowed and the loop just re-polls -- the
    same idiom used by ``apps/bdot_probe``. Returns the number of frames
    drawn. ``show`` and ``pause`` are injectable so the loop can be driven
    headlessly in tests. The interval is clamped to at least one millisecond
    because ``matplotlib.pyplot.pause(0)`` blocks forever.
    """
    plt.ion()
    if show:
        viewer.show()
    drawn = 0
    try:
        while frames is None or drawn < frames:
            if not plt.fignum_exists(viewer.figure.number):
                break
            try:
                capture = scope.acquire()
            except TimeoutError as exc:
                if "did not complete" not in str(exc):
                    raise
                pause(max(interval, 0.001))
                continue
            viewer.update(capture, channel)
            viewer.draw()
            pause(max(interval, 0.001))
            drawn += 1
    except KeyboardInterrupt:
        pass
    return drawn


def record_waterfall(
    scope: Scope,
    *,
    channel: str,
    n_sweeps: int,
    window: str = DEFAULT_WINDOW,
    detrend: DetrendMode | None = "constant",
    remove_comb: bool = False,
    comb_period: int = COMB_PERIOD,
    on_row: Callable[[int, Spectrum], None] | None = None,
) -> Spectrogram:
    """Acquire ``n_sweeps`` records and stack their spectra into a :class:`Spectrogram`.

    If the scope has a trigger configured (``trigger_mode`` ``SINGle`` or
    ``NORMal``), ``scope.acquire()`` blocks until it fires and raises
    ``TimeoutError`` on each polling timeout; per :class:`SiglentSDS6204L`'s
    convention that's treated as "no trigger yet" and the loop keeps polling,
    the same idiom used by ``apps/bdot_probe``.
    """
    if n_sweeps < 1:
        raise ValueError(f"n_sweeps must be >= 1, got {n_sweeps}")
    buffer = WaterfallBuffer(n_sweeps, window=window, detrend=detrend)
    row = 0
    while row < n_sweeps:
        try:
            capture = scope.acquire()
        except TimeoutError as exc:
            if "did not complete" not in str(exc):
                raise
            continue
        source = capture[channel]
        if remove_comb:
            source = remove_adc_comb(source, period=comb_period)
        spectrum = buffer.push(source)
        row += 1
        if on_row is not None:
            on_row(row, spectrum)
    return buffer.to_spectrogram()


def acquire_single(scope: Scope, channel: str) -> Channel:
    """Block until one triggered acquisition completes and return ``channel``.

    A ``TimeoutError`` on a polling timeout is "no trigger yet", not a
    failure -- see :func:`record_waterfall`'s docstring -- so it's swallowed
    and the wait continues.
    """
    while True:
        try:
            capture = scope.acquire()
        except TimeoutError as exc:
            if "did not complete" not in str(exc):
                raise
            continue
        return capture[channel]


def transient_waterfall(
    scope: Scope,
    *,
    channel: str,
    stft_window: float,
    stft_hop: float | None = None,
    window: str = DEFAULT_WINDOW,
    detrend: DetrendMode | None = "constant",
    remove_comb: bool = False,
    comb_period: int = COMB_PERIOD,
    on_capture: Callable[[Channel, Spectrogram], None] | None = None,
) -> Spectrogram:
    """Capture one triggered record and slice it into a :func:`stft` spectrogram.

    ``stft_window``/``stft_hop`` are given in seconds (converted to samples
    using the captured record's own ``dt``), matching how ``--timebase`` is
    specified elsewhere in this app rather than raw sample counts.
    ``on_capture``, if given, is called once with the raw captured channel
    (e.g. for its raw time-domain peak, directly comparable to the trigger
    level) and the resulting spectrogram (e.g. for its peak frequency/
    amplitude) -- the raw channel isn't otherwise returned.
    """
    source = acquire_single(scope, channel)
    if remove_comb:
        source = remove_adc_comb(source, period=comb_period)
    segment_samples = max(1, round(stft_window / source.dt))
    hop_samples = None if stft_hop is None else max(1, round(stft_hop / source.dt))
    spectrogram = stft(
        source,
        segment_samples=segment_samples,
        hop_samples=hop_samples,
        window=window,
        detrend=detrend,
    )
    if on_capture is not None:
        on_capture(source, spectrogram)
    return spectrogram


def plot_spectrogram(
    spectrogram: Spectrogram,
    *,
    db_scale: bool = True,
    db_floor: float = DEFAULT_DB_FLOOR,
    cmap: str = DEFAULT_CMAP,
    freq_max: float | None = None,
    time_axis: bool = False,
    title: str | None = None,
) -> Figure:
    """Render a static waterfall image: frequency vs. sweep index (or time).

    ``time_axis`` switches the y-axis from sweep index to
    :attr:`Spectrogram.times` -- meaningful for :func:`transient_waterfall`,
    where each row's time is its slice's position within the one recorded
    event, but only an approximation for ``record_waterfall``'s rows (the
    wall-clock moment each separate acquisition was pushed).
    """
    freq = spectrogram.freq
    grid = spectrogram.amplitude
    if freq_max is not None and freq.size:
        mask = freq <= freq_max
        freq = freq[mask]
        grid = grid[:, mask]
    if db_scale:
        floor_linear = 10.0 ** (db_floor / 20.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            grid = 20.0 * np.log10(np.maximum(grid, floor_linear))
        grid = np.clip(grid, db_floor, None)

    fig, ax = plt.subplots(figsize=(9.0, 6.0), layout="constrained")
    masked = np.ma.masked_invalid(grid)
    if time_axis and spectrogram.n_rows:
        y0, y1 = float(spectrogram.times[0]), float(spectrogram.times[-1])
        if y0 == y1:
            y1 = y0 + 1.0
        ylabel = "time (s)"
    else:
        y0, y1 = 0.0, float(spectrogram.n_rows)
        ylabel = "sweep index"
    extent = (
        (float(freq[0]), float(freq[-1]), y0, y1) if freq.size else (0.0, 1.0, y0, y1)
    )
    image = ax.imshow(masked, origin="lower", aspect="auto", cmap=cmap, extent=extent)
    fig.colorbar(image, ax=ax, label="amplitude (dB)" if db_scale else "amplitude (V)")
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel(ylabel)
    ax.set_title(title or f"RF waterfall ({spectrogram.n_rows} sweeps)")
    return fig


def save_csv(spectrogram: Spectrogram, path: str) -> None:
    """Write long-form ``sweep_index, time_s, freq_hz, amplitude`` rows."""
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sweep_index", "time_s", "freq_hz", "amplitude"])
        for row_index in range(spectrogram.n_rows):
            row_time = spectrogram.times[row_index]
            for freq, amplitude in zip(
                spectrogram.freq, spectrogram.amplitude[row_index], strict=True
            ):
                writer.writerow([row_index, row_time, freq, amplitude])


_NON_INTERACTIVE_BACKENDS = frozenset(
    {"agg", "cairo", "pdf", "pgf", "ps", "svg", "template"}
)


def _warn_if_non_interactive() -> None:
    backend = matplotlib.get_backend()
    if backend.lower() in _NON_INTERACTIVE_BACKENDS:
        print(
            f"warning: matplotlib is using the non-interactive {backend!r} "
            "backend, so no window will open. Use --output-plot to save a "
            "PNG instead, or run inside an environment with an interactive "
            "backend.",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="rf_waterfall",
        description="Live or recorded RF waterfall (spectrogram) of a scope channel.",
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS, help="scope IP for the raw socket"
    )
    parser.add_argument(
        "--channel", default=DEFAULT_CHANNEL, help="channel to display, e.g. C1"
    )
    parser.add_argument(
        "--mode",
        choices=("dynamic", "record", "transient"),
        default="dynamic",
        help=(
            "dynamic: live scrolling waterfall; record: N separate triggered "
            "sweeps; transient: one triggered record, sliced into an STFT "
            "spectrogram (default: dynamic)"
        ),
    )
    parser.add_argument(
        "--window", default=DEFAULT_WINDOW, help="FFT window (default: hann)"
    )
    parser.add_argument(
        "--detrend",
        choices=("constant", "linear", "none"),
        default="constant",
        help="trend removed before each sweep's FFT (default: constant)",
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
        "--vdiv", type=float, default=None, metavar="V", help="vertical scale in V/div"
    )
    parser.add_argument(
        "--impedance",
        choices=("1M", "50"),
        default="1M",
        help="input impedance for the channel (default: 1M)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help="rolling rows kept in dynamic mode (default: %(default)s)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="seconds between frames",
    )
    parser.add_argument(
        "--frames", type=int, default=None, help="dynamic mode: stop after N frames"
    )
    parser.add_argument(
        "--sweeps",
        type=int,
        default=None,
        help="record mode: number of sweeps to capture",
    )
    parser.add_argument(
        "--stft-window",
        type=float,
        default=None,
        help="transient mode: STFT segment length in seconds (required)",
    )
    parser.add_argument(
        "--stft-hop",
        type=float,
        default=None,
        help="transient mode: STFT hop in seconds (default: half the segment length)",
    )
    parser.add_argument(
        "--trigger-mode",
        choices=("SINGle", "AUTO", "NORMal"),
        default=None,
        help=(
            "scope sweep mode (default: AUTO in dynamic mode, so live viewing "
            "free-runs instead of blocking on an unconfigured trigger; SINGle "
            "in record/transient mode)"
        ),
    )
    parser.add_argument(
        "--trigger-channel",
        default=None,
        help="edge-trigger source (default: --channel)",
    )
    parser.add_argument(
        "--trigger-level", type=float, default=0.0, help="edge-trigger level in volts"
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
        help="seconds to wait per poll before repolling (default: 5)",
    )
    parser.add_argument(
        "--db",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="show amplitude in dB (--no-db for linear volts)",
    )
    parser.add_argument(
        "--db-floor",
        type=float,
        default=DEFAULT_DB_FLOOR,
        help="dB floor (default: -120)",
    )
    parser.add_argument("--cmap", default=DEFAULT_CMAP, help="matplotlib colormap")
    parser.add_argument(
        "--freq-max",
        type=float,
        default=None,
        help="upper frequency limit shown, in Hz",
    )
    parser.add_argument(
        "--output-csv", default=None, help="write sweep data to this CSV path"
    )
    parser.add_argument(
        "--output-plot", default=None, help="save the plot to this PNG path"
    )
    parser.add_argument(
        "--no-show", action="store_true", help="skip opening an interactive plot window"
    )
    parser.add_argument(
        "--demo", action="store_true", help="run against a synthetic source"
    )
    return parser


def _make_scope(args: argparse.Namespace) -> Scope:
    if args.demo:
        return DemoScope(channel=args.channel)
    return SiglentSDS6204L(
        address=args.address,
        channels=(args.channel,),
        trigger_mode=args.trigger_mode,
        acquire_timeout=args.trigger_timeout,
    )


def _configure_trigger(scope: Scope, args: argparse.Namespace) -> None:
    """Apply the edge trigger and warn if the scope clamped the level.

    A no-op outside ``SINGle``/``NORMal`` sweep mode (i.e. in ``AUTO``, where
    the scope free-runs and no trigger is needed).
    """
    if args.trigger_mode not in ("SINGle", "NORMal"):
        return
    trigger_channel = args.trigger_channel or args.channel
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
        "Callable[[], float] | None", getattr(scope, "trigger_level", None)
    )
    if read_level is not None:
        note = trigger_level_warning(args.trigger_level, read_level())
        if note is not None:
            print(f"warning: {note}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, connect, and run dynamic, record, or transient mode."""
    args = build_parser().parse_args(argv)
    if args.mode == "record" and args.sweeps is None:
        build_parser().error("--sweeps is required in --mode record")
    if args.mode == "transient" and args.stft_window is None:
        build_parser().error("--stft-window is required in --mode transient")
    if args.trigger_mode is None:
        args.trigger_mode = "SINGle" if args.mode in ("record", "transient") else "AUTO"
    detrend: DetrendMode | None = None if args.detrend == "none" else args.detrend

    scope = _make_scope(args)
    with ExitStack() as stack:
        stack.enter_context(scope)
        if args.timebase is not None:
            scope.configure({"timebase": args.timebase})
        scope.configure({"vertical": {args.channel: {"impedance": args.impedance}}})
        if args.vdiv is not None:
            scope.configure({"vertical": {args.channel: {"scale": args.vdiv}}})

        if args.mode == "dynamic":
            _warn_if_non_interactive()
            viewer = WaterfallViewer(
                depth=args.depth,
                window=args.window,
                detrend=detrend,
                db_scale=args.db,
                db_floor=args.db_floor,
                cmap=args.cmap,
                freq_max=args.freq_max,
                remove_comb=args.remove_comb,
                comb_period=args.comb_period,
            )
            try:
                run(
                    scope,
                    viewer,
                    channel=args.channel,
                    interval=args.interval,
                    frames=args.frames,
                    show=not args.no_show,
                )
                if args.output_csv is not None:
                    save_csv(viewer.to_spectrogram(), args.output_csv)
                    print(f"wrote {args.output_csv}")
                if args.output_plot is not None:
                    viewer.figure.savefig(args.output_plot, dpi=150)
                    print(f"wrote {args.output_plot}")
            finally:
                viewer.close()
            return 0

        _configure_trigger(scope, args)

        if args.mode == "record":

            def on_row(row: int, spectrum: Spectrum) -> None:
                freq, amplitude = spectrum.peak()
                print(
                    f"[{time.strftime('%H:%M:%S')}] trigger: captured sweep "
                    f"{row}/{args.sweeps}  peak {freq / 1e6:.3f} MHz @ {amplitude:.3e} V",
                    flush=True,
                )

            spectrogram = record_waterfall(
                scope,
                channel=args.channel,
                n_sweeps=args.sweeps,
                window=args.window,
                detrend=detrend,
                remove_comb=args.remove_comb,
                comb_period=args.comb_period,
                on_row=on_row,
            )
            title = None
            time_axis = False
        else:

            def on_capture(source: Channel, spectrogram: Spectrogram) -> None:
                raw_peak_volts = float(np.max(np.abs(source.volts)))
                _, peak_freq, peak_amplitude = spectrogram.peak()
                print(
                    f"[{time.strftime('%H:%M:%S')}] trigger: captured transient, "
                    f"{spectrogram.n_rows} segments  raw peak {raw_peak_volts:.3e} V  "
                    f"spectral peak {peak_freq / 1e6:.3f} MHz @ {peak_amplitude:.3e} V",
                    flush=True,
                )

            spectrogram = transient_waterfall(
                scope,
                channel=args.channel,
                stft_window=args.stft_window,
                stft_hop=args.stft_hop,
                window=args.window,
                detrend=detrend,
                remove_comb=args.remove_comb,
                comb_period=args.comb_period,
                on_capture=on_capture,
            )
            title = f"RF waterfall - transient ({spectrogram.n_rows} segments)"
            time_axis = True

    fig = plot_spectrogram(
        spectrogram,
        db_scale=args.db,
        db_floor=args.db_floor,
        cmap=args.cmap,
        freq_max=args.freq_max,
        time_axis=time_axis,
        title=title,
    )
    if args.output_csv is not None:
        save_csv(spectrogram, args.output_csv)
        print(f"wrote {args.output_csv}")
    if args.output_plot is not None:
        fig.savefig(args.output_plot, dpi=150)
        print(f"wrote {args.output_plot}")
    if not args.no_show:
        _warn_if_non_interactive()
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
