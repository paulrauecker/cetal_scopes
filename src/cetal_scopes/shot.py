"""In-memory grouping and time alignment of several captures.

A :class:`Shot` is a convenience aggregator for one experiment whose data was
recorded by more than one instrument. Each :class:`~cetal_scopes.capture.Capture`
has its own trigger and timebase, so different captures do not share a clock;
:class:`Shot` pairs them with a scalar time offset that puts them on a common
axis. It is an in-memory container only and is not persisted.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.channel import Channel

__all__ = ["Shot"]


@dataclass(eq=False)
class Shot:
    """Several captures from one experiment, with per-capture time offsets.

    Parameters
    ----------
    captures : mapping of str to Capture, optional
        Captures keyed by label (e.g. ``"siglent"``, ``"m4i"``), in the order
        they were taken.
    offsets : mapping of str to float, optional
        Seconds added to each capture's time axis to align it to the reference,
        ``t_ref = t_capture + offset[label]``. Missing labels default to ``0``.
    reference : str, optional
        Label whose time axis is the alignment target. Defaults to the first
        capture.
    metadata : dict, optional
        Free-form notes about the shot.

    Attributes
    ----------
    channels : dict of str to Channel
        Every channel of every capture, keyed ``"label:channel"``, with the
        recorded (unshifted) time axis.
    """

    captures: dict[str, Capture] = field(default_factory=dict)
    offsets: dict[str, float] = field(default_factory=dict)
    reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.captures = dict(self.captures)
        self.offsets = {label: float(value) for label, value in self.offsets.items()}
        self.metadata = dict(self.metadata)

        unknown = set(self.offsets) - set(self.captures)
        if unknown:
            raise ValueError(f"offsets given for unknown captures: {sorted(unknown)!r}")
        for label, offset in self.offsets.items():
            if not np.isfinite(offset):
                raise ValueError(f"offset for {label!r} must be finite, got {offset!r}")

        if self.reference is None:
            self.reference = next(iter(self.captures), None)
        elif self.reference not in self.captures:
            raise ValueError(f"reference {self.reference!r} is not a known capture")

    @classmethod
    def from_captures(
        cls,
        captures: Mapping[str, Capture],
        *,
        reference: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Shot:
        """Build a shot from a mapping of label to capture."""
        return cls(
            captures=dict(captures),
            reference=reference,
            metadata=dict(metadata or {}),
        )

    def add(self, label: str, capture: Capture, *, offset: float = 0.0) -> None:
        """Add a capture under ``label``.

        Raises
        ------
        ValueError
            If ``label`` is empty, already present, or ``offset`` is not finite.
        """
        if not label:
            raise ValueError("label must be non-empty")
        if label in self.captures:
            raise ValueError(f"capture {label!r} already added")
        if not np.isfinite(offset):
            raise ValueError(f"offset must be finite, got {offset!r}")
        self.captures[label] = capture
        self.offsets[label] = float(offset)
        if self.reference is None:
            self.reference = label

    def _require(self, label: str) -> None:
        if label not in self.captures:
            raise KeyError(label)

    def set_offset(self, label: str, offset: float) -> None:
        """Set the time offset of ``label`` in seconds."""
        self._require(label)
        if not np.isfinite(offset):
            raise ValueError(f"offset must be finite, got {offset!r}")
        self.offsets[label] = float(offset)

    def set_reference(self, label: str) -> None:
        """Make ``label`` the reference capture."""
        self._require(label)
        self.reference = label

    def time_offset(self, label: str) -> float:
        """Return the offset of ``label`` in seconds (``0`` when unset)."""
        self._require(label)
        return self.offsets.get(label, 0.0)

    @property
    def n_captures(self) -> int:
        """Number of captures in the shot."""
        return len(self.captures)

    @property
    def channels(self) -> dict[str, Channel]:
        """All channels keyed ``"label:channel"`` with unshifted times."""
        return {
            f"{label}:{name}": channel
            for label, capture in self.captures.items()
            for name, channel in capture.channels.items()
        }

    def aligned_channel(self, label: str, name: str) -> Channel:
        """Return channel ``name`` of ``label`` with its offset applied.

        The waveform data is shared, not copied.
        """
        channel = self.captures[label][name]
        return replace(channel, t0=channel.t0 + self.offsets.get(label, 0.0))

    def aligned_channels(self) -> dict[str, Channel]:
        """All channels keyed ``"label:channel"`` with offsets applied."""
        return {
            f"{label}:{name}": self.aligned_channel(label, name)
            for label, capture in self.captures.items()
            for name in capture.channels
        }

    def time_bounds(self) -> tuple[float, float]:
        """Return the ``(start, end)`` of all captures after applying offsets."""
        if not self.captures:
            raise ValueError("shot has no captures")
        starts: list[float] = []
        ends: list[float] = []
        for label, capture in self.captures.items():
            shift = self.offsets.get(label, 0.0)
            starts.append(capture.t0 + shift)
            ends.append(capture.t0 + shift + (capture.n_samples - 1) * capture.dt)
        return (min(starts), max(ends))

    def common_time(self, *, dt: float | None = None) -> NDArray[np.float64]:
        """Return a uniform time grid covering the aligned captures.

        Parameters
        ----------
        dt : float, optional
            Grid step in seconds. Defaults to the finest capture step.
        """
        if not self.captures:
            raise ValueError("shot has no captures")
        t0, t1 = self.time_bounds()
        if dt is None:
            dt = min(capture.dt for capture in self.captures.values())
        if dt <= 0:
            raise ValueError("dt must be positive")
        n = max(2, int(np.floor((t1 - t0) / dt)) + 1)
        return t0 + np.arange(n, dtype=np.float64) * dt

    def __getitem__(self, label: str) -> Capture:
        return self.captures[label]

    def __contains__(self, label: object) -> bool:
        return label in self.captures

    def __iter__(self) -> Iterator[str]:
        return iter(self.captures)

    def __len__(self) -> int:
        return len(self.captures)

    def keys(self) -> Iterator[str]:
        """Iterate over the capture labels."""
        return iter(self.captures)

    def values(self) -> Iterator[Capture]:
        """Iterate over the captures."""
        return iter(self.captures.values())

    def items(self) -> Iterator[tuple[str, Capture]]:
        """Iterate over ``(label, capture)`` pairs."""
        return iter(self.captures.items())

    def __repr__(self) -> str:
        return (
            f"Shot(n_captures={self.n_captures}, labels={list(self.captures)!r}, "
            f"reference={self.reference!r})"
        )
