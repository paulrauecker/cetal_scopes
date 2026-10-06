"""Real-time 3D view of the field vector seen by the 3D B-dot (C1-C3).

Bench toy, not library code. Captures C1-C3 continuously, finds the strongest
tone (or uses ``--freq``), and turns each channel into a phasor at that tone.
Every capture is shown on its own: no averaging, no smoothing between
captures. The view draws one arrow along the axis the field oscillates on (the
polarization ellipse's major axis), its length the peak field. Which end is
"+" depends only on when the capture started, so the arrow keeps the side
nearest the previous one rather than flipping at random.

The field is ``B_k = V_k / (j omega A)`` with one effective area ``A`` for all
three axes. Only C1 has been measured (~6.8e-4 m^2 against H3, 2026-10-05);
C2/C3 are assumed equal and cross-axis coupling is ignored, so the direction
is indicative only. The field source is up to you; this only reads the scope.

    uv run apps/bdot_vector/bdot_vector.py --address 192.168.5.162
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback
from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from numpy.typing import NDArray

from cetal_scopes import Capture, SiglentSDS6204L

CHANNELS = ("C1", "C2", "C3")
AXIS_COLORS = ("#2a78d6", "#eb6834", "#2f9e57")
DEFAULT_AREA = 6.8e-4
COMB_PERIOD = 256
"""The SDS6204L's ADC comb: spurs at ``k * fs / 256`` on every channel."""


def find_tone(volts: NDArray[np.float64], dt: float, *, f_min: float = 1.0e6) -> float:
    """Strongest tone across all rows of ``volts``, skipping the ADC comb."""
    n = volts.shape[-1]
    window = np.hanning(n)
    power = np.sum(np.abs(np.fft.rfft(volts * window, axis=-1)) ** 2, axis=0)
    freq = np.fft.rfftfreq(n, dt)
    comb_step = 1.0 / (dt * COMB_PERIOD)
    near_comb = np.abs((freq / comb_step) - np.round(freq / comb_step)) * comb_step
    power[(freq < f_min) | (near_comb < 4.0 / (n * dt))] = 0.0
    k = int(np.argmax(power[1:-1])) + 1
    # Parabolic interpolation on the log power for sub-bin accuracy.
    a, b, c = np.log(power[k - 1 : k + 2] + 1e-300)
    shift = 0.5 * (a - c) / (a - 2.0 * b + c) if a - 2.0 * b + c != 0 else 0.0
    return float((k + shift) * freq[1])


def phasors(
    volts: NDArray[np.float64], t: NDArray[np.float64], freq: float
) -> NDArray[np.complex128]:
    """Complex peak amplitude of each row at ``freq``: ``v = Re(P e^{j w t})``."""
    rotated = volts * np.exp(-2j * np.pi * freq * t)
    return (2.0 * np.mean(rotated, axis=-1)).astype(np.complex128)


class Acquirer(threading.Thread):
    """Captures in the background so the animation stays smooth."""

    def __init__(self, scope: SiglentSDS6204L, freq: float | None, area: float) -> None:
        super().__init__(daemon=True)
        self.scope = scope
        self.fixed_freq = freq
        self.area = area
        self.latest: NDArray[np.complex128] | None = None
        self.count = 0
        self.freq: float | None = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error: Exception | None = None

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.process(self.scope.acquire())
                self.error = None
            except Exception as exc:  # noqa: BLE001 - logged, shown, retried
                self.error = exc
                stamp = time.strftime("%H:%M:%S")
                print(f"[{stamp}] capture failed:", file=sys.stderr)
                traceback.print_exc()
                self.reconnect()

    def reconnect(self) -> None:
        """Drop the socket (and any half-read reply on it) and start over."""
        try:
            self.scope.abort()
        except Exception:  # noqa: BLE001, S110 - best effort, socket may be dead
            pass
        self.scope.close()
        while not self.stop.wait(1.0):
            try:
                self.scope.connect()
                return
            except OSError as exc:
                print(f"reconnect failed: {exc}", file=sys.stderr)

    def process(self, capture: Capture) -> None:
        volts = np.vstack([capture[ch].volts for ch in CHANNELS])
        t = capture[CHANNELS[0]].time
        freq = self.fixed_freq or find_tone(volts, capture.dt)
        b = phasors(volts, t, freq) / (2j * np.pi * freq * self.area)
        with self.lock:
            self.freq = freq
            self.latest = b
            self.count += 1

    def snapshot(self) -> tuple[float | None, NDArray[np.complex128] | None, int]:
        with self.lock:
            return self.freq, self.latest, self.count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Real-time 3D view of the 3D B-dot field vector."
    )
    parser.add_argument("--address", default="192.168.5.162", help="scope address")
    parser.add_argument(
        "--vdiv", type=float, default=5e-3, help="V/div on C1-C3 (default: 5 mV)"
    )
    parser.add_argument(
        "--window",
        type=float,
        default=5e-6,
        help=(
            "capture window in s (default: 5 us; the scope's ~0.6 s per-capture "
            "latency dominates below that, longer windows only slow it down)"
        ),
    )
    parser.add_argument(
        "--freq", type=float, default=None, help="tone in Hz (default: auto-detect)"
    )
    parser.add_argument(
        "--area",
        type=float,
        default=DEFAULT_AREA,
        help=f"effective area per axis in m^2 (default: {DEFAULT_AREA:g})",
    )
    parser.add_argument(
        "--range",
        type=float,
        default=None,
        help="fixed axis half-range in nT (default: twice the first reading)",
    )
    return parser


def major_axis(b: NDArray[np.complex128]) -> NDArray[np.float64]:
    """Peak real field vector of ``Re(b e^{j phi})`` over one period.

    ``|Re(b e^{j phi})|^2`` peaks where ``e^{2 j phi}`` cancels the phase of the
    non-conjugated ``b . b``, which gives the ellipse's semi-major axis.
    """
    phi = -0.5 * np.angle(np.sum(b * b))
    return np.real(b * np.exp(1j * phi))


def run_view(acquirer: Acquirer, field_range: float | None) -> None:
    fig = plt.figure(figsize=(7.5, 7.5))
    ax = fig.add_subplot(projection="3d")
    # Fixed axes: --range, or else twice the first capture's peak.
    state: dict[str, float] = {"scale": field_range or 0.0}
    drawn = {"count": -1}
    last = np.zeros(3)  # previous arrow, for sign continuity only

    def frame(_: int) -> None:
        freq, latest, count = acquirer.snapshot()
        if count == drawn["count"] and acquirer.error is None:
            return  # nothing new measured; leave the last capture up
        drawn["count"] = count
        ax.cla()
        if acquirer.error is not None:
            ax.set_title(f"capture failed, retrying: {acquirer.error}", color="#c0392b")
            return
        if latest is None or freq is None:
            ax.set_title("waiting for the first capture...")
            return
        b = latest * 1e9  # nT
        u = major_axis(b)
        if np.dot(u, last) < 0:  # same axis, other end; no value changes
            u = -u
        last[:] = u
        peak = float(np.linalg.norm(u))
        if not state["scale"]:
            state["scale"] = 2.0 * peak or 1.0
        lim = state["scale"]

        for k, color in enumerate(AXIS_COLORS):  # lab-frame axes, per channel
            end = np.zeros(3)
            end[k] = lim
            ax.plot(*zip(-end, end, strict=True), color=color, lw=1, alpha=0.35)
            x, y, z = end * 1.05
            ax.text(x, y, z, CHANNELS[k], color=color, fontsize=9)
        dx, dy, dz = u
        ax.quiver(0, 0, 0, dx, dy, dz, color="#c0392b", lw=2.5, arrow_length_ratio=0.1)
        for k in range(3):  # shadows on the back walls
            tip = u.copy()
            tip[k] = -lim
            base = np.zeros(3)
            base[k] = -lim
            ax.plot(*zip(base, tip, strict=True), color="#9a9890", lw=1)
        ax.set(xlim=(-lim, lim), ylim=(-lim, lim), zlim=(-lim, lim))
        ax.set(xlabel="B1 [nT]", ylabel="B2 [nT]", zlabel="B3 [nT]")
        direction = "  ".join(f"{c:+.2f}" for c in u / peak) if peak else "--"
        ax.set_title(
            f"{freq / 1e6:.3f} MHz, capture #{count}\n"
            f"|B| = {peak:.3g} nT peak, direction ({direction})",
            fontsize=10,
        )

    _anim = FuncAnimation(fig, frame, interval=50, cache_frame_data=False)
    plt.show()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scope = SiglentSDS6204L(args.address, channels=CHANNELS, trigger_mode="AUTO")
    with scope:
        scope.configure(
            {
                "timebase": args.window / 10.0,
                "vertical": {
                    ch: {"impedance": "50", "scale": args.vdiv, "coupling": "DC"}
                    for ch in CHANNELS
                },
            }
        )
        acquirer = Acquirer(scope, args.freq, args.area)
        acquirer.start()
        try:
            run_view(acquirer, args.range)
        finally:
            acquirer.stop.set()
            acquirer.join(timeout=15.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
