"""Driver for the Siglent SDS6000L-series oscilloscopes (SDS6204L).

The driver talks SCPI over the instrument's raw-socket LAN interface (TCP port
5025), which needs only the standard library. It fetches the binary
``WAVEDESC`` preamble and waveform payload, converts ADC codes to volts, and
returns a :class:`~cetal_scopes.capture.Capture`.
"""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.scopes._settings import (
    PHYSICAL_KEYS,
    normalize_physical,
    pretrigger_samples,
)
from cetal_scopes.scopes.base import Scope

__all__ = [
    "DEFAULT_INTERPOLATION",
    "MAX_RATE_HZ",
    "MEMORY_MANAGEMENT_MODES",
    "VDIV_LADDER",
    "AcquisitionPlan",
    "SiglentSDS6204L",
    "WaveDesc",
    "codes_to_volts",
    "parse_wavedesc",
    "snap_timebase",
    "snap_vdiv",
    "time_origin",
]

DEFAULT_ADDRESS = "192.168.5.193"
DEFAULT_PORT = 5025
GRID_NUM = 10
#: Vertical grid divisions; the ADC accommodates roughly +/-4.5 div around the
#: offset, so 8 keeps a margin (see the trigger-clamp note in the apps).
VGRID_NUM = 8

_WAVEDESC_LENGTH = 346
_VALID_ANALOG_CHANNELS = ("C1", "C2", "C3", "C4")
_PANEL_KEYS = frozenset(
    {
        "channels",
        "timebase",
        "delay",
        "acquire_type",
        "interpolation",
        "memory_management",
        "memory_depth",
        "trigger",
        "trigger_mode",
        "vertical",
    }
)
#: ``configure()`` accepts panel-native keys (SDS6204L SCPI vocabulary) and
#: the shared physical vocabulary documented on
#: :class:`~cetal_scopes.scopes.base.Scope`, additively -- every existing
#: panel key keeps working exactly as before.
_CONFIG_KEYS = _PANEL_KEYS | PHYSICAL_KEYS

# Timebase enumeration: :WAVeform:PREamble? stores the index, not seconds/div.
# Empirically verified against an SDS6204L (firmware 18.36.11.2.0.3.7): the
# sequence starts at 100 ps, one step below the 200 ps that the programming
# guide's table begins at, so the guide's indices are off by one.
TDIV_ENUM: tuple[float, ...] = (
    100e-12,
    200e-12,
    500e-12,
    1e-9,
    2e-9,
    5e-9,
    10e-9,
    20e-9,
    50e-9,
    100e-9,
    200e-9,
    500e-9,
    1e-6,
    2e-6,
    5e-6,
    10e-6,
    20e-6,
    50e-6,
    100e-6,
    200e-6,
    500e-6,
    1e-3,
    2e-3,
    5e-3,
    10e-3,
    20e-3,
    50e-3,
    100e-3,
    200e-3,
    500e-3,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
    1000.0,
)


@dataclass(frozen=True)
class WaveDesc:
    """Scaling fields parsed from the 346-byte ``WAVEDESC`` preamble."""

    comm_type: int
    comm_order: int
    payload_bytes: int
    frame_points: int
    first_point: int
    data_interval: int
    read_frames: int
    sum_frames: int
    vdiv: float
    voffset: float
    code_per_div: float
    adc_bit: int
    interval: float
    delay: float
    tdiv_index: int
    probe: float
    timebase: float

    @property
    def vdiv_scaled(self) -> float:
        """Vertical scale in volts/div, including probe attenuation."""
        return self.vdiv * self.probe

    @property
    def voffset_scaled(self) -> float:
        """Vertical offset in volts, including probe attenuation."""
        return self.voffset * self.probe


_FIELD_OFFSETS: tuple[tuple[str, int, str], ...] = (
    ("comm_type", 0x20, "h"),
    ("comm_order", 0x22, "h"),
    ("payload_bytes", 0x3C, "i"),
    ("frame_points", 0x74, "i"),
    ("first_point", 0x84, "i"),
    ("data_interval", 0x88, "i"),
    ("read_frames", 0x90, "i"),
    ("sum_frames", 0x94, "i"),
    ("vdiv", 0x9C, "f"),
    ("voffset", 0xA0, "f"),
    ("code_per_div", 0xA4, "f"),
    ("adc_bit", 0xAC, "h"),
    ("interval", 0xB0, "f"),
    ("delay", 0xB4, "d"),
    ("tdiv_index", 0x144, "h"),
    ("probe", 0x148, "f"),
)


def _strip_binary_header(data: bytes) -> bytes:
    """Remove an IEEE 488.2 ``#N<length>`` header if one is present."""
    start = data.find(b"#")
    if start == -1:
        return data
    header = data[start + 1 : start + 2]
    if not header.isdigit():
        raise ValueError(f"malformed IEEE 488.2 header in {data[:48]!r}")
    payload_start = start + 2 + int(header)
    if payload_start > len(data):
        raise ValueError(f"truncated IEEE 488.2 header in {data[:48]!r}")
    return data[payload_start:]


def parse_wavedesc(raw: bytes) -> WaveDesc:
    """Parse a ``:WAVeform:PREamble?`` response into a :class:`WaveDesc`.

    Parameters
    ----------
    raw : bytes
        The preamble payload. The transport has already removed any IEEE 488.2
        binary header, so the 346-byte descriptor starts at the first byte; do
        not strip again (the payload may itself contain a ``#`` byte).

    Returns
    -------
    WaveDesc
        The decoded descriptor.

    Raises
    ------
    ValueError
        If the payload is too short to contain the descriptor.
    """
    descriptor = raw
    if len(descriptor) < _WAVEDESC_LENGTH:
        raise ValueError(
            f"WAVEDESC payload must be at least {_WAVEDESC_LENGTH} bytes, "
            f"got {len(descriptor)}"
        )
    values: dict[str, int | float] = {}
    for name, offset, fmt in _FIELD_OFFSETS:
        values[name] = struct.unpack_from(f"<{fmt}", descriptor, offset)[0]

    tdiv_index = int(values["tdiv_index"])
    timebase = TDIV_ENUM[tdiv_index] if tdiv_index < len(TDIV_ENUM) else 1e-3

    return WaveDesc(
        comm_type=int(values["comm_type"]),
        comm_order=int(values["comm_order"]),
        payload_bytes=int(values["payload_bytes"]),
        frame_points=int(values["frame_points"]),
        first_point=int(values["first_point"]),
        data_interval=int(values["data_interval"]),
        read_frames=int(values["read_frames"]),
        sum_frames=int(values["sum_frames"]),
        vdiv=float(values["vdiv"]),
        voffset=float(values["voffset"]),
        code_per_div=float(values["code_per_div"]),
        adc_bit=int(values["adc_bit"]),
        interval=float(values["interval"]),
        delay=float(values["delay"]),
        tdiv_index=tdiv_index,
        probe=float(values["probe"]),
        timebase=timebase,
    )


def codes_to_volts(
    codes: NDArray[Any], desc: WaveDesc, *, shift: int = 0
) -> NDArray[np.float64]:
    """Convert signed ADC codes to volts using the preamble scaling.

    Implements ``V = code * 2**shift * (vdiv * probe / code_per_div)
    - voffset * probe``. ``shift`` left-aligns narrow transfers: when an
    ``adc_bit > 8`` scope is read in ``BYTE`` width, ``code_per_div`` still
    describes the full word, so the high byte must be scaled by ``2**8``.
    """
    if desc.code_per_div == 0:
        raise ValueError("code_per_div is zero; waveform cannot be scaled")
    values = codes.astype(np.float64)
    if shift:
        values = values * float(1 << shift)
    return values * (desc.vdiv_scaled / desc.code_per_div) - desc.voffset_scaled


def time_origin(desc: WaveDesc, grid_num: int = GRID_NUM) -> float:
    """Return the time of the first sample in seconds."""
    return desc.delay - desc.timebase * grid_num / 2.0


#: SDS6204L vertical-scale ladder (V/div), 1-2-5 sequence.
VDIV_LADDER: tuple[float, ...] = (
    0.0005,
    0.001,
    0.002,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
)


def snap_vdiv(value: float) -> float:
    """Snap ``value`` up to the next valid V/div step (clamped to the ends)."""
    for step in VDIV_LADDER:
        if step >= value:
            return step
    return VDIV_LADDER[-1]


def snap_timebase(seconds_per_div: float) -> float:
    """Snap ``seconds_per_div`` up to the next valid s/div step in :data:`TDIV_ENUM`."""
    for step in TDIV_ENUM:
        if step >= seconds_per_div:
            return step
    return TDIV_ENUM[-1]


# Memory-depth enumeration for ``:ACQuire:MDEPth``. NOT independently
# hardware-verified: the manual excerpt in docs/scopes/SDS6204L/code.md only
# gives these as *example* values for the command's syntax, not a complete,
# channel-count-aware table. Confirm the full ladder against a real SDS6204L
# before relying on exact depth selection for a demanding acquisition.
MDEPTH_ENUM: tuple[tuple[int, str], ...] = (
    (10_000, "10k"),
    (1_000_000, "1M"),
    (10_000_000, "10M"),
    (100_000_000, "100M"),
    (250_000_000, "250M"),
    (1_000_000_000, "1G"),
)


#: Interpolation state this driver asserts unless ``configure()`` overrides it.
#: ``sin(x)/x`` reconstruction (Siglent's "enhanced sample rate", ESR) invents
#: samples between the ones the ADC actually took, which is a post-processing
#: choice rather than an acquisition one -- so it is off by default, to be
#: applied deliberately downstream where it is visible in the code.
DEFAULT_INTERPOLATION = "OFF"

#: Native ADC rate, per channel (manual: 5 GSa/s at each of the four
#: channels, independent of how many are on -- each has its own converter).
#: The 10 GSa/s "ESR" headline is :data:`DEFAULT_INTERPOLATION` turned on,
#: which reconstructs points rather than measuring them, so it is not a rate
#: this driver will ever ask for.
MAX_RATE_HZ = 5e9


def snap_mdepth(samples: int) -> str:
    """Snap a sample count up to the next :data:`MDEPTH_ENUM` step (clamped to the ends)."""
    for value, label in MDEPTH_ENUM:
        if value >= samples:
            return label
    return MDEPTH_ENUM[-1][1]


#: ``:ACQuire:MMANagement`` modes, by the prefix the instrument answers with.
MEMORY_MANAGEMENT_MODES: tuple[str, ...] = ("AUTO", "FMDepth", "FSRate")


def _normalize_memory_management(value: object) -> str:
    """Normalize a memory-management mode to its SCPI spelling."""
    token = str(value).strip().upper()
    for mode in MEMORY_MANAGEMENT_MODES:
        if token in {mode.upper(), mode.upper()[:4]}:
            return mode
    raise ValueError(
        f"unsupported memory management {value!r}; expected one of "
        f"{list(MEMORY_MANAGEMENT_MODES)}"
    )


def _normalize_interpolation(value: object) -> str:
    """Normalize an ``interpolation`` setting to the SCPI ``ON``/``OFF`` token.

    ``OFF`` is linear reconstruction between acquired samples; ``ON`` is the
    ``sin(x)/x`` reconstruction that Siglent markets as the "enhanced sample
    rate" (ESR). Neither adds measured information, so this driver defaults
    to ``OFF`` -- see :data:`DEFAULT_INTERPOLATION`.
    """
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    token = str(value).strip().upper()
    if token in {"ON", "SINX", "SIN(X)/X"}:
        return "ON"
    if token in {"OFF", "LINEAR", "LIN"}:
        return "OFF"
    raise ValueError(
        f"unsupported interpolation {value!r}; expected 'ON' (sin(x)/x) or "
        "'OFF' (linear)"
    )


def _impedance_ohms_to_string(ohms: float) -> str:
    """Convert the physical-vocabulary ``impedance`` (ohms) to the SCPI alias."""
    if float(ohms) == 50.0:
        return "FIFTy"
    if float(ohms) == 1_000_000.0:
        return "ONEMeg"
    raise ValueError(
        f"unsupported impedance {ohms!r} ohm; expected 50 or 1000000 (1 Mohm)"
    )


@dataclass(frozen=True)
class AcquisitionPlan:
    """What :meth:`SiglentSDS6204L.set_acquisition` actually requested.

    ``sample_rate``/``record_length`` are honored via ``timebase`` (s/div)
    and ``memory_depth``; the scope's own readback (:class:`WaveDesc`, at
    acquire time) is the source of truth for what was actually achieved --
    both are rounded to the nearest achievable step, and sample rate in
    particular is not directly settable on this instrument.
    """

    requested_sample_rate: float | None
    requested_record_length: int | None
    timebase: float
    memory_depth: str
    delay: float


def _normalize_channel(channel: str) -> str:
    name = channel.strip().upper()
    if name.startswith("CH"):
        name = "C" + name[2:]
    if name not in _VALID_ANALOG_CHANNELS:
        raise ValueError(
            f"unsupported analog channel {channel!r}; expected one of "
            f"{_VALID_ANALOG_CHANNELS}"
        )
    return name


def _normalize_channels(channels: Sequence[str]) -> tuple[str, ...]:
    names = tuple(_normalize_channel(channel) for channel in channels)
    if not names:
        raise ValueError("at least one channel is required")
    if len(set(names)) != len(names):
        raise ValueError(f"channels must be unique, got {names!r}")
    return names


_VALID_SAMPLE_WIDTHS = ("BYTE", "WORD")


def _normalize_sample_width(width: str) -> str:
    name = width.strip().upper()
    if name not in _VALID_SAMPLE_WIDTHS:
        raise ValueError(
            f"unsupported sample width {width!r}; expected one of "
            f"{_VALID_SAMPLE_WIDTHS}"
        )
    return name


_IMPEDANCE_ALIASES = {
    "onemeg": "ONEMeg",
    "1m": "ONEMeg",
    "1meg": "ONEMeg",
    "1mohm": "ONEMeg",
    "1megohm": "ONEMeg",
    "1e6": "ONEMeg",
    "fifty": "FIFTy",
    "50": "FIFTy",
    "50ohm": "FIFTy",
    "50r": "FIFTy",
}


def _normalize_impedance(value: str) -> str:
    key = value.strip().lower().replace(" ", "").replace("Ω", "ohm")
    if key in _IMPEDANCE_ALIASES:
        return _IMPEDANCE_ALIASES[key]
    raise ValueError(
        f"unsupported impedance {value!r}; expected 'ONEMeg'/'1M' or 'FIFTy'/'50'"
    )


_TOP_TO_VERTICAL_KEY = {
    "range": "scale",
    "offset": "offset",
    "coupling": "coupling",
    "impedance": "impedance",
}


def _check_physical_panel_conflicts(
    settings: Mapping[str, Any], channels: Sequence[str]
) -> None:
    """Reject a ``configure()`` call that mixes a physical key with its panel alias.

    Both spellings write the same instrument setting, so there is no honest
    precedence rule between them -- see the collision-rule discussion in
    :mod:`cetal_scopes.scopes._settings`.
    """
    if ("sample_rate" in settings or "record_length" in settings) and (
        "timebase" in settings or "memory_depth" in settings
    ):
        raise ValueError(
            "cannot mix physical keys ('sample_rate'/'record_length') with "
            "panel keys ('timebase'/'memory_depth') in one configure() call"
        )
    if "pretrigger" in settings and "delay" in settings:
        raise ValueError(
            "cannot mix physical 'pretrigger' with panel 'delay' in one "
            "configure() call"
        )

    vertical = settings.get("vertical")
    if not isinstance(vertical, Mapping):
        return
    for physical_key, vertical_key in _TOP_TO_VERTICAL_KEY.items():
        if physical_key not in settings:
            continue
        value = settings[physical_key]
        touched = set(value) if isinstance(value, Mapping) else set(channels)
        for channel, params in vertical.items():
            if (
                channel in touched
                and isinstance(params, Mapping)
                and vertical_key in params
            ):
                raise ValueError(
                    f"cannot mix physical {physical_key!r} with panel "
                    f"'vertical[{channel!r}][{vertical_key!r}]' for the same "
                    f"channel in one configure() call"
                )


class _Transport(Protocol):
    """Byte transport to an instrument (real socket or test double)."""

    def open(self) -> None: ...

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...

    def query_block(self, command: str) -> bytes: ...

    def close(self) -> None: ...


class _SocketTransport:
    """Raw-socket SCPI transport (TCP port 5025, newline-terminated)."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = bytearray()

    def open(self) -> None:
        if self._socket is not None:
            return
        self._socket = socket.create_connection(
            (self._host, self._port), timeout=self._timeout
        )
        self._buffer.clear()

    def write(self, command: str) -> None:
        sock = self._require_socket()
        sock.sendall((command + "\n").encode("ascii"))

    def query(self, command: str) -> str:
        self.write(command)
        return self._read_line()

    def query_block(self, command: str) -> bytes:
        self.write(command)
        return self._read_block()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._buffer.clear()

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("transport is not open")
        return self._socket

    def _receive(self) -> None:
        chunk = self._require_socket().recv(65536)
        if not chunk:
            raise ConnectionError("instrument closed the connection")
        self._buffer.extend(chunk)

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            self._receive()
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def _read_line_bytes(self) -> bytes:
        while b"\n" not in self._buffer:
            self._receive()
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        return bytes(line)

    def _read_line(self) -> str:
        while True:
            text = self._read_line_bytes().decode("ascii", errors="replace").strip()
            if text:
                return text

    def _read_block(self) -> bytes:
        while True:
            while self._buffer[:1] in (b"\n", b"\r"):
                del self._buffer[:1]
            if b"#" in self._buffer:
                break
            if b"\n" in self._buffer:
                return self._read_line_bytes()
            self._receive()
        hash_at = self._buffer.find(b"#")
        del self._buffer[:hash_at]
        self._read_exact(1)
        digit_bytes = self._read_exact(1)
        if not digit_bytes.isdigit():
            raise ValueError(f"invalid binary block digit count {digit_bytes!r}")
        size = int(self._read_exact(int(digit_bytes)))
        payload = self._read_exact(size)
        if self._buffer[:1] in (b"\n", b"\r"):
            del self._buffer[:1]
        return payload


class _VisaTransport:
    """PyVISA-backed transport, used for USB and VXI-11 resource strings.

    ``pyvisa`` is imported lazily so that the raw-socket path never needs it.
    """

    def __init__(self, resource: str, timeout: float) -> None:
        self._resource = resource
        self._timeout_ms = int(timeout * 1000)
        self._resource_manager: Any = None
        self._instrument: Any = None

    def open(self) -> None:
        if self._instrument is not None:
            return
        import pyvisa

        self._resource_manager = pyvisa.ResourceManager()
        instrument = self._resource_manager.open_resource(self._resource)
        instrument.timeout = self._timeout_ms
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        try:
            instrument.chunk_size = 20 * 1024 * 1024
        except (AttributeError, ValueError):
            pass
        self._instrument = instrument

    def write(self, command: str) -> None:
        self._require_instrument().write(command)

    def query(self, command: str) -> str:
        return str(self._require_instrument().query(command)).strip()

    def query_block(self, command: str) -> bytes:
        self.write(command)
        return _strip_binary_header(self._require_instrument().read_raw())

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None
        if self._resource_manager is not None:
            self._resource_manager.close()
            self._resource_manager = None

    def _require_instrument(self) -> Any:
        if self._instrument is None:
            raise RuntimeError("transport is not open")
        return self._instrument


def _host_from_address(address: str) -> str:
    """Extract the host from a plain address or a VISA TCPIP resource string."""
    if "::" not in address:
        return address
    parts = address.split("::")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return address


def _port_from_address(address: str, default: int) -> int:
    """Extract the port from a VISA TCPIP resource string, if present."""
    if "::" not in address:
        return default
    parts = address.split("::")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return default


def _make_transport(address: str, port: int, timeout: float) -> _Transport:
    """Choose a transport based on the address style."""
    upper = address.strip().upper()
    if upper.startswith("USB") or "::INSTR" in upper:
        return _VisaTransport(address, timeout)
    return _SocketTransport(
        _host_from_address(address), _port_from_address(address, port), timeout
    )


class SiglentSDS6204L(Scope):
    """Driver for a Siglent SDS6204L oscilloscope over SCPI.

    Parameters
    ----------
    address : str, optional
        Instrument IP address (raw socket), or a PyVISA resource string such as
        ``USB0::0xF4EC::...::INSTR`` or ``TCPIP0::<ip>::inst0::INSTR``.
        Defaults to ``192.168.5.193``.
    channels : sequence of str, optional
        Analog channels to acquire, e.g. ``("C1", "C2")``. ``"CH1"`` and
        ``"C1"`` are equivalent. Defaults to ``("C1",)``.
    sample_width : str, optional
        Waveform transfer width, ``"BYTE"`` or ``"WORD"``. Defaults to
        ``"WORD"``: the 16-bit path carries the full acquired waveform, whereas
        ``"BYTE"`` is a coarse truncation (the top byte, ~94 mV per step at
        1 V/div) that discards small signals. Note the 16-bit path also carries
        a deterministic ``k * fs / 256`` comb; see
        :func:`cetal_scopes.analysis.remove_adc_comb`.
    streaming : bool, optional
        Keep the scope running between acquisitions instead of stopping it for
        every fetch. Defaults to ``False`` (one coherent snapshot per call).
        Enable it for a live view: the scope is started on the first
        :meth:`acquire` and left running, so the front panel does not stutter
        between ``Auto`` and ``Stop``. Intended for a free-running
        :attr:`trigger_mode` such as ``"AUTO"``.
    port : int, optional
        Raw-socket SCPI port. Defaults to ``5025``.
    timeout : float, optional
        Socket timeout in seconds.
    acquire_timeout : float, optional
        Maximum seconds to wait for a triggered acquisition to finish.
    trigger_mode : str, optional
        Sweep mode used by :meth:`acquire` (``"SINGle"``, ``"AUTO"``,
        ``"NORMal"``). Defaults to ``"SINGle"``; use ``"AUTO"`` on the bench
        when no trigger signal is present.
    grid_num : int, optional
        Horizontal grid divisions; ``10`` for SDS benchtop models.
    transport : _Transport, optional
        Injectable transport, primarily for tests.
    """

    supports_staged_acquisition = True
    supports_force_trigger = True

    def __init__(
        self,
        address: str = DEFAULT_ADDRESS,
        *,
        channels: Sequence[str] = ("C1",),
        sample_width: str = "WORD",
        streaming: bool = False,
        port: int = DEFAULT_PORT,
        timeout: float = 5.0,
        acquire_timeout: float = 10.0,
        trigger_mode: str = "SINGle",
        grid_num: int = GRID_NUM,
        transport: _Transport | None = None,
    ) -> None:
        self._address = address
        self._port = port
        self._timeout = timeout
        self._acquire_timeout = acquire_timeout
        self._trigger_mode = trigger_mode
        self._grid_num = grid_num
        self._channels = _normalize_channels(channels)
        self._sample_width = _normalize_sample_width(sample_width)
        self._streaming = streaming
        self._running = False
        self._transport = transport
        self._owns_transport = transport is None
        self._connected = False
        self._idn = ""
        self._interpolation = DEFAULT_INTERPOLATION
        self._trigger_source: str | None = None
        self._trigger_level: float | None = None
        self._trigger_slope: str | None = None
        self._last_sample_rate: float | None = None
        self._last_window_s: float | None = None
        self._last_record_length: int | None = None
        self._last_timebase: float | None = None
        self._last_memory_depth: str | None = None
        self._last_delay: float = 0.0
        self._last_acquisition_plan: AcquisitionPlan | None = None
        self._armed = False
        self._arm_count: int | None = None
        self._trigger_mode_dirty = False
        self._poll_interval = 0.05

    @property
    def idn(self) -> str:
        """Instrument identification string from ``*IDN?``."""
        return self._idn

    @property
    def trigger_mode(self) -> str:
        """Sweep mode used by :meth:`acquire`."""
        return self._trigger_mode

    @trigger_mode.setter
    def trigger_mode(self, value: str) -> None:
        self._trigger_mode = value

    @property
    def channels(self) -> tuple[str, ...]:
        """Analog channels fetched by :meth:`acquire`."""
        return self._channels

    @property
    def sample_width(self) -> str:
        """Waveform transfer width (``"BYTE"`` or ``"WORD"``)."""
        return self._sample_width

    @property
    def streaming(self) -> bool:
        """Whether :meth:`acquire` leaves the scope running between fetches."""
        return self._streaming

    @property
    def last_acquisition(self) -> AcquisitionPlan | None:
        """The most recent :meth:`set_acquisition` result, or ``None`` if never called."""
        return self._last_acquisition_plan

    @classmethod
    def max_sample_rate(cls, n_channels: int) -> float | None:
        """:data:`MAX_RATE_HZ`, whatever the channel count.

        The four channels do not share a converter, so unlike an interleaved
        digitiser this scope gives up no rate for using all of them.
        """
        return MAX_RATE_HZ

    @classmethod
    def min_record_length(cls, n_channels: int) -> int | None:
        """The shallowest step of :data:`MDEPTH_ENUM`.

        10 kpt, which at the 5 GS/s ceiling is a 2 us window -- and lands on
        a depth step exactly, so the rate comes out as asked rather than
        being inflated by a rounded-up depth.
        """
        return MDEPTH_ENUM[0][0]

    def connect(self) -> None:
        """Open the socket and identify the instrument."""
        if self._connected:
            return
        if self._transport is None:
            self._transport = _make_transport(self._address, self._port, self._timeout)
        self._transport.open()
        self._connected = True
        try:
            self._idn = self._query("*IDN?")
        except Exception:
            self._connected = False
            raise

    def close(self) -> None:
        """Close the transport. Safe to call repeatedly."""
        if self._transport is not None:
            self._transport.close()
            if self._owns_transport:
                self._transport = None
        self._connected = False
        self._running = False

    def configure(self, settings: Mapping[str, Any]) -> None:
        """Apply acquisition settings.

        Accepts this driver's panel-native keys -- ``channels``,
        ``timebase``, ``delay``, ``acquire_type``, ``interpolation``,
        ``memory_depth``,
        ``trigger`` (mapping with ``source``/``level``/``slope``), and
        ``vertical`` (mapping of channel name to
        ``scale``/``offset``/``coupling``/``probe``/``bandwidth_limit``/
        ``impedance``) -- additively alongside the shared physical
        vocabulary documented on :class:`~cetal_scopes.scopes.base.Scope`
        (``sample_rate``, ``record_length``, ``pretrigger``, ``range``,
        ``offset``, ``coupling``, ``impedance``; ``channels`` and
        ``trigger`` are identical in both vocabularies). Mixing a physical
        key with the panel key it aliases in one call (e.g. ``sample_rate``
        with ``timebase``, or ``range`` with ``vertical[ch]["scale"]`` for
        the same channel) raises :class:`ValueError`.
        """
        unknown = set(settings) - _CONFIG_KEYS
        if unknown:
            raise ValueError(
                f"unsupported setting(s) {sorted(unknown)!r}; expected a "
                f"subset of {sorted(_CONFIG_KEYS)!r}"
            )

        if "channels" in settings:
            self._channels = _normalize_channels(settings["channels"])

        _check_physical_panel_conflicts(settings, self._channels)
        physical = normalize_physical(settings, channels=self._channels)

        if "timebase" in settings or "delay" in settings:
            self.set_timebase(
                scale=settings.get("timebase"), delay=settings.get("delay")
            )
        if "acquire_type" in settings:
            self.set_acquire_type(str(settings["acquire_type"]))
        if "interpolation" in settings:
            self._interpolation = _normalize_interpolation(settings["interpolation"])
        self.set_interpolation(self._interpolation)
        # Before memory_depth: in AUTO the instrument ignores depth writes.
        if "memory_management" in settings:
            self.set_memory_management(str(settings["memory_management"]))
        if "memory_depth" in settings:
            self.set_memory_depth(str(settings["memory_depth"]))
        if "trigger_mode" in settings:
            self._trigger_mode = str(settings["trigger_mode"])

        trigger = settings.get("trigger")
        if trigger is not None:
            if not isinstance(trigger, Mapping):
                raise TypeError("trigger must be a mapping")
            self.set_edge_trigger(
                source=trigger.get("source"),
                level=trigger.get("level"),
                slope=trigger.get("slope"),
            )

        vertical = settings.get("vertical")
        if vertical is not None:
            if not isinstance(vertical, Mapping):
                raise TypeError("vertical must be a mapping")
            for channel, params in vertical.items():
                if not isinstance(params, Mapping):
                    raise TypeError(f"vertical[{channel!r}] must be a mapping")
                self.set_channel(
                    str(channel),
                    scale=params.get("scale"),
                    offset=params.get("offset"),
                    coupling=params.get("coupling"),
                    probe=params.get("probe"),
                    bandwidth_limit=params.get("bandwidth_limit"),
                    impedance=params.get("impedance"),
                )

        if (
            physical.sample_rate is not None
            or physical.record_length is not None
            or physical.pretrigger is not None
        ):
            self.set_acquisition(
                sample_rate=physical.sample_rate,
                record_length=physical.record_length,
                pretrigger=physical.pretrigger,
            )

        if physical.range or physical.offset or physical.coupling or physical.impedance:
            touched: set[str] = set()
            for mapping in (
                physical.range,
                physical.offset,
                physical.coupling,
                physical.impedance,
            ):
                if mapping:
                    touched |= set(mapping)
            for channel in touched:
                self.set_channel(
                    channel,
                    scale=(
                        snap_vdiv(physical.range[channel] / (VGRID_NUM / 2))
                        if physical.range and channel in physical.range
                        else None
                    ),
                    offset=(physical.offset.get(channel) if physical.offset else None),
                    coupling=(
                        physical.coupling.get(channel) if physical.coupling else None
                    ),
                    impedance=(
                        _impedance_ohms_to_string(physical.impedance[channel])
                        if physical.impedance and channel in physical.impedance
                        else None
                    ),
                )

    def set_acquisition(
        self,
        *,
        sample_rate: float | None = None,
        record_length: int | None = None,
        pretrigger: float | None = None,
    ) -> AcquisitionPlan:
        """Configure sample rate, record length, and/or pretrigger.

        Honored via ``timebase`` (s/div) and ``memory_depth``: together they
        determine the acquisition window (``record_length / sample_rate``)
        that both are derived from. ``sample_rate`` and ``record_length``
        must both be known to compute it -- give the one that is changing
        and the other is reused from the last :meth:`set_acquisition` call
        (an error if neither call ever set it).

        ``pretrigger`` alone is valid once a window has already been
        established (by this call or an earlier one); it is a sample count
        (``int``) or a fraction of the record (``float`` in ``[0, 1]``).

        Returns
        -------
        AcquisitionPlan
            The requested values alongside what was actually written. The
            scope's own readback (:class:`WaveDesc`, at :meth:`acquire`
            time) remains the source of truth for what was actually
            achieved -- sample rate in particular is not directly settable.
        """
        if sample_rate is None and record_length is None and pretrigger is None:
            raise ValueError(
                "at least one of sample_rate, record_length, or pretrigger is required"
            )

        if sample_rate is not None or record_length is not None:
            effective_rate = (
                sample_rate if sample_rate is not None else self._last_sample_rate
            )
            effective_length = (
                record_length if record_length is not None else self._last_record_length
            )
            if effective_rate is None or effective_length is None:
                raise ValueError(
                    "sample_rate and record_length must both be known: give "
                    "the missing one now, or set both together in an "
                    "earlier set_acquisition call, since the achievable "
                    "timebase depends on both"
                )
            window_s = effective_length / effective_rate
            tdiv = snap_timebase(window_s / self._grid_num)
            window_s = tdiv * self._grid_num
            depth_label = snap_mdepth(round(effective_rate * window_s))
            self.set_timebase(scale=tdiv)
            self.set_memory_depth(depth_label)
            self._last_sample_rate = effective_rate
            self._last_record_length = effective_length
            self._last_window_s = window_s
            self._last_timebase = tdiv
            self._last_memory_depth = depth_label

        if pretrigger is not None:
            if self._last_window_s is None or self._last_record_length is None:
                raise ValueError(
                    "pretrigger requires sample_rate and record_length to "
                    "have been set first (in this call or an earlier one)"
                )
            pretrig_samples = pretrigger_samples(pretrigger, self._last_record_length)
            fraction = pretrig_samples / self._last_record_length
            delay = self._last_window_s * (0.5 - fraction)
            self.set_timebase(delay=delay)
            self._last_delay = delay

        if self._last_timebase is None or self._last_memory_depth is None:
            raise RuntimeError(  # pragma: no cover - unreachable, see guards above
                "internal: set_acquisition completed without a resolved timebase"
            )
        self._last_acquisition_plan = AcquisitionPlan(
            requested_sample_rate=sample_rate,
            requested_record_length=record_length,
            timebase=self._last_timebase,
            memory_depth=self._last_memory_depth,
            delay=self._last_delay,
        )
        return self._last_acquisition_plan

    def set_channel(
        self,
        channel: str,
        *,
        scale: float | None = None,
        offset: float | None = None,
        coupling: str | None = None,
        probe: float | None = None,
        bandwidth_limit: str | None = None,
        impedance: str | None = None,
    ) -> None:
        """Configure one analog channel."""
        index = _normalize_channel(channel)[1:]
        if scale is not None:
            self._write(f":CHANnel{index}:SCALe {float(scale):.6g}")
        if offset is not None:
            self._write(f":CHANnel{index}:OFFSet {float(offset):.6g}")
        if coupling is not None:
            self._write(f":CHANnel{index}:COUPling {coupling}")
        if probe is not None:
            self._write(f":CHANnel{index}:PROBe VALue,{float(probe):.6g}")
        if bandwidth_limit is not None:
            self._write(f":CHANnel{index}:BWLimit {bandwidth_limit}")
        if impedance is not None:
            self._write(f":CHANnel{index}:IMPedance {_normalize_impedance(impedance)}")

    def set_timebase(
        self, *, scale: float | None = None, delay: float | None = None
    ) -> None:
        """Set the horizontal timebase scale (s/div) and/or delay (s)."""
        if scale is not None:
            self._write(f":TIMebase:SCALe {float(scale):.6g}")
        if delay is not None:
            self._write(f":TIMebase:DELay {float(delay):.6g}")

    def set_edge_trigger(
        self,
        *,
        source: str | None = None,
        level: float | None = None,
        slope: str | None = None,
    ) -> None:
        """Configure an edge trigger."""
        self._write(":TRIGger:TYPE EDGE")
        if source is not None:
            self._write(f":TRIGger:EDGE:SOURce {source}")
            self._trigger_source = str(source)
        if level is not None:
            self._write(f":TRIGger:EDGE:LEVel {float(level):.6g}")
            self._trigger_level = float(level)
        if slope is not None:
            self._write(f":TRIGger:EDGE:SLOPe {slope}")
            self._trigger_slope = str(slope)

    def trigger_level(self) -> float:
        """Return the edge-trigger level in volts, as the instrument reports it.

        The level is clamped by the source channel's vertical range (roughly
        ``+/-4.5 * V/div`` around its offset), so reading it back after
        :meth:`set_edge_trigger` reveals a silently clamped request.
        """
        return float(self._query(":TRIGger:EDGE:LEVel?"))

    def set_acquire_type(self, acquire_type: str) -> None:
        """Set the acquisition type (e.g. ``NORMal``, ``AVERage,16``)."""
        self._write(f":ACQuire:TYPE {acquire_type}")

    def set_interpolation(self, state: str) -> None:
        """Set waveform interpolation (``ON`` = ``sin(x)/x``, ``OFF`` = linear)."""
        self._write(f":ACQuire:INTerpolation {_normalize_interpolation(state)}")

    def sample_rate(self) -> float:
        """Query the sample rate the instrument will actually use (S/s).

        Sample rate is not directly settable on this scope -- it falls out of
        timebase and memory depth -- so this readback, not the requested
        value, is what the acquisition will run at.
        """
        return float(self._query(":ACQuire:SRATe?"))

    def event_status(self) -> int:
        """Read and clear ``*ESR?``, the standard event status register.

        Bit 4 (value 16) is a command error: the instrument did not
        understand the last command, or would not take the parameter. Since a
        setting write is not acknowledged in any other way, this is how a
        silently-discarded write is told apart from one that landed and had
        no visible effect. Reading clears the register, so read it once
        before the write you want to test.
        """
        return int(float(self._query("*ESR?")))

    def raw_write(self, command: str) -> None:
        """Send a command verbatim. For bench probing, not for drivers.

        The driver's own methods are the supported path; this exists so a
        probe can try spellings the driver does not know about.
        """
        self._write(command)

    def raw_query(self, command: str) -> str:
        """Send a query verbatim and return the reply. For bench probing."""
        return self._query(command)

    def set_memory_management(self, mode: str) -> None:
        """Set how the scope divides a window between sample rate and depth.

        ``AUTO`` lets the instrument choose both -- and then it *ignores*
        :meth:`set_memory_depth` entirely, silently, which is how a depth
        request can have no effect at all. ``FMDepth`` (fixed memory depth)
        holds the depth and lets the rate follow; ``FSRate`` (fixed sampling
        rate) holds the rate and lets the depth follow.
        """
        self._write(f":ACQuire:MMANagement {_normalize_memory_management(mode)}")

    def memory_management(self) -> str:
        """The memory-management mode the instrument reports."""
        return self._query(":ACQuire:MMANagement?")

    def memory_depth(self) -> str:
        """The memory depth the instrument reports, as its own label (e.g. ``1M``).

        A rejected :meth:`set_memory_depth` leaves the previous value in
        place rather than raising, so reading this back is how you find out
        which depths the instrument actually offers.
        """
        return self._query(":ACQuire:MDEPth?")

    def timebase(self) -> float:
        """The timebase the instrument reports, in seconds per division."""
        return float(self._query(":TIMebase:SCALe?"))

    def set_memory_depth(self, depth: str) -> None:
        """Set the maximum memory depth (e.g. ``10k``, ``1M``, ``10M``)."""
        self._write(f":ACQuire:MDEPth {depth}")

    def arm(self) -> None:
        """Set the sweep mode, latch the acquisition counter, and run.

        Returns as soon as the scope is running; it does not wait for the
        trigger. Note that ``:TRIGger:RUN`` is asynchronous, so a return here
        does not prove the front end has finished arming -- poll
        :meth:`trigger_status` for the instrument's own view.
        """
        self._require_connected()
        if self._armed:
            raise RuntimeError("already armed; call fetch() or abort() first")
        if self._streaming:
            self._ensure_running()
            self._arm_count = self._acquisition_count()
        else:
            self._write(":TRIGger:STOP")
            self._write(f":TRIGger:MODE {self._trigger_mode}")
            self._trigger_mode_dirty = False
            self._arm_count = self._acquisition_count()
            self._write(":TRIGger:RUN")
        self._armed = True

    def wait(self, timeout: float | None = None) -> bool:
        """Poll ``:ACQuire:NUMACq?`` until it advances past the armed count.

        Returns ``False`` on expiry without raising, leaving the scope armed so
        the call can be repeated.
        """
        if not self._armed or self._arm_count is None:
            raise RuntimeError("wait() called before arm()")
        limit = self._acquire_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            if self._acquisition_count() > self._arm_count:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self._poll_interval, remaining))

    def fetch(self) -> Capture:
        """Fetch the completed acquisition's channels and disarm.

        Returns
        -------
        Capture
            Voltage and raw-code arrays shaped ``(n_channels, n_samples)``,
            sharing the first channel's timebase.
        """
        self._require_connected()
        if not self._armed:
            raise RuntimeError("fetch() called before a completed wait()")
        if not self._streaming:
            self._write(":TRIGger:STOP")
        self._armed = False
        self._arm_count = None

        volts_rows: list[NDArray[np.float64]] = []
        raw_rows: list[NDArray[Any]] = []
        names: list[str] = []
        t0: float | None = None
        dt: float | None = None

        for channel in self._channels:
            codes, desc = self._fetch_codes(channel)
            shift = 8 if self._sample_width == "BYTE" and desc.adc_bit > 8 else 0
            volts_rows.append(codes_to_volts(codes, desc, shift=shift))
            raw_rows.append(codes)
            names.append(channel)
            if t0 is None:
                t0 = time_origin(desc, self._grid_num)
                dt = desc.interval

        if t0 is None or dt is None:  # pragma: no cover - channels is non-empty
            raise RuntimeError("no channels configured")

        n_samples = min(len(row) for row in raw_rows)
        volts = np.vstack([row[:n_samples] for row in volts_rows])
        raw = np.vstack([row[:n_samples] for row in raw_rows])
        return Capture(
            volts=volts,
            t0=t0,
            dt=dt,
            channel_names=tuple(names),
            raw=raw,
            metadata=self._scope_metadata(dt, n_samples),
        )

    def abort(self) -> None:
        """Disarm and stop the scope. Safe when unarmed or disconnected."""
        self._armed = False
        self._arm_count = None
        if not self._connected or self._transport is None:
            return
        if self._streaming:
            # Aborting a live view must not stop the front panel.
            return
        try:
            self._write(":TRIGger:STOP")
        except OSError:
            pass

    def force_trigger(self) -> None:
        """Force one acquisition via the ``FTRIG`` sweep mode.

        The SDS6204L has no stateless force command: force trigger is a *value*
        of ``:TRIGger:MODE``, so this clobbers the configured sweep mode. The
        mode is re-asserted on the next :meth:`arm` (and, while streaming, on
        the next :meth:`_ensure_running`).

        Raises
        ------
        RuntimeError
            If the scope is not armed.
        """
        self._require_connected()
        if not self._armed:
            raise RuntimeError("force_trigger() requires an armed scope")
        self._write(":TRIGger:MODE FTRIG")
        self._trigger_mode_dirty = True

    def trigger_status(self) -> str | None:
        """Return the instrument's trigger state.

        One of ``Arm``, ``Ready``, ``Auto``, ``Trig'd``, ``Stop`` or ``Roll``,
        as the scope spells it.
        """
        return self._query(":TRIGger:STATus?").strip()

    def acquire(self) -> Capture:
        """Arm an acquisition, wait for it, and fetch the channels.

        The sweep mode comes from :attr:`trigger_mode` (default ``"SINGle"``).
        Completion is detected via the ``:ACQuire:NUMACq?`` counter.

        In the default one-shot mode the scope is stopped before and after each
        acquisition so the fetched buffer is a coherent snapshot. With
        :attr:`streaming` enabled the scope is started on the first call and
        left running, so later calls only wait for the next acquisition and
        fetch it without stopping the scope. Streaming needs a free-running
        sweep mode (e.g. ``AUTO``); in ``SINGle`` the counter stops advancing
        between triggers.

        Returns
        -------
        Capture
            Voltage and raw-code arrays shaped ``(n_channels, n_samples)``,
            sharing the first channel's timebase.
        """
        return self._acquire_staged()

    def _scope_metadata(self, dt: float, n_samples: int) -> dict[str, Any]:
        """Provenance for a fetched capture, mirroring the M5i's card metadata."""
        metadata: dict[str, Any] = {
            "instrument": "Siglent SDS6204L",
            "address": self._address,
            "idn": self._idn,
            "channels": list(self._channels),
            "sample_width": self._sample_width,
            "sample_rate_hz": 1.0 / dt if dt > 0 else None,
            "record_length": n_samples,
            "trigger_mode": self._trigger_mode,
            "trigger_source": self._trigger_source,
            "trigger_slope": self._trigger_slope,
            # Requested vs. achieved: the level is silently clamped to roughly
            # +/-4.5 * V/div of the source channel, so the readback is the only
            # honest record of what the instrument actually triggered on.
            "trigger_level_requested_v": self._trigger_level,
            "trigger_level_v": self._trigger_level_readback(),
            "streaming": self._streaming,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        plan = self._last_acquisition_plan
        if plan is not None:
            metadata["timebase_s_per_div"] = plan.timebase
            metadata["memory_depth"] = plan.memory_depth
            metadata["delay_s"] = plan.delay
            metadata["sample_rate_requested_hz"] = plan.requested_sample_rate
            metadata["record_length_requested"] = plan.requested_record_length
        return metadata

    def _trigger_level_readback(self) -> float | None:
        """The instrument's own trigger level, or ``None`` if it will not say.

        Provenance must not be able to fail a fetch that otherwise succeeded,
        so a transport or parse problem here degrades to ``None``.
        """
        if self._trigger_source is None:
            return None
        try:
            return self.trigger_level()
        except (OSError, ValueError):
            return None

    def _fetch_codes(self, channel: str) -> tuple[NDArray[Any], WaveDesc]:
        self._write(f":WAVeform:SOURce {channel}")
        self._write(":WAVeform:STARt 0")
        self._write(":WAVeform:POINt 0")
        desc = parse_wavedesc(self._query_block(":WAVeform:PREamble?"))

        # ``WAVEDESC.frame_points`` is the acquisition *memory* depth.  With
        # ``:ACQuire:MMANagement FMDepth`` it can exceed the number of *screen*
        # points that ``:WAVeform:DATA?`` will actually transfer (e.g. 2.5M vs
        # 100k).  Treat it as an upper bound and follow the payload lengths the
        # instrument hands back instead of assuming they match.
        total = desc.frame_points
        if total <= 0:
            return np.empty(0, dtype=np.int8), desc

        max_points = int(float(self._query(":WAVeform:MAXPoint?")))
        if max_points <= 0:
            max_points = total

        word = self._sample_width == "WORD"
        self._write(f":WAVeform:WIDTh {'WORD' if word else 'BYTE'}")
        if word:
            self._write(":WAVeform:BYTeorder LSB")
        dtype = np.dtype(np.int16 if word else np.int8)
        bytes_per_sample = dtype.itemsize

        chunks: list[NDArray[Any]] = []
        start = 0
        while start < total:
            count = min(max_points, total - start)
            self._write(f":WAVeform:STARt {start}")
            self._write(f":WAVeform:POINt {count}")
            payload = self._query_block(":WAVeform:DATA?")
            if len(payload) % bytes_per_sample:
                raise RuntimeError(
                    f"scope returned {len(payload)} bytes, not a whole number "
                    f"of {bytes_per_sample}-byte samples"
                )
            returned = len(payload) // bytes_per_sample
            if returned == 0:
                if start == 0:
                    raise RuntimeError(
                        f"scope returned no waveform data for {channel}: "
                        f"{payload[:40]!r}; waveform not ready"
                    )
                break
            chunks.append(np.frombuffer(payload, dtype=dtype))
            start += returned
            if returned < count:
                # Fewer points than requested means we have hit the end of the
                # available record (typically screen vs. memory depth); stop
                # rather than asking for offsets the instrument will reject.
                break

        if not chunks:
            return np.empty(0, dtype), desc
        return np.concatenate(chunks)[:total], desc

    def _acquisition_count(self) -> int:
        return int(float(self._query(":ACQuire:NUMACq?")))

    def _ensure_running(self) -> None:
        """Start free-running acquisition, re-arming a stopped scope.

        The first call stops the scope, sets the sweep mode and runs it; later
        calls leave a running scope alone and only re-arm it if it was stopped
        externally. Re-arming issues ``RUN`` without a preceding ``STOP`` so the
        sweep mode is preserved.
        """
        if not self._running:
            self._write(":TRIGger:STOP")
            self._write(f":TRIGger:MODE {self._trigger_mode}")
            self._write(":TRIGger:RUN")
            self._running = True
            return
        if self._trigger_mode_dirty:
            self._write(f":TRIGger:MODE {self._trigger_mode}")
            self._trigger_mode_dirty = False
        if self._query(":TRIGger:STATus?").strip().lower() == "stop":
            self._write(":TRIGger:RUN")

    def _require_connected(self) -> _Transport:
        if not self._connected or self._transport is None:
            raise RuntimeError("scope is not connected; call connect() first")
        return self._transport

    def _write(self, command: str) -> None:
        self._require_connected().write(command)

    def _query(self, command: str) -> str:
        return self._require_connected().query(command)

    def _query_block(self, command: str) -> bytes:
        return self._require_connected().query_block(command)
