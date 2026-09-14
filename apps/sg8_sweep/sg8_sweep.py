"""Frequency-response sweep: SG8 generator RF Out -> Siglent scope C1-C3.

Downstream application built on :mod:`cetal_scopes`; it contains no reusable
library code. Drives the SG8 at a fixed output power across a frequency sweep
(default 10 MHz-2 GHz, log-spaced by default -- ``--no-log`` for a linear
grid), captures each requested scope channel at every step, measures the
response via coherent (matched-filter) detection at the commanded frequency,
and plots one trace per channel: dB gain relative to the source level by
default, or raw Vpp with ``--raw-volts``.

The scope's timebase is set **once**, sized for ``--cycles`` periods of the
sweep's *lowest* frequency, and reused unchanged for every point -- a scope
holds its full real-time sample rate across a wide range of window sizes, so
this gives every point the same generously large sample count for free,
instead of shrinking (and so getting noisier) at the high end of the sweep
the way a per-point, constant-cycle-count window would.

There's no hardware interlock between the two instruments -- each frequency
step confirms the SCPI command itself completed via ``*OPC?`` (per the SG8
manual's own recommendation over a fixed delay), then still waits
``--settle`` seconds as margin for the PLL/DDS to physically settle, which
``*OPC?`` doesn't cover. Every point is also self-checked for this: the first
and second half of its capture are independently measured and compared
(:func:`split_half_consistency_db`), and a large, above-noise-floor gap
between them -- a sign the signal was still transitioning mid-capture -- is
flagged on stderr and recorded per-channel in the output CSV
(``--settle-warn-db`` / ``--settle-warn-min-vpp`` tune the threshold).

Known hardware caveat: the SDS6204L's 16-bit acquisition path carries a
deterministic ADC-interleave comb (period 256 samples, spurs at multiples of
``fs/256``, e.g. ~39.06 MHz at 10 GSa/s -- see
:func:`cetal_scopes.analysis.remove_adc_comb`). It's a fixed pattern in ADC
*codes*, so it becomes a larger fraction of the signal -- and so more visible
in a coherent-amplitude reading -- either with a coarser V/div (each code
step is worth more volts) or with a longer capture window (more repeats of
the comb per record). Both conditions are easy to hit here: the
default-computed V/div (see ``--vdiv-divisor``) reflects the source's
*theoretical* level, which is often far coarser than a real (e.g. radiated,
not conducted) signal warrants, and the sweep's fixed window (see above) can
be fairly long. If readings look implausibly large or erratic, especially
near a multiple of ``fs/256``, pass a tighter ``--vdiv`` sized to the actual
signal rather than the source level.

Against real hardware (RF Out split/connected to C1-C3)::

    uv run apps/sg8_sweep/sg8_sweep.py \\
        --resource ASRL/dev/ttyUSB0::INSTR --address 192.168.5.181

A linear sweep plotted in raw volts instead of dB::

    uv run apps/sg8_sweep/sg8_sweep.py --no-log --raw-volts

Generator-only, e.g. when the response is read out on separate equipment --
skips the scope entirely, so no capture, plot, or CSV output happens::

    uv run apps/sg8_sweep/sg8_sweep.py --resource ASRL/dev/ttyUSB0::INSTR --no-scope

Without hardware, from a synthetic source with a simulated per-channel
low-pass response::

    uv run apps/sg8_sweep/sg8_sweep.py --demo
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from types import TracebackType
from typing import Protocol, Self

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from numpy.typing import NDArray

from cetal_scopes import Capture, SG8SignalGenerator, SiglentSDS6204L
from cetal_scopes.scopes.base import Scope
from cetal_scopes.scopes.siglent import DEFAULT_ADDRESS

DEFAULT_RESOURCE = "ASRL/dev/ttyUSB0::INSTR"
DEFAULT_START_HZ = 10e6
DEFAULT_STOP_HZ = 2e9
DEFAULT_POINTS = 40
DEFAULT_POWER_DBM = -10.0
DEFAULT_CYCLES = 20.0
DEFAULT_SETTLE = 0.05
#: Minimum dB gap between a capture's two halves (see
#: :func:`split_half_consistency_db`) before it's flagged as possibly unsettled.
DEFAULT_SETTLE_WARN_DB = 6.0
#: Minimum measured amplitude before a settle-gap warning is trusted; below
#: this, a large gap is more likely just noise than real timing drift.
DEFAULT_SETTLE_WARN_MIN_VPP = 2.0e-3
GRID_NUM = 10


def log_sweep(start: float, stop: float, points: int) -> NDArray[np.float64]:
    """Log-spaced frequencies from ``start`` to ``stop``, inclusive.

    Parameters
    ----------
    start, stop : float
        Sweep endpoints in hertz, ``0 < start < stop``.
    points : int
        Number of frequencies, at least 2.
    """
    if points < 2:
        raise ValueError(f"points must be >= 2, got {points}")
    if not (0.0 < start < stop):
        raise ValueError(f"expected 0 < start < stop, got {start!r}, {stop!r}")
    return np.geomspace(start, stop, points)


def linear_sweep(start: float, stop: float, points: int) -> NDArray[np.float64]:
    """Evenly-spaced frequencies from ``start`` to ``stop``, inclusive.

    Parameters
    ----------
    start, stop : float
        Sweep endpoints in hertz, ``0 <= start < stop``.
    points : int
        Number of frequencies, at least 2.
    """
    if points < 2:
        raise ValueError(f"points must be >= 2, got {points}")
    if not (0.0 <= start < stop):
        raise ValueError(f"expected 0 <= start < stop, got {start!r}, {stop!r}")
    return np.linspace(start, stop, points)


def frequency_sweep(start: float, stop: float, points: int, *, log: bool = True) -> NDArray[np.float64]:
    """Dispatch to :func:`log_sweep` or :func:`linear_sweep` on ``log``."""
    return log_sweep(start, stop, points) if log else linear_sweep(start, stop, points)


def timebase_for_frequency(
    freq: float, *, cycles: float = DEFAULT_CYCLES, grid_num: int = GRID_NUM
) -> float:
    """Horizontal scale (s/div) that fits ``cycles`` periods of ``freq``.

    :func:`run_sweep` calls this **once**, for the sweep's *lowest*
    frequency, and uses that one window for every point -- not per
    frequency. A per-point window sized to keep a constant cycle count
    shrinks (and so starves the sample count, since the scope's sample
    *rate* stays fixed regardless of timebase) as frequency rises: 20 cycles
    at 10 MHz is a ~20,000-sample record, but 20 cycles at 2 GHz is only
    ~100 samples. Too few samples starves both the scope's own FFT (which
    visibly flattens) and :func:`coherent_amplitude`'s SNR -- which showed
    up as spurious :func:`split_half_consistency_db` warnings that didn't
    actually improve with more settling time, because they were measurement
    noise, not drift. Measured against an SDS6204L with 3 channels active,
    the scope holds its full real-time sample rate for windows up to ~200 us
    (up to ~2,000,000 samples) before it has to drop the rate to keep up --
    far larger than any window this sweep needs -- so fixing the window at
    the lowest frequency's cycle count and reusing it everywhere gives every
    point in the sweep the same generous sample count, low frequency or
    high, with no per-point heuristic needed at all.
    """
    if freq <= 0.0:
        raise ValueError(f"freq must be positive, got {freq!r}")
    return cycles / freq / grid_num


def expected_vpp(power_dbm: float, impedance: float = 50.0) -> float:
    """Peak-to-peak voltage of a sine at ``power_dbm`` into ``impedance`` ohms."""
    watts = 1.0e-3 * 10.0 ** (power_dbm / 10.0)
    v_rms = math.sqrt(watts * impedance)
    return v_rms * 2.0 * math.sqrt(2.0)


def resolve_vdiv(
    power_dbm: float, impedance: float, divisor: float, override: float | None
) -> float:
    """Vertical scale (V/div) to configure on the scope.

    Returns ``override`` unchanged when given. Otherwise derives it from the
    *theoretical* source level, ``expected_vpp(power_dbm, impedance) /
    divisor`` (floored at 1 mV/div) -- which reflects a conducted connection
    at the commanded power, not necessarily the actual (e.g. radiated)
    signal reaching the channel. Pass ``override`` when the real signal is
    nowhere near that theoretical level.
    """
    if override is not None:
        return override
    vpp = expected_vpp(power_dbm, impedance)
    return max(vpp / divisor, 1.0e-3)


def _coherent_phasor(
    volts: NDArray[np.float64], t: NDArray[np.float64], freq: float
) -> complex:
    """In-phase/quadrature correlation of ``volts`` against ``freq``.

    For ``v = A sin(2*pi*f*t)``, ``mean(v * cos(2*pi*f*t)) = 0`` and
    ``mean(v * sin(2*pi*f*t)) = A/2``, so the peak amplitude is
    ``2 * abs(phasor)``. Shared by :func:`coherent_amplitude` and
    :func:`split_half_consistency_db`.
    """
    i = float(np.mean(volts * np.cos(2.0 * np.pi * freq * t)))
    q = float(np.mean(volts * np.sin(2.0 * np.pi * freq * t)))
    return complex(i, q)


def coherent_amplitude(
    volts: NDArray[np.float64], t: NDArray[np.float64], freq: float
) -> float:
    """Peak-to-peak sine amplitude at ``freq`` via coherent detection.

    Correlates the record against reference sine/cosine at the known
    frequency instead of searching an FFT bin, so it stays accurate even
    though the scope's timebase snaps to its own step ladder rather than
    landing on an exact integer number of cycles. The peak amplitude is
    ``2 * abs(phasor)`` (see :func:`_coherent_phasor`); doubled again here
    to return Vpp, matching every caller's units (CSV columns, plot labels,
    and :func:`expected_vpp`).
    """
    return 4.0 * abs(_coherent_phasor(volts, t, freq))


def split_half_consistency_db(
    volts: NDArray[np.float64], t: NDArray[np.float64], freq: float
) -> float:
    """Absolute dB gap between the first and second half of one capture.

    Answers "was the generator actually settled at ``freq`` for the whole
    capture, or still transitioning?" using only the capture itself -- no
    extra reference measurement needed. A signal that's a clean, stable tone
    at ``freq`` for the entire record gives near-identical coherent-detection
    estimates in both halves; a record that starts mid-transition (PLL still
    slewing, DDS still ramping, or genuinely the wrong frequency) decorrelates
    unevenly against the two halves and shows a large gap. This is a
    *diagnostic*, not proof either way: a weak/noisy signal near the noise
    floor will also show a large gap despite being perfectly settled, so
    callers should only act on this above some minimum amplitude.
    """
    n = volts.size
    if n < 4:
        return 0.0
    mid = n // 2
    first = coherent_amplitude(volts[:mid], t[:mid], freq)
    second = coherent_amplitude(volts[mid:], t[mid:], freq)
    if first <= 0.0 and second <= 0.0:
        return 0.0
    hi = max(first, second)
    lo = max(min(first, second), 1e-12)
    return 20.0 * math.log10(hi / lo)


@dataclass(frozen=True)
class SweepResult:
    """Per-channel measured peak-to-peak amplitude across swept frequencies."""

    frequencies: NDArray[np.float64]
    amplitudes: dict[str, NDArray[np.float64]]
    power_dbm: float
    settle_gap_db: dict[str, NDArray[np.float64]] = field(default_factory=dict)
    """Per-channel :func:`split_half_consistency_db`, a settling diagnostic."""


class SignalSource(Protocol):
    """The subset of :class:`SG8SignalGenerator`'s interface this app needs."""

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def reset(self) -> None: ...
    def set_power(self, dbm: float) -> None: ...
    def set_frequency(self, hertz: float) -> None: ...
    def frequency(self) -> float: ...
    def set_output(self, state: bool | str) -> None: ...
    def operation_complete(self) -> bool: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


def run_sweep(
    generator: SignalSource,
    scope: Scope | None,
    *,
    channels: Sequence[str],
    frequencies: NDArray[np.float64],
    power_dbm: float,
    cycles: float = DEFAULT_CYCLES,
    settle: float = DEFAULT_SETTLE,
    reset_settle: float = 1.0,
    settle_warn_db: float = DEFAULT_SETTLE_WARN_DB,
    settle_warn_min_vpp: float = DEFAULT_SETTLE_WARN_MIN_VPP,
    on_point: Callable[[int, float, dict[str, float]], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> SweepResult:
    """Sweep ``generator`` over ``frequencies`` and measure each channel.

    Sets the scope's timebase **once**, sized to fit ``cycles`` periods of
    the sweep's *lowest* frequency (see :func:`timebase_for_frequency`), and
    reuses that one window for every point -- a scope holds its full
    real-time sample rate across a wide range of window sizes (measured up
    to ~200 us on an SDS6204L with 3 channels active), so a window sized for
    the lowest frequency gives every higher frequency in the sweep the exact
    same, generously large sample count for free, with no per-point
    retuning needed.

    Then, resets and re-configures the generator once, and for every
    frequency: sets it, waits for :meth:`SignalSource.operation_complete` so
    the *SCPI command itself* is confirmed processed before counting any
    further settling time (the SG8 manual recommends ``*OPC?`` over a fixed
    delay for exactly this), waits ``settle`` seconds more as margin for the
    PLL/DDS to physically settle (a real hardware effect ``*OPC?`` doesn't
    cover), and measures each channel via :func:`coherent_amplitude` against
    the generator's actual (read-back) frequency. RF Out is always turned
    off again on exit, even on error.

    There's still no hardware interlock between the two instruments --
    ``settle`` is a heuristic, not a guarantee -- so every point is also
    checked with :func:`split_half_consistency_db`: when a channel's
    amplitude is at least ``settle_warn_min_vpp`` and its half-record gap is
    at least ``settle_warn_db``, ``on_warning`` (if given) is called with a
    human-readable message flagging that specific point as possibly
    unsettled. The gap itself is always recorded in
    :attr:`SweepResult.settle_gap_db` for offline inspection either way.

    ``scope=None`` drives the generator through the sweep without touching a
    scope at all (e.g. when it's read out separately); ``channels`` should
    then be empty, and the returned :class:`SweepResult` carries no
    amplitude data.
    """
    generator.reset()
    time.sleep(reset_settle)
    generator.set_power(power_dbm)
    generator.set_output(True)

    if scope is not None and len(frequencies) > 0:
        lowest_freq = float(np.min(frequencies))
        scope.configure({"timebase": timebase_for_frequency(lowest_freq, cycles=cycles)})

    amplitudes = {ch: np.full(len(frequencies), np.nan) for ch in channels}
    settle_gap = {ch: np.full(len(frequencies), np.nan) for ch in channels}
    try:
        for index, freq in enumerate(frequencies):
            generator.set_frequency(float(freq))
            generator.operation_complete()
            point: dict[str, float] = {}
            time.sleep(settle)
            actual_freq = generator.frequency()
            if scope is not None:
                capture = scope.acquire()
                for ch in channels:
                    if ch not in capture:
                        continue
                    channel = capture[ch]
                    amplitude = coherent_amplitude(
                        channel.volts, channel.time, actual_freq
                    )
                    amplitudes[ch][index] = amplitude
                    point[ch] = amplitude

                    gap_db = split_half_consistency_db(
                        channel.volts, channel.time, actual_freq
                    )
                    settle_gap[ch][index] = gap_db
                    if (
                        on_warning is not None
                        and amplitude >= settle_warn_min_vpp
                        and gap_db >= settle_warn_db
                    ):
                        on_warning(
                            f"{ch} @ {actual_freq / 1e6:.3f} MHz: {gap_db:.1f} dB gap "
                            "between capture halves (possibly not yet settled)"
                        )
            if on_point is not None:
                on_point(index, actual_freq, point)
    finally:
        generator.set_output(False)

    return SweepResult(
        frequencies=np.asarray(frequencies, dtype=np.float64),
        amplitudes=amplitudes,
        power_dbm=power_dbm,
        settle_gap_db=settle_gap,
    )


def plot_sweep(
    result: SweepResult,
    *,
    reference_vpp: float | None = None,
    log_x: bool = True,
) -> Figure:
    """One subplot per channel: amplitude (or gain, if referenced) vs frequency.

    ``reference_vpp`` picks dB gain (set) vs raw Vpp (``None``); ``log_x``
    picks a log- vs linearly-scaled frequency axis, independent of whether
    the underlying sweep itself was log- or linearly-spaced.
    """
    channels = list(result.amplitudes)
    fig, axes_obj = plt.subplots(
        len(channels),
        1,
        sharex=True,
        figsize=(9.0, 2.5 * len(channels)),
        layout="constrained",
    )
    axes = [axes_obj] if len(channels) == 1 else list(axes_obj)
    for ax, ch in zip(axes, channels):
        amp = result.amplitudes[ch]
        if reference_vpp:
            with np.errstate(divide="ignore"):
                y = 20.0 * np.log10(np.clip(amp, 1e-12, None) / reference_vpp)
            ax.set_ylabel("gain (dB)")
        else:
            y = amp
            ax.set_ylabel("Vpp (V)")
        ax.plot(result.frequencies, y, marker=".")
        ax.set_xscale("log" if log_x else "linear")
        ax.grid(True, which="both", alpha=0.3)
        ax.set_title(ch)
    axes[-1].set_xlabel("frequency (Hz)")
    fig.suptitle(f"SG8 sweep @ {result.power_dbm:g} dBm")
    return fig


def save_csv(result: SweepResult, path: str) -> None:
    """Write ``frequency_hz, <channel>_vpp, <channel>_settle_gap_db, ...`` rows."""
    channels = list(result.amplitudes)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frequency_hz",
                *[f"{ch}_vpp" for ch in channels],
                *[f"{ch}_settle_gap_db" for ch in channels],
            ]
        )
        for i, freq in enumerate(result.frequencies):
            gaps = [result.settle_gap_db.get(ch, [float("nan")] * (i + 1))[i] for ch in channels]
            writer.writerow([freq, *[result.amplitudes[ch][i] for ch in channels], *gaps])


class DemoGenerator:
    """Synthetic stand-in for :class:`SG8SignalGenerator` (no hardware)."""

    def __init__(self) -> None:
        self._frequency = 1.0e9
        self._power = 0.0
        self._output = False

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def close(self) -> None:
        """No-op; safe to call repeatedly."""

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def reset(self) -> None:
        """Restore SG8 power-on defaults (CW, 1 GHz, 0 dBm, RF Out off)."""
        self._frequency = 1.0e9
        self._power = 0.0
        self._output = False

    def set_power(self, dbm: float) -> None:
        self._power = float(dbm)

    def set_frequency(self, hertz: float) -> None:
        self._frequency = float(hertz)

    def frequency(self) -> float:
        return self._frequency

    def set_output(self, state: bool | str) -> None:
        if isinstance(state, bool):
            self._output = state
        else:
            self._output = state.strip().upper() in ("1", "ON")

    def operation_complete(self) -> bool:
        """No-op; the demo source applies every command synchronously."""
        return True

    @property
    def output(self) -> bool:
        return self._output

    @property
    def power(self) -> float:
        return self._power


#: Demo per-channel single-pole low-pass cutoffs, Hz. ``None`` means flat.
DEMO_CUTOFFS: dict[str, float | None] = {"C1": None, "C2": 500e6, "C3": 100e6}


class DemoScope(Scope):
    """Synthetic scope: a sine at the demo generator's frequency and power.

    Each channel gets an independent simulated single-pole low-pass response
    (see :data:`DEMO_CUTOFFS`) plus noise, so a demo sweep shows a plausible
    per-channel roll-off without any hardware attached.

    Samples at a **fixed** ``dt`` and grows the sample count with the
    requested window, capped at ``max_samples`` -- mirroring an SDS6204L,
    which was measured holding its full real-time sample rate across a wide
    range of window sizes rather than resampling to keep a fixed record
    length. Getting this backwards (fixed sample count, ``dt`` scaling with
    the window) would silently alias a high-frequency demo tone once
    :func:`run_sweep` stopped re-adapting the timebase per point.
    """

    def __init__(
        self,
        generator: DemoGenerator,
        *,
        channels: Sequence[str] = ("C1", "C2", "C3"),
        impedance: float = 50.0,
        sample_rate: float = 10.0e9,
        max_samples: int = 2_000_000,
        noise: float = 0.002,
        seed: int = 0,
    ) -> None:
        self._generator = generator
        self.channels = tuple(channels)
        self._impedance = impedance
        self._dt = 1.0 / sample_rate
        self._max_samples = max_samples
        self._noise = noise
        self._rng = np.random.default_rng(seed)
        self._timebase = 1.0e-8

    def connect(self) -> None:
        """No-op; the demo source is always available."""

    def configure(self, settings: object) -> None:
        """Apply ``{"timebase": scale}``; other keys are ignored."""
        if isinstance(settings, dict) and "timebase" in settings:
            self._timebase = float(settings["timebase"])

    def acquire(self) -> Capture:
        """Return one synthetic multi-channel sine capture."""
        window = self._timebase * GRID_NUM
        n_samples = min(self._max_samples, max(2, round(window / self._dt)))
        t = np.arange(n_samples, dtype=np.float64) * self._dt
        freq = self._generator.frequency()
        vpp = expected_vpp(self._generator.power, self._impedance) if self._generator.output else 0.0
        rows = []
        for ch in self.channels:
            cutoff = DEMO_CUTOFFS.get(ch)
            gain = 1.0 if cutoff is None else 1.0 / math.sqrt(1.0 + (freq / cutoff) ** 2)
            amplitude = 0.5 * vpp * gain
            noise = self._noise * self._rng.standard_normal(n_samples)
            rows.append(amplitude * np.sin(2.0 * np.pi * freq * t) + noise)
        return Capture(volts=np.vstack(rows), t0=0.0, dt=self._dt, channel_names=self.channels)

    def close(self) -> None:
        """No-op; safe to call repeatedly."""


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
        prog="sg8_sweep",
        description=(
            "Sweep the SG8 signal generator at a fixed power up to 2 GHz "
            "and record/plot the response on scope channels C1-C3."
        ),
    )
    parser.add_argument(
        "--resource", default=DEFAULT_RESOURCE, help="generator PyVISA ASRL resource"
    )
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="scope IP address")
    parser.add_argument(
        "--channels", nargs="+", default=["C1", "C2", "C3"], help="channels, e.g. C1 C2 C3"
    )
    parser.add_argument(
        "--power", type=float, default=DEFAULT_POWER_DBM, help="fixed RF Out level in dBm"
    )
    parser.add_argument(
        "--start", type=float, default=DEFAULT_START_HZ, help="sweep start frequency in Hz"
    )
    parser.add_argument(
        "--stop", type=float, default=DEFAULT_STOP_HZ, help="sweep stop frequency in Hz"
    )
    parser.add_argument(
        "--points", type=int, default=DEFAULT_POINTS, help="number of frequency points"
    )
    parser.add_argument(
        "--log",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="log-spaced frequency grid and log-scaled plot x-axis (--no-log for linear)",
    )
    parser.add_argument(
        "--raw-volts",
        action="store_true",
        help="plot/report raw Vpp instead of dB gain relative to the source level",
    )
    parser.add_argument(
        "--cycles",
        type=float,
        default=DEFAULT_CYCLES,
        help=(
            "signal periods to fit across the scope screen at the sweep's "
            "lowest frequency; the resulting window is fixed and reused for "
            "every point in the sweep (default: %(default)g)"
        ),
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE,
        help=(
            "seconds to wait after each frequency step before capturing, on "
            "top of confirming the SCPI command itself completed via *OPC?"
        ),
    )
    parser.add_argument(
        "--settle-warn-db",
        type=float,
        default=DEFAULT_SETTLE_WARN_DB,
        help=(
            "flag a point if its first/second-half coherent-detection amplitude "
            "differs by at least this many dB, a sign it may not have been "
            f"settled yet (default: {DEFAULT_SETTLE_WARN_DB:g})"
        ),
    )
    parser.add_argument(
        "--settle-warn-min-vpp",
        type=float,
        default=DEFAULT_SETTLE_WARN_MIN_VPP,
        help=(
            "only trust --settle-warn-db above this amplitude, since a weak "
            f"signal near the noise floor looks unsettled either way (default: "
            f"{DEFAULT_SETTLE_WARN_MIN_VPP * 1e3:g} mVpp)"
        ),
    )
    parser.add_argument(
        "--impedance",
        choices=("50", "1M"),
        default="50",
        help="scope input impedance for every channel (default: 50)",
    )
    parser.add_argument(
        "--vdiv-divisor",
        type=float,
        default=6.0,
        help=(
            "vertical scale = expected Vpp / this, leaving headroom (default: 6); "
            "ignored if --vdiv is given"
        ),
    )
    parser.add_argument(
        "--vdiv",
        type=float,
        default=None,
        metavar="V",
        help=(
            "vertical scale in V/div for every channel, set directly instead of "
            "deriving it from --power/--vdiv-divisor -- use this when the actual "
            "signal (e.g. radiated, not conducted) is nowhere near the "
            "commanded source level"
        ),
    )
    parser.add_argument("--output-csv", default=None, help="write sweep data to this CSV path")
    parser.add_argument("--output-plot", default=None, help="save the plot to this PNG path")
    parser.add_argument(
        "--no-show", action="store_true", help="skip opening an interactive plot window"
    )
    parser.add_argument("--demo", action="store_true", help="run against a synthetic source")
    parser.add_argument(
        "--no-scope",
        action="store_true",
        help=(
            "drive only the generator through the sweep; skip the scope "
            "entirely (no capture, no plot/CSV output)"
        ),
    )
    return parser


def _make_generator(args: argparse.Namespace) -> SignalSource:
    if args.demo:
        return DemoGenerator()
    return SG8SignalGenerator(args.resource)


def _make_scope(args: argparse.Namespace, generator: SignalSource) -> Scope | None:
    if args.no_scope:
        return None
    if args.demo:
        assert isinstance(generator, DemoGenerator)
        return DemoScope(generator, channels=tuple(args.channels))
    return SiglentSDS6204L(
        address=args.address, channels=tuple(args.channels), trigger_mode="AUTO"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run the sweep, and plot/save the result (unless ``--no-scope``)."""
    args = build_parser().parse_args(argv)
    frequencies = frequency_sweep(args.start, args.stop, args.points, log=args.log)
    impedance = 50.0 if args.impedance == "50" else 1.0e6
    channels = () if args.no_scope else tuple(args.channels)

    generator = _make_generator(args)
    scope = _make_scope(args, generator)

    def on_point(index: int, freq: float, point: dict[str, float]) -> None:
        readings = "  ".join(f"{ch}={v * 1e3:7.2f} mVpp" for ch, v in point.items())
        print(f"[{index + 1}/{len(frequencies)}] {freq / 1e6:9.3f} MHz  {readings}", flush=True)

    def on_warning(message: str) -> None:
        print(f"warning: {message}", file=sys.stderr, flush=True)

    with ExitStack() as stack:
        stack.enter_context(generator)
        if scope is not None:
            stack.enter_context(scope)
            if not args.demo:
                vdiv = resolve_vdiv(args.power, impedance, args.vdiv_divisor, args.vdiv)
                scope.configure(
                    {
                        "vertical": {
                            ch: {"impedance": args.impedance, "scale": vdiv, "coupling": "DC"}
                            for ch in channels
                        }
                    }
                )
        result = run_sweep(
            generator,
            scope,
            channels=channels,
            frequencies=frequencies,
            power_dbm=args.power,
            cycles=args.cycles,
            settle=args.settle,
            settle_warn_db=args.settle_warn_db,
            settle_warn_min_vpp=args.settle_warn_min_vpp,
            on_point=on_point,
            on_warning=on_warning,
        )

    if scope is None:
        print("done (generator-only sweep, --no-scope: no capture, no plot/CSV)")
        return 0

    reference = (
        None
        if args.raw_volts or args.impedance != "50"
        else expected_vpp(args.power, impedance)
    )
    fig = plot_sweep(result, reference_vpp=reference, log_x=args.log)

    if args.output_csv is not None:
        save_csv(result, args.output_csv)
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
