"""Progress events emitted while a multi-instrument shot runs.

These are plain dataclasses with no serialization of their own: an application
that needs JSON (for a WebSocket, a log file) encodes them itself, so the
library stays free of any transport or schema dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["AcquisitionEvent", "EventSink", "InstrumentState", "Phase"]

Phase = Literal[
    "idle",
    "preparing",
    "arming",
    "armed",
    "triggering",
    "waiting",
    "fetching",
    "assembling",
    "done",
]
"""Stage of a shot. ``armed`` marks the arm barrier: every instrument is ready
and no trigger has been solicited yet."""

InstrumentState = Literal[
    "idle",
    "arming",
    "armed",
    "waiting",
    "triggered",
    "fetching",
    "ok",
    "timeout",
    "error",
    "aborted",
    "skipped",
]
"""Where one instrument got to. Only ``ok`` means usable data."""


@dataclass(frozen=True)
class AcquisitionEvent:
    """One progress notification from a running shot.

    Attributes
    ----------
    kind : {"phase", "instrument", "log", "result"}
        Which of the other fields carry meaning: ``phase`` events set
        :attr:`phase`, ``instrument`` events set :attr:`label` and
        :attr:`state`, ``log`` events set :attr:`message`, and ``result``
        marks the end of the shot.
    shot_id : str
        Identifies the shot this event belongs to.
    t : float
        Seconds since the shot started, on a monotonic clock.
    detail : mapping
        Extra, event-specific values (e.g. ``arm_spread_s`` on the ``armed``
        phase event).
    """

    kind: Literal["phase", "instrument", "log", "result"]
    shot_id: str
    t: float
    phase: Phase | None = None
    label: str | None = None
    state: InstrumentState | None = None
    message: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


EventSink = Callable[[AcquisitionEvent], None]
"""Callback for :class:`AcquisitionEvent`.

Called on the event loop thread. It must not block and must not raise; the
orchestrator swallows exceptions from a sink so a broken consumer cannot fail
a shot, but a slow one still stalls the acquisition loop.
"""
