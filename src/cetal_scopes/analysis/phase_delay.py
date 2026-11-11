"""Delay differences between channels from multi-frequency phase.

A delay ``tau`` of channel B relative to channel A shifts B's phase by
``-2 pi f tau``, so ``dphi(f) = phi_A - phi_B = 2 pi f tau``. One frequency
is ambiguous modulo ``2 pi``; a sweep, unwrapped along ``f`` and fitted with a
straight line, is not: the slope is ``2 pi tau`` (the group delay) and the
intercept carries any polarity inversion (``~180 deg``). A ``2 pi k`` slip from
unwrapping only moves the intercept, never the slope, as long as the step
satisfies ``df < 1 / (2 |tau|)``.

Per acquisition the phase comes from sine fits that share **one** frequency
and **one** time axis across channels (:func:`phase_differences`): the
frequency is estimated once on the reference channel (:func:`estimate_frequency`,
an IEEE 1057 four-parameter fit), and every channel then gets a linear
three-parameter fit at that frequency (:func:`fit_sine`). Fitting each channel
at its own frequency would turn a frequency error into a phase error.

Phase convention: ``x(t) = A cos(2 pi f t + phi) + c``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from cetal_scopes.capture import Capture

__all__ = [
    "DelayFit",
    "SineFit",
    "delay_to_length",
    "estimate_frequency",
    "fit_delay",
    "fit_sine",
    "phase_differences",
    "wrap_phase",
]

#: Speed of light in vacuum, m/s.
SPEED_OF_LIGHT = 299_792_458.0


@dataclass(frozen=True)
class SineFit:
    """A three-parameter sine fit ``A cos(2 pi f t + phase) + offset``.

    Attributes
    ----------
    frequency : float
        Fit frequency in hertz (held fixed, not fitted).
    amplitude : float
        Peak amplitude in volts.
    phase : float
        Phase in radians, in ``(-pi, pi]``, relative to the time axis given.
    offset : float
        DC offset in volts.
    residual_rms : float
        RMS of the fit residual in volts.
    n_samples : int
        Number of samples fitted.
    """

    frequency: float
    amplitude: float
    phase: float
    offset: float
    residual_rms: float
    n_samples: int

    @property
    def phase_std(self) -> float:
        """Approximate 1-sigma phase noise in radians, from the residual.

        ``sigma_n / (A sqrt(N / 2))``, assuming white residual noise.
        """
        if self.amplitude <= 0:
            return float("inf")
        return self.residual_rms / (self.amplitude * np.sqrt(self.n_samples / 2))


def wrap_phase(phase: ArrayLike) -> NDArray[np.float64]:
    """Wrap phase (radians) into ``(-pi, pi]``."""
    values = np.asarray(phase, dtype=np.float64)
    wrapped = np.mod(values + np.pi, 2 * np.pi) - np.pi
    return np.where(wrapped == -np.pi, np.pi, wrapped)


def fit_sine(t: ArrayLike, x: ArrayLike, frequency: float) -> SineFit:
    """Linear least-squares fit of ``a cos(wt) + b sin(wt) + c`` at a fixed ``f``.

    Parameters
    ----------
    t : array_like
        Sample times in seconds. Every channel compared by phase must use the
        same ``t`` (and the same ``frequency``).
    x : array_like
        Samples in volts.
    frequency : float
        Fit frequency in hertz.

    Returns
    -------
    SineFit
        ``phase = atan2(-b, a)`` for the convention ``A cos(wt + phase)``.
    """
    t_arr = np.asarray(t, dtype=np.float64)
    x_arr = np.asarray(x, dtype=np.float64)
    if t_arr.shape != x_arr.shape or t_arr.ndim != 1:
        raise ValueError("t and x must be 1-D arrays of the same length")
    if t_arr.size < 4:
        raise ValueError("need at least 4 samples to fit a sine")
    return _fit_sines(t_arr, x_arr[None, :], frequency)[0]


def _fit_sines(
    t: NDArray[np.float64], rows: NDArray[np.float64], frequency: float
) -> list[SineFit]:
    """:func:`fit_sine` for every row of ``rows``, sharing one design matrix.

    Solves the 3x3 normal equations directly: on million-sample records this
    is an order of magnitude faster than ``lstsq`` on the full matrix, and
    with ``cos``, ``sin`` and ``1`` as columns it is well conditioned.
    """
    wt = 2 * np.pi * frequency * t
    design = np.vstack((np.cos(wt), np.sin(wt), np.ones_like(wt)))
    gram = design @ design.T
    rhs = rows @ design.T  # (n_rows, 3)
    params = np.linalg.solve(gram, rhs.T).T
    sum_sq = np.einsum("ij,ij->i", rows, rows)
    n = t.size
    fits = []
    for (a, b, c), proj, ss in zip(params, rhs, sum_sq, strict=True):
        residual_ss = max(float(ss - np.dot((a, b, c), proj)), 0.0)
        fits.append(
            SineFit(
                frequency=float(frequency),
                amplitude=float(np.hypot(a, b)),
                phase=float(np.arctan2(-b, a)),
                offset=float(c),
                residual_rms=float(np.sqrt(residual_ss / n)),
                n_samples=int(n),
            )
        )
    return fits


def estimate_frequency(
    t: ArrayLike,
    x: ArrayLike,
    guess: float,
    *,
    blocks: int = 16,
    iterations: int = 4,
) -> float:
    """Estimate a tone's frequency near ``guess`` (IEEE 1057 four-parameter fit).

    Two clocks that are ``p`` ppm apart (generator vs. digitizer) put the tone
    ``p * f`` away from ``guess``, which over a long record is far outside the
    four-parameter fit's capture range. So a coarse correction comes first:
    the record is split into ``blocks`` pieces, each is demodulated at
    ``guess``, and the slope of the block phases gives the offset (valid for
    offsets below ``blocks / (2 T)``, e.g. 16 kHz for 16 blocks of a 500 us
    record). Gauss-Newton iterations of the four-parameter model
    ``a cos(wt) + b sin(wt) + c`` with ``w`` free then refine it.

    Parameters
    ----------
    t, x : array_like
        Sample times (s) and values (V).
    guess : float
        Starting frequency in hertz, e.g. the generator's set frequency.
    blocks : int
        Number of blocks for the coarse correction.
    iterations : int
        Maximum Gauss-Newton iterations.

    Returns
    -------
    float
        The fitted frequency in hertz, on the time axis ``t``.
    """
    t_arr = np.asarray(t, dtype=np.float64)
    x_arr = np.asarray(x, dtype=np.float64)
    x_arr = x_arr - x_arr.mean()
    # Centre time for conditioning; the frequency doesn't depend on the origin.
    tc = t_arr - t_arr.mean()
    duration = float(tc[-1] - tc[0])

    pieces = np.array_split(np.arange(tc.size), blocks)
    lo = np.exp(-2j * np.pi * guess * tc)
    block_phase = np.unwrap([np.angle(np.sum(x_arr[i] * lo[i])) for i in pieces])
    block_time = np.array([tc[i].mean() for i in pieces])
    slope = np.polyfit(block_time, block_phase, 1)[0]
    w = 2 * np.pi * guess + slope

    for _ in range(iterations):
        wt = w * tc
        cos_wt, sin_wt = np.cos(wt), np.sin(wt)
        base = np.vstack((cos_wt, sin_wt, np.ones_like(wt)))
        a, b, c = np.linalg.solve(base @ base.T, base @ x_arr)
        design = np.vstack((base, tc * (-a * sin_wt + b * cos_wt)))
        residual = x_arr - (a * cos_wt + b * sin_wt + c)
        dw = float(np.linalg.solve(design @ design.T, design @ residual)[3])
        w += dw
        if abs(dw) * duration < 1e-6:
            break
    return float(w / (2 * np.pi))


def phase_differences(
    capture: Capture,
    reference: str,
    channels: Sequence[str],
    guess: float,
) -> tuple[float, dict[str, SineFit]]:
    """Fit every channel at the reference channel's fitted frequency.

    Parameters
    ----------
    capture : Capture
        One acquisition; all channels share its time axis.
    reference : str
        Channel the frequency is estimated on.
    channels : sequence of str
        Channels to fit (the reference is always included).
    guess : float
        Starting frequency in hertz.

    Returns
    -------
    frequency : float
        The common fit frequency in hertz.
    fits : dict of str to SineFit
        Per-channel fits. ``wrap_phase(fits[A].phase - fits[B].phase)`` is
        ``2 pi f tau`` with ``tau > 0`` when B arrives later than A.
    """
    ref = capture[reference]
    time = ref.time
    frequency = estimate_frequency(time, ref.volts, guess)
    names = list(dict.fromkeys((reference, *channels)))
    rows = np.vstack([capture[name].volts for name in names]).astype(np.float64)
    fits = dict(zip(names, _fit_sines(time, rows, frequency), strict=True))
    return frequency, fits


@dataclass(frozen=True)
class DelayFit:
    """A straight-line fit of unwrapped phase difference against frequency.

    Attributes
    ----------
    frequencies : ndarray
        Frequencies used in the fit, Hz, ascending.
    unwrapped : ndarray
        Unwrapped phase difference at each frequency, radians.
    residuals : ndarray
        ``unwrapped - (slope * f + intercept)``, radians.
    delay : float
        ``slope / (2 pi)`` in seconds; positive when B is later than A.
    delay_stderr : float
        Standard error of :attr:`delay` from the fit, seconds.
    intercept : float
        Phase intercept at ``f = 0``, radians, wrapped to ``(-pi, pi]``:
        ``~0`` for matching polarity, ``~pi`` for an inversion.
    """

    frequencies: NDArray[np.float64]
    unwrapped: NDArray[np.float64]
    residuals: NDArray[np.float64]
    delay: float
    delay_stderr: float
    intercept: float

    @property
    def intercept_deg(self) -> float:
        """Intercept in degrees, ``(-180, 180]``."""
        return float(np.degrees(self.intercept))

    @property
    def residual_rms_deg(self) -> float:
        """RMS residual in degrees."""
        return float(np.degrees(np.sqrt(np.mean(self.residuals**2))))


def fit_delay(
    frequencies: ArrayLike,
    phase_difference: ArrayLike,
    *,
    weights: ArrayLike | None = None,
) -> DelayFit:
    """Unwrap ``phase_difference`` along frequency and fit a straight line.

    Parameters
    ----------
    frequencies : array_like
        Frequencies in hertz; sorted ascending internally. The largest step
        must stay below ``1 / (2 |tau|)`` or unwrapping slips.
    phase_difference : array_like
        Wrapped phase difference ``phi_A - phi_B`` in radians.
    weights : array_like, optional
        Per-point weights, typically ``1 / sigma``; unweighted by default.

    Returns
    -------
    DelayFit
    """
    f = np.asarray(frequencies, dtype=np.float64)
    phase = np.asarray(phase_difference, dtype=np.float64)
    if f.shape != phase.shape or f.ndim != 1 or f.size < 3:
        raise ValueError("need matching 1-D frequency and phase arrays, >= 3 points")
    order = np.argsort(f)
    f, phase = f[order], phase[order]
    w = (
        np.ones_like(f)
        if weights is None
        else np.asarray(weights, dtype=np.float64)[order]
    )
    unwrapped = np.unwrap(wrap_phase(phase))
    design = np.column_stack((f, np.ones_like(f)))
    (slope, intercept), *_ = np.linalg.lstsq(
        design * w[:, None], unwrapped * w, rcond=None
    )
    residuals = unwrapped - (slope * f + intercept)
    dof = f.size - 2
    sigma2 = float(np.sum((w * residuals) ** 2) / dof) if dof > 0 else float("nan")
    cov = sigma2 * np.linalg.inv((design * w[:, None]).T @ (design * w[:, None]))
    return DelayFit(
        frequencies=f,
        unwrapped=unwrapped,
        residuals=residuals,
        delay=float(slope / (2 * np.pi)),
        delay_stderr=float(np.sqrt(cov[0, 0]) / (2 * np.pi)),
        intercept=float(wrap_phase(intercept)),
    )


def delay_to_length(delay: float, velocity_factor: float = 0.85) -> float:
    """Convert a delay (s) into cable length (m) at ``velocity_factor``."""
    return delay * velocity_factor * SPEED_OF_LIGHT
