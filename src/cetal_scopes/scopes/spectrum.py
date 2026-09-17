"""Driver for the Spectrum Instrumentation M5i.3367-x16 PCIe digitizer.

The card is a register-based instrument: every setting is a numeric register
written or read through a handful of vendor driver functions, not a SCPI
command language. See ``docs/scopes/M5i.3367-x16/`` for the full reference
this driver is built from.

Verified 2026-09-15 against a real card (serial 24123) opened at
``/dev/spcm0`` through the vendor driver (``libspcm_linux.so``, loaded via
``spcm_core``).

The register numbers below (``SPC_*``, ``M2CMD_*``, ...) are transcribed
directly from the vendor manual rather than imported from ``spcm_core.constants``,
so that this module never needs the vendor SDK at import time -- only the
:class:`_RealCard` implementation does, and only inside its methods, per
:class:`~cetal_scopes.scopes.base.Scope`'s lazy-import rule.

Hardware setup order is load-bearing on this card (channel enable constrains
the achievable sample rate and memory; the trigger OR mask defaults to the
software trigger and must be cleared explicitly; the sample rate you get back
is the nearest achievable divided clock, not what you asked for). Because of
that, :meth:`SpectrumM5i3367.configure` only validates and stores settings; it
performs no I/O. The full, correctly-ordered register sequence is applied
fresh by :meth:`~SpectrumM5i3367.acquire` and
:meth:`~SpectrumM5i3367.acquire_segments` on every call, rather than being
built up incrementally the way the Siglent driver writes each setting
immediately -- incremental writes on this card would make the ordering
constraint easy to violate across separate ``configure()`` calls.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.scopes._settings import (
    PHYSICAL_KEYS,
    TriggerSettings,
    normalize_physical,
    normalize_pretrigger,
    pretrigger_samples,
)
from cetal_scopes.scopes.base import Scope

__all__ = [
    "SpectrumM5i3367",
    "codes_to_volts",
    "deinterleave",
    "snap_input_range",
    "snap_pretrigger",
    "snap_record_length",
    "snap_sample_rate",
    "volts_to_code",
]

DEFAULT_DEVICE = "/dev/spcm0"

#: Fixed input ranges, volts full-scale in millivolts (SPC_AMPx).
INPUT_RANGES_MV: tuple[int, ...] = (200, 500, 1000, 2500)
#: Clock base frequencies the card divides down from (manual pp. 19, 97).
BASE_CLOCKS_HZ: tuple[float, ...] = (10e9, 8e9, 6.4e9)
#: Memory/segment/pre/posttrigger size grid, in samples (manual p. 90).
MEMSIZE_STEP = 32
MAX_RATE_1CH_HZ = 10e9
MAX_RATE_2CH_HZ = 5e9

_VALID_CHANNELS = ("CH0", "CH1")
_CHANNEL_INDEX = {"CH0": 0, "CH1": 1}
_PANEL_KEYS: frozenset[str] = frozenset({"segments"})
_CONFIG_KEYS = PHYSICAL_KEYS | _PANEL_KEYS

# ---------------------------------------------------------------------------
# Register numbers (see docs/scopes/M5i.3367-x16/code.md for the full tables)
# ---------------------------------------------------------------------------
SPC_M2CMD = 100
SPC_TIMEOUT = 295130
SPC_FNCTYPE = 2001
SPC_PCITYP = 2000
SPC_PCISERIALNO = 2030
SPC_MIINST_MAXADCVALUE = 1126
SPC_MIINST_BYTESPERSAMPLE = 1120
SPC_CHENABLE = 11000
SPC_CARDMODE = 9500
SPC_MEMSIZE = 10000
SPC_SEGMENTSIZE = 10010
SPC_POSTTRIGGER = 10100
SPC_CLOCKMODE = 20200
SPC_SAMPLERATE = 20000
SPC_AMP0 = 30010
SPC_AMP1 = 30110
SPC_OFFS0 = 30000
SPC_OFFS1 = 30100
SPC_TRIG_ORMASK = 40410
SPC_TRIG_ANDMASK = 40430
SPC_TRIG_CH_ORMASK0 = 40460
SPC_TRIG_CH_ANDMASK0 = 40480
SPC_TRIG_CH0_MODE = 40610
SPC_TRIG_CH0_LEVEL0 = 42200
SPC_TRIG_TERM = 40110
SPC_TRIG_EXT0_MODE = 40510
SPC_TRIG_EXT0_LEVEL0 = 42320

SPCM_TYPE_AI = 1
SPC_CM_INTPLL = 1
SPC_REC_STD_SINGLE = 1
SPC_REC_STD_MULTI = 2
SPC_TMASK_NONE = 0
SPC_TMASK_SOFTWARE = 1
SPC_TMASK_EXT0 = 2
SPC_TM_POS = 1
SPC_TM_NEG = 2

M2CMD_CARD_RESET = 1
M2CMD_CARD_START = 4
M2CMD_CARD_ENABLETRIGGER = 8
M2CMD_CARD_FORCETRIGGER = 16
M2CMD_CARD_STOP = 64
M2CMD_CARD_WAITREADY = 16384
M2CMD_DATA_STARTDMA = 65536
M2CMD_DATA_WAITDMA = 131072
M2CMD_DATA_STOPDMA = 262144

#: Accepted spellings of the card's external trigger input (Ext0, "Trig In").
_EXTERNAL_SOURCES = frozenset({"EXT", "EXT0", "EX"})

#: ``SPC_TRIG_EXT0_LEVEL0`` is in millivolts and clamps at +/-5 V.
EXT0_LEVEL_LIMIT_MV = 5000

_SLOPE_MODES = {
    "pos": SPC_TM_POS,
    "rising": SPC_TM_POS,
    "neg": SPC_TM_NEG,
    "falling": SPC_TM_NEG,
}


def snap_input_range(volts: float) -> int:
    """Snap a full-scale range (volts) up to the next :data:`INPUT_RANGES_MV` step.

    Clamped to the ends, matching the convention of the Siglent driver's
    ``snap_vdiv``: a request above the largest range is silently clamped
    (and will clip), a request at or below the smallest range gets the most
    sensitive range.
    """
    millivolts = volts * 1000.0
    for step in INPUT_RANGES_MV:
        if step >= millivolts:
            return step
    return INPUT_RANGES_MV[-1]


def snap_sample_rate(hertz: float, *, n_channels: int) -> float:
    """Clamp a requested sample rate to what the active channel count allows.

    This only enforces the channel-count ceiling (manual p. 19: 10 GS/s with
    one channel, 5 GS/s with two); it does not replicate the card's internal
    ``base / 2**n`` divider ladder. The card always rounds the value written
    to :data:`SPC_SAMPLERATE` to the nearest achievable divided clock and
    that is read back after every acquisition, so software never needs to
    guess it.
    """
    if hertz <= 0:
        raise ValueError(f"sample_rate must be positive, got {hertz!r}")
    ceiling = MAX_RATE_1CH_HZ if n_channels <= 1 else MAX_RATE_2CH_HZ
    return min(hertz, ceiling)


def snap_pretrigger(samples: int, record_length: int, step: int = MEMSIZE_STEP) -> int:
    """Put a pretrigger count on the hardware grid, inside its legal bounds.

    The card writes ``SPC_POSTTRIGGER`` and derives the pretrigger as
    ``memsize - posttrigger``, and the manual's limits table (p. 90) gives
    *both* a minimum of ``step`` and a step size of ``step``. Snapping the
    record length alone is not enough: half of a 32-aligned record is only
    32-aligned when the record is 64-aligned, so an innocuous record length
    like 250016 produces a pretrigger of 125008 that the card rejects with
    ``ERR_VALUE``.

    Rounds *down*, so the samples it gives up go to the posttrigger -- what
    happens after the trigger is normally the measurement.
    """
    snapped = (int(samples) // step) * step
    highest = record_length - step
    if highest <= 0:
        # Too short to hold both; the whole record becomes posttrigger.
        return 0
    return max(step, min(snapped, highest))


def snap_record_length(samples: int, step: int = MEMSIZE_STEP) -> int:
    """Round a sample count up to the next multiple of ``step``.

    The memory/segment/pre/posttrigger grid is enforced by hardware; an
    off-grid value is rejected outright with ``ERR_VALUE``.
    """
    if samples <= 0:
        raise ValueError(f"record length must be positive, got {samples!r}")
    remainder = samples % step
    return samples if remainder == 0 else samples + (step - remainder)


def deinterleave(raw: NDArray[np.int16], n_channels: int) -> NDArray[np.int16]:
    """Split an interleaved DMA buffer into ``(n_channels, n_samples)``.

    The buffer layout is ``A0 B0 A1 B1 ...`` for two channels (manual p. 94,
    Table 53); for one channel it is already contiguous per-sample.
    """
    if n_channels == 1:
        return raw.reshape(1, -1)
    return np.ascontiguousarray(raw.reshape(-1, n_channels).T)


def codes_to_volts(
    codes: NDArray[Any], range_mv: int, max_adc: int, *, offset_v: float = 0.0
) -> NDArray[np.float64]:
    """Convert signed ADC codes to volts.

    Implements ``V = code * (range_v / max_adc) - offset_v``: the raw code
    reflects the input *after* the hardware offset shift, so the offset is
    subtracted back out, mirroring the Siglent driver's ``codes_to_volts``.
    ``max_adc`` must come from ``SPC_MIINST_MAXADCVALUE`` -- never hard-code
    the bit-width full-scale code, since the card reserves some codes for
    gain/offset compensation (manual p. 95).
    """
    if max_adc == 0:
        raise ValueError("max_adc is zero; waveform cannot be scaled")
    range_v = range_mv / 1000.0
    return codes.astype(np.float64) * (range_v / max_adc) - offset_v


def volts_to_code(
    level_v: float, range_mv: int, max_adc: int, *, offset_v: float = 0.0
) -> int:
    """Convert a trigger level in volts to an ADC code for ``SPC_TRIG_CHn_LEVEL0``.

    The level is relative to the *current* input range and offset -- per the
    vendor manual's own troubleshooting table, forgetting this (giving a
    level in mV, or for the wrong range) is the most common reason a channel
    trigger never fires.
    """
    range_v = range_mv / 1000.0
    code = (level_v + offset_v) / range_v * max_adc
    if not -max_adc - 1 <= code <= max_adc:
        raise ValueError(
            f"trigger level {level_v:g} V is outside the +/-{range_v:g} V "
            f"input range at offset {offset_v:g} V; expected a level in "
            f"[{-range_v - offset_v:g}, {range_v - offset_v:g}] V. A channel "
            f"trigger's level is referred to that channel's input range, "
            f"unlike an external trigger's, which is referred to the "
            f"connector (+/-{EXT0_LEVEL_LIMIT_MV / 1000:g} V) -- so a level "
            f"carried over from an external source is often out of range here"
        )
    return round(code)


def _normalize_channel(channel: str) -> str:
    name = channel.strip().upper()
    if name not in _VALID_CHANNELS:
        raise ValueError(
            f"unsupported channel {channel!r}; expected one of {_VALID_CHANNELS}"
        )
    return name


def _normalize_channels(channels: Sequence[str]) -> tuple[str, ...]:
    names = tuple(_normalize_channel(channel) for channel in channels)
    if not names:
        raise ValueError("at least one channel is required")
    if len(set(names)) != len(names):
        raise ValueError(f"channels must be unique, got {names!r}")
    return names


def _slope_mode(slope: str | None) -> int:
    key = "pos" if slope is None else str(slope).strip().lower()
    if key not in _SLOPE_MODES:
        raise ValueError(
            f"unsupported trigger slope {slope!r}; expected one of "
            f"('POS', 'NEG', 'RISING', 'FALLING')"
        )
    return _SLOPE_MODES[key]


class _Card(Protocol):
    """Narrow seam between the driver and the vendor library; a fake for tests."""

    def open(self, device: str) -> None: ...

    def close(self) -> None: ...

    def get_i32(self, register: int) -> int: ...

    def get_i64(self, register: int) -> int: ...

    def get_str(self, register: int) -> str: ...

    def set_i32(self, register: int, value: int) -> None: ...

    def set_i64(self, register: int, value: int) -> None: ...

    def command(self, command: int, *, timeout_ms: int | None = None) -> None: ...

    def def_transfer(self, byte_count: int) -> None: ...

    def read_buffer(self, n_samples_total: int) -> NDArray[np.int16]: ...


class _RealCard:
    """``spcm_core``-backed implementation of :class:`_Card`.

    ``spcm_core`` is imported lazily inside :meth:`open`, never at module
    scope, so that importing :mod:`cetal_scopes` never requires the vendor
    driver to be installed.
    """

    def __init__(self) -> None:
        self._core: Any = None
        self._handle: Any = None
        self._buffer: Any = None

    def open(self, device: str) -> None:
        import spcm_core

        self._core = spcm_core
        handle = spcm_core.spcm_hOpen(device.encode("ascii"))
        if not handle:
            raise RuntimeError(
                f"could not open Spectrum card at {device!r}; check that the "
                f"device exists and the vendor driver is installed"
            )
        self._handle = handle

    def close(self) -> None:
        if self._handle is not None and self._core is not None:
            self._core.spcm_vClose(self._handle)
        self._handle = None

    def get_i32(self, register: int) -> int:
        core = self._core
        value = core.int32(0)
        self._check(core.spcm_dwGetParam_i32(self._handle, register, core.byref(value)))
        return value.value

    def get_i64(self, register: int) -> int:
        core = self._core
        value = core.int64(0)
        self._check(core.spcm_dwGetParam_i64(self._handle, register, core.byref(value)))
        return value.value

    def get_str(self, register: int) -> str:
        core = self._core
        buf = core.create_string_buffer(32)
        self._check(core.spcm_dwGetParam_ptr(self._handle, register, buf, 32))
        return buf.value.decode()

    def set_i32(self, register: int, value: int) -> None:
        self._check(self._core.spcm_dwSetParam_i32(self._handle, register, int(value)))

    def set_i64(self, register: int, value: int) -> None:
        self._check(self._core.spcm_dwSetParam_i64(self._handle, register, int(value)))

    def command(self, command: int, *, timeout_ms: int | None = None) -> None:
        core = self._core
        if timeout_ms is not None:
            self._check(core.spcm_dwSetParam_i32(self._handle, SPC_TIMEOUT, timeout_ms))
        self._check(core.spcm_dwSetParam_i32(self._handle, SPC_M2CMD, command))

    def def_transfer(self, byte_count: int) -> None:
        from spcm_core.spcm_tools import pvAllocMemPageAligned

        core = self._core
        self._buffer = pvAllocMemPageAligned(byte_count)
        self._check(
            core.spcm_dwDefTransfer_i64(
                self._handle,
                core.SPCM_BUF_DATA,
                core.SPCM_DIR_CARDTOPC,
                0,
                self._buffer,
                0,
                byte_count,
            )
        )

    def read_buffer(self, n_samples_total: int) -> NDArray[np.int16]:
        return np.frombuffer(self._buffer, dtype=np.int16, count=n_samples_total)

    def _check(self, err: int) -> None:
        core = self._core
        if err == core.ERR_OK:
            return
        if err == core.ERR_TIMEOUT:
            raise TimeoutError("wait timed out")
        text = core.create_string_buffer(core.ERRORTEXTLEN)
        core.spcm_dwGetErrorInfo_i32(self._handle, None, None, text)
        raise RuntimeError(
            f"Spectrum driver error {err}: {text.value.decode(errors='replace')}"
        )


@dataclass(frozen=True)
class _PendingAcquisition:
    """Transfer geometry latched at :meth:`SpectrumM5i3367.arm` time.

    Snapshotting these means a ``configure()`` arriving between arm and fetch
    cannot change the size of the transfer that is already in flight.
    """

    record_length: int
    """Samples per segment (the whole record in Standard Single)."""

    pretrigger: int
    n_segments: int | None
    n_channels: int
    total_samples: int


class SpectrumM5i3367(Scope):
    """Driver for a Spectrum Instrumentation M5i.3367-x16 PCIe digitizer.

    Unlike an oscilloscope, this card has no front panel: every setting is a
    numeric register. :meth:`configure` accepts the shared physical-unit
    vocabulary documented on :class:`~cetal_scopes.scopes.base.Scope`
    (``sample_rate``, ``record_length``, ``pretrigger``, ``range``,
    ``offset``, ``coupling``, ``impedance``, ``trigger``), plus one
    M5i-specific key, ``segments``: an ``int`` switches acquisition to
    Multiple Recording (use :meth:`acquire_segments`, not :meth:`acquire`);
    ``None`` (the default) is Standard Single.

    ``coupling`` and ``impedance`` are fixed in hardware (DC, 50 ohm): they
    are accepted as no-ops when they match, and raise :class:`ValueError`
    otherwise, so shared application code can set them on any driver without
    special-casing this card.

    Parameters
    ----------
    device : str, optional
        Device path, e.g. ``"/dev/spcm0"``.
    channels : sequence of str, optional
        Channels to acquire, e.g. ``("CH0", "CH1")``. Defaults to ``("CH0",)``.
    acquire_timeout : float, optional
        Maximum seconds to wait for a triggered acquisition to finish.
    card : _Card, optional
        Injectable card implementation, primarily for tests.
    """

    supports_staged_acquisition = True
    supports_force_trigger = True

    def __init__(
        self,
        device: str = DEFAULT_DEVICE,
        *,
        channels: Sequence[str] = ("CH0",),
        acquire_timeout: float = 10.0,
        card: _Card | None = None,
    ) -> None:
        self._device = device
        self._acquire_timeout = acquire_timeout
        self._channels = _normalize_channels(channels)
        self._card = card
        self._owns_card = card is None
        self._connected = False

        self._product_name = ""
        self._serial = 0
        self._max_adc = 0
        self._bytes_per_sample = 2
        self._actual_sample_rate = 0.0
        self._sample_rate_requested: float | None = None

        self._sample_rate: float = 1e6
        self._record_length: int = 4096
        self._pretrigger_spec: int | float = 0.5
        self._range_mv: dict[str, int] = dict.fromkeys(self._channels, 1000)
        self._offset_v: dict[str, float] = dict.fromkeys(self._channels, 0.0)
        self._trigger = TriggerSettings()
        self._segments: int | None = None
        self._armed = False
        self._pending: _PendingAcquisition | None = None

    @property
    def product_name(self) -> str:
        """Card product name from ``SPC_PCITYP``, populated on :meth:`connect`."""
        return self._product_name

    @property
    def serial_number(self) -> int:
        """Card serial number from ``SPC_PCISERIALNO``, populated on :meth:`connect`."""
        return self._serial

    @property
    def channels(self) -> tuple[str, ...]:
        """Channels acquired by :meth:`acquire` / :meth:`acquire_segments`."""
        return self._channels

    @classmethod
    def max_sample_rate(cls, n_channels: int) -> float | None:
        """:data:`MAX_RATE_1CH_HZ` with one channel, :data:`MAX_RATE_2CH_HZ` with two.

        The card interleaves its converters, so the second channel costs half
        the rate (manual p. 19). Dropping a channel really does double it.
        """
        return snap_sample_rate(float("inf"), n_channels=n_channels)

    @classmethod
    def min_record_length(cls, n_channels: int) -> int | None:
        """One :data:`MEMSIZE_STEP`, the hardware's record-length grid."""
        return MEMSIZE_STEP

    def connect(self) -> None:
        """Open the card and identify it."""
        if self._connected:
            return
        if self._card is None:
            self._card = _RealCard()
        self._card.open(self._device)
        try:
            function_type = self._card.get_i32(SPC_FNCTYPE)
            if function_type != SPCM_TYPE_AI:
                raise RuntimeError(
                    f"{self._device} is not an analog-input card "
                    f"(SPC_FNCTYPE={function_type})"
                )
            self._product_name = self._card.get_str(SPC_PCITYP)
            self._serial = self._card.get_i32(SPC_PCISERIALNO)
            self._max_adc = self._card.get_i32(SPC_MIINST_MAXADCVALUE)
            self._bytes_per_sample = self._card.get_i32(SPC_MIINST_BYTESPERSAMPLE)
        except Exception:
            self._connected = False
            raise
        self._connected = True

    def close(self) -> None:
        """Close the card. Safe to call repeatedly."""
        if self._card is not None:
            self._card.close()
            if self._owns_card:
                self._card = None
        self._connected = False

    def configure(self, settings: Mapping[str, Any]) -> None:
        """Validate and store acquisition settings; performs no I/O.

        See the class docstring for the accepted keys. Settings are applied
        to the card, in the hardware-required order, the next time
        :meth:`acquire` or :meth:`acquire_segments` runs.
        """
        unknown = set(settings) - _CONFIG_KEYS
        if unknown:
            raise ValueError(
                f"unsupported setting(s) {sorted(unknown)!r}; expected a "
                f"subset of {sorted(_CONFIG_KEYS)!r}"
            )

        if "channels" in settings:
            self.set_channels(settings["channels"])

        physical = normalize_physical(settings, channels=self._channels)

        if physical.sample_rate is not None:
            self.set_sample_rate(physical.sample_rate)
        if physical.record_length is not None:
            self.set_record_length(physical.record_length)
        if physical.pretrigger is not None:
            self.set_pretrigger(physical.pretrigger)
        if physical.range is not None:
            self.set_range(physical.range)
        if physical.offset is not None:
            self.set_offset(physical.offset)
        if physical.coupling is not None:
            self.set_coupling(physical.coupling)
        if physical.impedance is not None:
            self.set_impedance(physical.impedance)
        if physical.trigger is not None:
            self.set_edge_trigger(
                source=physical.trigger.source,
                level=physical.trigger.level,
                slope=physical.trigger.slope,
            )

        # Ranges and trigger are both in now, so the one cross-check between
        # them can be made here rather than at arm() time.
        self.validate_trigger()

        if "segments" in settings:
            self.set_segments(settings["segments"])

    def set_channels(self, channels: Sequence[str]) -> None:
        """Set the active channels, carrying over existing per-channel settings."""
        names = _normalize_channels(channels)
        self._range_mv = {ch: self._range_mv.get(ch, 1000) for ch in names}
        self._offset_v = {ch: self._offset_v.get(ch, 0.0) for ch in names}
        self._channels = names

    def set_sample_rate(self, hertz: float) -> None:
        """Request a sample rate in Hz, clamped to the channel-count ceiling.

        Clamping is not the only thing between this and the clock the card
        runs: only ``base / 2**n`` rates exist (manual p. 97), so a rate in
        between is rounded to the nearest divided clock. The request is kept
        alongside the read-back so a capture can show both -- asking for
        2 GS/s and getting 1.25 GS/s is otherwise invisible.
        """
        self._sample_rate_requested = float(hertz)
        self._sample_rate = snap_sample_rate(hertz, n_channels=len(self._channels))

    def set_record_length(self, samples: int) -> None:
        """Set the record length in samples (or segment length, in Multiple Recording)."""
        self._record_length = snap_record_length(samples)

    def set_pretrigger(self, value: float) -> None:
        """Set the pretrigger as a sample count (``int``) or record fraction (``float``)."""
        self._pretrigger_spec = normalize_pretrigger(value)

    def set_range(self, channel_values: Mapping[str, float]) -> None:
        """Set the full-scale input range (volts) for one or more channels."""
        for channel, volts in channel_values.items():
            self._range_mv[channel] = snap_input_range(float(volts))

    def set_offset(self, channel_values: Mapping[str, float]) -> None:
        """Set the input offset (volts) for one or more channels."""
        for channel, volts in channel_values.items():
            self._offset_v[channel] = float(volts)

    def set_coupling(self, channel_values: Mapping[str, str]) -> None:
        """Accept ``"DC"`` as a no-op; anything else raises, since coupling is fixed."""
        for channel, value in channel_values.items():
            if str(value).strip().upper() != "DC":
                raise ValueError(
                    f"unsupported coupling {value!r} for channel {channel!r}; "
                    f"the M5i.3367-x16 is fixed DC-coupled"
                )

    def set_impedance(self, channel_values: Mapping[str, float]) -> None:
        """Accept 50 ohm as a no-op; anything else raises, since impedance is fixed."""
        for channel, value in channel_values.items():
            if float(value) != 50.0:
                raise ValueError(
                    f"unsupported impedance {value!r} ohm for channel {channel!r}; "
                    f"the M5i.3367-x16 has a fixed 50 ohm input"
                )

    def set_edge_trigger(
        self,
        *,
        source: str | None = None,
        level: float | None = None,
        slope: str | None = None,
    ) -> None:
        """Configure an edge trigger on one of the acquired channels.

        ``source`` names either an acquired channel (``"CH0"``, ``"CH1"``) --
        where ``level`` is in volts relative to that channel's current range
        and offset -- or the card's external input (``"EXT"``), where ``level``
        is in volts at the connector, within +/-5 V. ``None`` leaves the
        existing setting unchanged; a ``source`` of ``None`` that was never set
        means the software trigger, which fires immediately on start.
        """
        self._trigger = TriggerSettings(
            source=source.strip().upper()
            if source is not None
            else self._trigger.source,
            level=level if level is not None else self._trigger.level,
            slope=slope if slope is not None else self._trigger.slope,
        )

    def _resolve_channel_trigger(self) -> tuple[int, int, int]:
        """Resolve a channel trigger to ``(index, slope mode, level code)``.

        Pure arithmetic over settings the card already holds, so it can be run
        to *validate* a configuration long before any register is written.

        Raises
        ------
        ValueError
            If the source is not one of the acquired channels, the slope is
            not a known spelling, or the level is outside that channel's
            input range.
        """
        channel = self._trigger.source
        if channel not in self._channels:
            raise ValueError(
                f"trigger source {channel!r} is not one of the acquired "
                f"channels {self._channels!r} and is not an external "
                f"source {tuple(sorted(_EXTERNAL_SOURCES))!r}"
            )
        level = self._trigger.level if self._trigger.level is not None else 0.0
        return (
            _CHANNEL_INDEX[channel],
            _slope_mode(self._trigger.slope),
            volts_to_code(
                level,
                self._range_mv[channel],
                self._max_adc,
                offset_v=self._offset_v[channel],
            ),
        )

    def validate_trigger(self) -> None:
        """Check the configured trigger against the configured input ranges.

        A channel trigger's level is referred to that channel's range, so a
        level that is perfectly legal for the external input (referred to the
        connector, +/-5 V) can be unreachable once the source becomes a
        channel -- and a level the channel can never cross is a trigger that
        never fires. The card only learns this when :meth:`arm` writes the
        registers, which is far too late: one instrument failing to arm takes
        the whole multi-instrument shot down with it, since a half-armed set
        is never fired. Running the same arithmetic at ``configure()`` time
        turns that into an error where the setting was made.

        Raises
        ------
        ValueError
            If the trigger cannot be realised as configured.
        """
        source = self._trigger.source
        if source is None or source in _EXTERNAL_SOURCES:
            return
        self._resolve_channel_trigger()

    def set_segments(self, segments: int | None) -> None:
        """Switch to Multiple Recording with ``segments`` segments, or ``None`` for Standard Single."""
        if segments is not None and segments < 1:
            raise ValueError(
                f"segments must be a positive int or None, got {segments!r}"
            )
        self._segments = segments

    def arm(self) -> None:
        """Write the full register sequence and start the card, trigger enabled.

        The whole ordered setup (:meth:`_apply_common_setup`, card mode, memory
        and transfer definition) happens here rather than in :meth:`configure`,
        which still performs no I/O. It cannot move later: the sequence opens
        with ``M2CMD_CARD_RESET``, which would destroy an in-flight
        acquisition.

        ``START | ENABLETRIGGER | STARTDMA`` does not block on the trigger, so
        this returns with the card genuinely armed.

        Raises
        ------
        RuntimeError
            If already armed.
        """
        card = self._require_connected()
        if self._armed:
            raise RuntimeError(
                "already armed; call fetch()/fetch_segments() or abort() first"
            )
        self._apply_common_setup()

        segment_length = self._record_length
        pretrigger = snap_pretrigger(
            pretrigger_samples(self._pretrigger_spec, segment_length),
            segment_length,
        )
        posttrigger = segment_length - pretrigger
        n_channels = len(self._channels)

        if self._segments is None:
            card.set_i32(SPC_CARDMODE, SPC_REC_STD_SINGLE)
            card.set_i64(SPC_MEMSIZE, segment_length)
            total_samples = segment_length * n_channels
        else:
            card.set_i32(SPC_CARDMODE, SPC_REC_STD_MULTI)
            card.set_i64(SPC_SEGMENTSIZE, segment_length)
            card.set_i64(SPC_MEMSIZE, segment_length * self._segments)
            total_samples = segment_length * self._segments * n_channels
        card.set_i64(SPC_POSTTRIGGER, posttrigger)

        card.def_transfer(total_samples * self._bytes_per_sample)
        card.command(M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_DATA_STARTDMA)
        self._pending = _PendingAcquisition(
            record_length=segment_length,
            pretrigger=pretrigger,
            n_segments=self._segments,
            n_channels=n_channels,
            total_samples=total_samples,
        )
        self._armed = True

    def wait(self, timeout: float | None = None) -> bool:
        """Wait up to ``timeout`` seconds for the run and its DMA to finish.

        A timeout is not an error state on this card -- the run is still going
        and the wait may simply be re-entered, which is how an application
        stays responsive while armed. Returns ``False`` rather than raising.
        """
        card = self._require_connected()
        if not self._armed:
            raise RuntimeError("wait() called before arm()")
        limit = self._acquire_timeout if timeout is None else timeout
        try:
            card.command(
                M2CMD_CARD_WAITREADY | M2CMD_DATA_WAITDMA,
                timeout_ms=max(1, int(limit * 1000)),
            )
        except TimeoutError:
            return False
        return True

    def fetch(self) -> Capture:
        """Return the completed Standard Single acquisition.

        Raises
        ------
        RuntimeError
            If the card is configured for Multiple Recording (call
            :meth:`fetch_segments`), or if no completed acquisition is pending.
        """
        if self._segments is not None:
            raise RuntimeError(
                "card is configured for Multiple Recording "
                f"({self._segments} segments); call fetch_segments() "
                "instead of fetch()"
            )
        pending = self._require_pending()
        codes = self._read_pending(pending)
        return self._build_capture(codes, pending.pretrigger, segment_index=None)

    def fetch_segments(self) -> list[Capture]:
        """Return one capture per segment of the completed Multiple Recording.

        Raises
        ------
        RuntimeError
            If the card is not configured for Multiple Recording, or if no
            completed acquisition is pending.
        """
        if self._segments is None:
            raise RuntimeError(
                "card is not configured for Multiple Recording; call "
                "set_segments(n) or pass 'segments' to configure() first"
            )
        pending = self._require_pending()
        codes_all = self._read_pending(pending)

        segment_length = pending.record_length
        captures = []
        for segment in range(pending.n_segments or 1):
            start = segment * segment_length
            end = start + segment_length
            captures.append(
                self._build_capture(
                    codes_all[:, start:end],
                    pending.pretrigger,
                    segment_index=segment,
                )
            )
        return captures

    def fetch_all(self) -> list[Capture]:
        """Every capture of the completed acquisition, whatever the card mode."""
        if self._segments is not None:
            return self.fetch_segments()
        return [self.fetch()]

    def abort(self) -> None:
        """Stop the card and discard the pending acquisition.

        Safe when unarmed and when disconnected; never raises.
        """
        self._armed = False
        self._pending = None
        if not self._connected or self._card is None:
            return
        try:
            self._card.command(M2CMD_CARD_STOP)
            self._card.command(M2CMD_DATA_STOPDMA)
        except (RuntimeError, TimeoutError, OSError):
            pass

    def force_trigger(self) -> None:
        """Generate exactly one trigger event in software.

        Takes effect only while the card is waiting for a trigger, and
        overrides the trigger enable state. In Multiple Recording it fills
        exactly *one* segment, so forcing an ``n``-segment run needs ``n``
        calls.

        Raises
        ------
        RuntimeError
            If the card is not armed.
        """
        card = self._require_connected()
        if not self._armed:
            raise RuntimeError("force_trigger() requires an armed card")
        card.command(M2CMD_CARD_FORCETRIGGER)

    def acquire(self) -> Capture:
        """Run one Standard Single acquisition and return the capture.

        Raises
        ------
        RuntimeError
            If the card is configured for Multiple Recording
            (:meth:`set_segments`); call :meth:`acquire_segments` instead.
        TimeoutError
            If no trigger arrives within the configured timeout. The message
            contains ``"did not complete"``.
        """
        if self._segments is not None:
            raise RuntimeError(
                "card is configured for Multiple Recording "
                f"({self._segments} segments); call acquire_segments() "
                "instead of acquire()"
            )
        return self._acquire_staged()

    def acquire_segments(self) -> list[Capture]:
        """Run one Multiple Recording acquisition and return one capture per segment.

        The record length set via :meth:`set_record_length` (or
        ``record_length`` in :meth:`configure`) is the *per-segment* length.

        Raises
        ------
        RuntimeError
            If the card is not configured for Multiple Recording; call
            :meth:`set_segments` (or pass ``"segments"`` to :meth:`configure`)
            first.
        TimeoutError
            If the run does not complete within the configured timeout. The
            message contains ``"did not complete"``.
        """
        if self._segments is None:
            raise RuntimeError(
                "card is not configured for Multiple Recording; call "
                "set_segments(n) or pass 'segments' to configure() first"
            )
        self.arm()
        try:
            if not self.wait():
                raise TimeoutError(
                    f"acquisition did not complete within {self._acquire_timeout:g}s"
                )
        except BaseException:
            self.abort()
            raise
        return self.fetch_segments()

    def _require_pending(self) -> _PendingAcquisition:
        pending = self._pending
        if not self._armed or pending is None:
            raise RuntimeError("fetch() called before a completed wait()")
        return pending

    def _read_pending(self, pending: _PendingAcquisition) -> NDArray[np.int16]:
        card = self._require_connected()
        raw = card.read_buffer(pending.total_samples)
        card.command(M2CMD_DATA_STOPDMA)
        self._armed = False
        self._pending = None
        return deinterleave(raw, pending.n_channels)

    def _apply_common_setup(self) -> None:
        """Write channel, clock, range/offset, and trigger registers, in order.

        Card mode and memory sizing are applied by the caller afterward,
        since Standard Single and Multiple Recording differ there.
        """
        card = self._require_connected()
        card.command(M2CMD_CARD_RESET)

        mask = sum(1 << _CHANNEL_INDEX[channel] for channel in self._channels)
        card.set_i32(SPC_CHENABLE, mask)

        card.set_i32(SPC_CLOCKMODE, SPC_CM_INTPLL)
        card.set_i64(SPC_SAMPLERATE, int(self._sample_rate))

        for channel in self._channels:
            index = _CHANNEL_INDEX[channel]
            range_mv = self._range_mv[channel]
            card.set_i32(SPC_AMP0 + index * (SPC_AMP1 - SPC_AMP0), range_mv)
            offset_pct = round(self._offset_v[channel] / (range_mv / 1000.0) * 100)
            card.set_i32(SPC_OFFS0 + index * (SPC_OFFS1 - SPC_OFFS0), offset_pct)

        # The OR mask defaults to the software trigger; always clear it
        # explicitly before deciding what the real source is (manual p. 104:
        # "the single most common mistake").
        card.set_i32(SPC_TRIG_ORMASK, SPC_TMASK_NONE)
        card.set_i32(SPC_TRIG_ANDMASK, 0)
        card.set_i32(SPC_TRIG_CH_ORMASK0, 0)
        card.set_i32(SPC_TRIG_CH_ANDMASK0, 0)

        if self._trigger.source is None:
            card.set_i32(SPC_TRIG_ORMASK, SPC_TMASK_SOFTWARE)
        elif self._trigger.source in _EXTERNAL_SOURCES:
            # Ext0 ("Trig In"). Its level register is in millivolts referred to
            # the connector, not to a channel's range, so it does not go
            # through volts_to_code().
            card.set_i32(SPC_TRIG_EXT0_MODE, _slope_mode(self._trigger.slope))
            level = self._trigger.level if self._trigger.level is not None else 0.0
            millivolts = round(level * 1000.0)
            if not -EXT0_LEVEL_LIMIT_MV <= millivolts <= EXT0_LEVEL_LIMIT_MV:
                raise ValueError(
                    f"external trigger level {level!r} V is outside the card's "
                    f"+/-{EXT0_LEVEL_LIMIT_MV / 1000:g} V range"
                )
            card.set_i32(SPC_TRIG_EXT0_LEVEL0, millivolts)
            card.set_i32(SPC_TRIG_ORMASK, SPC_TMASK_EXT0)
        else:
            index, mode, code = self._resolve_channel_trigger()
            card.set_i32(SPC_TRIG_CH0_MODE + index, mode)
            card.set_i32(SPC_TRIG_CH0_LEVEL0 + index, code)
            card.set_i32(SPC_TRIG_CH_ORMASK0, 1 << index)

        self._actual_sample_rate = float(card.get_i64(SPC_SAMPLERATE))

    def _build_capture(
        self, codes: NDArray[np.int16], pretrigger: int, *, segment_index: int | None
    ) -> Capture:
        dt = 1.0 / self._actual_sample_rate
        t0 = -pretrigger * dt
        volts = np.vstack(
            [
                codes_to_volts(
                    codes[i],
                    self._range_mv[channel],
                    self._max_adc,
                    offset_v=self._offset_v[channel],
                )
                for i, channel in enumerate(self._channels)
            ]
        )
        record_length = codes.shape[1]
        return Capture(
            volts=volts,
            t0=t0,
            dt=dt,
            channel_names=self._channels,
            raw=codes,
            metadata=self._card_metadata(
                pretrigger=pretrigger,
                record_length=record_length,
                segment_index=segment_index,
            ),
        )

    def _card_metadata(
        self, *, pretrigger: int, record_length: int, segment_index: int | None
    ) -> dict[str, Any]:
        return {
            "instrument": "Spectrum M5i.3367-x16",
            "device": self._device,
            "product_name": self._product_name,
            "serial_number": self._serial,
            "sample_rate_hz": self._actual_sample_rate,
            # What was asked for, so the gap the clock divider opens up is
            # visible: only base/2**n rates exist, so 2 GS/s becomes 1.25.
            "sample_rate_requested_hz": self._sample_rate_requested,
            "record_length": record_length,
            "pretrigger": pretrigger,
            "posttrigger": record_length - pretrigger,
            "channel_range_mv": dict(self._range_mv),
            "channel_offset_v": dict(self._offset_v),
            "trigger_source": self._trigger.source,
            "trigger_level_v": self._trigger.level,
            "trigger_slope": self._trigger.slope,
            "max_adc_value": self._max_adc,
            "bytes_per_sample": self._bytes_per_sample,
            "segments": self._segments,
            "segment_index": segment_index,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def _require_connected(self) -> _Card:
        if not self._connected or self._card is None:
            raise RuntimeError("scope is not connected; call connect() first")
        return self._card
