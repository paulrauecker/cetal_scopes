"""Abstract base class for instrument drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import TracebackType
from typing import Any, ClassVar, Self

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

    In addition to a driver's own panel-native keys (e.g. Siglent's
    ``timebase`` in s/div), every driver also accepts a shared, physical,
    SI-unit vocabulary in :meth:`configure`, so that application code can
    drive any instrument without knowing its front panel (see
    :mod:`cetal_scopes.scopes._settings`):

    - ``sample_rate`` -- Hz
    - ``record_length`` -- samples
    - ``pretrigger`` -- samples (``int``) or a fraction of the record
      (``float`` in ``[0, 1]``)
    - ``channels`` -- sequence of channel names
    - ``range`` -- volts full-scale (the channel spans ``+/-range``)
    - ``offset`` -- volts
    - ``coupling`` -- e.g. ``"DC"``, ``"AC"``
    - ``impedance`` -- ohms
    - ``trigger`` -- mapping with ``source``, ``level`` (volts), ``slope``

    ``range``, ``offset``, ``coupling``, and ``impedance`` each accept either
    a single value (applied to every channel) or a mapping from channel name
    to value. A driver whose hardware fixes one of these (e.g. the M5i's
    coupling and impedance) accepts the value that matches as a no-op and
    raises :class:`ValueError` for any other value, so shared application
    code can set it on any driver without special-casing the instrument.

    **Acquisition lifecycle.** Besides the one-shot :meth:`acquire`, a scope
    exposes a staged lifecycle so that several instruments can be armed
    together before any of them is triggered::

        idle --arm()--> armed --wait()--> triggered --fetch()--> idle

    :meth:`abort` returns to ``idle`` from anywhere. The base class provides a
    working *emulation* of the staged methods on top of :meth:`acquire`, so
    every driver has them; the emulation cannot actually separate arming from
    triggering (the whole acquisition happens inside :meth:`wait`), and
    advertises that by leaving :attr:`supports_staged_acquisition` ``False``.
    A driver that arms the hardware for real overrides the three methods and
    sets the flag to ``True``.
    """

    #: ``True`` when :meth:`arm` really arms the hardware and returns before
    #: the trigger, so this instrument can join a multi-instrument arm
    #: barrier. ``False`` means the base-class emulation is in use and the
    #: acquisition does not start until :meth:`wait`.
    supports_staged_acquisition: ClassVar[bool] = False

    #: ``True`` when :meth:`force_trigger` is implemented against the hardware.
    supports_force_trigger: ClassVar[bool] = False

    # Class-level defaults, so that no subclass is required to call
    # ``super().__init__()`` -- none of the existing drivers do.
    _armed: bool = False
    _staged_capture: Capture | None = None

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

    @property
    def armed(self) -> bool:
        """Whether an acquisition is armed and not yet fetched."""
        return self._armed

    def arm(self) -> None:
        """Prepare the instrument to capture the next trigger, without blocking.

        Returns as soon as the instrument will record an incoming trigger. It
        does **not** wait for the trigger; that is :meth:`wait`.

        Raises
        ------
        RuntimeError
            If already armed. Call :meth:`fetch` or :meth:`abort` first.
        """
        if self._armed:
            raise RuntimeError("already armed; call fetch() or abort() first")
        self._staged_capture = None
        self._armed = True

    def wait(self, timeout: float | None = None) -> bool:
        """Block up to ``timeout`` seconds for the armed acquisition to finish.

        Never raises :class:`TimeoutError`: a return of ``False`` leaves the
        instrument armed, so ``wait`` may be called again. That makes it the
        polling primitive an application slices to stay responsive (checking an
        abort flag, refreshing a status display) while waiting for a trigger.

        Parameters
        ----------
        timeout : float, optional
            Seconds to wait. ``None`` uses the driver's own default.

        Returns
        -------
        bool
            ``True`` when the acquisition is complete, ``False`` on expiry.

        Raises
        ------
        RuntimeError
            If the instrument is not armed.
        """
        # Degraded emulation: this base implementation cannot separate arming
        # from triggering, so the whole acquisition happens here and ``timeout``
        # is ignored in favour of the driver's own. See
        # ``supports_staged_acquisition``.
        if not self._armed:
            raise RuntimeError("wait() called before arm()")
        if self._staged_capture is None:
            self._staged_capture = self.acquire()
        return True

    def fetch(self) -> Capture:
        """Return the completed acquisition and disarm.

        Raises
        ------
        RuntimeError
            If not armed, or if :meth:`wait` has not yet returned ``True``.
        """
        capture = self._staged_capture
        if not self._armed or capture is None:
            raise RuntimeError("fetch() called before a completed wait()")
        self._armed = False
        self._staged_capture = None
        return capture

    def fetch_all(self) -> list[Capture]:
        """Return every capture of the completed acquisition.

        The uniform entry point for callers that must cope with instruments
        producing more than one capture per trigger (hardware-segmented
        acquisition). Defaults to a single-element list of :meth:`fetch`.
        """
        return [self.fetch()]

    def abort(self) -> None:
        """Disarm and discard any pending acquisition.

        Must be safe to call when not armed and when never connected, and must
        not raise -- it runs on error and cleanup paths.
        """
        self._armed = False
        self._staged_capture = None

    def force_trigger(self) -> None:
        """Trigger the armed acquisition in software.

        Only meaningful between :meth:`arm` and :meth:`fetch`.

        Raises
        ------
        NotImplementedError
            If the driver cannot force a trigger; check
            :attr:`supports_force_trigger` first.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot force a trigger in software"
        )

    def trigger_status(self) -> str | None:
        """Vendor trigger state for display, or ``None`` when unavailable."""
        return None

    def _default_timeout(self) -> float:
        """Seconds :meth:`wait` waits when given no explicit timeout."""
        return float(getattr(self, "_acquire_timeout", 0.0))

    def _acquire_staged(self, timeout: float | None = None) -> Capture:
        """Run one acquisition through the staged lifecycle.

        Drivers that implement :meth:`arm` / :meth:`wait` / :meth:`fetch`
        natively delegate :meth:`acquire` here.
        """
        self.arm()
        try:
            if not self.wait(timeout):
                limit = self._default_timeout() if timeout is None else timeout
                raise TimeoutError(f"acquisition did not complete within {limit:g}s")
        except BaseException:
            self.abort()
            raise
        return self.fetch()

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
