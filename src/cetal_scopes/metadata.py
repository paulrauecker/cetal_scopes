"""Pydantic models for the on-disk capture format.

These are the *serialization* schema: :func:`cetal_scopes.save_capture` builds
them from a :class:`~cetal_scopes.capture.Capture` and
:func:`cetal_scopes.load_capture` reconstructs one. The runtime containers stay
plain dataclasses with NumPy arrays.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cetal_scopes.antenna import Antenna, TransferFunction

__all__ = [
    "FORMAT_VERSION",
    "SHOT_FORMAT_VERSION",
    "AntennaModel",
    "CaptureFile",
    "ChannelMetadata",
    "ShotCaptureEntry",
    "ShotFile",
    "TransferFunctionModel",
]

FORMAT_VERSION = 1

#: Versioned independently of ``FORMAT_VERSION``: a shot is a container of
#: captures, so the two schemas can move apart.
SHOT_FORMAT_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransferFunctionModel(_Model):
    """JSON form of :class:`~cetal_scopes.antenna.TransferFunction`."""

    unit: str = "V/(T/s)"
    freq_hz: list[float]
    gain_real: list[float]
    gain_imag: list[float]

    @classmethod
    def from_runtime(cls, transfer_function: TransferFunction) -> TransferFunctionModel:
        """Build the model from a runtime :class:`TransferFunction`."""
        gain = np.asarray(transfer_function.gain, dtype=np.complex128)
        return cls(
            unit=transfer_function.unit,
            freq_hz=[float(value) for value in np.asarray(transfer_function.freq)],
            gain_real=[float(value) for value in gain.real],
            gain_imag=[float(value) for value in gain.imag],
        )

    def to_runtime(self) -> TransferFunction:
        """Rebuild the runtime :class:`TransferFunction`."""
        return TransferFunction(
            freq=np.asarray(self.freq_hz, dtype=np.float64),
            gain=np.asarray(self.gain_real, dtype=np.float64)
            + 1j * np.asarray(self.gain_imag, dtype=np.float64),
            unit=self.unit,
        )


class AntennaModel(_Model):
    """JSON form of :class:`~cetal_scopes.antenna.Antenna`."""

    name: str
    kind: str = "unknown"
    axis: list[float] | None = None
    position: list[float] | None = None
    delay_s: float = 0.0
    transfer_function: TransferFunctionModel | None = None

    @classmethod
    def from_runtime(cls, antenna: Antenna) -> AntennaModel:
        """Build the model from a runtime :class:`Antenna`."""
        return cls(
            name=antenna.name,
            kind=antenna.kind,
            axis=None if antenna.axis is None else list(antenna.axis),
            position=None if antenna.position is None else list(antenna.position),
            delay_s=float(antenna.delay),
            transfer_function=(
                None
                if antenna.transfer_function is None
                else TransferFunctionModel.from_runtime(antenna.transfer_function)
            ),
        )

    def to_runtime(self) -> Antenna:
        """Rebuild the runtime :class:`Antenna`."""
        return Antenna(
            name=self.name,
            kind=self.kind,
            axis=None if self.axis is None else tuple(self.axis),
            position=None if self.position is None else tuple(self.position),
            delay=float(self.delay_s),
            transfer_function=(
                None
                if self.transfer_function is None
                else self.transfer_function.to_runtime()
            ),
        )


class ChannelMetadata(_Model):
    """Per-channel metadata persisted alongside a capture."""

    name: str
    antenna: AntennaModel | None = None
    unit: str = "V"


class CaptureFile(_Model):
    """Top-level JSON schema of a saved capture."""

    format_version: int = FORMAT_VERSION
    t0: float
    dt: float
    sample_rate: float
    channel_names: list[str]
    n_samples: int
    volts_sidecar: str
    raw_sidecar: str | None = None
    channels: list[ChannelMetadata] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShotCaptureEntry(_Model):
    """One capture's place in a saved shot."""

    label: str
    offset_s: float = 0.0
    capture_json: str
    """Bare filename of the capture's JSON, resolved next to ``shot.json``."""


class ShotFile(_Model):
    """Top-level JSON schema of a saved shot.

    The waveform data lives in the per-capture files this references, so a
    shot directory is self-contained and movable.
    """

    format_version: int = SHOT_FORMAT_VERSION
    reference: str | None = None
    captures: list[ShotCaptureEntry] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
