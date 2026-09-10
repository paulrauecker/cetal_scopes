"""Matplotlib plotting helpers for captures and spectra."""

from __future__ import annotations

from collections.abc import Sequence

import matplotlib.pyplot as plt
from matplotlib.axes import Axes

from cetal_scopes.analysis.results import Spectrum
from cetal_scopes.capture import Capture

__all__ = ["plot_capture", "plot_spectrum"]

_TIME_UNITS = {
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "\u00b5s": 1e-6,
    "ns": 1e-9,
    "ps": 1e-12,
}


def plot_capture(
    capture: Capture,
    *,
    channels: Sequence[str] | None = None,
    ax: Axes | None = None,
    time_unit: str = "s",
) -> Axes:
    """Plot each channel's voltage against time.

    Parameters
    ----------
    capture : Capture
        Capture to plot.
    channels : sequence of str, optional
        Channel names to include; defaults to all channels.
    ax : matplotlib.axes.Axes, optional
        Axes to draw on; a new figure is created when omitted.
    time_unit : str
        Time unit for the x-axis, one of ``s``, ``ms``, ``us``, ``ns``, ``ps``.

    Returns
    -------
    matplotlib.axes.Axes
        The axes containing the plot.
    """
    if time_unit not in _TIME_UNITS:
        raise ValueError(f"unknown time_unit {time_unit!r}")
    if ax is None:
        _, ax = plt.subplots()
    scale = 1.0 / _TIME_UNITS[time_unit]
    names: Sequence[str]
    if channels is None:
        names = capture.channel_names or ()
    else:
        names = tuple(channels)
    for name in names:
        channel = capture[name]
        ax.plot(channel.time * scale, channel.volts, label=name)
    ax.set_xlabel(f"time ({time_unit})")
    ax.set_ylabel("volts (V)")
    if len(names) > 1:
        ax.legend()
    return ax


def plot_spectrum(
    spectrum: Spectrum,
    *,
    ax: Axes | None = None,
    psd: bool = False,
    log_x: bool = True,
    log_y: bool = False,
) -> Axes:
    """Plot an amplitude or PSD spectrum.

    Parameters
    ----------
    spectrum : Spectrum
        Spectrum to plot.
    ax : matplotlib.axes.Axes, optional
        Axes to draw on; a new figure is created when omitted.
    psd : bool
        Plot the PSD instead of the peak amplitude.
    log_x, log_y : bool
        Use logarithmic scaling on the respective axis (DC is dropped for a
        logarithmic x-axis).

    Returns
    -------
    matplotlib.axes.Axes
        The axes containing the plot.
    """
    if ax is None:
        _, ax = plt.subplots()
    freq = spectrum.freq
    values = spectrum.psd if psd else spectrum.amplitude
    unit = spectrum.psd_unit if psd else spectrum.amplitude_unit
    if log_x:
        freq = freq[1:]
        values = values[1:]
    ax.plot(freq, values)
    if log_x:
        ax.set_xscale("log")
    if log_y:
        ax.set_yscale("log")
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel(f"{'PSD' if psd else 'amplitude'} ({unit})")
    return ax
