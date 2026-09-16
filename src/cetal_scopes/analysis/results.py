"""Result containers returned by analysis functions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = ["ChannelStats", "Spectrogram", "Spectrum", "TimeOffset", "Tone"]


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


@dataclass(frozen=True, eq=False)
class Spectrogram:
    """A time x frequency amplitude grid, produced by :class:`~cetal_scopes.analysis.spectral.WaterfallBuffer`.

    Attributes
    ----------
    freq : numpy.ndarray
        Frequency axis in Hz, shared by every row.
    times : numpy.ndarray
        Time of each row, in seconds, oldest to newest -- see
        :meth:`~cetal_scopes.analysis.spectral.WaterfallBuffer.push` for what
        this means (wall-clock by default, or an explicit override such as
        :func:`~cetal_scopes.analysis.spectral.stft`'s segment center time).
    amplitude : numpy.ndarray
        One-sided amplitude, shape ``(n_rows, n_freq)``, oldest row first.
    window : str
        Name of the window applied before each row's transform.
    """

    freq: NDArray[np.float64]
    times: NDArray[np.float64]
    amplitude: NDArray[np.float64]
    window: str = "hann"

    @property
    def n_rows(self) -> int:
        """Number of stacked spectra."""
        return int(self.amplitude.shape[0])

    @property
    def n_bins(self) -> int:
        """Number of frequency bins."""
        return int(self.freq.size)

    def peak(self) -> tuple[float, float, float]:
        """Return ``(time, frequency, amplitude)`` of the largest amplitude bin.

        NaN rows (e.g. a :class:`~cetal_scopes.analysis.spectral.WaterfallBuffer`
        that hasn't filled yet) are ignored.
        """
        row, col = np.unravel_index(np.nanargmax(self.amplitude), self.amplitude.shape)
        return (
            float(self.times[row]),
            float(self.freq[col]),
            float(self.amplitude[row, col]),
        )


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


@dataclass(frozen=True)
class TimeOffset:
    """A lag measured between two channels by cross-correlation.

    Attributes
    ----------
    offset : float
        Seconds to add to the signal channel's time axis to align it with the
        reference channel.
    correlation : float
        Normalized peak correlation in ``[-1, 1]``. A negative value means the
        two signals are inverted relative to each other.
    inverted : bool
        ``True`` when :attr:`correlation` is negative.
    max_lag : float
        Half-width of the searched lag range, in seconds.
    """

    offset: float
    correlation: float
    inverted: bool
    max_lag: float

    def __repr__(self) -> str:
        return (
            f"TimeOffset(offset={self.offset!r}, "
            f"correlation={self.correlation!r}, inverted={self.inverted!r})"
        )


@dataclass(frozen=True)
class Peak:
    """One local maximum located in a channel.

    Attributes
    ----------
    index : int
        Sample index of the peak.
    time : float
        Time of the peak, in seconds.
    value : float
        Sample value at the peak.
    prominence : float
        How far the peak stands out from the surrounding baseline. This, not
        height, is what separates a real feature from a ripple on a larger one.
    width : float
        Width at half prominence, in seconds.
    """

    index: int
    time: float
    value: float
    prominence: float
    width: float

    def __repr__(self) -> str:
        return (
            f"Peak(time={self.time!r}, value={self.value!r}, "
            f"prominence={self.prominence!r})"
        )


@dataclass(frozen=True)
class PulseMetrics:
    """Scope-style edge and pulse measurements of a channel.

    ``base`` and ``top`` are the histogram-mode levels of the flat parts of the
    waveform, not the minimum and maximum: overshoot and ringing would
    otherwise inflate the amplitude and push the reference levels out, which is
    exactly what makes naive rise times read short.

    Every duration is ``nan`` when the waveform does not actually cross the
    levels it would need to -- a channel with no edge has no rise time, and
    reporting one would be a fabrication.

    Attributes
    ----------
    base, top : float
        Lower and upper settled levels.
    amplitude : float
        ``top - base``.
    low_reference, high_reference : float
        The crossing levels used, derived from ``low``/``high`` fractions.
    rise_time, fall_time : float
        Seconds between the reference crossings of the first rising and first
        falling edge.
    width : float
        Seconds the waveform spends above the 50% level, around the peak.
    fwhm : float
        Full width at half maximum measured from ``base``, for a pulse sitting
        on a baseline.
    overshoot, undershoot : float
        Excursion past ``top``/``base`` as a fraction of ``amplitude``.
    peak_time, peak_value : float
        The largest absolute excursion.
    """

    name: str
    base: float
    top: float
    amplitude: float
    low_reference: float
    high_reference: float
    rise_time: float
    fall_time: float
    width: float
    fwhm: float
    overshoot: float
    undershoot: float
    peak_time: float
    peak_value: float
