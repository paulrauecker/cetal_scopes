"""Single-shot transient capture and 3D field-vector replay for the 3D B-dot.

Bench tool, not library code. Two subcommands:

``capture``
    Arms the SDS6204L for one edge trigger on C1-C3 (e.g. a piezo lighter's
    spark next to the probe), waits until it fires, warns about clipped
    channels and saves the shot with :func:`cetal_scopes.save_capture`.

``view``
    Loads a saved shot and replays the field vector sample by sample: an
    arrow at the current sample, the path so far coloured by time (viridis),
    the rest of it faint, per-axis traces zoomed on the cursor and an overview of the
    whole record (click to jump). Only measured samples are drawn, nothing
    between them; fast playback skips samples rather than inventing any.
    Slider or arrow keys step, the play button or space plays.

Uncalibrated: each axis is converted with one flat effective area,
``B_k(t) = (1/A_k) * integral(V_k dt)`` from the pretrigger baseline, a plain
running sum over the samples. That holds only where the loop is an ideal dB/dt
sensor (the 28 mm probe: below ~130 MHz) and ignores the channel-to-channel
skew. ``--raw`` shows the volts instead. Use calibrated transfer functions and
:func:`cetal_scopes.analysis.fields.b_field` once they exist.

    uv run apps/bdot_transient/bdot_transient.py capture shot1 --source C2
    uv run apps/bdot_transient/bdot_transient.py view shot1
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.artist import Artist
from matplotlib.backend_bases import Event, KeyEvent, MouseEvent
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.patheffects import Normal, Stroke
from matplotlib.widgets import Button, Slider
from mpl_toolkits.mplot3d.art3d import Line3DCollection
from numpy.typing import NDArray

from cetal_scopes import Capture, SiglentSDS6204L, load_capture, save_capture

CHANNELS = ("C1", "C2", "C3")
AXIS_COLORS = ("#2a78d6", "#eb6834", "#2f9e57")
DEFAULT_AREA = 6.8e-4
GRID_DIVS_VERTICAL = 8
"""The SDS6204L screen spans +/-4 divisions, which is also the ADC range."""
CLIP_FRACTION = 0.98


@dataclass(frozen=True)
class FieldTrace:
    """Per-axis signal of one shot on a shared time axis."""

    t: NDArray[np.float64]
    values: NDArray[np.float64]  # (3, n)
    unit: str


def clipped_channels(capture: Capture, vdiv: float) -> list[str]:
    """Channels whose samples reach the edge of the ADC range."""
    limit = CLIP_FRACTION * vdiv * GRID_DIVS_VERTICAL / 2.0
    return [ch for ch in CHANNELS if np.max(np.abs(capture[ch].volts)) >= limit]


def pretrigger_mask(t: NDArray[np.float64], margin: float) -> NDArray[np.bool_]:
    """Samples before the trigger, minus ``margin`` seconds of slack."""
    mask = t < -margin
    if not mask.any():
        raise ValueError("no pretrigger samples to take a baseline from")
    return mask


def field_trace(
    capture: Capture,
    areas: Sequence[float],
    *,
    raw: bool = False,
    baseline_margin: float = 5e-9,
) -> FieldTrace:
    """Baseline-subtracted volts, or their running integral divided by area.

    The baseline is each channel's mean before the trigger (less
    ``baseline_margin``), so an offset is not integrated into a ramp.
    """
    t = capture[CHANNELS[0]].time
    volts = np.vstack([capture[ch].volts for ch in CHANNELS])
    volts = volts - volts[:, pretrigger_mask(t, baseline_margin)].mean(
        axis=1, keepdims=True
    )
    if raw:
        return FieldTrace(t=t, values=volts, unit="V")
    b = np.cumsum(volts, axis=1) * capture.dt / np.asarray(areas)[:, None]
    return FieldTrace(t=t, values=b, unit="T")


def event_window(
    trace: FieldTrace, *, threshold: float = 0.05, pad: float = 10e-9
) -> tuple[int, int]:
    """Sample range where the vector exceeds ``threshold`` of its peak, padded.

    Pass the raw (volts) trace: it returns to zero after the event, while an
    integrated field can stay offset.
    """
    magnitude = np.linalg.norm(trace.values, axis=0)
    above = np.flatnonzero(magnitude >= threshold * magnitude.max())
    if above.size == 0:
        return 0, trace.t.size
    dt = float(trace.t[1] - trace.t[0])
    pad_n = round(pad / dt)
    return max(0, int(above[0]) - pad_n), min(trace.t.size, int(above[-1]) + pad_n + 1)


# --- capture ---------------------------------------------------------------


def run_capture(args: argparse.Namespace) -> int:
    scope = SiglentSDS6204L(
        args.address, channels=CHANNELS, trigger_mode="SINGle", acquire_timeout=1.0
    )
    with scope:
        scope.configure(
            {
                "timebase": args.window / 10.0,
                "vertical": {
                    ch: {"impedance": "50", "scale": args.vdiv, "coupling": "DC"}
                    for ch in CHANNELS
                },
                "trigger": {
                    "source": args.source,
                    "level": args.level,
                    "slope": args.slope,
                },
            }
        )
        scope.arm()
        print(
            f"armed: {args.source} {args.slope} through {args.level * 1e3:g} mV, "
            f"{args.window * 1e9:g} ns window. Fire the source (Ctrl-C to give up).",
            flush=True,
        )
        try:
            while not scope.wait(1.0):
                pass
        except KeyboardInterrupt:
            scope.abort()
            print("aborted, nothing saved")
            return 1
        capture = scope.fetch()

    clipped = clipped_channels(capture, args.vdiv)
    if clipped:
        print(
            f"warning: {', '.join(clipped)} clipped; raise --vdiv or move the source "
            "away (a clipped channel's integral is wrong from that point on)",
            file=sys.stderr,
        )
    path = save_capture(capture, args.output)
    print(
        f"saved {path} ({capture.volts.shape[1]} samples at {1e-9 / capture.dt:g} GS/s)"
    )
    return 0


# --- view ------------------------------------------------------------------


MAX_PATH_POINTS = 20_000
"""The faint full path is thinned to this many measured samples for drawing."""
FRAME_S = 0.05
"""Playback timer interval (20 fps)."""


def playback_step(speed_ns_per_s: float, dt: float) -> int:
    """Samples to advance per frame: whole measured samples, at least one."""
    return max(1, round(speed_ns_per_s * 1e-9 * FRAME_S / dt))


def run_view(args: argparse.Namespace) -> int:
    capture = load_capture(args.shot)
    areas = args.area if len(args.area) == 3 else args.area * 3
    trace = field_trace(capture, areas, raw=args.raw)
    # Window on the volts: the integral keeps any net step after the event.
    start, stop = event_window(field_trace(capture, areas, raw=True))
    t = trace.t[start:stop]
    if trace.unit == "T":
        values, unit = trace.values[:, start:stop] * 1e9, "nT"
    else:
        values, unit = trace.values[:, start:stop] * 1e3, "mV"
    n = t.size
    dt_ns = capture.dt * 1e9
    t_ns = t * 1e9
    magnitude = np.linalg.norm(values, axis=0)
    lim = float(np.max(np.abs(values))) * 1.1 or 1.0
    zoom_n = max(10, round(args.zoom / dt_ns))
    labels = (
        [f"{ch} [{unit}]" for ch in CHANNELS]
        if args.raw
        else [f"B{k + 1} [{unit}]" for k in range(3)]
    )

    fig = plt.figure(figsize=(13, 7.5))
    grid = fig.add_gridspec(
        4,
        2,
        width_ratios=(1.15, 1),
        height_ratios=(1, 1, 1, 0.8),
        hspace=0.45,
        wspace=0.3,
        bottom=0.14,
    )
    ax3d = fig.add_subplot(grid[:, 0], projection="3d")
    ax3d.computed_zorder = False  # draw in zorder, so the arrow stays on top
    ax_axes = [fig.add_subplot(grid[k, 1]) for k in range(3)]
    ax_overview = fig.add_subplot(grid[3, 1])
    ax_slider = fig.add_axes((0.08, 0.05, 0.36, 0.03))
    ax_play = fig.add_axes((0.08, 0.0, 0.07, 0.04))
    ax_speed = fig.add_axes((0.24, 0.005, 0.2, 0.03))

    # Background: the whole path, faint and thinned, so the arrow stays on top.
    stride = max(1, n // MAX_PATH_POINTS)
    ax3d.plot(*values[:, ::stride], color="#c8c6bf", lw=0.6, alpha=0.6)
    # The history spans all of viridis: first sample purple, current yellow.
    time_scale = ScalarMappable(norm=Normalize(t_ns[0], t_ns[-1]), cmap="viridis")
    fig.colorbar(
        time_scale, ax=ax3d, shrink=0.45, pad=0.14, label="time from trigger [ns]"
    )
    for k, color in enumerate(AXIS_COLORS):
        end = np.zeros(3)
        end[k] = lim
        ax3d.plot(*zip(-end, end, strict=True), color=color, lw=1, alpha=0.35)
    ax3d.set(xlim=(-lim, lim), ylim=(-lim, lim), zlim=(-lim, lim))
    ax3d.set(xlabel=labels[0], ylabel=labels[1], zlabel=labels[2])

    marker = "." if zoom_n <= 5000 else ""
    cursors = []
    for k, ax in enumerate(ax_axes):
        ax.plot(t_ns, values[k], color=AXIS_COLORS[k], lw=1, marker=marker, ms=2)
        ax.axhline(0, color="#9a9890", lw=0.6)
        ax.set_ylabel(labels[k], fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_ylim(-lim, lim)
        cursors.append(ax.axvline(t_ns[0], color="#c0392b", lw=1))
    for ax in ax_axes[:-1]:
        ax.tick_params(labelbottom=False)
    ax_axes[-1].set_xlabel("time from trigger [ns] (zoomed on the cursor)", fontsize=8)
    ax_overview.plot(t_ns[::stride], magnitude[::stride], color="#1f1f1e", lw=0.6)
    ax_overview.set_xlim(t_ns[0], t_ns[-1])
    ax_overview.set_ylabel(f"|v| [{unit}]", fontsize=8)
    ax_overview.set_xlabel("whole record [ns]: click to jump", fontsize=8)
    ax_overview.tick_params(labelsize=8)
    overview_cursor = ax_overview.axvline(t_ns[0], color="#c0392b", lw=1)
    mode = "raw volts" if args.raw else "uncalibrated: flat area, running integral"
    fig.suptitle(f"{Path(args.shot).name}  ({mode})", fontsize=10)

    dynamic: list[Artist] = []
    slider = Slider(ax_slider, "sample", 0, n - 1, valinit=0, valstep=1)
    speed = Slider(
        ax_speed,
        "ns/s",
        -1.0,
        6.0,
        valinit=float(np.log10(args.speed)),
    )
    speed.valtext.set_text(f"{args.speed:g}")
    play = Button(ax_play, "play")

    def show(index: int) -> None:
        for artist in dynamic:
            artist.remove()
        dynamic.clear()
        # Everything up to now; long records are thinned to measured samples,
        # always ending on the current one.
        picks = np.r_[0:index:stride, index]
        if picks.size > 1:
            points = values[:, picks].T
            history = Line3DCollection(
                np.stack([points[:-1], points[1:]], axis=1),
                cmap="viridis",
                norm=Normalize(t_ns[0], t_ns[index]),
                lw=1.5,
            )
            history.set_array(t_ns[picks[1:]])
            time_scale.set_clim(t_ns[0], t_ns[index])
            ax3d.add_collection3d(history)
            dynamic.append(history)
        v = values[:, index]
        arrow = ax3d.quiver(
            0,
            0,
            0,
            *v,
            color="#c0392b",
            lw=3,
            arrow_length_ratio=0.15,
            zorder=10,
        )
        # White halo so the arrow reads against every viridis colour.
        arrow.set_path_effects([Stroke(linewidth=5.5, foreground="white"), Normal()])
        dynamic.append(arrow)
        for k, cursor in enumerate(cursors):
            cursor.set_xdata([t_ns[index], t_ns[index]])
            ax_axes[k].set_xlim(
                t_ns[max(0, index - zoom_n)], t_ns[min(n - 1, index + zoom_n)]
            )
        overview_cursor.set_xdata([t_ns[index], t_ns[index]])
        ax3d.set_title(
            f"t = {t_ns[index]:+.2f} ns   |v| = {magnitude[index]:.3g} {unit}",
            fontsize=10,
        )
        fig.canvas.draw_idle()

    slider.on_changed(lambda value: show(int(value)))
    timer = fig.canvas.new_timer(interval=round(1000 * FRAME_S))
    state = {"playing": False}

    def set_playing(on: bool) -> None:
        state["playing"] = on
        play.label.set_text("pause" if on else "play")
        if on:
            if int(slider.val) >= n - 1:
                slider.set_val(0)
            timer.start()
        else:
            timer.stop()
        fig.canvas.draw_idle()

    def step() -> None:
        index = int(slider.val) + playback_step(10.0**speed.val, capture.dt)
        if index >= n - 1:
            slider.set_val(n - 1)
            set_playing(False)
            return
        slider.set_val(index)

    timer.add_callback(step)
    play.on_clicked(lambda _: set_playing(not state["playing"]))
    speed.on_changed(lambda value: speed.valtext.set_text(f"{10.0**value:.3g}"))

    def on_key(event: Event) -> None:
        if not isinstance(event, KeyEvent):
            return
        if event.key == "right":
            slider.set_val(min(n - 1, int(slider.val) + 1))
        elif event.key == "left":
            slider.set_val(max(0, int(slider.val) - 1))
        elif event.key == " ":
            set_playing(not state["playing"])

    def on_click(event: Event) -> None:
        if (
            isinstance(event, MouseEvent)
            and event.inaxes is ax_overview
            and event.xdata is not None
        ):
            index = int(np.clip(np.searchsorted(t_ns, event.xdata), 0, n - 1))
            slider.set_val(index)

    fig.canvas.mpl_connect("key_press_event", on_key)
    fig.canvas.mpl_connect("button_press_event", on_click)
    show(0)
    plt.show()
    return 0


# --- CLI -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Single-shot 3D B-dot transient capture and vector replay."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="arm, wait for one trigger, save the shot")
    cap.add_argument("output", help="capture path (writes <output>.json + .npy)")
    cap.add_argument("--address", default="192.168.5.162", help="scope address")
    cap.add_argument("--source", default="C2", choices=CHANNELS, help="trigger source")
    cap.add_argument(
        "--level", type=float, default=0.02, help="trigger level in V (default: 20 mV)"
    )
    cap.add_argument(
        "--slope",
        default="RISing",
        choices=("RISing", "FALLing", "ALTernate"),
        help="trigger slope (default: RISing)",
    )
    cap.add_argument(
        "--vdiv", type=float, default=0.05, help="V/div on C1-C3 (default: 50 mV)"
    )
    cap.add_argument(
        "--window",
        type=float,
        default=500e-9,
        help="capture window in s, trigger centred (default: 500 ns)",
    )
    cap.set_defaults(func=run_capture)

    view = sub.add_parser("view", help="replay a saved shot")
    view.add_argument("shot", help="capture path saved by 'capture'")
    view.add_argument(
        "--area",
        type=float,
        nargs="+",
        default=[DEFAULT_AREA],
        help=f"effective area in m^2, one for all or one per axis (default: {DEFAULT_AREA:g})",
    )
    view.add_argument(
        "--raw", action="store_true", help="show baseline-subtracted volts, no integral"
    )
    view.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="initial playback speed in ns of signal per second (default: 1)",
    )
    view.add_argument(
        "--zoom",
        type=float,
        default=50.0,
        help="per-axis plots show +/- this many ns around the cursor (default: 50)",
    )
    view.set_defaults(func=run_view)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "view" and len(args.area) not in (1, 3):
        build_parser().error("--area takes one value or three")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
