"""A named catalogue of the antennas on a bench.

A probe is a physical object: its calibration belongs to the probe, not to
whichever channel it happens to be plugged into today. So probes are defined
once, by name, and a bench description only says which name sits on which
channel (plus the per-installation placement -- cable delay, axis, position).

The catalogue always holds the vendor reference probes (``PBS-H1`` ...
``PBS-E1``, see :mod:`cetal_scopes.antennas.aaronia.pbs1`). Bench-specific
probes are added from a TOML file::

    [antenna.bdot-large]
    kind = "b-dot"
    turns = 1
    radius = 0.01            # m; or `area` in m^2
    f_max = 100e6            # Hz, upper edge of the calibrated band

    [antenna.bdot-small]
    kind = "b-dot"
    sensitivity = 2.0e-5     # V/(T/s), measured
    f_max = 500e6

    [antenna.bdot-spare]     # no calibration: attached, but b_field skips it
    kind = "b-dot"

A gain is either measured (``sensitivity``) or geometric (``turns`` times the
loop ``area``, the ideal B-dot response ``V = N A dB/dt``). Either way the
transfer function is flat and real over ``[f_min, f_max]``. An entry with
neither is still a valid antenna, deliberately: knowing *which* probe took a
trace is worth recording before its calibration exists, and
:func:`~cetal_scopes.analysis.fields.b_field` refuses an uncalibrated channel
rather than inventing a gain for it.
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator

from cetal_scopes.antenna import Antenna, TransferFunction
from cetal_scopes.antennas.aaronia.pbs1 import PBS1_PROBES, pbs1

__all__ = [
    "AntennaCatalog",
    "AntennaEntry",
    "builtin_antennas",
    "load_antenna_catalog",
    "parse_antenna_catalog",
]


def builtin_antennas() -> dict[str, Antenna]:
    """The vendor reference probes, keyed by their default names."""
    antennas = (pbs1(probe.model) for probe in PBS1_PROBES.values())
    return {antenna.name: antenna for antenna in antennas}


class AntennaEntry(BaseModel):
    """One probe's entry in a catalogue file.

    Attributes
    ----------
    kind : str
        Sensor family, e.g. ``"b-dot"``.
    sensitivity : float, optional
        Measured flat gain in :attr:`unit`. Excludes ``turns``/``area``/
        ``radius``.
    turns : int, optional
        Loop turns, for a geometric gain ``turns * area``.
    area : float, optional
        Loop area in m^2. Give this or ``radius``, with ``turns``.
    radius : float, optional
        Loop radius in m, for a circular loop.
    f_min, f_max : float, optional
        The calibrated band in Hz. ``f_max`` is required whenever a gain is.
    unit : str
        Unit of the gain.
    notes : str, optional
        Free text, e.g. where the calibration came from. Not carried onto the
        :class:`~cetal_scopes.antenna.Antenna`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "b-dot"
    sensitivity: float | None = None
    turns: int | None = None
    area: float | None = None
    radius: float | None = None
    f_min: float = 0.0
    f_max: float | None = None
    unit: str = "V/(T/s)"
    notes: str | None = None

    @model_validator(mode="after")
    def _one_calibration(self) -> Self:
        geometric = (self.turns, self.area, self.radius)
        if self.sensitivity is not None and any(v is not None for v in geometric):
            raise ValueError(
                "give 'sensitivity' or a loop geometry ('turns' with 'area' or "
                "'radius'), not both"
            )
        if self.sensitivity is not None and not (
            math.isfinite(self.sensitivity) and self.sensitivity > 0
        ):
            raise ValueError(f"sensitivity must be positive, got {self.sensitivity!r}")
        if any(v is not None for v in geometric):
            if self.turns is None or self.turns < 1:
                raise ValueError("a loop geometry needs 'turns' >= 1")
            if (self.area is None) == (self.radius is None):
                raise ValueError(
                    "a loop geometry needs exactly one of 'area', 'radius'"
                )
            size = self.area if self.area is not None else self.radius
            if size is None or not (math.isfinite(size) and size > 0):
                raise ValueError(f"loop size must be positive, got {size!r}")
        if self.gain is None:
            if self.f_max is not None:
                raise ValueError("'f_max' given without a gain to apply over it")
        elif self.f_max is None:
            raise ValueError("a calibrated probe needs 'f_max', its band's upper edge")
        elif not (0.0 <= self.f_min < self.f_max):
            raise ValueError(
                f"expected 0 <= f_min < f_max, got {self.f_min!r}, {self.f_max!r}"
            )
        return self

    @property
    def gain(self) -> float | None:
        """The flat gain in :attr:`unit`, or ``None`` when uncalibrated."""
        if self.sensitivity is not None:
            return self.sensitivity
        if self.turns is None:
            return None
        area = self.area if self.area is not None else math.pi * (self.radius or 0) ** 2
        return self.turns * area

    def build(self, name: str) -> Antenna:
        """The :class:`~cetal_scopes.antenna.Antenna` this entry describes."""
        gain = self.gain
        transfer = None
        if gain is not None and self.f_max is not None:
            transfer = TransferFunction(
                freq=[self.f_min, self.f_max], gain=[gain, gain], unit=self.unit
            )
        return Antenna(name=name, kind=self.kind, transfer_function=transfer)


class AntennaCatalog(Mapping[str, Antenna]):
    """Antennas by name: the built-in reference probes plus any added.

    Parameters
    ----------
    antennas : mapping of str to Antenna, optional
        Extra probes. A name may not shadow a built-in one, so ``PBS-H3``
        always means the vendor calibration.
    """

    def __init__(self, antennas: Mapping[str, Antenna] | None = None) -> None:
        builtin = builtin_antennas()
        extra = dict(antennas or {})
        clash = sorted(set(extra) & set(builtin))
        if clash:
            raise ValueError(
                f"these names are built-in and cannot be redefined: {clash}"
            )
        self._antennas = {**builtin, **extra}

    def __getitem__(self, name: str) -> Antenna:
        return self._antennas[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._antennas)

    def __len__(self) -> int:
        return len(self._antennas)

    def resolve(
        self,
        name: str,
        *,
        axis: Sequence[float] | None = None,
        position: Sequence[float] | None = None,
        delay: float | None = None,
    ) -> Antenna:
        """The antenna called ``name``, placed for one channel.

        Placement given here replaces the catalogue's; anything left ``None``
        keeps it.

        Raises
        ------
        ValueError
            If no antenna is called ``name``.
        """
        try:
            antenna = self._antennas[name]
        except KeyError:
            raise ValueError(
                f"unknown antenna {name!r}; the catalogue has {sorted(self)}"
            ) from None
        changes: dict[str, Any] = {}
        if axis is not None:
            changes["axis"] = axis
        if position is not None:
            changes["position"] = position
        if delay is not None:
            changes["delay"] = delay
        return replace(antenna, **changes) if changes else antenna

    def __repr__(self) -> str:
        return f"AntennaCatalog({sorted(self)!r})"


def parse_antenna_catalog(raw: Mapping[str, Any]) -> AntennaCatalog:
    """Build a catalogue from a parsed TOML mapping of ``[antenna.<name>]`` tables.

    Raises
    ------
    ValueError
        If the mapping has other top-level keys or an entry is invalid.
    """
    unknown = sorted(set(raw) - {"antenna"})
    if unknown:
        raise ValueError(f"unexpected top-level keys {unknown}; expected [antenna.*]")
    entries = raw.get("antenna", {})
    if not isinstance(entries, Mapping):
        raise ValueError("'antenna' must be a table of [antenna.<name>] tables")
    antennas: dict[str, Antenna] = {}
    for name, entry in entries.items():
        try:
            antennas[name] = AntennaEntry.model_validate(entry).build(name)
        except ValueError as exc:
            raise ValueError(f"antenna {name!r}: {exc}") from exc
    return AntennaCatalog(antennas)


def load_antenna_catalog(path: str | Path) -> AntennaCatalog:
    """Read a catalogue file; the built-in probes are always included.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is not valid TOML or an entry is invalid.
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"no antenna catalogue at {target}")
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{target} is not valid TOML: {exc}") from exc
    try:
        return parse_antenna_catalog(raw)
    except ValueError as exc:
        raise ValueError(f"{target}: {exc}") from exc
