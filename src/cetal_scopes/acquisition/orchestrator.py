"""Arm several instruments together and capture one synchronized shot.

The problem this solves: a driver's :meth:`~cetal_scopes.scopes.base.Scope.acquire`
arms, waits and fetches in one blocking call, so running N of them concurrently
races -- an instrument still writing registers misses a trigger a faster one
already caught. :class:`MultiScopeAcquisition` uses the staged lifecycle instead,
holding a barrier until every instrument has armed before any trigger is
solicited.

What the barrier does and does not promise: *no trigger is solicited and no
instrument is waited on until every instrument has returned from ``arm()``.*
That is all that is achievable in software; it is not clock synchronization.
The residual is measured rather than hidden -- :attr:`ShotResult.arm_spread_s`
is the spread between the first and last instrument to finish arming, and it
bounds how much of a shot's jitter comes from the arming itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Self

from cetal_scopes.acquisition.events import (
    AcquisitionEvent,
    EventSink,
    InstrumentState,
    Phase,
)
from cetal_scopes.acquisition.instrument import AsyncScope, InstrumentSpec
from cetal_scopes.capture import Capture
from cetal_scopes.shot import Shot

__all__ = ["InstrumentResult", "MultiScopeAcquisition", "ShotResult"]

_WaitOutcome = Literal["ok", "timeout", "aborted", "error"]


@dataclass(frozen=True)
class InstrumentResult:
    """What one instrument contributed to a shot.

    Only :attr:`state` ``"ok"`` means the captures came from this shot's
    trigger. The timestamps are on a monotonic clock, relative to the start of
    the shot.
    """

    label: str
    state: InstrumentState
    captures: tuple[Capture, ...] = ()
    error: str | None = None
    armed_at: float | None = None
    triggered_at: float | None = None
    fetched_at: float | None = None
    forced: bool = False
    late_armed: bool = False
    required: bool = True

    @property
    def ok(self) -> bool:
        """Whether this instrument produced data from this shot's trigger."""
        return self.state == "ok"


@dataclass(frozen=True)
class ShotResult:
    """The outcome of one :meth:`MultiScopeAcquisition.run_shot`.

    A shot that partly failed is still returned, with the captures that did
    arrive: on a bench where a shot is expensive, discarding good data because
    one instrument missed is the wrong default.
    """

    shot_id: str
    shot: Shot
    results: tuple[InstrumentResult, ...]
    started_at: datetime
    arm_spread_s: float | None = None
    complete: bool = False
    aborted: bool = False

    @property
    def failures(self) -> tuple[InstrumentResult, ...]:
        """Instruments that did not reach ``"ok"``."""
        return tuple(r for r in self.results if not r.ok)

    def result(self, label: str) -> InstrumentResult:
        """Return the result for ``label``.

        Raises
        ------
        KeyError
            If no instrument in the shot has that label.
        """
        for item in self.results:
            if item.label == label:
                return item
        raise KeyError(label)

    def __repr__(self) -> str:
        return (
            f"ShotResult(shot_id={self.shot_id!r}, complete={self.complete}, "
            f"captures={len(self.shot)}, failures={[r.label for r in self.failures]!r})"
        )


class MultiScopeAcquisition:
    """Drive several instruments through one synchronized shot.

    Parameters
    ----------
    instruments : sequence of InstrumentSpec
        The instruments, in the order they should be armed. Labels must be
        unique.
    poll_interval : float, optional
        How finely the trigger wait is sliced, in seconds. This bounds how
        quickly :meth:`abort` and a mid-wait :meth:`force_trigger` take effect.
    default_timeout : float, optional
        Seconds to wait for a trigger when a spec does not set its own.
    arm_timeout : float, optional
        Seconds allowed for the whole arming phase.
    on_event : callable, optional
        Called with each :class:`~cetal_scopes.acquisition.events.AcquisitionEvent`.

    Examples
    --------
    >>> async with MultiScopeAcquisition(specs) as run:  # doctest: +SKIP
    ...     result = await run.run_shot(direct_trigger=True)
    ...     print(result.complete, list(result.shot))
    """

    def __init__(
        self,
        instruments: Sequence[InstrumentSpec],
        *,
        poll_interval: float = 0.2,
        default_timeout: float = 10.0,
        arm_timeout: float = 5.0,
        on_event: EventSink | None = None,
    ) -> None:
        if not instruments:
            raise ValueError("at least one instrument is required")
        labels = [spec.label for spec in instruments]
        duplicates = {label for label in labels if labels.count(label) > 1}
        if duplicates:
            raise ValueError(
                f"instrument labels must be unique: {sorted(duplicates)!r}"
            )
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be positive, got {poll_interval!r}")

        self._specs = tuple(instruments)
        self._scopes = {
            spec.label: AsyncScope(spec.label, spec.scope) for spec in self._specs
        }
        self._poll_interval = poll_interval
        self._default_timeout = default_timeout
        self._arm_timeout = arm_timeout
        self._on_event = on_event

        self._shot_lock = asyncio.Lock()
        self._abort = asyncio.Event()
        self._subscribers: list[asyncio.Queue[AcquisitionEvent]] = []
        self._shot_id = ""
        self._t0 = 0.0
        self._opened = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def labels(self) -> tuple[str, ...]:
        """Instrument labels, in arming order."""
        return tuple(spec.label for spec in self._specs)

    def spec(self, label: str) -> InstrumentSpec:
        """Return the spec for ``label``.

        Raises
        ------
        KeyError
            If no instrument has that label.
        """
        for spec in self._specs:
            if spec.label == label:
                return spec
        raise KeyError(label)

    async def open(self) -> None:
        """Connect and configure every instrument, concurrently.

        A failure is re-raised naming the instrument that caused it. Without
        the label, a bench of several scopes reports a settings error with no
        way to tell whose setting it was.
        """
        await asyncio.gather(
            *(
                self._labelled(spec.label, self._scopes[spec.label].connect())
                for spec in self._specs
            )
        )
        await asyncio.gather(
            *(
                self._labelled(
                    spec.label, self._scopes[spec.label].configure(spec.settings)
                )
                for spec in self._specs
                if spec.settings
            )
        )
        self._opened = True

    @staticmethod
    async def _labelled(label: str, awaitable: Awaitable[None]) -> None:
        """Await ``awaitable``, prefixing any failure with ``label``."""
        try:
            await awaitable
        except Exception as exc:
            message = f"{label}: {exc}"
            try:
                relabelled: Exception = type(exc)(message)
            except Exception:  # noqa: BLE001 - any exception that will not rebuild
                relabelled = RuntimeError(message)
            raise relabelled from exc

    async def aclose(self) -> None:
        """Abort anything in flight, then close every instrument."""
        self._abort.set()
        results = await asyncio.gather(
            *(scope.aclose() for scope in self._scopes.values()),
            return_exceptions=True,
        )
        self._opened = False
        for item in results:
            if isinstance(item, BaseException):
                raise item

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def reconfigure(self, label: str, settings: Mapping[str, Any]) -> None:
        """Apply new settings to one instrument between shots."""
        await self._scopes[label].configure(settings)

    # -- events ------------------------------------------------------------

    def subscribe(self) -> AsyncIterator[AcquisitionEvent]:
        """Yield events as they are emitted, until the subscriber is dropped.

        The queue drops its oldest event when a consumer falls behind: progress
        is not data, and a slow consumer must never back-pressure an
        acquisition.
        """
        queue: asyncio.Queue[AcquisitionEvent] = asyncio.Queue(maxsize=256)
        self._subscribers.append(queue)

        async def iterator() -> AsyncIterator[AcquisitionEvent]:
            try:
                while True:
                    yield await queue.get()
            finally:
                with suppress(ValueError):
                    self._subscribers.remove(queue)

        return iterator()

    def _emit(self, event: AcquisitionEvent) -> None:
        if self._on_event is not None:
            # A broken subscriber must not be able to fail a shot.
            with suppress(Exception):
                self._on_event(event)
        for queue in self._subscribers:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def _now(self) -> float:
        return asyncio.get_running_loop().time() - self._t0

    def _phase(self, phase: Phase, **detail: Any) -> None:
        self._emit(
            AcquisitionEvent(
                kind="phase",
                shot_id=self._shot_id,
                t=self._now(),
                phase=phase,
                detail=detail,
            )
        )

    def _instrument(self, label: str, state: InstrumentState, **detail: Any) -> None:
        self._emit(
            AcquisitionEvent(
                kind="instrument",
                shot_id=self._shot_id,
                t=self._now(),
                label=label,
                state=state,
                detail=detail,
            )
        )

    # -- the shot ----------------------------------------------------------

    async def run_shot(
        self,
        *,
        shot_id: str | None = None,
        direct_trigger: bool | None = None,
        timeout: float | None = None,
    ) -> ShotResult:
        """Arm every instrument, take one shot, and assemble the result.

        Never raises for a hardware outcome -- a timeout, a dropped connection
        or an abort all come back inside the :class:`ShotResult`. Exceptions
        are reserved for programmer errors and cancellation.

        Parameters
        ----------
        shot_id : str, optional
            Identifier for this shot. Defaults to a UTC timestamp.
        direct_trigger : bool, optional
            Force every capable instrument's trigger in software once the
            barrier is reached. ``None`` honours each spec's own
            ``direct_trigger``.
        timeout : float, optional
            Override the per-instrument trigger timeout for this shot.

        Returns
        -------
        ShotResult
            The captures that arrived, plus per-instrument diagnostics.

        Raises
        ------
        RuntimeError
            If a shot is already in progress.
        """
        if self._shot_lock.locked():
            raise RuntimeError("a shot is already in progress")
        async with self._shot_lock:
            return await self._run_shot(shot_id, direct_trigger, timeout)

    async def _run_shot(
        self,
        shot_id: str | None,
        direct_trigger: bool | None,
        timeout: float | None,
    ) -> ShotResult:
        loop = asyncio.get_running_loop()
        started_at = datetime.now(UTC)
        self._shot_id = shot_id or started_at.strftime("%Y%m%dT%H%M%S.%fZ")
        self._t0 = loop.time()
        self._abort.clear()

        states: dict[str, InstrumentResult] = {
            spec.label: InstrumentResult(
                label=spec.label,
                state="idle",
                late_armed=not self._scopes[spec.label].supports_staged_acquisition,
                required=spec.required,
            )
            for spec in self._specs
        }

        try:
            arm_spread = await self._arm_all(states)
            if arm_spread is None:
                # Arming failed somewhere: a half-armed set is never worth
                # firing, and firing it would hide the cause downstream.
                await self._abort_all()
                return self._assemble(states, started_at, None, aborted=False)

            await self._trigger_all(states, direct_trigger)
            await self._wait_and_fetch(states, timeout)
        except asyncio.CancelledError:
            await self._abort_all()
            raise
        finally:
            self._abort.clear()

        return self._assemble(
            states,
            started_at,
            arm_spread,
            aborted=any(r.state == "aborted" for r in states.values()),
        )

    async def _arm_all(self, states: dict[str, InstrumentResult]) -> float | None:
        """Arm every instrument. Returns the arm spread, or ``None`` on failure."""
        self._phase("arming")
        armed_at: dict[str, float] = {}

        async def arm_one(spec: InstrumentSpec) -> BaseException | None:
            self._instrument(spec.label, "arming")
            states[spec.label] = replace(states[spec.label], state="arming")
            try:
                await self._scopes[spec.label].arm()
            except BaseException as exc:  # noqa: BLE001 - reported, not raised
                states[spec.label] = replace(
                    states[spec.label], state="error", error=repr(exc)
                )
                self._instrument(spec.label, "error", error=repr(exc))
                return exc
            at = self._now()
            armed_at[spec.label] = at
            states[spec.label] = replace(states[spec.label], state="armed", armed_at=at)
            self._instrument(
                spec.label,
                "armed",
                late_armed=states[spec.label].late_armed,
            )
            return None

        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(*(arm_one(spec) for spec in self._specs)),
                timeout=self._arm_timeout,
            )
        except TimeoutError:
            for label, item in states.items():
                if item.state != "armed":
                    states[label] = replace(
                        item, state="timeout", error="arming timed out"
                    )
                    self._instrument(label, "timeout", error="arming timed out")
            return None

        if any(outcome is not None for outcome in outcomes):
            return None

        spread = max(armed_at.values()) - min(armed_at.values()) if armed_at else 0.0
        self._phase("armed", arm_spread_s=spread)
        return spread

    async def _trigger_all(
        self, states: dict[str, InstrumentResult], direct_trigger: bool | None
    ) -> None:
        """Force triggers, but only now -- after every instrument is armed."""
        targets = [
            spec
            for spec in self._specs
            if (spec.direct_trigger if direct_trigger is None else direct_trigger)
            and self._scopes[spec.label].supports_force_trigger
        ]
        if not targets:
            return
        self._phase("triggering", labels=[spec.label for spec in targets])
        for spec in targets:
            try:
                await self._scopes[spec.label].force_trigger()
            except BaseException as exc:  # noqa: BLE001 - reported, not raised
                states[spec.label] = replace(
                    states[spec.label], state="error", error=repr(exc)
                )
                self._instrument(spec.label, "error", error=repr(exc))
                continue
            states[spec.label] = replace(states[spec.label], forced=True)

    async def _wait_and_fetch(
        self, states: dict[str, InstrumentResult], timeout: float | None
    ) -> None:
        self._phase("waiting")
        loop = asyncio.get_running_loop()
        # Every deadline is measured from this one instant -- the barrier --
        # so an instrument that armed slowly does not also get a later
        # timeout. Instruments may still differ in how long they wait, when
        # their specs say so.
        barrier = loop.time()

        async def one(spec: InstrumentSpec) -> None:
            if states[spec.label].state != "armed":
                return  # never armed; already reported
            limit = (
                timeout
                if timeout is not None
                else (
                    spec.timeout if spec.timeout is not None else self._default_timeout
                )
            )
            own_deadline = barrier + limit
            states[spec.label] = replace(states[spec.label], state="waiting")
            self._instrument(spec.label, "waiting", timeout_s=limit)

            outcome, error = await self._wait_sliced(spec.label, own_deadline)
            if outcome == "ok":
                states[spec.label] = replace(
                    states[spec.label], state="triggered", triggered_at=self._now()
                )
                self._instrument(spec.label, "triggered")
                await self._fetch_one(spec, states)
                return

            if outcome == "error":
                states[spec.label] = replace(
                    states[spec.label], state="error", error=error
                )
                self._instrument(spec.label, "error", error=error)
                await self._scopes[spec.label].abort()
                return

            state: InstrumentState = "aborted" if outcome == "aborted" else "timeout"
            if state == "timeout" and spec.fetch_on_timeout:
                await self._fetch_one(spec, states, untriggered=True)
                return
            states[spec.label] = replace(
                states[spec.label],
                state=state,
                error=None if state == "aborted" else f"no trigger within {limit:g}s",
            )
            self._instrument(spec.label, state, timeout_s=limit)
            await self._scopes[spec.label].abort()

        await asyncio.gather(*(one(spec) for spec in self._specs))

    async def _wait_sliced(
        self, label: str, deadline: float
    ) -> tuple[_WaitOutcome, str | None]:
        """Poll the instrument in short slices so abort stays responsive."""
        loop = asyncio.get_running_loop()
        scope = self._scopes[label]
        while True:
            if self._abort.is_set():
                return "aborted", None
            remaining = deadline - loop.time()
            if remaining <= 0:
                return "timeout", None
            try:
                if await scope.wait(min(self._poll_interval, remaining)):
                    return "ok", None
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - reported, not raised
                return "error", repr(exc)

    async def _fetch_one(
        self,
        spec: InstrumentSpec,
        states: dict[str, InstrumentResult],
        *,
        untriggered: bool = False,
    ) -> None:
        label = spec.label
        states[label] = replace(states[label], state="fetching")
        self._instrument(label, "fetching")
        try:
            captures = await self._scopes[label].fetch_all()
        except BaseException as exc:  # noqa: BLE001 - reported, not raised
            states[label] = replace(states[label], state="error", error=repr(exc))
            self._instrument(label, "error", error=repr(exc))
            await self._scopes[label].abort()
            return

        if untriggered:
            # Stamped and still reported as a timeout, so this can never be
            # mistaken for data from this shot's trigger.
            for capture in captures:
                capture.metadata["untriggered"] = True
            states[label] = replace(
                states[label],
                state="timeout",
                captures=tuple(captures),
                fetched_at=self._now(),
                error="no trigger; buffer read anyway (fetch_on_timeout)",
            )
            self._instrument(label, "timeout", untriggered=True)
            return

        states[label] = replace(
            states[label],
            state="ok",
            captures=tuple(captures),
            fetched_at=self._now(),
        )
        self._instrument(
            label,
            "ok",
            n_captures=len(captures),
            n_samples=captures[0].n_samples if captures else 0,
        )

    async def _abort_all(self) -> None:
        self._abort.set()
        await asyncio.gather(
            *(scope.abort() for scope in self._scopes.values()),
            return_exceptions=True,
        )

    async def abort(self, *, reason: str = "user") -> None:
        """Stop an in-flight shot at the next wait slice.

        Instruments that already completed keep their captures, so aborting
        during the fetch phase still yields whatever landed.
        """
        self._emit(
            AcquisitionEvent(
                kind="log",
                shot_id=self._shot_id,
                t=self._now() if self._t0 else 0.0,
                message=f"abort requested ({reason})",
            )
        )
        self._abort.set()

    async def force_trigger(self, labels: Sequence[str] | None = None) -> None:
        """Force the trigger on the named instruments, or on all capable ones."""
        targets = list(labels) if labels is not None else list(self._scopes)
        await asyncio.gather(
            *(
                self._scopes[label].force_trigger()
                for label in targets
                if self._scopes[label].supports_force_trigger
            ),
            return_exceptions=True,
        )

    # -- assembly ----------------------------------------------------------

    def _assemble(
        self,
        states: dict[str, InstrumentResult],
        started_at: datetime,
        arm_spread: float | None,
        *,
        aborted: bool,
    ) -> ShotResult:
        self._phase("assembling")
        results = tuple(states[spec.label] for spec in self._specs)

        shot = Shot()
        for item in results:
            if not item.ok:
                continue
            if len(item.captures) == 1:
                shot.add(item.label, item.captures[0])
            else:
                # Segmented acquisition: one Shot entry per segment.
                for index, capture in enumerate(item.captures):
                    shot.add(f"{item.label}[{index}]", capture)

        reference = next(
            (item.label for item in results if item.ok and item.required), None
        )
        if reference is not None and reference in shot:
            shot.set_reference(reference)

        complete = all(item.ok for item in results if item.required)
        shot.metadata.update(
            {
                "shot_id": self._shot_id,
                "started_at": started_at.isoformat(),
                "arm_spread_s": arm_spread,
                "complete": complete,
                "aborted": aborted,
                "instruments": [
                    {
                        "label": item.label,
                        "state": item.state,
                        "error": item.error,
                        "armed_at": item.armed_at,
                        "triggered_at": item.triggered_at,
                        "fetched_at": item.fetched_at,
                        "forced": item.forced,
                        "late_armed": item.late_armed,
                        "required": item.required,
                    }
                    for item in results
                ],
            }
        )

        result = ShotResult(
            shot_id=self._shot_id,
            shot=shot,
            results=results,
            started_at=started_at,
            arm_spread_s=arm_spread,
            complete=complete,
            aborted=aborted,
        )
        self._emit(
            AcquisitionEvent(
                kind="result",
                shot_id=self._shot_id,
                t=self._now(),
                detail={
                    "complete": complete,
                    "aborted": aborted,
                    "arm_spread_s": arm_spread,
                    "failures": [item.label for item in result.failures],
                },
            )
        )
        self._phase("done")
        return result

    def __repr__(self) -> str:
        return f"MultiScopeAcquisition(labels={list(self.labels)!r})"
