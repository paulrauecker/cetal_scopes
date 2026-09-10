import socket
import struct
import threading
from dataclasses import FrozenInstanceError, replace
from typing import Any

import numpy as np
import pytest

from cetal_scopes import Capture, SiglentSDS6204L
from cetal_scopes.scopes.siglent import (
    WaveDesc,
    _host_from_address,
    _make_transport,
    _port_from_address,
    _SocketTransport,
    _VisaTransport,
    codes_to_volts,
    parse_wavedesc,
    time_origin,
)


def make_descriptor(
    *,
    frame_points: int = 3,
    adc_bit: int = 8,
    vdiv: float = 0.5,
    voffset: float = 0.1,
    code_per_div: float = 25.0,
    interval: float = 1e-9,
    delay: float = 0.0,
    tdiv_index: int = 9,
    probe: float = 1.0,
) -> bytes:
    buf = bytearray(346)
    struct.pack_into("<h", buf, 0x20, 0)
    struct.pack_into("<h", buf, 0x22, 0)
    struct.pack_into("<i", buf, 0x3C, frame_points)
    struct.pack_into("<i", buf, 0x74, frame_points)
    struct.pack_into("<i", buf, 0x84, 0)
    struct.pack_into("<i", buf, 0x88, 1)
    struct.pack_into("<i", buf, 0x90, 1)
    struct.pack_into("<i", buf, 0x94, 1)
    struct.pack_into("<f", buf, 0x9C, vdiv)
    struct.pack_into("<f", buf, 0xA0, voffset)
    struct.pack_into("<f", buf, 0xA4, code_per_div)
    struct.pack_into("<h", buf, 0xAC, adc_bit)
    struct.pack_into("<f", buf, 0xB0, interval)
    struct.pack_into("<d", buf, 0xB4, delay)
    struct.pack_into("<h", buf, 0x144, tdiv_index)
    struct.pack_into("<f", buf, 0x148, probe)
    return bytes(buf)


class FakeTransport:
    def __init__(
        self,
        descriptor: bytes,
        data_arrays: list[np.ndarray],
        *,
        idn: str = "Siglent Technologies,SDS6204L,TEST,1.0",
        max_points: str = "1000",
        status: str = "Stop",
        advance_on_run: bool = True,
    ) -> None:
        self.descriptor = descriptor
        self.data_arrays = list(data_arrays)
        self.idn = idn
        self.max_points = max_points
        self.status = status
        self.advance_on_run = advance_on_run
        self.num_acq = 0
        self.opened = False
        self.closed = False
        self.written: list[str] = []
        self.queried: list[str] = []

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def write(self, command: str) -> None:
        self.written.append(command)
        if command == ":TRIGger:RUN" and self.advance_on_run:
            self.num_acq += 1

    def query(self, command: str) -> str:
        self.queried.append(command)
        if command == "*IDN?":
            return self.idn
        if command == ":WAVeform:MAXPoint?":
            return self.max_points
        if command == ":ACQuire:NUMACq?":
            return str(self.num_acq)
        if command == ":TRIGger:STATus?":
            return self.status
        raise AssertionError(f"unexpected query {command!r}")

    def query_block(self, command: str) -> bytes:
        self.queried.append(command)
        if command == ":WAVeform:PREamble?":
            return self.descriptor
        if command == ":WAVeform:DATA?":
            return np.asarray(self.data_arrays.pop(0)).tobytes()
        raise AssertionError(f"unexpected block query {command!r}")


def make_driver(
    channels: tuple[str, ...] = ("C1", "C2"),
    *,
    descriptor: bytes | None = None,
    data_arrays: list[np.ndarray] | None = None,
    max_points: str = "1000",
) -> tuple[SiglentSDS6204L, FakeTransport]:
    descriptor = descriptor or make_descriptor()
    data_arrays = data_arrays or [
        np.array([0, 25, -25], dtype=np.int8),
        np.array([10, -10, 0], dtype=np.int8),
    ]
    fake = FakeTransport(descriptor, data_arrays, max_points=max_points)
    return SiglentSDS6204L(channels=channels, transport=fake), fake


def test_descriptor_parsing() -> None:
    desc = parse_wavedesc(make_descriptor(frame_points=500))
    assert desc.frame_points == 500
    assert desc.adc_bit == 8
    assert desc.vdiv == pytest.approx(0.5)
    assert desc.voffset == pytest.approx(0.1)
    assert desc.code_per_div == pytest.approx(25.0)
    assert desc.interval == pytest.approx(1e-9)
    assert desc.timebase == pytest.approx(100e-9)
    assert desc.vdiv_scaled == pytest.approx(0.5)
    assert desc.voffset_scaled == pytest.approx(0.1)


def test_descriptor_parsing_strips_binary_header() -> None:
    payload = make_descriptor(frame_points=7)
    desc = parse_wavedesc(b"#9000000346" + payload)
    assert desc.frame_points == 7


def test_descriptor_parsing_rejects_short_payload() -> None:
    with pytest.raises(ValueError, match="at least 346 bytes"):
        parse_wavedesc(b"\x00" * 10)


def test_probe_scaling() -> None:
    desc = parse_wavedesc(make_descriptor(vdiv=0.5, voffset=0.1, probe=10.0))
    assert desc.vdiv_scaled == pytest.approx(5.0)
    assert desc.voffset_scaled == pytest.approx(1.0)


def test_codes_to_volts() -> None:
    desc = parse_wavedesc(make_descriptor())
    volts = codes_to_volts(np.array([0, 25, -25], dtype=np.int8), desc)
    np.testing.assert_allclose(volts, [-0.1, 0.4, -0.6])


def test_codes_to_volts_rejects_zero_code_per_div() -> None:
    desc = replace(parse_wavedesc(make_descriptor()), code_per_div=0.0)
    with pytest.raises(ValueError, match="code_per_div is zero"):
        codes_to_volts(np.array([1], dtype=np.int8), desc)


def test_time_origin() -> None:
    desc = parse_wavedesc(make_descriptor(delay=1e-6))
    assert time_origin(desc) == pytest.approx(1e-6 - 100e-9 * 10 / 2)


def test_connect_identifies_instrument() -> None:
    scope, fake = make_driver()
    scope.connect()
    assert fake.opened
    assert scope.idn == "Siglent Technologies,SDS6204L,TEST,1.0"


def test_close_is_idempotent() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.close()
    scope.close()
    assert fake.closed


def test_acquire_before_connect_raises() -> None:
    scope, _ = make_driver()
    with pytest.raises(RuntimeError, match="not connected"):
        scope.acquire()


def test_acquire_returns_capture() -> None:
    scope, fake = make_driver()
    scope.connect()
    capture = scope.acquire()

    assert isinstance(capture, Capture)
    assert capture.channel_names == ("C1", "C2")
    assert capture.volts.shape == (2, 3)
    assert capture.raw is not None
    assert capture.raw.shape == (2, 3)
    assert capture.t0 == pytest.approx(-5e-7)
    assert capture.dt == pytest.approx(1e-9)
    np.testing.assert_allclose(
        capture.volts,
        [[-0.1, 0.4, -0.6], [0.1, -0.3, -0.1]],
    )
    assert ":TRIGger:MODE SINGle" in fake.written
    assert ":TRIGger:RUN" in fake.written


def test_acquire_chunks_large_waveforms() -> None:
    descriptor = make_descriptor(frame_points=3)
    data = [np.array([1, 2], dtype=np.int8), np.array([3], dtype=np.int8)]
    scope, fake = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, max_points="2"
    )
    scope.connect()
    capture = scope.acquire()

    assert capture.volts.shape == (1, 3)
    assert ":WAVeform:STARt 0" in fake.written
    assert ":WAVeform:STARt 2" in fake.written
    assert ":WAVeform:POINt 2" in fake.written
    assert ":WAVeform:POINt 1" in fake.written


def test_acquire_uses_word_width_for_hd_adc() -> None:
    descriptor = make_descriptor(adc_bit=12)
    data = [np.array([1, 2, 3], dtype=np.int16)]
    scope, fake = make_driver(("C1",), descriptor=descriptor, data_arrays=data)
    scope.connect()
    scope.acquire()

    assert ":WAVeform:WIDTh WORD" in fake.written
    assert ":WAVeform:BYTeorder LSB" in fake.written


def test_configure_applies_settings() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.configure(
        {
            "timebase": 1e-3,
            "delay": 1e-6,
            "acquire_type": "AVERage,16",
            "memory_depth": "1M",
            "channels": ("C1", "C3"),
            "trigger": {"source": "C2", "level": 0.5, "slope": "RISing"},
            "vertical": {"C1": {"scale": 0.2, "offset": 0.0, "coupling": "DC"}},
        }
    )

    assert scope.channels == ("C1", "C3")
    assert ":TIMebase:SCALe 0.001" in fake.written
    assert ":TIMebase:DELay 1e-06" in fake.written
    assert ":ACQuire:TYPE AVERage,16" in fake.written
    assert ":ACQuire:MDEPth 1M" in fake.written
    assert ":TRIGger:TYPE EDGE" in fake.written
    assert ":TRIGger:EDGE:SOURce C2" in fake.written
    assert ":TRIGger:EDGE:LEVel 0.5" in fake.written
    assert ":TRIGger:EDGE:SLOPe RISing" in fake.written
    assert ":CHANnel1:SCALe 0.2" in fake.written
    assert ":CHANnel1:OFFSet 0" in fake.written
    assert ":CHANnel1:COUPling DC" in fake.written


def test_configure_rejects_unknown_key() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(ValueError, match="unsupported setting"):
        scope.configure({"nonsense": 1})


def test_channel_names_are_normalized() -> None:
    scope, _ = make_driver(("ch1", "CH2"))
    assert scope.channels == ("C1", "C2")


def test_invalid_channel_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported analog channel"):
        SiglentSDS6204L(channels=("C9",))


def test_set_channel_probe() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.set_channel("C1", probe=10.0)
    assert ":CHANnel1:PROBe VALue,10" in fake.written


def test_wave_desc_is_frozen() -> None:
    desc: Any = parse_wavedesc(make_descriptor())
    assert isinstance(desc, WaveDesc)
    with pytest.raises(FrozenInstanceError):
        desc.vdiv = 1.0


def test_timebase_index_out_of_range_falls_back() -> None:
    desc = parse_wavedesc(make_descriptor(tdiv_index=255))
    assert desc.timebase == pytest.approx(1e-3)


def test_empty_channels_rejected() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        SiglentSDS6204L(channels=())


def test_duplicate_channels_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        SiglentSDS6204L(channels=("C1", "CH1"))


def test_acquire_times_out_without_trigger() -> None:
    fake = FakeTransport(make_descriptor(), [], advance_on_run=False)
    scope = SiglentSDS6204L(channels=("C1",), transport=fake, acquire_timeout=0.0)
    scope.connect()
    with pytest.raises(TimeoutError):
        scope.acquire()


def test_acquire_honors_trigger_mode() -> None:
    scope, fake = make_driver()
    scope.trigger_mode = "AUTO"
    scope.connect()
    scope.acquire()
    assert ":TRIGger:MODE AUTO" in fake.written
    assert ":TRIGger:STOP" in fake.written


def test_configure_rejects_non_mapping_trigger() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(TypeError, match="trigger must be a mapping"):
        scope.configure({"trigger": "C1"})


def test_configure_rejects_non_mapping_vertical() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(TypeError, match="vertical must be a mapping"):
        scope.configure({"vertical": "C1"})


def test_configure_rejects_non_mapping_channel_params() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(TypeError, match="must be a mapping"):
        scope.configure({"vertical": {"C1": 1.0}})


def test_transport_selection() -> None:
    assert isinstance(_make_transport("192.168.5.193", 5025, 1.0), _SocketTransport)
    assert isinstance(
        _make_transport("TCPIP0::1.2.3.4::5025::SOCKET", 5025, 1.0),
        _SocketTransport,
    )
    assert isinstance(
        _make_transport("USB0::0xF4EC::0xEE38::0123::INSTR", 5025, 1.0),
        _VisaTransport,
    )
    assert isinstance(
        _make_transport("TCPIP0::1.2.3.4::inst0::INSTR", 5025, 1.0),
        _VisaTransport,
    )


def test_address_helpers() -> None:
    assert _host_from_address("10.0.0.1") == "10.0.0.1"
    assert _host_from_address("TCPIP0::10.0.0.1::5025::SOCKET") == "10.0.0.1"
    assert _port_from_address("10.0.0.1", 5025) == 5025
    assert _port_from_address("TCPIP0::10.0.0.1::4000::SOCKET", 5025) == 4000


def test_socket_transport_roundtrip() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def handle(conn: socket.socket) -> None:
        with conn:
            buffer = b""
            while True:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    command = line.decode().strip()
                    if command == "*IDN?":
                        conn.sendall(b"IDN,OK\n")
                    elif command == ":WAVeform:DATA?":
                        payload = b"\x01\x02\x03"
                        conn.sendall(b"#1" + b"3" + payload + b"\n")
                    else:
                        conn.sendall(b"1000\n")

    def serve() -> None:
        conn, _ = server.accept()
        handle(conn)
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    transport = _SocketTransport("127.0.0.1", port, 1.0)
    transport.open()
    assert transport.query("*IDN?") == "IDN,OK"
    assert transport.query_block(":WAVeform:DATA?") == b"\x01\x02\x03"
    assert transport.query_block(":WAVeform:MAXPoint?") == b"1000"
    transport.write(":TRIGger:RUN")
    transport.close()
    thread.join(timeout=1.0)
