"""Find the SDS6204L's real memory-depth ladder, by asking the instrument.

``cetal_scopes.scopes.siglent.MDEPTH_ENUM`` was read off example syntax in the
programming guide, not off hardware, and the source says so. It matters
because ``set_acquisition`` derives memory depth from it and rounds *up*: if
the table is missing a step, a record length lands on a deeper one, and the
scope raises its sample rate to fill the extra depth over the same window --
handing back reconstructed samples instead of the rate that was asked for.

This walks a wide list of candidate depths, writes each one, and reads it
back. A depth the instrument does not offer is rejected silently, leaving the
previous value in place, so the read-back is the answer. At a known timebase
the reported sample rate is a second, independent check: it is
``depth / window`` for a depth the scope really took.

Nothing is armed and no trigger is touched. The acquisition settings this
changes are restored on the way out, including after Ctrl-C.

Run it with the bench's address::

    uv run apps/capture_studio/probe_memory_depth.py --address 192.168.5.171

and paste the tuple it prints over ``MDEPTH_ENUM``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from cetal_scopes.scopes.siglent import GRID_NUM, SiglentSDS6204L

__all__ = ["CANDIDATES", "Probe", "build_parser", "main", "probe_depths"]

#: Depth labels to try. A superset of both the programming guide's examples
#: and the ladder Siglent benchtops usually carry, so the result distinguishes
#: "the table is missing steps" from "the table is right".
CANDIDATES: tuple[str, ...] = (
    "1k",
    "2.5k",
    "5k",
    "10k",
    "25k",
    "50k",
    "100k",
    "250k",
    "500k",
    "1M",
    "2.5M",
    "5M",
    "10M",
    "25M",
    "50M",
    "100M",
    "250M",
    "500M",
    "1G",
)

#: Seconds per division held during the probe. With :data:`GRID_NUM` divisions
#: this is a 200 us window, whose implied rate (depth / window) stays inside
#: the instrument's range for every depth worth probing.
PROBE_TIMEBASE = 20e-6


@dataclass(frozen=True)
class Probe:
    """One candidate depth and what the instrument did with it."""

    asked: str
    readback: str
    sample_rate: float | None

    @property
    def accepted(self) -> bool:
        """Whether the instrument reports back the depth that was asked for."""
        return _normalise(self.readback) == _normalise(self.asked)


def _normalise(label: str) -> str:
    """Compare depth labels without tripping over spelling (``1M`` vs ``1.0M``)."""
    text = label.strip().upper().removesuffix("PTS").removesuffix("PT").strip()
    for suffix, factor in (("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if text.endswith(suffix):
            return f"{float(text[:-1]) * factor:.0f}"
    try:
        return f"{float(text):.0f}"
    except ValueError:
        return text


def probe_depths(
    scope: SiglentSDS6204L, candidates: Sequence[str] = CANDIDATES
) -> list[Probe]:
    """Try each candidate depth and report what the instrument reports back."""
    results: list[Probe] = []
    for label in candidates:
        scope.set_memory_depth(label)
        readback = scope.memory_depth()
        try:
            rate: float | None = scope.sample_rate()
        except (OSError, ValueError):
            rate = None
        results.append(Probe(asked=label, readback=readback, sample_rate=rate))
    return results


def render(results: Sequence[Probe], *, window: float) -> str:
    """The probe table plus a ready-to-paste ``MDEPTH_ENUM``."""
    header = (
        f"{'asked':>8}  {'readback':>10}  {'reported rate':>14}  "
        f"{'implied (depth/window)':>22}"
    )
    lines = [header, "-" * 62]
    for item in results:
        rate = "?" if item.sample_rate is None else f"{item.sample_rate / 1e9:.4g} GS/s"
        implied = float(_normalise(item.readback) or 0) / window if window else 0.0
        mark = "" if item.accepted else "   <- not offered"
        lines.append(
            f"{item.asked:>8}  {item.readback:>10}  {rate:>14}  "
            f"{implied / 1e9:>17.4g} GS/s{mark}"
        )

    offered = [item.asked for item in results if item.accepted]
    lines.append("")
    if offered:
        lines.append("MDEPTH_ENUM: tuple[tuple[int, str], ...] = (")
        for label in offered:
            lines.append(f"    ({int(float(_normalise(label))):_}, {label!r}),")
        lines.append(")")
    else:
        lines.append(
            "No candidate was accepted. The instrument may reject depth "
            "changes while running, or use labels this probe does not spell "
            "the same way -- check the readback column."
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Find the SDS6204L's real memory-depth ladder."
    )
    parser.add_argument(
        "--address",
        default="192.168.5.171",
        help="Instrument address (default: the bench.toml value).",
    )
    parser.add_argument("--port", type=int, default=5025, help="Socket port.")
    parser.add_argument(
        "--channels",
        default="C1",
        help=(
            "Comma-separated channels to have active. The available depth is "
            "shared between channels, so probe the set you actually acquire."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Probe the instrument and print the ladder. Returns a process exit status."""
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
        print(f"channels:   {', '.join(channels)}")
        was_depth = scope.memory_depth()
        was_timebase = scope.timebase()
        print(f"restoring afterwards: depth {was_depth}, {was_timebase:g} s/div\n")
        try:
            scope.set_timebase(scale=PROBE_TIMEBASE)
            window = PROBE_TIMEBASE * GRID_NUM
            print(render(probe_depths(scope), window=window))
        finally:
            # Leave the bench as it was found, including on Ctrl-C.
            scope.set_memory_depth(was_depth)
            scope.set_timebase(scale=was_timebase)
    finally:
        scope.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
