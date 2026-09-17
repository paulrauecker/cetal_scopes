"""Map the SDS6204L's timebase to the sample rate and depth it chooses.

The write-path probe established that on this instrument ``:ACQuire:MDEPth``
is **rejected** (``*ESR?`` bit 4) in every spelling, and
``:ACQuire:MMANagement`` is accepted and ignored. Memory management is
permanently ``AUTO``: the scope picks both the sample rate and the record
depth itself, and the only acquisition knob that writes is
``:TIMebase:SCALe``.

So the useful question is no longer "which depths exist" but "given a window,
what rate does the scope choose". That is a function this probe can simply
measure: set each timebase, read ``:ACQuire:SRATe?`` and ``:ACQuire:MDEPth?``
back, and tabulate.

The catch, learned the hard way: **a stopped scope reports the setup of its
last acquisition, not the pending one.** Reading straight after setting the
timebase gives the same stale rate and depth at every step. So each point
here free-runs the instrument until the acquisition counter advances, and
only then reads back.

What the map is for: the scope maximises the rate, reaching for the 10 GS/s
"enhanced sample rate" (interpolated, not measured) whenever memory allows.
At a long enough window 10 GS/s would overrun the memory, so the rate falls
to something the ADC can really sample. The shortest window whose rate is at
or below the native 5 GS/s per channel is the shortest honest capture this
instrument will give -- and that is the number to put in ``bench.toml``.

Nothing is armed and no trigger is touched; the timebase is restored on the
way out, including after Ctrl-C.

    uv run apps/capture_studio/probe_timebase_map.py --address 192.168.5.171
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import dataclass

from cetal_scopes.scopes.siglent import GRID_NUM, MAX_RATE_HZ, SiglentSDS6204L

__all__ = ["TIMEBASES", "Point", "build_parser", "main", "sweep"]

#: Timebases to sweep, in seconds per division. Spans the range a fast
#: transient is plausibly caught in, from the shortest the ladder offers up to
#: a millisecond per division.
TIMEBASES: tuple[float, ...] = (
    1e-9,
    5e-9,
    10e-9,
    50e-9,
    100e-9,
    500e-9,
    1e-6,
    5e-6,
    10e-6,
    20e-6,
    50e-6,
    100e-6,
    200e-6,
    500e-6,
    1e-3,
)


@dataclass(frozen=True)
class Point:
    """One timebase and what the instrument chose for it."""

    timebase: float
    readback: float
    sample_rate: float
    depth: str
    acquired: bool = True

    @property
    def window(self) -> float:
        """Seconds across the screen, from the timebase the scope confirms."""
        return self.readback * GRID_NUM

    @property
    def points(self) -> float:
        """Samples the window actually holds at the chosen rate."""
        return self.sample_rate * self.window

    @property
    def honest(self) -> bool:
        """Whether the chosen rate is one the ADC can really sample.

        Above :data:`MAX_RATE_HZ` the extra samples are reconstructed, not
        measured -- Siglent's "ESR".
        """
        return self.sample_rate <= MAX_RATE_HZ * 1.0025


def acquire_once(scope: SiglentSDS6204L, timeout: float = 5.0) -> bool:
    """Free-run until one acquisition completes, so the read-backs are current.

    Returns whether the acquisition counter actually advanced. ``AUTO`` sweep
    mode self-triggers, so this needs no signal at the input.
    """
    scope.raw_write(":TRIGger:MODE AUTO")
    # Latch the count *before* running: the driver's own arm() does the same,
    # because an acquisition can complete before the next query goes out.
    started = int(float(scope.raw_query(":ACQuire:NUMACq?")))
    scope.raw_write(":TRIGger:RUN")
    deadline = time.monotonic() + timeout
    advanced = False
    while True:
        if int(float(scope.raw_query(":ACQuire:NUMACq?"))) > started:
            advanced = True
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    scope.raw_write(":TRIGger:STOP")
    return advanced


def sweep(
    scope: SiglentSDS6204L,
    timebases: Sequence[float] = TIMEBASES,
    *,
    timeout: float = 5.0,
) -> list[Point]:
    """Set each timebase, let it acquire, and read back what the scope chose."""
    points: list[Point] = []
    for tdiv in timebases:
        scope.set_timebase(scale=tdiv)
        acquired = acquire_once(scope, timeout=timeout)
        try:
            points.append(
                Point(
                    timebase=tdiv,
                    readback=scope.timebase(),
                    sample_rate=scope.sample_rate(),
                    depth=scope.memory_depth(),
                    acquired=acquired,
                )
            )
        except (OSError, ValueError):
            continue
    return points


def render(points: Sequence[Point]) -> str:
    """The map, and the shortest window that is sampled rather than interpolated."""
    header = (
        f"{'s/div':>10} {'window':>10} {'rate':>12} {'depth':>8} "
        f"{'points':>12}  measured?"
    )
    lines = [header, "-" * 68]
    for item in points:
        lines.append(
            f"{item.readback:>10.3g} {item.window:>10.3g} "
            f"{item.sample_rate / 1e9:>9.4g} GS/s {item.depth:>8} "
            f"{item.points:>12,.0f}  {'yes' if item.honest else 'INTERPOLATED'}"
            f"{'' if item.acquired else '  (stale: never triggered)'}"
        )

    lines.append("")
    if not any(item.acquired for item in points):
        lines.append(
            "No acquisition completed, so every row is the last capture's "
            "setup rather than the pending one. Check the trigger: AUTO "
            "sweep should self-trigger with no signal at all."
        )
        return "\n".join(lines)
    if len({item.sample_rate for item in points}) == 1:
        lines.append(
            "The rate is identical at every timebase, which cannot be true "
            "across this range -- the read-back is not tracking the setup. "
            "Read the rate from a real capture's WaveDesc instead."
        )
        return "\n".join(lines)
    honest = [item for item in points if item.honest]
    if not honest:
        lines.append(
            "Every window in this sweep is interpolated. Try longer "
            "timebases: the rate only falls once 10 GS/s would overrun memory."
        )
        return "\n".join(lines)

    best = min(honest, key=lambda item: item.window)
    lines.append(
        f"Shortest fully-measured window: {best.window * 1e6:g} us "
        f"({best.readback * 1e6:g} us/div) at {best.sample_rate / 1e9:g} GS/s, "
        f"{best.points:,.0f} points."
    )
    lines.append(f'For bench.toml:  record_length = "{best.window * 1e6:g}us"')
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Map SDS6204L timebase to the rate and depth it chooses."
    )
    parser.add_argument(
        "--address", default="192.168.5.171", help="Instrument address."
    )
    parser.add_argument("--port", type=int, default=5025, help="Socket port.")
    parser.add_argument(
        "--channels",
        default="C1,C2,C3",
        help="Comma-separated channels; memory is shared between them.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Sweep the timebase and print the map. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    channels = tuple(name.strip() for name in args.channels.split(",") if name.strip())

    scope = SiglentSDS6204L(address=args.address, port=args.port, channels=channels)
    try:
        scope.connect()
    except OSError as exc:
        print(f"cannot reach {args.address}:{args.port}: {exc}")
        return 2

    try:
        print(f"instrument: {scope.idn}")
        print(f"channels:   {', '.join(channels)}  (memory is shared)")
        was_timebase = scope.timebase()
        print(f"restoring afterwards: {was_timebase:g} s/div\n")
        was_mode = scope.raw_query(":TRIGger:MODE?").strip()
        try:
            print(render(sweep(scope)))
        finally:
            scope.raw_write(f":TRIGger:MODE {was_mode}")
            scope.set_timebase(scale=was_timebase)
            scope.abort()
    finally:
        scope.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
