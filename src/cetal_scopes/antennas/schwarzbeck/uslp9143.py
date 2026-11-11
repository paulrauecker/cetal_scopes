"""Schwarzbeck USLP 9143 log-periodic antenna, 300 MHz to 7 GHz.

The calibration is the table printed on the antenna's label: isotropic gain
``gi`` in dBi and antenna factor ``AF`` in dB/m at eight frequencies. The
antenna factor converts the voltage into a 50 ohm load to the incident electric
field::

    E [dBuV/m] = V [dBuV] + AF [dB/m]

so the antenna is a plain E-field sensor (not a derivative probe) with gain
``V / E = 10**(-AF / 20)`` in V/(V/m), i.e. an effective length in metres. The
label's two columns are consistent with the far-field relation
``AF = 20 log10(f / 1 MHz) - gi - 29.79`` to within 0.1 dB.

Eight points are too sparse for :class:`~cetal_scopes.antenna.TransferFunction`,
which interpolates linearly on the linear gain: between 0.3 and 1 GHz that is
off by ~2 dB. :func:`uslp9143` therefore resamples the label onto a dense
log-spaced grid, interpolating ``AF`` linearly in ``log10(f)``. That is the same
as interpolating ``gi`` linearly in ``log10(f)``, since ``AF`` and ``-gi``
differ by a term linear in ``log10(f)``; the label points are kept exactly.

The calibration is magnitude only (no phase), valid only in the far field, on
boresight, with matched polarization and a 50 ohm termination. Below 300 MHz
and above 7 GHz the transfer function is undefined (``gain_at`` returns
``nan``).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from cetal_scopes.antenna import Antenna, TransferFunction

__all__ = ["USLP9143_CALIBRATION", "uslp9143"]

USLP9143_CALIBRATION: tuple[tuple[float, float, float], ...] = (
    # (frequency in Hz, gi in dBi, AF in dB/m), as printed on the label.
    (0.3e9, 5.7, 14.0),
    (1.0e9, 6.8, 23.5),
    (2.0e9, 6.2, 30.1),
    (3.0e9, 5.6, 34.1),
    (4.0e9, 5.0, 37.3),
    (5.0e9, 4.8, 39.4),
    (6.0e9, 5.4, 40.4),
    (7.0e9, 5.0, 42.1),
)
"""The label's calibration table: ``(freq_hz, gain_dbi, af_db_per_m)`` rows."""

_GRID_POINTS = 256
"""Log-spaced resampling points across the band, before adding the label's."""


def _transfer_function() -> TransferFunction:
    table = np.asarray(USLP9143_CALIBRATION, dtype=np.float64)
    label_freq, label_af = table[:, 0], table[:, 2]
    freq = np.union1d(
        np.geomspace(label_freq[0], label_freq[-1], _GRID_POINTS), label_freq
    )
    af = np.interp(np.log10(freq), np.log10(label_freq), label_af)
    return TransferFunction(freq=freq, gain=10.0 ** (-af / 20.0), unit="V/(V/m)")


def uslp9143(
    *,
    name: str | None = None,
    axis: Sequence[float] | None = None,
    position: Sequence[float] | None = None,
    delay: float = 0.0,
) -> Antenna:
    """Build a calibrated :class:`~cetal_scopes.antenna.Antenna` for the USLP 9143.

    Parameters
    ----------
    name : str, optional
        Antenna name; defaults to ``"USLP-9143"``.
    axis : sequence of float, optional
        Orientation in the lab frame, passed through to :class:`Antenna`. For
        this antenna, the polarization direction (along the dipole elements).
    position : sequence of float, optional
        Position in the lab frame, passed through to :class:`Antenna`.
    delay : float, optional
        Cable delay in seconds, passed through to :class:`Antenna`.

    Returns
    -------
    Antenna
        The antenna with the label's antenna factor as a transfer function.
    """
    return Antenna(
        name=name if name is not None else "USLP-9143",
        kind="e-field",
        axis=axis,
        transfer_function=_transfer_function(),
        delay=delay,
        position=position,
    )
