"""Server-side state: the instruments, the current shot, and the view.

One :class:`StudioSession` owns the hardware for the whole process. Every
mutating operation goes through a single lock, so two browser tabs cannot arm
the same scopes at once -- a real hazard, since a driver is one stateful
connection with no thread safety of its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import Inventory, build_specs, save_inventory
from processing import ProcessingStep, apply_pipeline, pipeline_to_metadata

from cetal_scopes.acquisition import (
    AcquisitionEvent,
    MultiScopeAcquisition,
    ShotResult,
)
from cetal_scopes.analysis import align_shot
from cetal_scopes.analysis.results import TimeOffset
from cetal_scopes.channel import Channel
from cetal_scopes.shot import Shot
from cetal_scopes.storage import load_shot, save_shot

__all__ = ["SessionStatus", "StudioSession", "summarize_shot"]

_MAX_LOG = 500


def _eng(value: float | None, unit: str, digits: int = 4) -> str:
    """Format a quantity with an SI prefix, e.g. ``2.000 GS/s``."""
    if value is None:
        return "?"
    magnitude = abs(value)
    for factor, prefix in (
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "u"),
        (1e-9, "n"),
        (1e-12, "p"),
    ):
        if magnitude >= factor:
            return f"{value / factor:.{digits}g} {prefix}{unit}"
    return f"{value:.{digits}g} {unit}"


def _differs(actual: float | None, asked: float | None, tolerance: float) -> bool:
    """Whether a readback misses its request by more than ``tolerance`` (relative).

    Sample rate is not directly settable on every instrument and a trigger
    level is silently clamped, so a mismatch is not an error -- but it must be
    visible rather than silent, which is the whole point of printing both.
    """
    if actual is None or asked is None:
        return False
    if asked == 0.0:
        return actual != 0.0
    return abs(actual - asked) > tolerance * abs(asked)


def _asked_vs_got(actual: float | None, asked: float | None, unit: str) -> str:
    """``got`` alone, or ``got (asked X)`` when the instrument did something else."""
    got = _eng(actual, unit)
    if _differs(actual, asked, 0.0025):
        return f"{got} (asked {_eng(asked, unit)})"
    return got


def _trigger_line(metadata: Mapping[str, Any]) -> str | None:
    """One line describing the trigger, or ``None`` if the driver records none.

    Both hardware drivers store ``trigger_source``/``trigger_level_v``; the
    Siglent additionally stores the *requested* level, because it clamps to
    roughly +/-4.5 * V/div of the source channel without saying so.
    """
    source = metadata.get("trigger_source")
    if source is None:
        return None
    parts = [f"trigger {source}"]
    clamped = False
    level = metadata.get("trigger_level_v")
    if level is not None:
        asked = metadata.get("trigger_level_requested_v")
        clamped = _differs(float(level), asked, 0.0025)
        parts.append(f"at {_asked_vs_got(float(level), asked, 'V')}")
    slope = metadata.get("trigger_slope")
    if slope is not None:
        parts.append(str(slope))
    mode = metadata.get("trigger_mode")
    if mode is not None:
        parts.append(f"[{mode}]")
    if clamped:
        parts.append("<-- clamped by the channel range")
    return " ".join(parts)


def summarize_shot(shot: Shot, *, verbose: bool = False) -> list[str]:
    """Describe what each instrument in ``shot`` actually did.

    Everything here comes from the capture metadata the drivers record at
    fetch time, so a shot reloaded from disk describes itself the same way a
    fresh one does.
    """
    lines: list[str] = []
    for label, capture in shot.captures.items():
        metadata = capture.metadata
        rate = 1.0 / capture.dt if capture.dt > 0 else None
        window = capture.n_samples * capture.dt
        offset = shot.time_offset(label)

        lines.append(f"{label}: {metadata.get('instrument', 'instrument')}")
        lines.append(
            "  "
            + ", ".join(
                [
                    f"{len(capture.channels)} ch ({', '.join(capture.channels)})",
                    _asked_vs_got(
                        rate, metadata.get("sample_rate_requested_hz"), "S/s"
                    ),
                    f"{capture.n_samples} pts"
                    + (
                        f" (asked {metadata['record_length_requested']})"
                        if metadata.get("record_length_requested")
                        not in (None, capture.n_samples)
                        else ""
                    ),
                    f"window {_eng(window, 's')}",
                ]
            )
        )
        detail = [f"t0 {_eng(capture.t0, 's')}", f"offset {_eng(offset, 's')}"]
        if metadata.get("timebase_s_per_div") is not None:
            detail.append(f"{_eng(metadata['timebase_s_per_div'], 's')}/div")
        if metadata.get("memory_depth") is not None:
            detail.append(f"depth {metadata['memory_depth']}")
        lines.append("  " + ", ".join(detail))

        trigger = _trigger_line(metadata)
        if trigger is not None:
            lines.append(f"  {trigger}")

        if verbose:
            for key in sorted(metadata):
                lines.append(f"    {key} = {metadata[key]!r}")
    return lines


@dataclass
class SessionStatus:
    """What the UI needs to render the toolbar."""

    connected: bool = False
    busy: bool = False
    shot_id: str | None = None
    complete: bool | None = None
    arm_spread_s: float | None = None
    failures: list[str] = field(default_factory=list)
    message: str = "idle"


class StudioSession:
    """Owns the instruments, the most recent shot, and the processing state.

    Parameters
    ----------
    inventory : Inventory
        The instruments to drive.
    config_path : Path, optional
        Where :meth:`save_inventory_file` writes. ``None`` means the inventory
        came from nowhere on disk and cannot be saved without a path.
    echo : bool, default True
        Also write the session log to stdout, so a bench run leaves a record
        in the terminal and not only in a browser tab that may be closed.
    verbose : bool, default False
        Include every metadata key of every capture in the shot summary.
    """

    def __init__(
        self,
        inventory: Inventory,
        *,
        config_path: Path | None = None,
        echo: bool = True,
        verbose: bool = False,
    ) -> None:
        self._inventory = inventory
        self._config_path = config_path
        self._echo = echo
        self._verbose = verbose
        self._lock = asyncio.Lock()
        self._run: MultiScopeAcquisition | None = None
        self._result: ShotResult | None = None
        self._shot: Shot | None = None
        self._offsets_fit: dict[str, TimeOffset] = {}
        self._pipeline: list[ProcessingStep] = []
        self._processed: tuple[Any, dict[str, Channel], list[str]] | None = None
        self._log: list[dict[str, Any]] = []
        self._events: list[AcquisitionEvent] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._status = SessionStatus()

    # -- inventory ---------------------------------------------------------

    @property
    def inventory(self) -> Inventory:
        """The current inventory."""
        return self._inventory

    @property
    def config_path(self) -> Path | None:
        """Where the inventory is saved, when it has a home on disk."""
        return self._config_path

    async def set_inventory(self, inventory: Inventory) -> None:
        """Replace the inventory. Disconnects first, since the instruments change.

        Raises
        ------
        RuntimeError
            If a shot is in progress.
        """
        async with self._lock:
            if self._status.busy:
                raise RuntimeError("cannot change the inventory during a shot")
            await self._disconnect()
            self._inventory = inventory
            self._log_line("inventory replaced")

    def save_inventory_file(self, path: Path | None = None) -> Path:
        """Write the inventory back to TOML.

        Raises
        ------
        ValueError
            If no path is known and none is given.
        """
        target = path or self._config_path
        if target is None:
            raise ValueError("no inventory path is configured; give one explicitly")
        saved = save_inventory(self._inventory, target)
        self._config_path = saved
        self._log_line(f"inventory saved to {saved}")
        return saved

    # -- connection --------------------------------------------------------

    @property
    def status(self) -> SessionStatus:
        """A snapshot of the session state."""
        return self._status

    @property
    def connected(self) -> bool:
        """Whether the instruments are open."""
        return self._run is not None

    async def connect(self) -> None:
        """Open and configure every enabled instrument.

        Raises
        ------
        ValueError
            If no instrument is enabled.
        """
        async with self._lock:
            if self._run is not None:
                return
            specs = build_specs(self._inventory)
            run = MultiScopeAcquisition(
                specs,
                poll_interval=self._inventory.poll_interval,
                default_timeout=self._inventory.default_timeout,
                arm_timeout=self._inventory.arm_timeout,
                on_event=self._on_event,
            )
            try:
                await run.open()
            except BaseException:
                await run.aclose()
                raise
            self._run = run
            self._status.connected = True
            self._status.message = f"connected: {', '.join(run.labels)}"
            self._log_line(self._status.message)

    async def disconnect(self) -> None:
        """Close every instrument."""
        async with self._lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        if self._run is None:
            return
        try:
            await self._run.aclose()
        finally:
            self._run = None
            self._status.connected = False
            self._status.message = "disconnected"
            self._log_line("disconnected")

    # -- shots -------------------------------------------------------------

    async def capture(
        self, *, direct_trigger: bool | None = None, timeout: float | None = None
    ) -> ShotResult:
        """Run one shot, connecting first if needed.

        Raises
        ------
        RuntimeError
            If a shot is already in progress.
        """
        if self._status.busy:
            raise RuntimeError("a shot is already in progress")
        if self._run is None:
            await self.connect()
        run = self._run
        if run is None:  # pragma: no cover - connect() raises instead
            raise RuntimeError("not connected")

        self._status.busy = True
        self._status.message = "capturing"
        try:
            result = await run.run_shot(direct_trigger=direct_trigger, timeout=timeout)
        finally:
            self._status.busy = False

        self._result = result
        self._shot = result.shot
        self._offsets_fit = {}
        self._processed = None
        self._status.shot_id = result.shot_id
        self._status.complete = result.complete
        self._status.arm_spread_s = result.arm_spread_s
        self._status.failures = [item.label for item in result.failures]
        self._status.message = f"shot {result.shot_id}: " + (
            "complete" if result.complete else "incomplete"
        )
        self._log_line(self._status.message)
        for item in result.failures:
            self._log_line(f"  {item.label}: {item.state} {item.error or ''}".rstrip())
        if result.shot is not None:
            if result.arm_spread_s is not None:
                self._log_line(f"  arm spread {_eng(result.arm_spread_s, 's')}")
            for line in summarize_shot(result.shot, verbose=self._verbose):
                self._log_line(line)
        return result

    async def abort(self) -> None:
        """Ask an in-flight shot to stop at the next wait slice."""
        if self._run is not None:
            await self._run.abort(reason="user")
            self._log_line("abort requested")

    async def force_trigger(self, labels: Sequence[str] | None = None) -> None:
        """Force the trigger on armed instruments, for debugging."""
        if self._run is None:
            raise RuntimeError("not connected")
        await self._run.force_trigger(labels)
        self._log_line(f"forced trigger on {list(labels) if labels else 'all'}")

    @property
    def shot(self) -> Shot | None:
        """The most recent shot, when there is one."""
        return self._shot

    @property
    def result(self) -> ShotResult | None:
        """The most recent shot's full diagnostics."""
        return self._result

    def require_shot(self) -> Shot:
        """Return the current shot.

        Raises
        ------
        RuntimeError
            If no shot has been captured or loaded.
        """
        if self._shot is None:
            raise RuntimeError("no shot yet; capture or load one first")
        return self._shot

    # -- alignment ---------------------------------------------------------

    def set_offsets(self, offsets: Mapping[str, float]) -> None:
        """Set per-capture time offsets by hand.

        Raises
        ------
        KeyError
            If a label is not in the shot.
        """
        shot = self.require_shot()
        for label, value in offsets.items():
            shot.set_offset(label, float(value))
        self._offsets_fit = {}
        self._log_line(f"offsets set by hand: {dict(offsets)}")

    def autofit(
        self,
        *,
        reference: str | None = None,
        channels: Mapping[str, str] | str | None = None,
        max_lag: float | None = None,
    ) -> dict[str, TimeOffset]:
        """Fit every capture's offset by cross-correlation.

        Raises
        ------
        RuntimeError
            If there is no shot.
        ValueError
            If the shot has fewer than two captures.
        """
        shot = self.require_shot()
        fitted = align_shot(
            shot, reference=reference, channels=channels, max_lag=max_lag
        )
        self._offsets_fit = fitted
        for label, offset in fitted.items():
            self._log_line(
                f"autofit {label}: {offset.offset * 1e9:+.3f} ns "
                f"(r={offset.correlation:.3f}"
                + (", inverted" if offset.inverted else "")
                + ")"
            )
        return fitted

    @property
    def fitted_offsets(self) -> dict[str, TimeOffset]:
        """The most recent auto-fit, so the UI can show how good it was."""
        return self._offsets_fit

    # -- processing --------------------------------------------------------

    @property
    def pipeline(self) -> list[ProcessingStep]:
        """The current processing pipeline."""
        return list(self._pipeline)

    def set_pipeline(self, steps: Sequence[ProcessingStep]) -> None:
        """Replace the processing pipeline."""
        self._pipeline = list(steps)
        self._processed = None
        names = [step.name for step in self._pipeline if step.enabled]
        self._log_line(f"pipeline: {names or 'none'}")

    def processed_channels(self) -> tuple[dict[str, Channel], list[str]]:
        """Apply the pipeline to every channel of the current shot.

        Returns
        -------
        channels : dict of str to Channel
            Keyed ``"label:channel"``, on the aligned time axis.
        warnings : list of str
            Steps that could not run on a particular channel. A pipeline
            covers a whole shot, so a step meaningful for only some channels
            is normal, not an error.
        """
        shot = self.require_shot()
        aligned = shot.aligned_channels()
        if not self._pipeline:
            return aligned, []

        # One screen refresh asks for the traces, the spectrum, a two-channel
        # panel and the measurements, and every one of them needs the same
        # processed channels. Filtering a multi-megasample record four times
        # over is most of the wait between a pipeline edit and the plots
        # catching up, so the result is kept until something it depends on
        # changes.
        token = self._processed_token(shot)
        if self._processed is not None and self._processed[0] == token:
            return self._processed[1], self._processed[2]

        processed: dict[str, Channel] = {}
        warnings: list[str] = []
        for key, channel in aligned.items():
            result, notes = apply_pipeline(channel, self._pipeline)
            processed[key] = result
            warnings.extend(notes)
        self._processed = (token, processed, warnings)
        return processed, warnings

    def _processed_token(self, shot: Shot) -> Any:
        """Everything the processed channels depend on, as a comparable key."""
        return (
            id(shot),
            tuple(sorted(shot.offsets.items())),
            tuple(step.model_dump_json() for step in self._pipeline),
        )

    # -- persistence -------------------------------------------------------

    def save_shot_to(self, path: str | Path) -> Path:
        """Save the current shot, stamping it with the pipeline that made it.

        Storing the pipeline is what makes the saved shot reproducible: the
        recorded samples plus the exact steps applied to them.
        """
        shot = self.require_shot()
        shot.metadata["processing"] = pipeline_to_metadata(self._pipeline)
        shot.metadata["saved_at"] = datetime.now(UTC).isoformat()
        index = save_shot(shot, path)
        self._log_line(f"shot saved to {index.parent}")
        return index

    def load_shot_from(self, path: str | Path) -> Shot:
        """Load a saved shot and make it the current one."""
        shot = load_shot(path)
        self._shot = shot
        self._result = None
        self._offsets_fit = {}
        self._processed = None
        self._status.shot_id = str(shot.metadata.get("shot_id") or Path(path).name)
        self._status.complete = shot.metadata.get("complete")
        self._status.arm_spread_s = shot.metadata.get("arm_spread_s")
        self._status.failures = []
        self._status.message = f"loaded {self._status.shot_id}"
        self._log_line(self._status.message)
        return shot

    # -- events and log ----------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a WebSocket subscriber and return its queue."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Drop a subscriber."""
        self._subscribers.discard(queue)

    @property
    def log(self) -> list[dict[str, Any]]:
        """The session log, oldest first."""
        return list(self._log)

    def clear_log(self) -> None:
        """Empty the session log."""
        self._log.clear()

    def _log_line(self, message: str) -> None:
        if self._echo:
            print(message, flush=True)
        entry = {"t": datetime.now(UTC).isoformat(), "message": message}
        self._log.append(entry)
        del self._log[:-_MAX_LOG]
        self._publish({"type": "log", **entry})

    def _on_event(self, event: AcquisitionEvent) -> None:
        self._events.append(event)
        del self._events[:-_MAX_LOG]
        self._publish(encode_event(event))

    def _publish(self, payload: dict[str, Any]) -> None:
        for queue in self._subscribers:
            if queue.full():
                # Status is not data: a browser that has fallen behind loses
                # old progress lines rather than stalling the acquisition.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - full implies non-empty
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover - just made room
                pass


def encode_event(event: AcquisitionEvent) -> dict[str, Any]:
    """Render an acquisition event as the JSON the browser receives.

    Waveform arrays never travel on the status socket: the ``result`` event
    carries only shapes and labels, and the browser fetches figures over HTTP.
    """
    payload: dict[str, Any] = {
        "type": event.kind,
        "shot": event.shot_id,
        "t": round(event.t, 4),
    }
    if event.phase is not None:
        payload["phase"] = event.phase
    if event.label is not None:
        payload["label"] = event.label
    if event.state is not None:
        payload["state"] = event.state
    if event.message is not None:
        payload["message"] = event.message
    if event.detail:
        payload["detail"] = dict(event.detail)
    return payload
