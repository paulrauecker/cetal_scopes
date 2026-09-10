"""Result containers returned by analysis functions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = ["ChannelStats", "Spectrum", "Tone"]


@dataclass(frozen=True, eq=False)
class Spectrum:
    """A one-sided frequency spectrum produced by :func:`cetal_scopes.analysis.fft`.

    Attributes
    ----------
    freq : numpy.ndarray
        Frequency axis in Hz, from DC to Nyquist.
    amplitude : numpy.ndarray
        One-sided amplitude in ``amplitude_unit`` (peak amplitude per bin).
    psd : numpy.ndarray
        One-sided power spectral density in ``psd_unit``.
    amplitude_unit : str
        Unit of :attr:`amplitude`; ``"V"`` for voltage channels.
    psd_unit : str
        Unit of :attr:`psd`.
    window : str
        Name of the window applied before the transform.
    """

    freq: NDArray[np.float64]
    amplitude: NDArray[np.float64]
    psd: NDArray[np.float64]
    amplitude_unit: str = "V"
    psd_unit: str = "V^2/Hz"
    window: str = "boxcar"

    @property
    def n_bins(self) -> int:
        """Number of frequency bins."""
        return int(self.freq.size)

    @property
    def df(self) -> float:
        """Frequency resolution in Hz (bin spacing)."""
        if self.freq.size < 2:
            return 0.0
        return float(self.freq[1] - self.freq[0])

    @property
    def f_nyquist(self) -> float:
        """Highest resolved frequency in Hz."""
        return float(self.freq[-1]) if self.freq.size else 0.0

    def peak(self) -> tuple[float, float]:
        """Return the ``(frequency, amplitude)`` of the largest amplitude bin."""
        index = int(np.argmax(self.amplitude))
        return float(self.freq[index]), float(self.amplitude[index])


@dataclass(frozen=True)
class ChannelStats:
    """Descriptive time-domain statistics of a channel."""

    name: str
    n_samples: int
    mean: float
    rms: float
    std: float
    minimum: float
    maximum: float
    peak_to_peak: float
    peak_value: float
    peak_time: float


@dataclass(frozen=True)
class Tone:
    """A tone coherently detected at a known reference frequency.

    Attributes
    ----------
    frequency : float
        Reference frequency in Hz used for detection.
    amplitude : float
        Peak amplitude in volts.
    phase : float
        Phase in radians, relative to the channel's time axis.
    """

    frequency: float
    amplitude: float
    phase: float

    @property
    def phase_deg(self) -> float:
        """Phase in degrees."""
        return float(np.degrees(self.phase))
