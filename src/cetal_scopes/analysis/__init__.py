"""Downstream analysis for :class:`~cetal_scopes.capture.Capture` channels.

The subpackage is split by domain:

- :mod:`~cetal_scopes.analysis.analytic` — Hilbert/analytic-signal operations.
- :mod:`~cetal_scopes.analysis.metrics` — scalar metrics.
- :mod:`~cetal_scopes.analysis.spectral` — windowing and FFT.
- :mod:`~cetal_scopes.analysis.time` — gating, baselines, detrending, resampling.

Functions take and/or return :class:`~cetal_scopes.channel.Channel` objects and
the result containers in :mod:`~cetal_scopes.analysis.results`.
"""

from cetal_scopes.analysis.analytic import (
    analytic_signal,
    envelope,
    instantaneous_frequency,
    instantaneous_phase,
)
from cetal_scopes.analysis.metrics import stats
from cetal_scopes.analysis.results import ChannelStats, Spectrum, Tone
from cetal_scopes.analysis.spectral import (
    band_amplitude,
    fft,
    tone_amplitude,
    window_values,
)
from cetal_scopes.analysis.time import (
    detrend,
    gate,
    remove_adc_comb,
    resample,
    subtract_baseline,
)

__all__ = [
    "ChannelStats",
    "Spectrum",
    "Tone",
    "analytic_signal",
    "band_amplitude",
    "detrend",
    "envelope",
    "fft",
    "gate",
    "instantaneous_frequency",
    "instantaneous_phase",
    "remove_adc_comb",
    "resample",
    "stats",
    "subtract_baseline",
    "tone_amplitude",
    "window_values",
]
