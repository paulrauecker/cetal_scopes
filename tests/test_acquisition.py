"""Synchronized multi-instrument acquisition.

Every rendezvous in these tests is a :class:`threading.Event`, never a sleep,
so the ordering assertions are deterministic rather than timing-dependent.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pytest

from cetal_scopes import Capture, Scope
from cetal_scopes.acquisition import (
    AcquisitionEvent,
    InstrumentSpec,
    MultiScopeAcquisition,
)


class StagedFakeScope(Scope):
    """A driver that really separates arming from triggering.

    ``journal`` is shared between the instruments of one test, so the order in
    which they arm, wait and fetch is directly observable.
    """

    supports_staged_acquisition = True
    supports_force_trigger = True

    def __init__(
        self,
        label: str,
        journal: list[str],
        *,
        n_samples: int = 8,
        arm_barrier: threading.Event | None = None,
        fail_on_arm: bool = False,
        fail_on_fetch: bool = False,
        n_captures: int = 1,
    ) -> None:
        self.label = label
        self.journal = journal
        self.n_samples = n_samples
        self.n_captures = n_captures
        self.arm_barrier = arm_barrier
        self.fail_on_arm = fail_on_arm
        self.fail_on_fetch = fail_on_fetch
        self.triggered = threading.Event()
        self.settings: dict[str, Any] = {}
        self.connected = False
        self.closed = False
        self.aborts = 0

    def connect(self) -> None:
        self.connected = True

    def configure(self, settings: Mapping[str, Any]) -> None:
        self.settings = dict(settings)

    def close(self) -> None:
        self.closed = True

    def arm(self) -> None:
        if self.arm_barrier is not None:
            self.arm_barrier.wait(timeout=5.0)
        if self.fail_on_arm:
            raise RuntimeError(f"{self.label}: cannot arm")
        self.journal.append(f"arm:{self.label}")
        self.triggered.clear()
        self._armed = True

    def wait(self, timeout: float | None = None) -> bool:
        self.journal.append(f"wait:{self.label}")
        return self.triggered.wait(timeout=timeout)

    def fetch(self) -> Capture:
        return self.fetch_all()[0]

    def fetch_all(self) -> list[Capture]:
        if self.fail_on_fetch:
            raise RuntimeError(f"{self.label}: transfer failed")
        self.journal.append(f"fetch:{self.label}")
        self._armed = False
        return [
            Capture(
                volts=np.full((1, self.n_samples), float(index)),
                t0=0.0,
                dt=1e-9,
                channel_names=("CH1",),
            )
            for index in range(self.n_captures)
        ]

    def abort(self) -> None:
        self.aborts += 1
        self._armed = False
        self.triggered.clear()

    def force_trigger(self) -> None:
        self.journal.append(f"force:{self.label}")
        self.triggered.set()

    def acquire(self) -> Capture:
        return self._acquire_staged()


class UnstagedFakeScope(Scope):
    """A driver with only ``acquire()``, exercising the base-class emulation."""

    def __init__(self, label: str) -> None:
        self.label = label

    def connect(self) -> None: ...

    def configure(self, settings: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...

    def acquire(self) -> Capture:
        return Capture(volts=np.zeros((1, 4)), t0=0.0, dt=1e-9)


def make_specs(
    journal: list[str], labels: Sequence[str] = ("a", "b"), **kwargs: Any
) -> list[InstrumentSpec]:
    return [
        InstrumentSpec(
            label=label,
            scope=StagedFakeScope(label, journal, **kwargs),
            direct_trigger=True,
        )
        for label in labels
    ]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_requires_at_least_one_instrument() -> None:
    with pytest.raises(ValueError, match="at least one instrument"):
        MultiScopeAcquisition([])


def test_rejects_duplicate_labels() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a", "a"))
    with pytest.raises(ValueError, match="labels must be unique"):
        MultiScopeAcquisition(specs)


def test_spec_rejects_an_empty_label() -> None:
    with pytest.raises(ValueError, match="label must be non-empty"):
        InstrumentSpec(label="", scope=StagedFakeScope("x", []))


def test_labels_are_reported_in_arming_order() -> None:
    journal: list[str] = []
    run_ = MultiScopeAcquisition(make_specs(journal, labels=("a", "b", "c")))
    assert run_.labels == ("a", "b", "c")


# ---------------------------------------------------------------------------
# The arm barrier
# ---------------------------------------------------------------------------


def test_no_instrument_is_waited_on_before_every_one_has_armed() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a", "b", "c"))

    async def main() -> None:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            await run_.run_shot()

    run(main())

    arms = [i for i, entry in enumerate(journal) if entry.startswith("arm:")]
    waits = [i for i, entry in enumerate(journal) if entry.startswith("wait:")]
    assert len(arms) == 3
    assert min(waits) > max(arms)


def test_no_trigger_is_forced_before_every_instrument_has_armed() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a", "b", "c"))

    async def main() -> None:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            await run_.run_shot(direct_trigger=True)

    run(main())

    arms = [i for i, entry in enumerate(journal) if entry.startswith("arm:")]
    forces = [i for i, entry in enumerate(journal) if entry.startswith("force:")]
    assert len(forces) == 3
    assert min(forces) > max(arms)


def test_arm_spread_is_measured_and_reported() -> None:
    journal: list[str] = []
    specs = make_specs(journal)

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())
    assert result.arm_spread_s is not None
    assert result.arm_spread_s >= 0.0
    assert result.shot.metadata["arm_spread_s"] == result.arm_spread_s


def test_an_arm_failure_aborts_the_armed_peers_and_fires_nothing() -> None:
    journal: list[str] = []
    good = StagedFakeScope("good", journal)
    bad = StagedFakeScope("bad", journal, fail_on_arm=True)
    specs = [
        InstrumentSpec(label="good", scope=good, direct_trigger=True),
        InstrumentSpec(label="bad", scope=bad, direct_trigger=True),
    ]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())

    assert not result.complete
    assert not any(entry.startswith("force:") for entry in journal)
    assert not any(entry.startswith("wait:") for entry in journal)
    assert good.aborts >= 1
    assert result.result("bad").state == "error"
    assert "cannot arm" in (result.result("bad").error or "")


# ---------------------------------------------------------------------------
# A complete shot
# ---------------------------------------------------------------------------


def test_a_complete_shot_collects_every_capture() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("siglent", "m5i"))

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot(direct_trigger=True)

    result = run(main())

    assert result.complete
    assert not result.failures
    assert sorted(result.shot) == ["m5i", "siglent"]
    assert result.shot.reference == "siglent"  # first ok, required instrument
    assert all(item.state == "ok" for item in result.results)


def test_shot_metadata_records_the_diagnostics() -> None:
    journal: list[str] = []
    specs = make_specs(journal)

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot(shot_id="shot-7", direct_trigger=True)

    result = run(main())
    metadata = result.shot.metadata

    assert metadata["shot_id"] == "shot-7"
    assert metadata["complete"] is True
    assert [item["label"] for item in metadata["instruments"]] == ["a", "b"]
    assert all(item["forced"] for item in metadata["instruments"])


def test_settings_are_applied_on_open() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("a", journal)
    specs = [
        InstrumentSpec(label="a", scope=scope, settings={"record_length": 64}),
    ]

    async def main() -> None:
        async with MultiScopeAcquisition(specs):
            pass

    run(main())
    assert scope.settings == {"record_length": 64}


def test_segmented_instruments_get_one_shot_entry_per_segment() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("m5i",), n_captures=3)

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot(direct_trigger=True)

    result = run(main())
    assert sorted(result.shot) == ["m5i[0]", "m5i[1]", "m5i[2]"]


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


def test_one_timeout_still_returns_the_other_captures() -> None:
    journal: list[str] = []
    fast = StagedFakeScope("fast", journal)
    slow = StagedFakeScope("slow", journal)
    specs = [
        InstrumentSpec(label="fast", scope=fast, direct_trigger=True),
        InstrumentSpec(label="slow", scope=slow, timeout=0.05),
    ]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())

    assert not result.complete
    assert list(result.shot) == ["fast"]
    assert result.result("fast").state == "ok"
    assert result.result("slow").state == "timeout"
    assert [item.label for item in result.failures] == ["slow"]


def test_a_timed_out_instrument_contributes_no_capture_by_default() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("only", journal)
    specs = [InstrumentSpec(label="only", scope=scope, timeout=0.05)]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())

    # An untriggered scope still holds the *previous* shot's waveform, so it
    # must not silently land in the shot.
    assert result.result("only").captures == ()
    assert len(result.shot) == 0
    assert "fetch:only" not in journal
    assert scope.aborts >= 1


def test_fetch_on_timeout_marks_the_capture_untriggered() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("only", journal)
    specs = [
        InstrumentSpec(label="only", scope=scope, timeout=0.05, fetch_on_timeout=True)
    ]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())
    item = result.result("only")

    assert item.state == "timeout"  # never "ok"
    assert len(item.captures) == 1
    assert item.captures[0].metadata["untriggered"] is True
    assert len(result.shot) == 0  # still kept out of the shot


def test_an_optional_instrument_failing_leaves_the_shot_complete() -> None:
    journal: list[str] = []
    good = StagedFakeScope("good", journal)
    spare = StagedFakeScope("spare", journal)
    specs = [
        InstrumentSpec(label="good", scope=good, direct_trigger=True),
        InstrumentSpec(label="spare", scope=spare, timeout=0.05, required=False),
    ]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())

    assert result.complete
    assert result.result("spare").state == "timeout"


def test_a_fetch_error_fails_only_its_own_instrument() -> None:
    journal: list[str] = []
    good = StagedFakeScope("good", journal)
    broken = StagedFakeScope("broken", journal, fail_on_fetch=True)
    specs = [
        InstrumentSpec(label="good", scope=good, direct_trigger=True),
        InstrumentSpec(label="broken", scope=broken, direct_trigger=True),
    ]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())

    assert result.result("good").state == "ok"
    assert result.result("broken").state == "error"
    assert "transfer failed" in (result.result("broken").error or "")
    assert list(result.shot) == ["good"]


def test_result_lookup_rejects_an_unknown_label() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a",))

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot(direct_trigger=True)

    result = run(main())
    with pytest.raises(KeyError):
        result.result("nope")


# ---------------------------------------------------------------------------
# Abort and cancellation
# ---------------------------------------------------------------------------


def test_abort_during_the_wait_returns_a_partial_shot() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("waiting", journal)
    specs = [InstrumentSpec(label="waiting", scope=scope, timeout=5.0)]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            task = asyncio.create_task(run_.run_shot())
            await asyncio.sleep(0.05)
            await run_.abort(reason="test")
            return await task

    result = run(main())

    assert result.aborted
    assert not result.complete
    assert result.result("waiting").state == "aborted"
    assert scope.aborts >= 1


def test_cancelling_the_shot_task_aborts_the_instruments() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("waiting", journal)
    specs = [InstrumentSpec(label="waiting", scope=scope, timeout=5.0)]

    async def main() -> None:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            task = asyncio.create_task(run_.run_shot())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    run(main())
    assert scope.aborts >= 1


def test_a_second_concurrent_shot_is_refused() -> None:
    journal: list[str] = []
    scope = StagedFakeScope("waiting", journal)
    specs = [InstrumentSpec(label="waiting", scope=scope, timeout=5.0)]

    async def main() -> None:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            task = asyncio.create_task(run_.run_shot())
            await asyncio.sleep(0.05)
            with pytest.raises(RuntimeError, match="already in progress"):
                await run_.run_shot()
            await run_.abort()
            await task

    run(main())


def test_closing_closes_every_instrument() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a", "b"))

    async def main() -> None:
        async with MultiScopeAcquisition(specs):
            pass

    run(main())
    assert all(spec.scope.closed for spec in specs)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Instruments that cannot really be staged
# ---------------------------------------------------------------------------


def test_an_unstaged_instrument_is_flagged_late_armed() -> None:
    specs = [InstrumentSpec(label="legacy", scope=UnstagedFakeScope("legacy"))]

    async def main() -> Any:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            return await run_.run_shot()

    result = run(main())
    item = result.result("legacy")

    assert item.state == "ok"
    assert item.late_armed is True  # its acquisition only starts in wait()
    assert result.shot.metadata["instruments"][0]["late_armed"] is True


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_events_report_the_phases_in_order() -> None:
    journal: list[str] = []
    specs = make_specs(journal)
    events: list[AcquisitionEvent] = []

    async def main() -> None:
        async with MultiScopeAcquisition(
            specs, poll_interval=0.01, on_event=events.append
        ) as run_:
            await run_.run_shot(shot_id="s1", direct_trigger=True)

    run(main())

    phases = [event.phase for event in events if event.kind == "phase"]
    assert phases == [
        "arming",
        "armed",
        "triggering",
        "waiting",
        "assembling",
        "done",
    ]
    assert all(event.shot_id == "s1" for event in events)


def test_instrument_events_walk_through_the_states() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a",))
    events: list[AcquisitionEvent] = []

    async def main() -> None:
        async with MultiScopeAcquisition(
            specs, poll_interval=0.01, on_event=events.append
        ) as run_:
            await run_.run_shot(direct_trigger=True)

    run(main())

    states = [event.state for event in events if event.kind == "instrument"]
    assert states == ["arming", "armed", "waiting", "triggered", "fetching", "ok"]


def test_the_result_event_names_the_failures() -> None:
    journal: list[str] = []
    good = StagedFakeScope("good", journal)
    slow = StagedFakeScope("slow", journal)
    specs = [
        InstrumentSpec(label="good", scope=good, direct_trigger=True),
        InstrumentSpec(label="slow", scope=slow, timeout=0.05),
    ]
    events: list[AcquisitionEvent] = []

    async def main() -> None:
        async with MultiScopeAcquisition(
            specs, poll_interval=0.01, on_event=events.append
        ) as run_:
            await run_.run_shot()

    run(main())

    result_events = [event for event in events if event.kind == "result"]
    assert len(result_events) == 1
    assert result_events[0].detail["failures"] == ["slow"]
    assert result_events[0].detail["complete"] is False


def test_a_broken_event_sink_cannot_fail_a_shot() -> None:
    journal: list[str] = []
    specs = make_specs(journal)

    def explode(event: AcquisitionEvent) -> None:
        raise RuntimeError("subscriber is broken")

    async def main() -> Any:
        async with MultiScopeAcquisition(
            specs, poll_interval=0.01, on_event=explode
        ) as run_:
            return await run_.run_shot(direct_trigger=True)

    assert run(main()).complete


def test_subscribe_streams_events() -> None:
    journal: list[str] = []
    specs = make_specs(journal, labels=("a",))

    async def main() -> list[AcquisitionEvent]:
        async with MultiScopeAcquisition(specs, poll_interval=0.01) as run_:
            stream = run_.subscribe()
            collected: list[AcquisitionEvent] = []

            async def drain() -> None:
                async for event in stream:
                    collected.append(event)
                    if event.kind == "result":
                        return

            task = asyncio.create_task(drain())
            await run_.run_shot(direct_trigger=True)
            await asyncio.wait_for(task, timeout=2.0)
            return collected

    events = run(main())
    assert events[0].phase == "arming"
    assert events[-1].kind == "result"
