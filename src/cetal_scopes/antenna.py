"""Physical detector descriptions and their calibration.

An :class:`Antenna` records which sensor is attached to a channel and how its
raw voltage maps to a physical field. The mapping is a frequency-dependent
complex :class:`TransferFunction`, so magnitude *and* phase calibration are
preserved (e.g. a B-dot probe measured against a Helmholtz coil).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = ["Antenna", "TransferFunction"]


def _as_vector3(value: Sequence[float], name: str) -> tuple[float, float, float]:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError(f"{name} must have 3 components, got shape {vector.shape}")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return (float(vector[0]), float(vector[1]), float(vector[2]))


@dataclass(frozen=True, eq=False)
class TransferFunction:
    """A sampled complex calibration ``gain`` versus ``freq``.

    The gain converts a measured voltage into the sensor's output quantity, so
    the raw voltage is the field times the gain (e.g. ``V = H(f) * dB/dt`` for a
    B-dot probe). Interpolation is linear on the real and imaginary parts.

    Parameters
    ----------
    freq : array_like
        Calibration frequencies in hertz, strictly increasing.
    gain : array_like
        Complex gain at each frequency, same length as ``freq``. Must be finite.
    unit : str, optional
        Unit of ``gain``, e.g. ``"V/(T/s)"`` for a B-dot.

    Attributes
    ----------
    f_min, f_max : float
        Lowest and highest calibrated frequency in Hz.
    """

    freq: ArrayLike
    gain: ArrayLike
    unit: str = "V/(T/s)"

    def __post_init__(self) -> None:
        freq = np.asarray(self.freq, dtype=np.float64)
        gain = np.asarray(self.gain, dtype=np.complex128)
        if freq.ndim != 1:
            raise ValueError(f"freq must be 1D, got {freq.ndim}D")
        if gain.shape != freq.shape:
            raise ValueError(
                f"gain shape {gain.shape} does not match freq shape {freq.shape}"
            )
        if freq.size < 2:
            raise ValueError("a transfer function needs at least two frequencies")
        if not np.all(np.isfinite(freq)) or not np.all(np.isfinite(gain)):
            raise ValueError("freq and gain must be finite")
        if np.any(np.diff(freq) <= 0):
            raise ValueError("freq must be strictly increasing")
        if not self.unit:
            raise ValueError("unit must be non-empty")
        object.__setattr__(self, "freq", freq)
        object.__setattr__(self, "gain", gain)

    @property
    def f_min(self) -> float:
        """Lowest calibrated frequency in Hz."""
        return float(np.asarray(self.freq)[0])

    @property
    def f_max(self) -> float:
        """Highest calibrated frequency in Hz."""
        return float(np.asarray(self.freq)[-1])

    def gain_at(self, freq: ArrayLike) -> NDArray[np.complex128]:
        """Interpolate the complex gain at ``freq``.

        Frequencies outside ``[f_min, f_max]`` return ``nan`` rather than being
        silently clamped, so callers can decide how to treat uncalibrated bands.
        """
        freqs = np.asarray(freq, dtype=np.float64)
        grid = np.asarray(self.freq, dtype=np.float64)
        gain = np.asarray(self.gain, dtype=np.complex128)
        real = np.interp(freqs, grid, gain.real, left=np.nan, right=np.nan)
        imag = np.interp(freqs, grid, gain.imag, left=np.nan, right=np.nan)
        return (real + 1j * imag).astype(np.complex128)

    def __repr__(self) -> str:
        return (
            f"TransferFunction(n_points={np.asarray(self.freq).size}, "
            f"f_min={self.f_min!r}, f_max={self.f_max!r}, unit={self.unit!r})"
        )


@dataclass(frozen=True, eq=False)
class Antenna:
    """The physical sensor attached to a channel.

    Parameters
    ----------
    name : str
        Human-readable sensor name, e.g. ``"Bdot-X"``. Must be non-empty.
    kind : str, optional
        Sensor family, e.g. ``"b-dot"``, ``"loop"``, ``"e-field"``.
    axis : sequence of float, optional
        Sensor orientation as a 3-vector in the lab frame. Only its direction
        matters; :attr:`unit_axis` returns the normalized form.
    transfer_function : TransferFunction, optional
        Frequency-dependent complex calibration.
    delay : float, optional
        Signal/cable delay in seconds, subtracted before calibration.
    position : sequence of float, optional
        Sensor position as a 3-vector in the lab frame, in metres.

    Attributes
    ----------
    unit_axis : numpy.ndarray or None
        :attr:`axis` normalized to unit length, or ``None`` when unset.
    """

    name: str
    kind: str = "unknown"
    axis: Sequence[float] | None = None
    transfer_function: TransferFunction | None = None
    delay: float = 0.0
    position: Sequence[float] | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")
        if not self.kind:
            raise ValueError("kind must be non-empty")
        if self.axis is not None:
            axis = _as_vector3(self.axis, "axis")
            if float(np.linalg.norm(axis)) == 0.0:
                raise ValueError("axis must be a non-zero vector")
            object.__setattr__(self, "axis", axis)
        if self.position is not None:
            object.__setattr__(self, "position", _as_vector3(self.position, "position"))
        if not np.isfinite(self.delay):
            raise ValueError(f"delay must be finite, got {self.delay!r}")

    @property
    def unit_axis(self) -> NDArray[np.float64] | None:
        """The orientation unit vector, or ``None`` when unset."""
        if self.axis is None:
            return None
        return np.asarray(self.axis, dtype=np.float64) / float(
            np.linalg.norm(self.axis)
        )

    def gain_at(self, freq: ArrayLike) -> NDArray[np.complex128]:
        """Interpolate the calibration gain at ``freq``.

        Raises
        ------
        ValueError
            If the antenna has no transfer function.
        """
        if self.transfer_function is None:
            raise ValueError(f"antenna {self.name!r} has no transfer function")
        return self.transfer_function.gain_at(freq)

    def __repr__(self) -> str:
        return (
            f"Antenna(name={self.name!r}, kind={self.kind!r}, "
            f"calibrated={self.transfer_function is not None})"
        )
