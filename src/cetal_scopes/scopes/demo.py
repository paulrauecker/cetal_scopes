"""A synthetic :class:`~cetal_scopes.scopes.base.Scope` for running without hardware.

:class:`DemoScope` implements the full staged acquisition lifecycle -- it arms,
waits for a simulated trigger that arrives after a configurable delay, and can
be forced -- so multi-instrument orchestration, timeouts and partial-shot
handling can all be exercised on a laptop.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.scopes._settings import (
    PHYSICAL_KEYS,
    TriggerSettings,
    normalize_physical,
    pretrigger_samples,
)
from cetal_scopes.scopes.base import Scope

__all__ = ["DemoScope"]

_PANEL_KEYS = frozenset({"trigger_delay", "frequency", "amplitude", "noise"})
_CONFIG_KEYS = _PANEL_KEYS | PHYSICAL_KEYS


class DemoScope(Scope):
    """A synthetic instrument that behaves like a staged, triggered digitizer.

    Each channel carries a tone at a distinct frequency plus noise, with a
    Gaussian burst at the trigger instant so cross-correlation alignment has a
    feature to lock onto. Every capture is generated with the channel's own
    deterministic seed, so two demo instruments produce correlated -- but not
    identical -- records.

    Parameters
    ----------
    label : str, optional
        Name reported in the capture metadata.
    channels : sequence of str, optional
        Channel names. Defaults to ``("CH1",)``.
    trigger_delay : float or None, optional
        Seconds between :meth:`arm` and the simulated trigger. ``None`` means
        no trigger ever arrives on its own, modelling an instrument waiting on
        an external signal -- use :meth:`force_trigger` or let it time out.
    skew : float, optional
        Seconds this instrument's trigger lags the nominal one, written into
        the capture's ``t0``. Lets a demo shot need real time alignment.
    burst_width : float, optional
        Gaussian width of the burst, in seconds. An absolute duration rather
        than a fraction of the record, so two instruments digitizing at
        different rates see the *same* physical event -- which is what makes
        cross-correlating them across a demo shot meaningful.
    seed : int, optional
        Seed for the additive noise.
    """

    supports_staged_acquisition = True
    supports_force_trigger = True

    def __init__(
        self,
        label: str = "demo",
        *,
        channels: Sequence[str] = ("CH1",),
        sample_rate: float = 1e9,
        record_length: int = 8192,
        trigger_delay: float | None = 0.05,
        skew: float = 0.0,
        burst_width: float = 2e-8,
        acquire_timeout: float = 10.0,
        seed: int = 0,
    ) -> None:
        self._label = label
        self._channels = tuple(channels)
        if not self._channels:
            raise ValueError("at least one channel is required")
        self._sample_rate = float(sample_rate)
        self._record_length = int(record_length)
        self._pretrigger_spec: int | float = 0.5
        self._trigger_delay = trigger_delay
        self._skew = float(skew)
        self._burst_width = float(burst_width)
        self._acquire_timeout = acquire_timeout
        self._trigger = TriggerSettings()
        self._frequency = 1e6
        self._amplitude = 0.5
        self._noise = 0.01
        self._rng = np.random.default_rng(seed)
        self._connected = False
        self._trigger_at: float | None = None
        self._triggered_at: float | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Mark the instrument available."""
        self._connected = True

    def close(self) -> None:
        """Release the instrument. Safe to call repeatedly."""
        self._connected = False
        self._armed = False

    @property
    def channels(self) -> tuple[str, ...]:
        """Channels this instrument acquires."""
        return self._channels

    def configure(self, settings: Mapping[str, Any]) -> None:
        """Apply the shared physical vocabulary plus a few demo-only keys.

        The demo-only keys are ``trigger_delay`` (seconds, or ``None`` for an
        externally triggered instrument), ``frequency``, ``amplitude`` and
        ``noise``.
        """
        unknown = set(settings) - _CONFIG_KEYS
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)!r}")

        if "channels" in settings:
            channels = tuple(settings["channels"])
            if not channels:
                raise ValueError("at least one channel is required")
            self._channels = channels

        physical = normalize_physical(settings, channels=self._channels)
        if physical.sample_rate is not None:
            self._sample_rate = physical.sample_rate
        if physical.record_length is not None:
            self._record_length = physical.record_length
        if physical.pretrigger is not None:
            self._pretrigger_spec = physical.pretrigger
        if physical.trigger is not None:
            self._trigger = physical.trigger

        if "trigger_delay" in settings:
            delay = settings["trigger_delay"]
            self._trigger_delay = None if delay is None else float(delay)
        if "frequency" in settings:
            self._frequency = float(settings["frequency"])
        if "amplitude" in settings:
            self._amplitude = float(settings["amplitude"])
        if "noise" in settings:
            self._noise = float(settings["noise"])

    def arm(self) -> None:
        """Schedule the simulated trigger and return immediately."""
        self._require_connected()
        if self._armed:
            raise RuntimeError("already armed; call fetch() or abort() first")
        if self._trigger_delay is None:
            self._trigger_at = None
        else:
            self._trigger_at = time.monotonic() + self._trigger_delay
        self._triggered_at = None
        self._armed = True

    def wait(self, timeout: float | None = None) -> bool:
        """Wait up to ``timeout`` seconds for the simulated trigger."""
        if not self._armed:
            raise RuntimeError("wait() called before arm()")
        limit = self._acquire_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            now = time.monotonic()
            if self._trigger_at is not None and now >= self._trigger_at:
                if self._triggered_at is None:
                    self._triggered_at = self._trigger_at
                return True
            if now >= deadline:
                return False
            time.sleep(min(0.005, deadline - now))

    def fetch(self) -> Capture:
        """Generate the record for the trigger that arrived."""
        if not self._armed or self._triggered_at is None:
            raise RuntimeError("fetch() called before a completed wait()")
        self._armed = False
        self._trigger_at = None
        self._triggered_at = None
        return self._build_capture()

    def abort(self) -> None:
        """Disarm. Safe when unarmed and when never connected."""
        self._armed = False
        self._trigger_at = None
        self._triggered_at = None

    def force_trigger(self) -> None:
        """Fire the simulated trigger now."""
        if not self._armed:
            raise RuntimeError("force_trigger() requires an armed instrument")
        self._trigger_at = time.monotonic()

    def trigger_status(self) -> str | None:
        """``"Ready"`` while armed and waiting, ``"Stop"`` otherwise."""
        if not self._armed:
            return "Stop"
        return "Ready"

    def acquire(self) -> Capture:
        """Arm, wait for the simulated trigger, and return the record."""
        return self._acquire_staged()

    # -- waveform synthesis ------------------------------------------------

    def _build_capture(self) -> Capture:
        n = self._record_length
        dt = 1.0 / self._sample_rate
        pretrigger = pretrigger_samples(self._pretrigger_spec, n)
        t0 = -pretrigger * dt + self._skew
        t = t0 + np.arange(n, dtype=np.float64) * dt

        rows: list[NDArray[np.float64]] = []
        # The waveform is built on the axis *relative to this instrument's own
        # trigger*, so every instrument records the same physical event and
        # only its t0 differs. Tying the carrier phase to absolute time
        # instead would give each instrument a differently phased wavelet,
        # which no cross-correlation could align to better than a fraction of
        # a period.
        local = t - self._skew
        # A narrow burst at the trigger gives alignment a feature to lock on
        # to; the tone alone is ambiguous by whole periods. The width is an
        # absolute duration, so instruments digitizing at different rates see
        # the same event rather than differently shaped ones.
        width = max(8.0 * dt, self._burst_width)
        burst = np.exp(-0.5 * (local / width) ** 2)
        for index in range(len(self._channels)):
            frequency = self._frequency * (index + 1)
            tone = self._amplitude * np.sin(2.0 * np.pi * frequency * local)
            noise = self._noise * self._rng.standard_normal(n)
            rows.append(tone * burst + noise)

        return Capture(
            volts=np.vstack(rows),
            t0=t0,
            dt=dt,
            channel_names=self._channels,
            metadata=self._metadata(n, dt, pretrigger),
        )

    def _metadata(self, n_samples: int, dt: float, pretrigger: int) -> dict[str, Any]:
        return {
            "instrument": "DemoScope",
            "label": self._label,
            "channels": list(self._channels),
            "sample_rate_hz": 1.0 / dt,
            "record_length": n_samples,
            "pretrigger": pretrigger,
            "skew_s": self._skew,
            "burst_width_s": self._burst_width,
            "trigger_source": self._trigger.source,
            "trigger_level_v": self._trigger.level,
            "trigger_slope": self._trigger.slope,
            "synthetic": True,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("scope is not connected; call connect() first")

    def __repr__(self) -> str:
        return (
            f"DemoScope(label={self._label!r}, channels={self._channels!r}, "
            f"trigger_delay={self._trigger_delay!r})"
        )
