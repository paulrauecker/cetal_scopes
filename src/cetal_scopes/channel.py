"""Per-channel view of a :class:`~cetal_scopes.capture.Capture`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.antenna import Antenna

__all__ = ["Channel"]


@dataclass(frozen=True, eq=False)
class Channel:
    """A single channel of a :class:`~cetal_scopes.capture.Capture`.

    A channel is a lightweight row-view onto the arrays owned by its capture:
    constructing one does not copy waveform data. Both waveform arrays are 1D
    and share the capture's ``t0`` and ``dt``.

    Parameters
    ----------
    name : str
        Channel name, e.g. ``"CH1"``. Unique within a capture.
    volts : numpy.ndarray
        Waveform samples in volts, shape ``(n_samples,)``, float64.
    t0 : float
        Time of the first sample, in seconds.
    dt : float
        Sample interval, in seconds. Must be positive.
    raw : numpy.ndarray, optional
        Raw ADC codes for the same samples, shape ``(n_samples,)``. ``None``
        when the driver only exposes converted data.
    antenna : Antenna, optional
        The physical sensor attached to this channel, persisted with the
        capture. ``None`` when the sensor is unknown.

    Attributes
    ----------
    time : numpy.ndarray
        Derived time axis, ``t0 + arange(n_samples) * dt``, in seconds.
    """

    name: str
    volts: NDArray[np.float64]
    t0: float
    dt: float
    raw: NDArray[Any] | None = None
    antenna: Antenna | None = None

    @property
    def n_samples(self) -> int:
        """Number of samples in the channel."""
        return int(self.volts.shape[0])

    @property
    def time(self) -> NDArray[np.float64]:
        """Time axis in seconds, derived from ``t0`` and ``dt``."""
        return self.t0 + np.arange(self.n_samples, dtype=np.float64) * self.dt

    def __len__(self) -> int:
        """Return the number of samples."""
        return self.n_samples

    def __repr__(self) -> str:
        return f"Channel(name={self.name!r}, n_samples={self.n_samples})"
