"""B-dot calibration: convert probe volts to ``dB/dt`` and ``B``.

A B-dot probe responds to the field's time derivative, ``V(f) = H(f) * dB/dt``,
where ``H`` is the antenna's complex :class:`~cetal_scopes.antenna.TransferFunction`
(volts per tesla-per-second). Recovering the field is a frequency-domain
deconvolution followed by one integration::

    dB/dt(f) = V(f) / H(f)
    B(f)     = V(f) / (H(f) * i 2 pi f)

The record is mean-subtracted (and optionally detrended) so the integration
constant is zero, and the DC bin is dropped because ``1 / (i 2 pi f)`` is
singular there. ``Antenna.delay`` is applied as an extra phase ramp; leave it at
zero when the calibration already includes the cable delay.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import signal as _scipy_signal

from cetal_scopes.analysis._util import with_volts
from cetal_scopes.antenna import TransferFunction
from cetal_scopes.channel import Channel

__all__ = ["b_field", "b_field_rate", "b_magnitude"]

OutsideMode = Literal["raise", "zero", "clamp"]


def _transfer_function(channel: Channel) -> TransferFunction:
    antenna = channel.antenna
    if antenna is None or antenna.transfer_function is None:
        raise ValueError(
            f"channel {channel.name!r} has no antenna with a transfer function"
        )
    return antenna.transfer_function


def _calibrate(
    channel: Channel,
    *,
    integrate: bool,
    outside: OutsideMode,
    rtol: float,
    detrend: bool,
) -> NDArray[np.float64]:
    transfer = _transfer_function(channel)
    antenna = channel.antenna
    if antenna is None:  # pragma: no cover - guarded by _transfer_function
        raise RuntimeError("antenna disappeared")

    n_samples = channel.n_samples
    if n_samples < 1:
        raise ValueError("channel is empty")

    values = np.asarray(channel.volts, dtype=np.float64)
    values = values - values.mean()
    if detrend:
        values = _scipy_signal.detrend(values)

    spectrum = np.fft.rfft(values)
    freq = np.fft.rfftfreq(n_samples, channel.dt)

    gain = transfer.gain_at(freq)
    if antenna.delay:
        gain = gain * np.exp(-2j * np.pi * freq * antenna.delay)

    valid = np.isfinite(gain) & (freq > 0.0)
    if not np.any(valid):
        raise ValueError(f"transfer function for {channel.name!r} has no usable band")

    if outside == "raise":
        total = float(np.sum(np.abs(spectrum) ** 2))
        leaked = float(np.sum(np.abs(spectrum[~valid]) ** 2))
        if total > 0.0 and leaked / total > rtol:
            raise ValueError(
                f"{channel.name!r}: {leaked / total:.1%} of the signal energy is "
                f"outside the calibrated band "
                f"[{transfer.f_min:g}, {transfer.f_max:g}] Hz; "
                'pass outside="zero" or "clamp" to allow it'
            )

    if outside == "clamp":
        index = np.nonzero(valid)[0]
        real = np.interp(np.arange(freq.size), index, gain.real[index])
        imag = np.interp(np.arange(freq.size), index, gain.imag[index])
        gain_used = real + 1j * imag
        spectrum = spectrum.copy()
    else:
        gain_used = np.where(valid, gain, 1.0)
        spectrum = np.where(valid, spectrum, 0.0)
    spectrum[0] = 0.0

    response = spectrum / gain_used
    if integrate:
        with np.errstate(divide="ignore", invalid="ignore"):
            response = response / (1j * 2.0 * np.pi * freq)
        response[0] = 0.0
    return np.fft.irfft(response, n=n_samples).astype(np.float64)


def b_field_rate(
    channel: Channel,
    *,
    outside: OutsideMode = "raise",
    rtol: float = 1.0e-6,
    detrend: bool = False,
) -> Channel:
    """Convert a B-dot channel's volts to ``dB/dt`` in T/s.

    Parameters
    ----------
    channel : Channel
        B-dot channel carrying an antenna with a transfer function.
    outside : {"raise", "zero", "clamp"}
        How to treat frequency bins outside the calibrated band. ``"raise"``
        (default) errors when more than ``rtol`` of the signal energy is
        uncalibrated, ``"zero"`` silently band-limits, ``"clamp"`` uses the
        nearest edge gain.
    rtol : float
        Energy fraction tolerated outside the band before ``"raise"`` fires.
    detrend : bool
        Also remove a linear trend. Off by default: a linear ramp is real
        low-frequency field content and, once integrated, ``1/f`` amplifies it.

    Returns
    -------
    Channel
        ``dB/dt`` in tesla per second.
    """
    values = _calibrate(
        channel, integrate=False, outside=outside, rtol=rtol, detrend=detrend
    )
    return with_volts(channel, values, unit="T/s")


def b_field(
    channel: Channel,
    *,
    outside: OutsideMode = "raise",
    rtol: float = 1.0e-6,
    detrend: bool = False,
) -> Channel:
    """Integrate a B-dot channel to the magnetic field ``B`` in tesla.

    Parameters are as for :func:`b_field_rate`; the mean (and optional linear
    trend) is removed first, so the recovered field has no DC offset.

    Returns
    -------
    Channel
        ``B`` in tesla.
    """
    values = _calibrate(
        channel, integrate=True, outside=outside, rtol=rtol, detrend=detrend
    )
    return with_volts(channel, values, unit="T")


def b_magnitude(*channels: Channel) -> Channel:
    """Return ``sqrt(sum(B_i**2))`` for field components on a shared timebase.

    Intended for an orthogonal B-dot triad, but any consistent set of field
    channels works. All channels must share ``t0``, ``dt``, length and unit.

    Parameters
    ----------
    *channels : Channel
        Field components (e.g. the output of :func:`b_field`).

    Returns
    -------
    Channel
        Vector magnitude with the same unit and time axis.
    """
    if not channels:
        raise ValueError("need at least one channel")
    first = channels[0]
    n_samples = first.n_samples
    total = np.zeros(n_samples, dtype=np.float64)
    for channel in channels:
        if (
            channel.n_samples != n_samples
            or not np.isclose(channel.dt, first.dt)
            or not np.isclose(channel.t0, first.t0)
        ):
            raise ValueError("channels must share a timebase")
        if channel.unit != first.unit:
            raise ValueError(
                f"channels have mixed units: {first.unit!r} and {channel.unit!r}"
            )
        total = total + np.asarray(channel.volts, dtype=np.float64) ** 2
    return Channel(
        name="|B|",
        volts=np.sqrt(total),
        t0=first.t0,
        dt=first.dt,
        unit=first.unit,
    )
