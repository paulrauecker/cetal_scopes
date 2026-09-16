"""Downstream analysis for :class:`~cetal_scopes.capture.Capture` channels.

The subpackage is split by domain:

- :mod:`~cetal_scopes.analysis.alignment` — cross-correlation time offsets,
  pairwise and across a whole :class:`~cetal_scopes.shot.Shot`.
- :mod:`~cetal_scopes.analysis.analytic` — Hilbert/analytic-signal operations.
- :mod:`~cetal_scopes.analysis.cross` — two-channel spectral comparison.
- :mod:`~cetal_scopes.analysis.fields` — B-dot calibration to field units.
- :mod:`~cetal_scopes.analysis.filters` — zero-phase filtering and smoothing.
- :mod:`~cetal_scopes.analysis.metrics` — scalar metrics, pulse measurements
  and peak finding.
- :mod:`~cetal_scopes.analysis.spectral` — windowing, FFT, and spectrograms.
- :mod:`~cetal_scopes.analysis.time` — gating, baselines, detrending, resampling.
- :mod:`~cetal_scopes.analysis.vector` — phase-correct three-axis vectors.

Functions take and/or return :class:`~cetal_scopes.channel.Channel` objects and
the result containers in :mod:`~cetal_scopes.analysis.results`. Nothing here
mutates a capture.
"""

from cetal_scopes.analysis.alignment import align_shot, estimate_time_offset
from cetal_scopes.analysis.analytic import (
    analytic_signal,
    envelope,
    instantaneous_frequency,
    instantaneous_phase,
)
from cetal_scopes.analysis.cross import coherence, cross_spectrum, transfer_function
from cetal_scopes.analysis.fields import b_field, b_field_rate, b_magnitude
from cetal_scopes.analysis.filters import (
    bandpass,
    bandstop,
    highpass,
    lowpass,
    moving_average,
    savgol,
)
from cetal_scopes.analysis.metrics import find_peaks, pulse_metrics, stats
from cetal_scopes.analysis.results import (
    ChannelStats,
    Peak,
    PulseMetrics,
    Spectrogram,
    Spectrum,
    TimeOffset,
    Tone,
)
from cetal_scopes.analysis.spectral import (
    WaterfallBuffer,
    band_amplitude,
    fft,
    stft,
    tone_amplitude,
    window_values,
)
from cetal_scopes.analysis.time import (
    detrend,
    gate,
    remove_adc_comb,
    resample,
    resample_onto,
    subtract_baseline,
)
from cetal_scopes.analysis.vector import FieldVector, vector_at

__all__ = [
    "ChannelStats",
    "FieldVector",
    "Peak",
    "PulseMetrics",
    "Spectrogram",
    "Spectrum",
    "TimeOffset",
    "Tone",
    "WaterfallBuffer",
    "align_shot",
    "analytic_signal",
    "b_field",
    "b_field_rate",
    "b_magnitude",
    "band_amplitude",
    "bandpass",
    "bandstop",
    "coherence",
    "cross_spectrum",
    "detrend",
    "envelope",
    "estimate_time_offset",
    "fft",
    "find_peaks",
    "gate",
    "highpass",
    "instantaneous_frequency",
    "instantaneous_phase",
    "lowpass",
    "moving_average",
    "pulse_metrics",
    "remove_adc_comb",
    "resample",
    "resample_onto",
    "savgol",
    "stats",
    "stft",
    "subtract_baseline",
    "tone_amplitude",
    "transfer_function",
    "vector_at",
    "window_values",
]
