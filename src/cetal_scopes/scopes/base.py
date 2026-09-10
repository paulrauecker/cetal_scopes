"""Abstract base class for instrument drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

from cetal_scopes.capture import Capture

__all__ = ["Scope"]


class Scope(ABC):
    """Abstract base class for an acquisition instrument.

    A :class:`Scope` talks to one piece of hardware and normalizes each
    acquisition into a :class:`~cetal_scopes.capture.Capture`. Subclasses
    implement the vendor protocol; the base class only fixes the lifecycle and
    the context-manager behavior::

        with MyScope(address="192.0.2.1") as scope:
            scope.configure({"sample_rate": 1e9})
            capture = scope.acquire()

    Entering the context calls :meth:`connect`; leaving it calls
    :meth:`close`, even when the body raises. Vendor SDKs must be imported
    lazily inside subclasses so that importing :mod:`cetal_scopes` never
    requires vendor libraries or hardware.
    """

    @abstractmethod
    def connect(self) -> None:
        """Open the connection to the instrument."""

    @abstractmethod
    def configure(self, settings: Mapping[str, Any]) -> None:
        """Apply acquisition settings to the instrument.

        Parameters
        ----------
        settings : mapping of str to Any
            Instrument settings, keyed by a driver-defined name.
        """

    @abstractmethod
    def acquire(self) -> Capture:
        """Run one acquisition and return the normalized capture.

        Returns
        -------
        Capture
            The acquired, fully populated capture.
        """

    @abstractmethod
    def close(self) -> None:
        """Release the connection.

        Must be safe to call more than once and when never connected.
        """

    def __enter__(self) -> Self:
        """Connect and return the scope."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the scope, even if the body raised."""
        self.close()
