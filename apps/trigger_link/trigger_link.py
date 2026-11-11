"""Siglent Aux Out -> M5i Trig In: trigger transfer, delay and jitter.

Downstream application built on :mod:`cetal_scopes`. The SG8 drives a CW tone
into the Siglent; the Siglent runs SINGle shots on a Normal edge trigger on
that tone, and its Aux Out (trigger out) is wired to the M5i's Trig In. Per
shot it:

1. arms the M5i and checks it stays quiet for ``--quiet`` s (a trigger now is
   *spurious*: nothing the Siglent did);
2. fires one Siglent single and checks the M5i fired (else *missed*);
3. fits the tone's phase at each instrument's ``t = 0`` -- its trigger point.

Because the Siglent triggers on the tone itself, the spread of its own
``t = 0`` phase is its trigger jitter against the tone. If the M5i channel
carries the same tone (``--m5i-channel``, e.g. a splitter output), the spread
of ``phase(M5i) - phase(Siglent)`` is the jitter of the whole link, and its
slope across frequencies is the trigger delay (plus the difference of the two
signal paths). Without the tone on the M5i, the transfer counts and the
Siglent jitter are still valid; the link figures are flagged as unmeasurable.

The SG8 drives the scope inputs directly, so ``--power`` is hard-capped at
+5 dBm.

Against hardware::

    uv run apps/trigger_link/trigger_link.py --shots 30 --freqs 100 300 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from cetal_scopes import Channel, SG8SignalGenerator, SiglentSDS6204L
from cetal_scopes.analysis.phase_delay import estimate_frequency, fit_sine
from cetal_scopes.scopes.spectrum import SpectrumM5i3367

#: The SG8 drives the scope inputs directly; it can do far more than this.
MAX_POWER_DBM = 5.0
OUTPUT_ON_SETTLE_S = 3.0
#: A link jitter below this many times the per-shot fit noise is not resolved.
RESOLVED_FACTOR = 3.0


def circular_spread(phases: ArrayLike) -> float:
    """Rms deviation (rad) of ``phases`` about their circular mean."""
    p = np.asarray(phases, dtype=float)
    mean = np.angle(np.mean(np.exp(1j * p)))
    return float(np.std(np.angle(np.exp(1j * (p - mean)))))


def circular_mean(phases: ArrayLike) -> float:
    """Circular mean (rad) of ``phases``."""
    return float(np.angle(np.mean(np.exp(1j * np.asarray(phases, dtype=float)))))


def phase_at_zero(channel: Channel, frequency: float) -> tuple[float, float, float]:
    """Fit ``channel`` near ``frequency`` -> (phase at t = 0, amplitude, phase std)."""
    t = channel.time
    f_fit = estimate_frequency(t, channel.volts, frequency)
    fit = fit_sine(t, channel.volts, f_fit)
    return fit.phase, fit.amplitude, fit.phase_std


@dataclass
class Shot:
    """One linked shot: phases (rad) at each instrument's trigger point."""

    siglent: float
    siglent_other: float | None
    m5i: float
    m5i_amplitude: float
    m5i_phase_std: float


@dataclass
class FrequencyResult:
    """Per-frequency summary; times in seconds."""

    frequency: float
    shots: int
    missed: int
    spurious: int
    siglent_jitter: float
    siglent_pair_jitter: float | None
    link_jitter: float
    link_fit_noise: float
    link_resolved: bool
    link_mean_phase: float
    m5i_vpp: float


def summarize(
    frequency: float, shots: Sequence[Shot], missed: int, spurious: int
) -> FrequencyResult:
    """Reduce one frequency's shots to jitter figures."""
    w = 2 * np.pi * frequency
    sig = np.array([s.siglent for s in shots])
    link = np.array([s.m5i - s.siglent for s in shots])
    others = [s.siglent_other for s in shots]
    pair = (
        circular_spread(np.array(others, dtype=float) - sig) / w
        if shots and all(o is not None for o in others)
        else None
    )
    link_jitter = circular_spread(link) / w
    fit_noise = float(np.median([s.m5i_phase_std for s in shots])) / w
    return FrequencyResult(
        frequency=frequency,
        shots=len(shots),
        missed=missed,
        spurious=spurious,
        siglent_jitter=circular_spread(sig) / w,
        siglent_pair_jitter=pair,
        link_jitter=link_jitter,
        link_fit_noise=fit_noise,
        link_resolved=link_jitter > RESOLVED_FACTOR * fit_noise,
        link_mean_phase=circular_mean(link),
        m5i_vpp=2 * float(np.median([s.m5i_amplitude for s in shots])),
    )


def link_delay(results: Sequence[FrequencyResult]) -> float | None:
    """Slope of the unwrapped mean link phase over frequency, in seconds.

    Needs at least two resolved frequencies, spaced closely enough that the
    phase moves by less than half a turn between neighbours.
    """
    ok = sorted((r for r in results if r.link_resolved), key=lambda r: r.frequency)
    if len(ok) < 2:
        return None
    f = np.array([r.frequency for r in ok])
    p = np.unwrap(np.array([r.link_mean_phase for r in ok]))
    slope: NDArray[np.float64] = np.polyfit(f, p, 1)
    return float(slope[0] / (2 * np.pi))


def run(args: argparse.Namespace) -> list[FrequencyResult]:
    """Drive the instruments through every frequency; RF off on any exit."""
    others = [c for c in args.siglent_channels if c != args.trigger_channel]
    gen = SG8SignalGenerator(args.resource)
    sig = SiglentSDS6204L(address=args.address, channels=tuple(args.siglent_channels))
    m5i = SpectrumM5i3367(args.device, channels=(args.m5i_channel,))
    results: list[FrequencyResult] = []
    with gen, sig, m5i:
        sig.configure(
            {
                "timebase": args.timebase,
                "range": args.range,
                "offset": 0.0,
                "coupling": "DC",
                "impedance": 50,
                "trigger_mode": "SINGle",
                "trigger": {
                    "source": args.trigger_channel,
                    "level": 0.0,
                    "slope": "RISING",
                },
            }
        )
        m5i.configure(
            {
                "sample_rate": 5e9,
                "record_length": args.m5i_samples,
                "pretrigger": 0.5,
                "range": args.m5i_range,
                "offset": 0.0,
                "trigger": {"source": "EXT", "level": args.level, "slope": "RISING"},
                "trigger_termination": args.termination,
            }
        )
        gen.set_power(args.power)
        gen.set_frequency(args.freqs[0] * 1e6)
        gen.set_output(True)
        time.sleep(OUTPUT_ON_SETTLE_S)
        try:
            for f_mhz in args.freqs:
                f = f_mhz * 1e6
                gen.set_frequency(f)
                gen.operation_complete()
                time.sleep(args.settle)
                shots: list[Shot] = []
                missed = spurious = 0
                for _ in range(args.shots):
                    m5i.arm()
                    if m5i.wait(args.quiet):
                        spurious += 1
                        m5i.fetch()
                        m5i.arm()
                    sig.arm()
                    ok_sig = sig.wait(args.timeout)
                    ok_m5i = m5i.wait(args.timeout)
                    if not ok_m5i:
                        missed += 1
                        m5i.abort()
                    if not ok_sig:
                        sig.abort()
                    if not (ok_sig and ok_m5i):
                        if ok_sig:
                            sig.fetch()
                        continue
                    cs, cm = sig.fetch(), m5i.fetch()
                    ps, _, _ = phase_at_zero(cs[args.trigger_channel], f)
                    po = phase_at_zero(cs[others[0]], f)[0] if others else None
                    pm, am, sm = phase_at_zero(cm[args.m5i_channel], f)
                    shots.append(Shot(ps, po, pm, am, sm))
                if not shots:
                    print(f"{f_mhz:8.1f} MHz  no linked shots (missed {missed})")
                    continue
                r = summarize(f, shots, missed, spurious)
                results.append(r)
                print(format_result(r), flush=True)
        finally:
            gen.set_output(False)
    return results


def format_result(r: FrequencyResult) -> str:
    """One console line per frequency."""
    pair = (
        ""
        if r.siglent_pair_jitter is None
        else f" (pair {r.siglent_pair_jitter * 1e12:.2f})"
    )
    link = (
        f"{r.link_jitter * 1e12:7.1f} ps"
        if r.link_resolved
        else f"unresolved (fit noise {r.link_fit_noise * 1e12:.0f} ps)"
    )
    return (
        f"{r.frequency / 1e6:8.1f} MHz  shots {r.shots}  missed {r.missed}"
        f"  spurious {r.spurious}  | Siglent jitter {r.siglent_jitter * 1e12:6.1f} ps"
        f"{pair}  | link jitter {link}  M5i {r.m5i_vpp * 1e3:.3f} mVpp"
    )


def build_parser() -> argparse.ArgumentParser:
    """Command-line options."""
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--resource", default="ASRL/dev/ttyUSB0::INSTR")
    p.add_argument("--address", default="192.168.0.11")
    p.add_argument("--device", default="/dev/spcm0")
    p.add_argument("--siglent-channels", nargs="+", default=["C2", "C3"])
    p.add_argument("--trigger-channel", default="C2")
    p.add_argument("--m5i-channel", default="CH0")
    p.add_argument("--power", type=float, default=0.0, help="dBm (max +5)")
    p.add_argument("--freqs", type=float, nargs="+", default=[100, 300, 1000])
    p.add_argument("--shots", type=int, default=30)
    p.add_argument("--level", type=float, default=1.5, help="M5i EXT level, V")
    p.add_argument("--termination", type=int, default=3000, choices=(50, 3000))
    p.add_argument("--timebase", type=float, default=1e-6, help="Siglent s/div")
    p.add_argument("--range", type=float, default=0.1, help="Siglent V/div")
    p.add_argument("--m5i-range", type=float, default=0.2, help="M5i ±V")
    p.add_argument("--m5i-samples", type=int, default=65536)
    p.add_argument("--settle", type=float, default=0.3, help="s after a step")
    p.add_argument("--quiet", type=float, default=0.3, help="s spurious window")
    p.add_argument("--timeout", type=float, default=5.0, help="s per shot")
    p.add_argument("--out", type=Path, default=Path("measurements/trigger_link"))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run the link test, save the summary, print the delay if resolvable."""
    args = build_parser().parse_args(argv)
    if args.power > MAX_POWER_DBM:
        print(f"--power capped at {MAX_POWER_DBM:+g} dBm", file=sys.stderr)
        return 2
    if args.trigger_channel not in args.siglent_channels:
        print("--trigger-channel must be one of --siglent-channels", file=sys.stderr)
        return 2
    results = run(args)
    delay = link_delay(results)
    if delay is None:
        print("link delay: not resolvable (put the tone on the M5i channel)")
    else:
        print(f"link delay: {delay * 1e9:+.3f} ns (includes signal-path difference)")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    record: dict[str, Any] = {
        "args": {
            k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
        },
        "results": [asdict(r) for r in results],
        "link_delay_s": delay,
    }
    path.write_text(json.dumps(record, indent=2))
    print("saved", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
