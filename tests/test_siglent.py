import socket
import struct
import threading
from dataclasses import FrozenInstanceError, replace
from typing import Any

import numpy as np
import pytest

from cetal_scopes import Capture, SiglentSDS6204L
from cetal_scopes.scopes.siglent import (
    MDEPTH_ENUM,
    TDIV_ENUM,
    AcquisitionPlan,
    WaveDesc,
    _host_from_address,
    _impedance_ohms_to_string,
    _make_transport,
    _port_from_address,
    _SocketTransport,
    _strip_binary_header,
    _VisaTransport,
    codes_to_volts,
    parse_wavedesc,
    snap_mdepth,
    snap_timebase,
    snap_vdiv,
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
        advance_on_query: bool = False,
        trigger_level: str = "0.00E+00",
        sample_rate: str = "2.00E+09",
    ) -> None:
        self.descriptor = descriptor
        self.data_arrays = list(data_arrays)
        self.idn = idn
        self.max_points = max_points
        self.status = status
        self.advance_on_run = advance_on_run
        self.advance_on_query = advance_on_query
        self.trigger_level = trigger_level
        self.sample_rate = sample_rate
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
            if self.advance_on_query:
                self.num_acq += 1
            return str(self.num_acq)
        if command == ":TRIGger:STATus?":
            return self.status
        if command == ":TRIGger:EDGE:LEVel?":
            return self.trigger_level
        if command == ":ACQuire:SRATe?":
            return self.sample_rate
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
    sample_width: str = "WORD",
    streaming: bool = False,
    trigger_level: str = "0.00E+00",
) -> tuple[SiglentSDS6204L, FakeTransport]:
    descriptor = descriptor or make_descriptor()
    data_arrays = data_arrays or [
        np.array([0, 25, -25], dtype=np.int16),
        np.array([10, -10, 0], dtype=np.int16),
    ]
    fake = FakeTransport(
        descriptor, data_arrays, max_points=max_points, trigger_level=trigger_level
    )
    return (
        SiglentSDS6204L(
            channels=channels,
            sample_width=sample_width,
            streaming=streaming,
            transport=fake,
        ),
        fake,
    )


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


def test_strip_binary_header() -> None:
    assert _strip_binary_header(b"#9000000346payload") == b"payload"
    assert _strip_binary_header(b"12345") == b"12345"
    with pytest.raises(ValueError, match="malformed"):
        _strip_binary_header(b"#<oops")


def test_parse_wavedesc_tolerates_hash_in_payload() -> None:
    # The 346-byte payload may itself contain a '#' byte (e.g. inside the vdiv
    # float); it must not be mistaken for an IEEE 488.2 header.
    payload = bytearray(make_descriptor(frame_points=7))
    payload[0x00] = ord("#")
    payload[0x01] = ord("<")
    desc = parse_wavedesc(bytes(payload))
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
    data = [np.array([1, 2], dtype=np.int16), np.array([3], dtype=np.int16)]
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


def test_acquire_uses_word_width_by_default() -> None:
    descriptor = make_descriptor(adc_bit=12)
    data = [np.array([1, 2, 3], dtype=np.int16)]
    scope, fake = make_driver(("C1",), descriptor=descriptor, data_arrays=data)
    scope.connect()
    assert scope.sample_width == "WORD"
    scope.acquire()

    assert ":WAVeform:WIDTh WORD" in fake.written
    assert ":WAVeform:BYTeorder LSB" in fake.written


def test_acquire_uses_byte_width_when_requested() -> None:
    descriptor = make_descriptor(adc_bit=12)
    data = [np.array([1, 2, 3], dtype=np.int8)]
    scope, fake = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, sample_width="BYTE"
    )
    scope.connect()
    scope.acquire()

    assert ":WAVeform:WIDTh BYTE" in fake.written
    assert ":WAVeform:BYTeorder LSB" not in fake.written


def test_byte_transfer_left_aligns_hd_codes() -> None:
    descriptor = make_descriptor(adc_bit=12, vdiv=1.0, voffset=0.0, code_per_div=2560.0)
    data = [np.array([1, -1], dtype=np.int8)]
    scope, _ = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, sample_width="BYTE"
    )
    scope.connect()
    capture = scope.acquire()

    np.testing.assert_allclose(capture.volts, [[0.1, -0.1]])


def test_invalid_sample_width_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported sample width"):
        SiglentSDS6204L(sample_width="HALF")


def test_acquire_rejects_short_waveform_block() -> None:
    fake = FakeTransport(make_descriptor(frame_points=3), [np.array([], dtype=np.int8)])
    scope = SiglentSDS6204L(channels=("C1",), transport=fake)
    scope.connect()
    with pytest.raises(RuntimeError, match="waveform not ready"):
        scope.acquire()


def test_acquire_clamps_to_returned_screen_points() -> None:
    descriptor = make_descriptor(frame_points=100)
    data = [np.array([1, 2, 3, 4], dtype=np.int16)]
    scope, _ = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, max_points="1000"
    )
    scope.connect()
    capture = scope.acquire()

    assert capture.volts.shape == (1, 4)
    assert capture.raw is not None
    assert capture.raw.shape == (1, 4)


def test_acquire_stops_when_a_chunk_is_short() -> None:
    descriptor = make_descriptor(frame_points=10)
    data = [np.array([1, 2], dtype=np.int16), np.array([3], dtype=np.int16)]
    scope, fake = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, max_points="2"
    )
    scope.connect()
    capture = scope.acquire()

    assert capture.volts.shape == (1, 3)
    assert ":WAVeform:STARt 2" in fake.written


def test_acquire_rejects_partial_sample() -> None:
    descriptor = make_descriptor(frame_points=3, adc_bit=12)
    data = [np.array([1, 2, 3], dtype=np.uint8)]
    scope, _ = make_driver(
        ("C1",), descriptor=descriptor, data_arrays=data, sample_width="WORD"
    )
    scope.connect()
    with pytest.raises(RuntimeError, match="not a whole number"):
        scope.acquire()


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


def test_configure_turns_interpolation_off_by_default() -> None:
    """sin(x)/x reconstruction invents samples; it is opt-in, not the default."""
    scope, fake = make_driver()
    scope.connect()
    scope.configure({"timebase": 1e-6})

    assert ":ACQuire:INTerpolation OFF" in fake.written


def test_configure_can_opt_into_sinx_interpolation() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.configure({"interpolation": "ON"})

    assert ":ACQuire:INTerpolation ON" in fake.written


def test_interpolation_choice_persists_across_configure_calls() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.configure({"interpolation": True})
    fake.written.clear()
    scope.configure({"timebase": 1e-6})

    assert ":ACQuire:INTerpolation ON" in fake.written


def test_configure_rejects_an_unknown_interpolation_state() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(ValueError, match="unsupported interpolation"):
        scope.configure({"interpolation": "SOMETIMES"})


def test_sample_rate_reads_back_what_the_scope_will_actually_use() -> None:
    scope, fake = make_driver()
    fake.sample_rate = "5.00E+09"
    scope.connect()

    assert scope.sample_rate() == pytest.approx(5e9)


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


def test_set_channel_impedance_aliases() -> None:
    scope, fake = make_driver()
    scope.connect()
    scope.set_channel("C1", impedance="50")
    scope.set_channel("C2", impedance="1M")
    assert ":CHANnel1:IMPedance FIFTy" in fake.written
    assert ":CHANnel2:IMPedance ONEMeg" in fake.written


def test_set_channel_rejects_unknown_impedance() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(ValueError, match="unsupported impedance"):
        scope.set_channel("C1", impedance="75")


def test_configure_vertical_impedance() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.configure({"vertical": {"C1": {"impedance": "1M", "scale": 0.1}}})
    assert ":CHANnel1:IMPedance ONEMeg" in fake.written
    assert ":CHANnel1:SCALe 0.1" in fake.written


def test_wave_desc_is_frozen() -> None:
    desc: Any = parse_wavedesc(make_descriptor())
    assert isinstance(desc, WaveDesc)
    writable: Any = desc
    with pytest.raises(FrozenInstanceError):
        writable.vdiv = 1.0


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


def test_streaming_starts_scope_once_and_keeps_it_running() -> None:
    data = [
        np.array([1, 2, 3], dtype=np.int16),
        np.array([4, 5, 6], dtype=np.int16),
    ]
    fake = FakeTransport(make_descriptor(), data, status="Auto", advance_on_query=True)
    scope = SiglentSDS6204L(
        channels=("C1",),
        transport=fake,
        streaming=True,
        trigger_mode="AUTO",
    )
    scope.connect()

    first = scope.acquire()
    second = scope.acquire()

    assert fake.written.count(":TRIGger:RUN") == 1
    assert fake.written.count(":TRIGger:STOP") == 1
    assert ":TRIGger:MODE AUTO" in fake.written
    assert first.volts.shape == (1, 3)
    assert second.volts.shape == (1, 3)


def test_ensure_running_rearms_a_stopped_scope() -> None:
    fake = FakeTransport(make_descriptor(), [], status="Stop")
    scope = SiglentSDS6204L(
        channels=("C1",), transport=fake, streaming=True, trigger_mode="AUTO"
    )
    scope.connect()

    scope._ensure_running()
    scope._ensure_running()

    assert fake.written.count(":TRIGger:RUN") == 2
    assert fake.written.count(":TRIGger:STOP") == 1


def test_streaming_property() -> None:
    streaming, _ = make_driver(streaming=True)
    assert streaming.streaming is True
    one_shot, _ = make_driver()
    assert one_shot.streaming is False


def test_configure_rejects_non_mapping_trigger() -> None:
    scope, _ = make_driver()
    scope.connect()
    with pytest.raises(TypeError, match="trigger must be a mapping"):
        scope.configure({"trigger": "C1"})


def test_set_edge_trigger_writes_and_reads_level() -> None:
    scope, transport = make_driver(trigger_level="2.25E-03")
    scope.connect()
    scope.set_edge_trigger(source="C1", level=0.05, slope="RISing")
    assert ":TRIGger:EDGE:SOURce C1" in transport.written
    assert ":TRIGger:EDGE:LEVel 0.05" in transport.written
    assert ":TRIGger:EDGE:SLOPe RISing" in transport.written
    # The instrument may report a clamped value; the getter must pass it through.
    assert scope.trigger_level() == pytest.approx(2.25e-3)


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
                    elif command == ":ACQuire:NUMACq?":
                        conn.sendall(b"\n42\n")
                    elif command == ":WAVeform:DATA?":
                        payload = b"\x01\x02\x03"
                        conn.sendall(b"\nC1:WF DAT2," + b"#1" + b"3" + payload + b"\n")
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
    assert transport.query(":ACQuire:NUMACq?") == "42"
    assert transport.query_block(":WAVeform:DATA?") == b"\x01\x02\x03"
    assert transport.query_block(":WAVeform:MAXPoint?") == b"1000"
    transport.write(":TRIGger:RUN")
    transport.close()
    thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Physical-vocabulary additive layer
# ---------------------------------------------------------------------------


class TestSnapVdiv:
    def test_rounds_up_the_ladder(self) -> None:
        assert snap_vdiv(0.0001) == 0.0005
        assert snap_vdiv(0.03) == 0.05
        assert snap_vdiv(1.5) == 2.0
        assert snap_vdiv(100.0) == 10.0


class TestSnapTimebase:
    def test_exact_step_passes_through(self) -> None:
        assert snap_timebase(1e-3) == pytest.approx(1e-3)

    def test_rounds_up_to_next_step(self) -> None:
        assert snap_timebase(1.5e-3) == pytest.approx(2e-3)

    def test_clamps_above_the_top(self) -> None:
        assert snap_timebase(1e6) == TDIV_ENUM[-1]


class TestSnapMdepth:
    def test_exact_step_passes_through(self) -> None:
        assert snap_mdepth(10_000) == "10k"

    def test_rounds_up_to_next_step(self) -> None:
        assert snap_mdepth(10_001) == "1M"

    def test_clamps_above_the_top(self) -> None:
        assert snap_mdepth(10**12) == MDEPTH_ENUM[-1][1]


class TestImpedanceOhmsToString:
    def test_known_values(self) -> None:
        assert _impedance_ohms_to_string(50) == "FIFTy"
        assert _impedance_ohms_to_string(1_000_000) == "ONEMeg"

    def test_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError, match="unsupported impedance"):
            _impedance_ohms_to_string(75)


def test_set_acquisition_writes_timebase_and_memory_depth() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()

    plan = scope.set_acquisition(sample_rate=1e6, record_length=10_000)

    assert ":TIMebase:SCALe 0.001" in fake.written
    assert ":ACQuire:MDEPth 10k" in fake.written
    assert plan == AcquisitionPlan(
        requested_sample_rate=1e6,
        requested_record_length=10_000,
        timebase=pytest.approx(1e-3),
        memory_depth="10k",
        delay=0.0,
    )
    assert scope.last_acquisition == plan


def test_set_acquisition_requires_both_together_on_first_call() -> None:
    scope, _fake = make_driver(("C1",))
    scope.connect()
    with pytest.raises(ValueError, match="must both be known"):
        scope.set_acquisition(sample_rate=1e6)
    with pytest.raises(ValueError, match="must both be known"):
        scope.set_acquisition(record_length=1000)


def test_set_acquisition_reuses_the_other_value_on_a_later_call() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.set_acquisition(sample_rate=1e6, record_length=10_000)
    fake.written.clear()

    plan = scope.set_acquisition(record_length=20_000)

    assert plan.requested_sample_rate is None
    assert plan.requested_record_length == 20_000
    assert ":ACQuire:MDEPth 1M" in fake.written  # 1e6 Hz * 0.02 s = 20_000 samples


def test_set_acquisition_pretrigger_requires_a_prior_window() -> None:
    scope, _fake = make_driver(("C1",))
    scope.connect()
    with pytest.raises(ValueError, match="requires sample_rate and record_length"):
        scope.set_acquisition(pretrigger=0.5)


def test_set_acquisition_pretrigger_centered_gives_zero_delay() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.set_acquisition(sample_rate=1e6, record_length=10_000)
    fake.written.clear()

    scope.set_acquisition(pretrigger=0.5)

    assert ":TIMebase:DELay 0" in fake.written


def test_set_acquisition_pretrigger_fraction_computes_delay() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.set_acquisition(sample_rate=1e6, record_length=10_000)  # window = 0.01 s
    fake.written.clear()

    scope.set_acquisition(pretrigger=0.25)  # 2500 samples pretrigger

    assert ":TIMebase:DELay 0.0025" in fake.written


def test_configure_physical_sample_rate_and_record_length() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.configure({"sample_rate": 1e6, "record_length": 10_000})
    assert ":TIMebase:SCALe 0.001" in fake.written
    assert ":ACQuire:MDEPth 10k" in fake.written


def test_configure_rejects_mixing_sample_rate_with_timebase() -> None:
    scope, _fake = make_driver(("C1",))
    scope.connect()
    with pytest.raises(ValueError, match="cannot mix"):
        scope.configure({"sample_rate": 1e6, "timebase": 1e-3})


def test_configure_rejects_mixing_pretrigger_with_delay() -> None:
    scope, _fake = make_driver(("C1",))
    scope.connect()
    with pytest.raises(ValueError, match="cannot mix"):
        scope.configure({"pretrigger": 0.5, "delay": 1e-6})


def test_configure_rejects_mixing_range_with_vertical_scale_same_channel() -> None:
    scope, _fake = make_driver(("C1",))
    scope.connect()
    with pytest.raises(ValueError, match="cannot mix"):
        scope.configure({"range": 1.0, "vertical": {"C1": {"scale": 0.5}}})


def test_configure_range_and_vertical_scale_different_channels_is_fine() -> None:
    scope, fake = make_driver(("C1", "C2"))
    scope.connect()
    scope.configure({"range": {"C1": 1.0}, "vertical": {"C2": {"scale": 0.2}}})
    assert ":CHANnel1:SCALe 0.5" in fake.written  # snap_vdiv(1.0 / 4) == 0.5
    assert ":CHANnel2:SCALe 0.2" in fake.written


def test_configure_range_maps_to_scale() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.configure({"range": 1.0})
    assert ":CHANnel1:SCALe 0.5" in fake.written


def test_configure_offset_top_level_passthrough() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.configure({"offset": 0.05})
    assert ":CHANnel1:OFFSet 0.05" in fake.written


def test_configure_coupling_top_level_passthrough() -> None:
    scope, fake = make_driver(("C1",))
    scope.connect()
    scope.configure({"coupling": "AC"})
    assert ":CHANnel1:COUPling AC" in fake.written


def test_configure_impedance_top_level_converts_ohms() -> None:
    scope, fake = make_driver(("C1", "C2"))
    scope.connect()
    scope.configure({"impedance": {"C1": 50, "C2": 1_000_000}})
    assert ":CHANnel1:IMPedance FIFTy" in fake.written
    assert ":CHANnel2:IMPedance ONEMeg" in fake.written


def test_last_acquisition_is_none_before_any_call() -> None:
    scope, _fake = make_driver(("C1",))
    assert scope.last_acquisition is None
