"""Cable delay differences from a multi-frequency phase sweep (SG8 -> Siglent).

Downstream application built on :mod:`cetal_scopes`. Steps the SG8 through a
CW frequency plan; at each step it acquires every requested Siglent channel in
**one** acquisition, fits them all at one frequency on one time axis
(:func:`cetal_scopes.analysis.phase_delay.phase_differences`), and records the
phase of each channel relative to ``--reference``. Per channel it then
unwraps along frequency and fits a line: slope -> delay, intercept -> polarity
(:func:`~cetal_scopes.analysis.phase_delay.fit_delay`).

What a single run measures, per channel X: ``tau(X) - tau(reference)`` of the
**whole path** -- cable, the pickup point of its far end in the field, and the
scope channel's own skew (spec'd only to < 100 ps). Separating the cable from
the skew needs a second run with the cables moved between scope inputs; each
run's ``points.csv`` and ``result.json`` are kept for exactly that.

Scope settings are written once, before the sweep, and never touched during
it: a skew calibration is only valid for the settings it was taken with.
Every run records them in ``metadata.json``.

Against hardware (SG8 on /dev/ttyUSB0, Siglent on the LAN)::

    uv run apps/phase_delay/phase_delay.py --label "cable1-4 on C1-C4"

Without hardware, with known per-channel delays (validates the pipeline)::

    uv run apps/phase_delay/phase_delay.py --demo
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import NDArray

from cetal_scopes import Capture, SG8SignalGenerator, SiglentSDS6204L
from cetal_scopes.analysis.phase_delay import (
    DelayFit,
    delay_to_length,
    fit_delay,
    phase_differences,
    wrap_phase,
)

#: The user's cap for this setup; the SG8 itself goes to ~+30 dBm.
MAX_POWER_DBM = 5.0
#: Siglent bandwidth; nothing above is measured.
MAX_FREQUENCY_HZ = 2e9
#: SDS6204L ADC comb at 5 GS/s: 128-sample period -> spurs every fs/128.
ADC_COMB_SPACING_HZ = 5e9 / 128
#: 500 us: the shortest window that stays at the native 5 GS/s (AGENTS.md).
TIMEBASE_S_PER_DIV = 50e-6
#: SG8 needs ~0.9 s after output-on to reach level; the first point still
#: showed extra phase scatter after 1.5 s, so wait longer.
OUTPUT_ON_SETTLE_S = 3.0
#: Residual RMS above this is worth a warning.
RESIDUAL_WARN_DEG = 1.5


class Generator(Protocol):
    """The SG8 subset this app uses."""

    def set_frequency(self, hertz: float) -> None: ...
    def set_power(self, dbm: float) -> None: ...
    def set_output(self, state: bool | str) -> None: ...
    def operation_complete(self) -> bool: ...
    def frequency(self) -> float: ...


class Digitizer(Protocol):
    """The scope subset this app uses."""

    def configure(self, settings: Any) -> None: ...
    def acquire(self) -> Capture: ...


def frequency_plan(start: float, stop: float, step: float) -> NDArray[np.float64]:
    """Linear plan ``start..stop`` inclusive, capped at :data:`MAX_FREQUENCY_HZ`."""
    if not 0 < start < stop or step <= 0:
        raise ValueError("expected 0 < start < stop and step > 0")
    if stop > MAX_FREQUENCY_HZ:
        raise ValueError(f"stop above {MAX_FREQUENCY_HZ / 1e9:g} GHz scope bandwidth")
    return np.arange(start, stop + step / 2, step)


def near_adc_comb(freqs: NDArray[np.float64], guard: float) -> NDArray[np.bool_]:
    """True where a frequency is within ``guard`` Hz of an ADC comb spur."""
    k = np.round(freqs / ADC_COMB_SPACING_HZ)
    return (k > 0) & (np.abs(freqs - k * ADC_COMB_SPACING_HZ) < guard)


@dataclass
class Sweep:
    """Raw per-frequency, per-repeat results of one run."""

    channels: tuple[str, ...]
    reference: str
    f_set: list[float] = field(default_factory=list)
    f_fit: list[list[float]] = field(default_factory=list)
    #: [freq][repeat][channel] wrapped phi_ref - phi_ch, radians
    dphi: list[list[list[float]]] = field(default_factory=list)
    #: [freq][repeat][channel] amplitude, volts (reference included, first)
    amp: list[list[list[float]]] = field(default_factory=list)
    #: [freq][repeat][channel] record peak-to-peak, volts
    vpp: list[list[list[float]]] = field(default_factory=list)

    def arrays(self) -> dict[str, Any]:
        """Everything as arrays, for ``np.savez``."""
        return {
            "f_set": np.asarray(self.f_set),
            "f_fit": np.asarray(self.f_fit),
            "dphi": np.asarray(self.dphi),
            "amp": np.asarray(self.amp),
            "vpp": np.asarray(self.vpp),
        }


def circular_stats(
    values: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Mean (wrapped) and spread of phases (radians) along the last axis."""
    mean = np.angle(np.mean(np.exp(1j * values), axis=-1))
    spread = np.std(wrap_phase(values - mean[..., None]), axis=-1, ddof=1)
    return mean, spread


def _fit_one(
    capture: Capture, reference: str, others: tuple[str, ...], f: float
) -> tuple[float, list[float], list[float], list[float]]:
    """Fit one acquisition: (f_fit, dphi per channel, amplitudes, record pp)."""
    f_hat, fits = phase_differences(capture, reference, others, f)
    ref_phase = fits[reference].phase
    dphi = [float(wrap_phase(ref_phase - fits[ch].phase)) for ch in others]
    names = (reference, *others)
    amp = [fits[ch].amplitude for ch in names]
    vpp = [float(np.ptp(capture[ch].volts)) for ch in names]
    return f_hat, dphi, amp, vpp


def run_sweep(
    generator: Generator,
    scope: Digitizer,
    *,
    freqs: NDArray[np.float64],
    reference: str,
    channels: Sequence[str],
    repeats: int,
    settle: float,
    log: Any = print,
) -> Sweep:
    """Step ``generator`` through ``freqs`` and fit every channel per acquisition."""
    others = tuple(ch for ch in channels if ch != reference)
    sweep = Sweep(channels=others, reference=reference)
    generator.set_frequency(float(freqs[0]))
    generator.set_output(True)
    time.sleep(OUTPUT_ON_SETTLE_S)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        for i, f in enumerate(freqs):
            generator.set_frequency(float(f))
            generator.operation_complete()
            time.sleep(settle)
            # Fit in a worker while the next record transfers.
            jobs = [
                pool.submit(_fit_one, scope.acquire(), reference, others, float(f))
                for _ in range(repeats)
            ]
            f_fit, dphi, amp, vpp = zip(*(job.result() for job in jobs), strict=True)
            sweep.f_set.append(float(f))
            sweep.f_fit.append(list(f_fit))
            sweep.dphi.append(list(dphi))
            sweep.amp.append(list(amp))
            sweep.vpp.append(list(vpp))
            mean, spread = circular_stats(np.asarray(dphi, dtype=np.float64).T)
            text = "  ".join(
                f"{ch} {np.degrees(m):+7.2f}±{np.degrees(s):.2f}°"
                for ch, m, s in zip(others, mean, spread, strict=True)
            )
            ppm = (np.mean(f_fit) / f - 1) * 1e6
            log(
                f"[{i + 1:3d}/{len(freqs)}] {f / 1e6:7.1f} MHz ({ppm:+.2f} ppm)  {text}"
            )
    finally:
        pool.shutdown(cancel_futures=True)
        generator.set_output(False)
    return sweep


@dataclass(frozen=True)
class ChannelResult:
    """Line fit for one channel against the reference."""

    channel: str
    fit: DelayFit
    excluded: NDArray[np.float64]


def analyze(
    sweep: Sweep, *, comb_guard: float
) -> tuple[list[ChannelResult], dict[str, Any]]:
    """Fit a delay per channel; excludes frequencies on the ADC comb."""
    f_fit = np.mean(np.asarray(sweep.f_fit), axis=1)
    mean, spread = circular_stats(np.moveaxis(np.asarray(sweep.dphi), 1, -1))
    keep = ~near_adc_comb(np.asarray(sweep.f_set), comb_guard)
    results = []
    for j, ch in enumerate(sweep.channels):
        fit = fit_delay(f_fit[keep], mean[keep, j])
        results.append(ChannelResult(ch, fit, f_fit[~keep]))
    stats = {"mean": mean, "spread": spread, "f_fit": f_fit, "keep": keep}
    return results, stats


def save_run(
    out: Path,
    sweep: Sweep,
    results: list[ChannelResult],
    stats: dict[str, Any],
    metadata: dict[str, Any],
    velocity_factor: float,
) -> dict[str, Any]:
    """Write ``points.csv``, ``sweep.npz``, ``result.json``, ``metadata.json``."""
    out.mkdir(parents=True, exist_ok=True)
    np.savez(out / "sweep.npz", **sweep.arrays())
    amp = np.mean(np.asarray(sweep.amp), axis=1)
    with (out / "points.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "channel",
                "f_set_hz",
                "f_fit_hz",
                "dphi_deg",
                "dphi_unwrapped_deg",
                "dphi_std_deg",
                "amp_ref_v",
                "amp_v",
                "residual_deg",
                "used_in_fit",
            ]
        )
        for j, result in enumerate(results):
            kept = [int(k) for k in np.flatnonzero(stats["keep"])]
            unwrapped = dict(zip(kept, np.degrees(result.fit.unwrapped), strict=True))
            residual = dict(zip(kept, np.degrees(result.fit.residuals), strict=True))
            for i, f in enumerate(sweep.f_set):
                writer.writerow(
                    [
                        result.channel,
                        f"{f:.0f}",
                        f"{stats['f_fit'][i]:.3f}",
                        f"{np.degrees(stats['mean'][i, j]):.5f}",
                        f"{unwrapped.get(i, float('nan')):.5f}",
                        f"{np.degrees(stats['spread'][i, j]):.5f}",
                        f"{amp[i, 0]:.6e}",
                        f"{amp[i, j + 1]:.6e}",
                        f"{residual.get(i, float('nan')):.5f}",
                        int(stats["keep"][i]),
                    ]
                )
    summary = {
        "reference": sweep.reference,
        "velocity_factor": velocity_factor,
        "channels": {
            r.channel: {
                "delay_ps": r.fit.delay * 1e12,
                "delay_stderr_ps": r.fit.delay_stderr * 1e12,
                "length_mm": delay_to_length(r.fit.delay, velocity_factor) * 1e3,
                "intercept_deg": r.fit.intercept_deg,
                "residual_rms_deg": r.fit.residual_rms_deg,
                "excluded_hz": r.excluded.tolist(),
            }
            for r in results
        },
    }
    (out / "result.json").write_text(json.dumps(summary, indent=2))
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    return summary


def plot_run(
    out: Path, sweep: Sweep, results: list[ChannelResult], stats: dict[str, Any]
) -> None:
    """Unwrapped phase with fit, residuals, and amplitudes vs frequency."""
    fig, (ax_phase, ax_res, ax_amp) = plt.subplots(
        3, 1, figsize=(9, 10), sharex=True, constrained_layout=True
    )
    amp = np.mean(np.asarray(sweep.amp), axis=1)
    for j, r in enumerate(results):
        f_ghz = r.fit.frequencies / 1e9
        line = r.fit.unwrapped - r.fit.residuals
        (p,) = ax_phase.plot(f_ghz, np.degrees(r.fit.unwrapped), "o", ms=3)
        ax_phase.plot(
            f_ghz,
            np.degrees(line),
            "-",
            color=p.get_color(),
            label=f"{r.channel}: {r.fit.delay * 1e12:+.2f} ps",
        )
        ax_res.plot(f_ghz, np.degrees(r.fit.residuals), ".-", color=p.get_color())
        ax_amp.plot(
            np.asarray(sweep.f_set) / 1e9, amp[:, j + 1] * 1e3, color=p.get_color()
        )
    ax_amp.plot(np.asarray(sweep.f_set) / 1e9, amp[:, 0] * 1e3, "k--", label="ref")
    for f in results[0].excluded if results else []:
        for ax in (ax_phase, ax_res, ax_amp):
            ax.axvline(f / 1e9, color="0.85", lw=4, zorder=0)
    ax_phase.set_ylabel(f"φ({sweep.reference}) − φ(ch)  [deg]")
    ax_phase.legend()
    ax_res.set_ylabel("residual [deg]")
    ax_res.axhline(0, color="0.5", lw=0.8)
    ax_amp.set_ylabel("amplitude [mV]")
    ax_amp.set_xlabel("frequency [GHz]")
    ax_amp.legend()
    fig.savefig(out / "phase_delay.png", dpi=120)
    plt.close(fig)


class DemoGenerator:
    """Stands in for the SG8; remembers the frequency."""

    def __init__(self) -> None:
        self.f = 0.0

    def set_frequency(self, hertz: float) -> None:
        self.f = hertz

    def set_power(self, dbm: float) -> None:
        pass

    def set_output(self, state: bool | str) -> None:
        pass

    def operation_complete(self) -> bool:
        return True

    def frequency(self) -> float:
        return self.f


class DemoScope:
    """Synthetic 4-channel scope: per-channel delays, a 2 ppm clock, noise."""

    DELAYS: ClassVar[dict[str, float]] = {
        "C1": 0.0,
        "C2": 12.0e-12,
        "C3": -30.0e-12,
        "C4": 85.0e-12,
    }

    def __init__(self, generator: DemoGenerator, channels: Sequence[str]) -> None:
        self._gen = generator
        self._channels = tuple(channels)
        self._rng = np.random.default_rng(0)

    def configure(self, settings: Any) -> None:
        pass

    def acquire(self) -> Capture:
        n = 100_000
        t = -n / 2 / 5e9 + np.arange(n) / 5e9
        f = self._gen.f * (1 + 2e-6)
        phase0 = self._rng.uniform(0, 2 * np.pi)
        rows = [
            1e-3 * np.cos(2 * np.pi * f * (t - self.DELAYS[ch]) + phase0)
            + self._rng.normal(0, 3e-4, n)
            for ch in self._channels
        ]
        return Capture(
            volts=np.vstack(rows),
            t0=float(t[0]),
            dt=2e-10,
            channel_names=self._channels,
        )


def build_parser() -> argparse.ArgumentParser:
    """Command-line options."""
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--resource", default="ASRL/dev/ttyUSB0::INSTR")
    p.add_argument("--address", default="192.168.0.11")
    p.add_argument("--channels", nargs="+", default=["C1", "C2", "C3", "C4"])
    p.add_argument("--reference", default="C1")
    p.add_argument("--start", type=float, default=100e6, help="Hz")
    p.add_argument("--stop", type=float, default=2e9, help="Hz")
    p.add_argument("--step", type=float, default=25e6, help="Hz")
    p.add_argument("--power", type=float, default=5.0, help="dBm (max +5)")
    p.add_argument("--range", type=float, default=0.008, help="V/div (screen = 8 div)")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--settle", type=float, default=0.2, help="s after each step")
    p.add_argument("--comb-guard", type=float, default=100e3, help="Hz")
    p.add_argument("--vf", type=float, default=0.85, help="cable velocity factor")
    p.add_argument("--label", default="", help="free text: what is wired where")
    p.add_argument("--out", type=Path, default=Path("measurements/phase_delay"))
    p.add_argument("--demo", action="store_true", help="synthetic instruments")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run one sweep, save it, print the per-channel delays."""
    args = build_parser().parse_args(argv)
    if args.power > MAX_POWER_DBM:
        print(f"--power capped at {MAX_POWER_DBM:+g} dBm", file=sys.stderr)
        return 2
    if args.reference not in args.channels:
        print("--reference must be one of --channels", file=sys.stderr)
        return 2
    freqs = frequency_plan(args.start, args.stop, args.step)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / stamp
    settings = {
        "timebase": TIMEBASE_S_PER_DIV,
        "range": args.range,
        "offset": 0.0,
        "coupling": "DC",
        "impedance": 50,
        "channels": list(args.channels),
    }

    if args.demo:
        generator: Any = DemoGenerator()
        scope: Any = DemoScope(generator, args.channels)
        close: list[Any] = []
    else:
        generator = SG8SignalGenerator(args.resource)
        scope = SiglentSDS6204L(
            address=args.address, channels=tuple(args.channels), trigger_mode="AUTO"
        )
        generator.connect()
        scope.connect()
        close = [scope, generator]
    try:
        generator.set_power(args.power)
        scope.configure(settings)
        first = scope.acquire()
        metadata = {
            "timestamp": stamp,
            "label": args.label,
            "mode": "single run, reference " + args.reference,
            "source_power_dbm": args.power,
            "frequency_plan_hz": [args.start, args.stop, args.step],
            "repeats": args.repeats,
            "settle_s": args.settle,
            "scope_settings": settings,
            "scope_metadata": first.metadata,
            "demo": args.demo,
        }
        start = time.monotonic()
        sweep = run_sweep(
            generator,
            scope,
            freqs=freqs,
            reference=args.reference,
            channels=args.channels,
            repeats=args.repeats,
            settle=args.settle,
        )
        metadata["duration_s"] = time.monotonic() - start
    finally:
        for instrument in close:
            instrument.close()

    results, stats = analyze(sweep, comb_guard=args.comb_guard)
    summary = save_run(out, sweep, results, stats, metadata, args.vf)
    plot_run(out, sweep, results, stats)

    vpp_max = float(np.max(np.asarray(sweep.vpp)))
    if not args.demo and vpp_max > 0.9 * 8 * args.range:
        print(f"WARNING: record pp {vpp_max * 1e3:.1f} mV near full scale (clipping?)")
    print(f"\nreference {args.reference}; saved to {out}")
    for ch, r in summary["channels"].items():
        flag = "  <-- residual" if r["residual_rms_deg"] > RESIDUAL_WARN_DEG else ""
        print(
            f"  {ch}: tau = {r['delay_ps']:+8.2f} ± {r['delay_stderr_ps']:.2f} ps"
            f"  ({r['length_mm']:+6.2f} mm @ VF {args.vf})"
            f"  intercept {r['intercept_deg']:6.1f}°"
            f"  resid {r['residual_rms_deg']:.2f}° rms{flag}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
