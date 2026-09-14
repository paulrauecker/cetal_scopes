"""Frequency-domain operations on channels."""

from __future__ import annotations

import time

import numpy as np
from numpy.typing import NDArray
from scipy.signal import detrend as _scipy_detrend
from scipy.signal import get_window

from cetal_scopes.analysis._util import DetrendMode
from cetal_scopes.analysis.results import Spectrogram, Spectrum, Tone
from cetal_scopes.channel import Channel

__all__ = [
    "WaterfallBuffer",
    "band_amplitude",
    "fft",
    "stft",
    "tone_amplitude",
    "window_values",
]


def window_values(channel: Channel, *, window: str = "hann") -> NDArray[np.float64]:
    """Return the window coefficients for a channel's length.

    Parameters
    ----------
    channel : Channel
        Channel whose length sets the window size.
    window : str
        Any name accepted by :func:`scipy.signal.get_window`, or ``"boxcar"``
        for no window.

    Returns
    -------
    numpy.ndarray
        Window coefficients, length ``channel.n_samples``.
    """
    return np.asarray(
        get_window(window, channel.n_samples, fftbins=True), dtype=np.float64
    )


def fft(
    channel: Channel,
    *,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Spectrum:
    """One-sided amplitude/PSD spectrum of a channel.

    Parameters
    ----------
    channel : Channel
        Source channel; its uniform ``dt`` sets the sample rate.
    window : str
        Window applied before the transform (see :func:`window_values`).
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    Spectrum
        Frequency axis, one-sided peak amplitude and one-sided PSD.

    Raises
    ------
    ValueError
        If the channel is empty.
    """
    values = channel.volts.astype(np.float64)
    n_samples = values.size
    if n_samples == 0:
        raise ValueError("cannot transform an empty channel")
    if detrend is not None:
        values = _scipy_detrend(values, type=detrend)

    weights = window_values(channel, window=window)
    spectrum = np.fft.rfft(values * weights)
    freq = np.fft.rfftfreq(n_samples, d=channel.dt)

    amplitude = np.abs(spectrum) / weights.sum()
    psd = np.abs(spectrum) ** 2 * channel.dt / np.sum(weights**2)

    interior = slice(1, freq.size - (1 if n_samples % 2 == 0 else 0))
    amplitude[interior] *= 2.0
    psd[interior] *= 2.0

    return Spectrum(
        freq=np.asarray(freq, dtype=np.float64),
        amplitude=np.asarray(amplitude, dtype=np.float64),
        psd=np.asarray(psd, dtype=np.float64),
        window=window,
    )


class WaterfallBuffer:
    """A fixed-depth rolling buffer of spectra, one row per :meth:`push`.

    Each call to :meth:`push` runs :func:`fft` on a channel and rolls the
    result into a ``(depth, n_freq)`` grid, oldest row first. This is the
    shared core of a waterfall/spectrogram display: a live view calls
    :meth:`push` once per acquisition and reads :attr:`amplitude` directly;
    a fixed-length recording calls :meth:`push` in a plain loop and then
    :meth:`to_spectrogram` once it has enough rows.

    Every pushed channel must share the same frequency axis (same sample
    count and ``dt`` as the first push); a mismatch raises ``ValueError``
    rather than silently resampling.

    A row's time (see :attr:`Spectrogram.times`) defaults to the wall-clock
    moment it was pushed, in seconds since the first push -- not the
    channel's own ``t0``, which is a within-record offset (usually ``0.0``)
    and says nothing about when successive acquisitions happened. Pass an
    explicit ``t`` to :meth:`push` to override this, e.g. for :func:`stft`,
    where each row is a slice of a *single* record and the physically
    meaningful time is the slice's position within it, not when the push
    call happened to run.
    """

    def __init__(
        self,
        depth: int,
        *,
        window: str = "hann",
        detrend: DetrendMode | None = "constant",
    ) -> None:
        if depth < 1:
            raise ValueError(f"depth must be >= 1, got {depth}")
        self._depth = depth
        self._window = window
        self._detrend: DetrendMode | None = detrend
        self._freq: NDArray[np.float64] | None = None
        self._amplitude = np.full((depth, 0), np.nan, dtype=np.float64)
        self._times = np.full(depth, np.nan, dtype=np.float64)
        self._n_pushed = 0
        self._t_start: float | None = None

    def push(self, channel: Channel, *, t: float | None = None) -> Spectrum:
        """Compute :func:`fft` on ``channel`` and roll it into the buffer.

        ``t`` overrides the row's default wall-clock time (see the class
        docstring); pass it when the caller has a more meaningful time axis
        of its own.
        """
        spectrum = fft(channel, window=self._window, detrend=self._detrend)
        if self._freq is None:
            self._freq = spectrum.freq
            self._amplitude = np.full(
                (self._depth, spectrum.freq.size), np.nan, dtype=np.float64
            )
        elif spectrum.freq.shape != self._freq.shape or not np.allclose(
            spectrum.freq, self._freq
        ):
            raise ValueError(
                "channel's frequency axis does not match the buffer's "
                f"(expected {self._freq.size} bins, got {spectrum.freq.size})"
            )
        if t is None:
            if self._t_start is None:
                self._t_start = time.monotonic()
            t = time.monotonic() - self._t_start
        self._amplitude = np.roll(self._amplitude, -1, axis=0)
        self._amplitude[-1] = spectrum.amplitude
        self._times = np.roll(self._times, -1)
        self._times[-1] = t
        self._n_pushed += 1
        return spectrum

    @property
    def freq(self) -> NDArray[np.float64]:
        """Shared frequency axis, or an empty array before the first push."""
        return np.zeros(0) if self._freq is None else self._freq

    @property
    def amplitude(self) -> NDArray[np.float64]:
        """``(depth, n_freq)`` grid; unfilled rows are NaN until the buffer fills."""
        return self._amplitude

    @property
    def n_pushed(self) -> int:
        """Total number of rows pushed so far (may exceed ``depth``)."""
        return self._n_pushed

    def to_spectrogram(self) -> Spectrogram:
        """Return the buffer's current contents as a :class:`Spectrogram`."""
        return Spectrogram(
            freq=self.freq,
            times=self._times.copy(),
            amplitude=self._amplitude.copy(),
            window=self._window,
        )


def stft(
    channel: Channel,
    *,
    segment_samples: int,
    hop_samples: int | None = None,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Spectrogram:
    """Short-time Fourier transform: a spectrogram of a *single* record.

    Slices ``channel`` into overlapping ``segment_samples``-long windows,
    hopping by ``hop_samples`` (default ``segment_samples // 2``, i.e. 50%
    overlap), and stacks each slice's :func:`fft` into a :class:`Spectrogram`
    via :class:`WaterfallBuffer`.

    Unlike :class:`WaterfallBuffer` pushed directly across *separate*
    acquisitions (where a row's time is the wall-clock moment it was
    pushed), each row's time here is the slice's **center time within the
    channel** -- the physically meaningful axis for watching one transient's
    spectral content evolve, e.g. a single triggered EMP capture.

    Raises
    ------
    ValueError
        If ``segment_samples`` doesn't fit within the channel, or
        ``hop_samples`` is not positive.
    """
    if not (1 <= segment_samples <= channel.n_samples):
        raise ValueError(
            f"segment_samples must be in [1, {channel.n_samples}], got {segment_samples}"
        )
    hop = segment_samples // 2 if hop_samples is None else hop_samples
    if hop < 1:
        raise ValueError(f"hop_samples must be >= 1, got {hop}")

    starts = list(range(0, channel.n_samples - segment_samples + 1, hop))
    buffer = WaterfallBuffer(len(starts), window=window, detrend=detrend)
    for start in starts:
        segment = Channel(
            name=channel.name,
            volts=channel.volts[start : start + segment_samples],
            t0=channel.t0 + start * channel.dt,
            dt=channel.dt,
            antenna=channel.antenna,
            unit=channel.unit,
        )
        center_time = segment.t0 + 0.5 * segment_samples * channel.dt
        buffer.push(segment, t=center_time)
    return buffer.to_spectrogram()


def band_amplitude(
    channel: Channel,
    *,
    low: float,
    high: float,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> float:
    """Return the largest spectral amplitude within ``[low, high]`` Hz.

    This is the right way to measure a tone whose exact frequency is unknown:
    the global FFT peak can be dominated by instrument spurs outside the band.

    Parameters
    ----------
    channel : Channel
        Source channel.
    low, high : float
        Band edges in Hz.
    window : str
        Window applied before the transform.
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    float
        Peak amplitude in volts, or ``0.0`` when the band is empty.
    """
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    spectrum = fft(channel, window=window, detrend=detrend)
    mask = (spectrum.freq >= low) & (spectrum.freq <= high)
    if not mask.any():
        return 0.0
    return float(spectrum.amplitude[mask].max())


def tone_amplitude(
    channel: Channel,
    frequency: float,
    *,
    window: str = "hann",
    detrend: DetrendMode | None = "constant",
) -> Tone:
    """Coherently detect a tone at a known frequency.

    The signal is projected onto a complex reference ``exp(-j 2 pi f t)`` and
    averaged, which acts as a very narrow band-pass filter. Unlike an FFT peak,
    this is immune to instrument spurs and broadband noise at other
    frequencies, and it recovers the phase relative to the channel's time axis.

    Parameters
    ----------
    channel : Channel
        Source channel.
    frequency : float
        Reference frequency in Hz.
    window : str
        Window applied before the projection.
    detrend : {"constant", "linear", None}
        Trend removed before windowing.

    Returns
    -------
    Tone
        Detected amplitude (volts) and phase (radians).

    Raises
    ------
    ValueError
        If the channel is empty.
    """
    values = channel.volts.astype(np.float64)
    if values.size == 0:
        raise ValueError("cannot detect a tone in an empty channel")
    if detrend is not None:
        values = _scipy_detrend(values, type=detrend)
    weights = window_values(channel, window=window)
    reference = np.exp(-2j * np.pi * frequency * channel.time)
    projection = np.sum(values * weights * reference) / weights.sum()
    amplitude = abs(projection) * (1.0 if frequency == 0.0 else 2.0)
    return Tone(
        frequency=frequency,
        amplitude=float(amplitude),
        phase=float(np.angle(projection)),
    )
