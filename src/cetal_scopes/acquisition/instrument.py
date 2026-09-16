"""One instrument in a multi-instrument shot, and its async facade."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

from cetal_scopes.capture import Capture
from cetal_scopes.scopes.base import Scope

__all__ = ["AsyncScope", "InstrumentSpec"]

T = TypeVar("T")


@dataclass(frozen=True)
class InstrumentSpec:
    """One instrument's role in a shot.

    Parameters
    ----------
    label : str
        Key this instrument's capture gets in the resulting
        :class:`~cetal_scopes.shot.Shot`. Must be unique within a shot.
    scope : Scope
        The driver, connected or not.
    settings : mapping, optional
        Passed to :meth:`~cetal_scopes.scopes.base.Scope.configure` when the
        orchestrator opens.
    timeout : float, optional
        Seconds to wait for this instrument's trigger. ``None`` uses the
        orchestrator's default.
    direct_trigger : bool, optional
        Force this instrument's trigger in software once every instrument is
        armed. A debugging path: it fires the shot without the experiment.
    required : bool, optional
        Whether this instrument failing makes the shot incomplete.
    fetch_on_timeout : bool, optional
        Read the instrument's buffer even though no trigger arrived. **Off by
        default, deliberately**: an untriggered scope still holds the
        *previous* shot's waveform, so this yields a well-formed capture that
        is silently from the wrong event -- and time alignment will happily fit
        an offset to it. Any capture obtained this way is marked
        ``metadata["untriggered"] = True`` and the instrument is still reported
        as ``"timeout"``, never ``"ok"``.
    """

    label: str
    scope: Scope
    settings: Mapping[str, Any] = field(default_factory=dict)
    timeout: float | None = None
    direct_trigger: bool = False
    required: bool = True
    fetch_on_timeout: bool = False

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("label must be non-empty")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError(f"timeout must be positive, got {self.timeout!r}")


class AsyncScope:
    """Serialized async facade over one blocking, non-thread-safe driver.

    Every call runs on this instrument's own single worker thread, so the
    driver is never re-entered concurrently and always sees the same thread
    (vendor SDKs with thread-local state, PyVISA included, prefer that
    affinity). Calls are FIFO, so a :meth:`force_trigger` issued during a
    sliced :meth:`wait` runs as soon as that slice returns.

    A dedicated pool rather than :func:`asyncio.to_thread`: that shares the
    loop's default executor, so N instruments parked in a blocking wait would
    occupy N of its limited threads for the whole shot and starve the rest of
    the application.
    """

    def __init__(self, label: str, scope: Scope) -> None:
        self._label = label
        self._scope = scope
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"scope-{label}"
        )

    @property
    def label(self) -> str:
        """The instrument's label within the shot."""
        return self._label

    @property
    def scope(self) -> Scope:
        """The wrapped driver."""
        return self._scope

    @property
    def supports_staged_acquisition(self) -> bool:
        """Whether this driver can join a real arm barrier."""
        return self._scope.supports_staged_acquisition

    @property
    def supports_force_trigger(self) -> bool:
        """Whether this driver can be triggered in software."""
        return self._scope.supports_force_trigger

    async def _call(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        # Shielded: executor work is not cancellable, so dropping the future
        # would leave the driver mid-transaction while the next call re-enters
        # it on the same thread. Cancellation is deferred to a call boundary,
        # which is safe because every call is short by construction.
        return await asyncio.shield(loop.run_in_executor(self._pool, fn))

    async def connect(self) -> None:
        """Open the instrument connection."""
        await self._call(self._scope.connect)

    async def configure(self, settings: Mapping[str, Any]) -> None:
        """Apply settings to the instrument."""
        await self._call(lambda: self._scope.configure(settings))

    async def arm(self) -> None:
        """Arm the instrument for the next trigger."""
        await self._call(self._scope.arm)

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds; ``False`` means not yet, not failed."""
        return await self._call(lambda: self._scope.wait(timeout))

    async def fetch_all(self) -> list[Capture]:
        """Transfer every capture of the completed acquisition."""
        return await self._call(self._scope.fetch_all)

    async def force_trigger(self) -> None:
        """Trigger the armed instrument in software."""
        await self._call(self._scope.force_trigger)

    async def abort(self) -> None:
        """Disarm and stop. Never raises."""
        await self._call(self._scope.abort)

    async def trigger_status(self) -> str | None:
        """The instrument's own trigger state, when it reports one."""
        return await self._call(self._scope.trigger_status)

    async def aclose(self) -> None:
        """Close the driver and shut the worker thread down.

        Waits for the thread: dropping a pool with a live driver call in it
        leaves a thread writing to a handle the process is about to reuse.
        """
        try:
            await self._call(self._scope.close)
        finally:
            self._pool.shutdown(wait=True)

    def __repr__(self) -> str:
        return f"AsyncScope(label={self._label!r}, scope={self._scope!r})"
