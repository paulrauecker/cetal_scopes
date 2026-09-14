"""The :class:`Capture` container: the acquisition/persistence boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.antenna import Antenna
from cetal_scopes.channel import Channel

__all__ = ["Capture"]


def _default_channel_names(n_channels: int) -> tuple[str, ...]:
    return tuple(f"CH{i + 1}" for i in range(n_channels))


@dataclass(eq=False)
class Capture:
    """One multi-channel acquisition sharing a single timebase.

    A capture owns a 2D array per representation: ``volts`` (float64) and,
    optionally, ``raw`` (native ADC code dtype). Both have shape
    ``(n_channels, n_samples)``. Channel ``i`` of ``channel_names`` corresponds
    to row ``i``; the per-channel views are exposed through :attr:`channels`.

    Parameters
    ----------
    volts : numpy.ndarray
        Waveform data in volts, shape ``(n_channels, n_samples)``. Converted to
        float64 if needed.
    t0 : float
        Time of the first sample, in seconds.
    dt : float
        Sample interval, in seconds. Must be positive.
    channel_names : tuple of str, optional
        Name of each row. Defaults to ``("CH1", "CH2", ...)``. Must be unique
        and non-empty.
    raw : numpy.ndarray, optional
        Raw ADC codes, shape ``(n_channels, n_samples)``. ``None`` when only
        converted data is available.
    antennas : mapping of str to Antenna, optional
        Sensors keyed by channel name. Names not present in ``channel_names``
        are rejected. Unmapped channels get ``None``.
    units : mapping of str to str, optional
        Physical unit per channel name. Unmapped channels default to ``"V"``.
    metadata : dict, optional
        Free-form provenance (instrument, timestamp, notes), persisted verbatim.

    Attributes
    ----------
    channels : dict of str to Channel
        Maps each channel name to its row-view, carrying its antenna.
    """

    volts: NDArray[np.float64]
    t0: float
    dt: float
    channel_names: tuple[str, ...] | None = None
    raw: NDArray[Any] | None = None
    antennas: Mapping[str, Antenna] | None = None
    units: Mapping[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, Channel] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        volts = np.asarray(self.volts, dtype=np.float64)
        if volts.ndim != 2:
            raise ValueError(
                f"volts must be 2D (n_channels, n_samples), got {volts.ndim}D"
            )
        self.volts = volts

        raw: NDArray[Any] | None = None
        if self.raw is not None:
            raw = np.asarray(self.raw)
            if raw.ndim != 2:
                raise ValueError(
                    f"raw must be 2D (n_channels, n_samples), got {raw.ndim}D"
                )
            if raw.shape != volts.shape:
                raise ValueError(
                    f"raw shape {raw.shape} does not match volts shape {volts.shape}"
                )
        self.raw = raw

        if not np.isfinite(self.t0):
            raise ValueError(f"t0 must be finite, got {self.t0!r}")
        if not np.isfinite(self.dt) or self.dt <= 0:
            raise ValueError(f"dt must be positive and finite, got {self.dt!r}")

        self._set_channel_names(volts.shape[0])
        self._set_antennas()
        self._set_units()
        self.channels = self._build_channels()

    def _set_channel_names(self, n_channels: int) -> None:
        names = (
            _default_channel_names(n_channels)
            if self.channel_names is None
            else tuple(self.channel_names)
        )
        if len(names) != n_channels:
            raise ValueError(
                f"got {len(names)} channel names for {n_channels} channels"
            )
        if any(not name for name in names):
            raise ValueError("channel names must be non-empty")
        if len(set(names)) != len(names):
            raise ValueError(f"channel names must be unique, got {names!r}")
        self.channel_names = names

    def _set_antennas(self) -> None:
        if self.channel_names is None:  # pragma: no cover - set just above
            raise RuntimeError("channel names not initialized")
        antennas = {} if self.antennas is None else dict(self.antennas)
        unknown = set(antennas) - set(self.channel_names)
        if unknown:
            raise ValueError(
                f"antennas given for unknown channels: {sorted(unknown)!r}"
            )
        self.antennas = antennas

    def _set_units(self) -> None:
        if self.channel_names is None:  # pragma: no cover - set just above
            raise RuntimeError("channel names not initialized")
        units = {} if self.units is None else dict(self.units)
        unknown = set(units) - set(self.channel_names)
        if unknown:
            raise ValueError(f"units given for unknown channels: {sorted(unknown)!r}")
        if any(not unit for unit in units.values()):
            raise ValueError("units must be non-empty strings")
        self.units = units

    def _build_channels(self) -> dict[str, Channel]:
        if self.channel_names is None:  # pragma: no cover - set just above
            raise RuntimeError("channel names not initialized")
        antennas = self.antennas or {}
        units = self.units or {}
        return {
            name: Channel(
                name=name,
                volts=self.volts[i],
                t0=self.t0,
                dt=self.dt,
                raw=None if self.raw is None else self.raw[i],
                antenna=antennas.get(name),
                unit=units.get(name, "V"),
            )
            for i, name in enumerate(self.channel_names)
        }

    def with_antennas(self, antennas: Mapping[str, Antenna]) -> Capture:
        """Return a copy of this capture with ``antennas`` merged in.

        Channel views are rebuilt, so a driver-produced capture can be
        annotated with its sensors without touching the waveform data.
        """
        merged = dict(self.antennas or {})
        merged.update(antennas)
        return Capture(
            volts=self.volts,
            t0=self.t0,
            dt=self.dt,
            channel_names=self.channel_names,
            raw=self.raw,
            antennas=merged,
            units=self.units,
            metadata=dict(self.metadata),
        )

    @property
    def n_channels(self) -> int:
        """Number of channels."""
        return int(self.volts.shape[0])

    @property
    def n_samples(self) -> int:
        """Number of samples per channel."""
        return int(self.volts.shape[1])

    @property
    def time(self) -> NDArray[np.float64]:
        """Shared time axis in seconds, derived from ``t0`` and ``dt``."""
        return self.t0 + np.arange(self.n_samples, dtype=np.float64) * self.dt

    def __getitem__(self, name: str) -> Channel:
        """Return the channel called ``name``."""
        return self.channels[name]

    def __contains__(self, name: object) -> bool:
        return name in self.channels

    def __iter__(self) -> Iterator[Channel]:
        return iter(self.channels.values())

    def __len__(self) -> int:
        """Return the number of channels."""
        return self.n_channels

    def __repr__(self) -> str:
        return (
            f"Capture(n_channels={self.n_channels}, n_samples={self.n_samples}, "
            f"t0={self.t0!r}, dt={self.dt!r})"
        )
