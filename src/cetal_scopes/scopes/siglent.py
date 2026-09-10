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
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from cetal_scopes.capture import Capture
from cetal_scopes.scopes.base import Scope

__all__ = [
    "SiglentSDS6204L",
    "WaveDesc",
    "codes_to_volts",
    "parse_wavedesc",
    "time_origin",
]

DEFAULT_ADDRESS = "192.168.5.193"
DEFAULT_PORT = 5025
GRID_NUM = 10

_WAVEDESC_LENGTH = 346
_VALID_ANALOG_CHANNELS = ("C1", "C2", "C3", "C4")
_CONFIG_KEYS = frozenset(
    {
        "channels",
        "timebase",
        "delay",
        "acquire_type",
        "memory_depth",
        "trigger",
        "trigger_mode",
        "vertical",
    }
)

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
    if start + 2 > len(data):
        raise ValueError("truncated IEEE 488.2 binary header")
    digit_count = int(chr(data[start + 1]))
    payload_start = start + 2 + digit_count
    return data[payload_start:]


def parse_wavedesc(raw: bytes) -> WaveDesc:
    """Parse a ``:WAVeform:PREamble?`` response into a :class:`WaveDesc`.

    Parameters
    ----------
    raw : bytes
        The preamble payload, with or without its IEEE 488.2 binary header.

    Returns
    -------
    WaveDesc
        The decoded descriptor.

    Raises
    ------
    ValueError
        If the payload is too short to contain the descriptor.
    """
    descriptor = _strip_binary_header(raw)
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


def codes_to_volts(codes: NDArray[Any], desc: WaveDesc) -> NDArray[np.float64]:
    """Convert signed ADC codes to volts using the preamble scaling.

    Implements ``V = code * (vdiv * probe / code_per_div) - voffset * probe``.
    """
    if desc.code_per_div == 0:
        raise ValueError("code_per_div is zero; waveform cannot be scaled")
    return (
        codes.astype(np.float64) * (desc.vdiv_scaled / desc.code_per_div)
        - desc.voffset_scaled
    )


def time_origin(desc: WaveDesc, grid_num: int = GRID_NUM) -> float:
    """Return the time of the first sample in seconds."""
    return desc.delay - desc.timebase * grid_num / 2.0


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

    def __init__(
        self,
        address: str = DEFAULT_ADDRESS,
        *,
        channels: Sequence[str] = ("C1",),
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
        self._transport = transport
        self._owns_transport = transport is None
        self._connected = False
        self._idn = ""

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

    def configure(self, settings: Mapping[str, Any]) -> None:
        """Apply acquisition settings.

        Supported keys: ``channels``, ``timebase``, ``delay``,
        ``acquire_type``, ``memory_depth``, ``trigger`` (mapping with
        ``source``/``level``/``slope``) and ``vertical`` (mapping of channel
        name to ``scale``/``offset``/``coupling``/``probe``/
        ``bandwidth_limit``).
        """
        unknown = set(settings) - _CONFIG_KEYS
        if unknown:
            raise ValueError(
                f"unsupported setting(s) {sorted(unknown)!r}; expected a "
                f"subset of {sorted(_CONFIG_KEYS)!r}"
            )

        if "channels" in settings:
            self._channels = _normalize_channels(settings["channels"])
        if "timebase" in settings or "delay" in settings:
            self.set_timebase(
                scale=settings.get("timebase"), delay=settings.get("delay")
            )
        if "acquire_type" in settings:
            self.set_acquire_type(str(settings["acquire_type"]))
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
                )

    def set_channel(
        self,
        channel: str,
        *,
        scale: float | None = None,
        offset: float | None = None,
        coupling: str | None = None,
        probe: float | None = None,
        bandwidth_limit: str | None = None,
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
        if level is not None:
            self._write(f":TRIGger:EDGE:LEVel {float(level):.6g}")
        if slope is not None:
            self._write(f":TRIGger:EDGE:SLOPe {slope}")

    def set_acquire_type(self, acquire_type: str) -> None:
        """Set the acquisition type (e.g. ``NORMal``, ``AVERage,16``)."""
        self._write(f":ACQuire:TYPE {acquire_type}")

    def set_memory_depth(self, depth: str) -> None:
        """Set the maximum memory depth (e.g. ``10k``, ``1M``, ``10M``)."""
        self._write(f":ACQuire:MDEPth {depth}")

    def acquire(self) -> Capture:
        """Arm an acquisition, wait for it, and fetch the channels.

        The sweep mode comes from :attr:`trigger_mode` (default ``"SINGle"``).
        Completion is detected via the ``:ACQuire:NUMACq?`` counter, then the
        scope is stopped so the fetched buffer is a coherent snapshot.

        Returns
        -------
        Capture
            Voltage and raw-code arrays shaped ``(n_channels, n_samples)``,
            sharing the first channel's timebase.
        """
        self._require_connected()
        self._write(":TRIGger:STOP")
        self._write(f":TRIGger:MODE {self._trigger_mode}")
        before = self._acquisition_count()
        self._write(":TRIGger:RUN")
        self._wait_for_acquisition(before)
        self._write(":TRIGger:STOP")

        volts_rows: list[NDArray[np.float64]] = []
        raw_rows: list[NDArray[Any]] = []
        names: list[str] = []
        t0: float | None = None
        dt: float | None = None

        for channel in self._channels:
            codes, desc = self._fetch_codes(channel)
            volts_rows.append(codes_to_volts(codes, desc))
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
        )

    def _fetch_codes(self, channel: str) -> tuple[NDArray[Any], WaveDesc]:
        self._write(f":WAVeform:SOURce {channel}")
        self._write(":WAVeform:STARt 0")
        self._write(":WAVeform:POINt 0")
        desc = parse_wavedesc(self._query_block(":WAVeform:PREamble?"))

        total = desc.frame_points
        if total <= 0:
            return np.empty(0, dtype=np.int8), desc

        max_points = int(float(self._query(":WAVeform:MAXPoint?")))
        if max_points <= 0:
            max_points = total

        word = desc.adc_bit > 8
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
            usable = count * bytes_per_sample
            if len(payload) < usable:
                raise RuntimeError(
                    f"scope returned {len(payload)} bytes for {count} points "
                    f"({usable} expected); waveform not ready: {payload[:40]!r}"
                )
            chunks.append(np.frombuffer(payload[:usable], dtype=dtype))
            start += count

        codes = np.concatenate(chunks)[:total] if chunks else np.empty(0, dtype)
        return codes, desc

    def _acquisition_count(self) -> int:
        return int(float(self._query(":ACQuire:NUMACq?")))

    def _wait_for_acquisition(self, before: int) -> None:
        deadline = time.monotonic() + self._acquire_timeout
        while time.monotonic() < deadline:
            if self._acquisition_count() > before:
                return
            time.sleep(0.05)
        raise TimeoutError(
            f"acquisition did not complete within {self._acquire_timeout:g}s"
        )

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
