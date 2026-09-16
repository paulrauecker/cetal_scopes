import json

import numpy as np
import pytest

from cetal_scopes import Capture, SpectrumM5i3367
from cetal_scopes.scopes import spectrum
from cetal_scopes.scopes.spectrum import (
    M2CMD_CARD_ENABLETRIGGER,
    M2CMD_CARD_RESET,
    M2CMD_CARD_START,
    M2CMD_CARD_STOP,
    M2CMD_CARD_WAITREADY,
    M2CMD_DATA_STARTDMA,
    M2CMD_DATA_STOPDMA,
    M2CMD_DATA_WAITDMA,
    SPC_AMP0,
    SPC_CARDMODE,
    SPC_CHENABLE,
    SPC_MEMSIZE,
    SPC_OFFS0,
    SPC_POSTTRIGGER,
    SPC_REC_STD_MULTI,
    SPC_REC_STD_SINGLE,
    SPC_SAMPLERATE,
    SPC_SEGMENTSIZE,
    SPC_TIMEOUT,
    SPC_TM_NEG,
    SPC_TMASK_NONE,
    SPC_TMASK_SOFTWARE,
    SPC_TRIG_ANDMASK,
    SPC_TRIG_CH0_LEVEL0,
    SPC_TRIG_CH0_MODE,
    SPC_TRIG_CH_ANDMASK0,
    SPC_TRIG_CH_ORMASK0,
    SPC_TRIG_ORMASK,
    SPCM_TYPE_AI,
    codes_to_volts,
    deinterleave,
    snap_input_range,
    snap_record_length,
    snap_sample_rate,
    volts_to_code,
)


class FakeCard:
    """In-memory double for :class:`spectrum._Card`.

    Records every register write; ``get_i32``/``get_i64``/``get_str`` raise
    ``AssertionError`` on any register this double does not model, so
    unintended register traffic fails loudly.
    """

    def __init__(
        self,
        *,
        product_name: str = "M5i.3367-x16",
        serial_number: int = 24123,
        max_adc: int = 2047,
        bytes_per_sample: int = 2,
        function_type: int = SPCM_TYPE_AI,
        timeout: bool = False,
    ) -> None:
        self.registers: dict[int, int] = {}
        self.commands: list[int] = []
        self.opened_device: str | None = None
        self.closed = False
        self.defined_byte_count: int | None = None
        self.fake_waveform: np.ndarray | None = None
        self._product_name = product_name
        self._serial_number = serial_number
        self._max_adc = max_adc
        self._bytes_per_sample = bytes_per_sample
        self._function_type = function_type
        self._timeout = timeout

    def open(self, device: str) -> None:
        self.opened_device = device

    def close(self) -> None:
        self.closed = True

    def get_i32(self, register: int) -> int:
        if register == spectrum.SPC_FNCTYPE:
            return self._function_type
        if register == spectrum.SPC_PCISERIALNO:
            return self._serial_number
        if register == spectrum.SPC_MIINST_MAXADCVALUE:
            return self._max_adc
        if register == spectrum.SPC_MIINST_BYTESPERSAMPLE:
            return self._bytes_per_sample
        raise AssertionError(f"unexpected get_i32({register})")

    def get_i64(self, register: int) -> int:
        if register == SPC_SAMPLERATE:
            return self.registers.get(SPC_SAMPLERATE, 0)
        raise AssertionError(f"unexpected get_i64({register})")

    def get_str(self, register: int) -> str:
        if register == spectrum.SPC_PCITYP:
            return self._product_name
        raise AssertionError(f"unexpected get_str({register})")

    def set_i32(self, register: int, value: int) -> None:
        self.registers[register] = value

    def set_i64(self, register: int, value: int) -> None:
        self.registers[register] = value

    def command(self, command: int, *, timeout_ms: int | None = None) -> None:
        self.commands.append(command)
        if timeout_ms is not None:
            self.registers[SPC_TIMEOUT] = timeout_ms
        if self._timeout and command & M2CMD_CARD_WAITREADY:
            raise TimeoutError("wait timed out")

    def def_transfer(self, byte_count: int) -> None:
        self.defined_byte_count = byte_count

    def read_buffer(self, n_samples_total: int) -> np.ndarray:
        assert self.fake_waveform is not None, "test must set fake_waveform first"
        assert len(self.fake_waveform) == n_samples_total, (
            f"driver asked for {n_samples_total} samples, "
            f"fake_waveform has {len(self.fake_waveform)}"
        )
        return self.fake_waveform


def make_driver(**kwargs) -> tuple[SpectrumM5i3367, FakeCard]:
    card = FakeCard()
    channels = kwargs.pop("channels", ("CH0",))
    scope = SpectrumM5i3367(channels=channels, card=card, **kwargs)
    scope.connect()
    return scope, card


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


class TestSnapInputRange:
    def test_snaps_up_to_nearest_step(self) -> None:
        assert snap_input_range(0.05) == 200
        assert snap_input_range(0.2) == 200
        assert snap_input_range(0.3) == 500
        assert snap_input_range(1.5) == 2500

    def test_clamps_above_the_top(self) -> None:
        assert snap_input_range(10.0) == 2500


class TestSnapSampleRate:
    def test_passes_through_within_ceiling(self) -> None:
        assert snap_sample_rate(1e9, n_channels=1) == 1e9

    def test_clamps_to_one_channel_ceiling(self) -> None:
        assert snap_sample_rate(20e9, n_channels=1) == 10e9

    def test_clamps_to_two_channel_ceiling(self) -> None:
        assert snap_sample_rate(10e9, n_channels=2) == 5e9

    def test_rejects_nonpositive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            snap_sample_rate(0, n_channels=1)


class TestSnapRecordLength:
    def test_already_on_grid(self) -> None:
        assert snap_record_length(4096) == 4096

    def test_rounds_up_to_next_step(self) -> None:
        assert snap_record_length(4097) == 4128
        assert snap_record_length(1) == 32

    def test_rejects_nonpositive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            snap_record_length(0)


class TestDeinterleave:
    def test_single_channel_is_a_reshape(self) -> None:
        raw = np.array([1, 2, 3, 4], dtype=np.int16)
        result = deinterleave(raw, 1)
        assert result.shape == (1, 4)
        np.testing.assert_array_equal(result[0], raw)

    def test_two_channels_split_alternating_samples(self) -> None:
        # layout: A0 B0 A1 B1 A2 B2
        raw = np.array([10, -10, 11, -11, 12, -12], dtype=np.int16)
        result = deinterleave(raw, 2)
        assert result.shape == (2, 3)
        np.testing.assert_array_equal(result[0], [10, 11, 12])
        np.testing.assert_array_equal(result[1], [-10, -11, -12])


class TestCodesToVolts:
    def test_full_scale_code_gives_full_scale_voltage(self) -> None:
        codes = np.array([2047, -2047, 0], dtype=np.int16)
        volts = codes_to_volts(codes, 1000, 2047)
        np.testing.assert_allclose(volts, [1.0, -1.0, 0.0])

    def test_offset_is_subtracted(self) -> None:
        codes = np.array([0], dtype=np.int16)
        volts = codes_to_volts(codes, 1000, 2047, offset_v=0.1)
        np.testing.assert_allclose(volts, [-0.1])

    def test_rejects_zero_max_adc(self) -> None:
        with pytest.raises(ValueError, match="max_adc"):
            codes_to_volts(np.array([0]), 1000, 0)


class TestVoltsToCode:
    def test_round_trips_with_codes_to_volts(self) -> None:
        for level in (-0.99, -0.1, 0.0, 0.1, 0.99):
            code = volts_to_code(level, 1000, 2047)
            back = codes_to_volts(np.array([code]), 1000, 2047)[0]
            assert abs(back - level) < 1e-3

    def test_accounts_for_offset(self) -> None:
        code = volts_to_code(0.05, 1000, 2047, offset_v=0.1)
        back = codes_to_volts(np.array([code]), 1000, 2047, offset_v=0.1)[0]
        assert abs(back - 0.05) < 1e-3

    def test_rejects_level_outside_range(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            volts_to_code(5.0, 200, 2047)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_connect_populates_card_identity() -> None:
    scope, _card = make_driver()
    assert scope.product_name == "M5i.3367-x16"
    assert scope.serial_number == 24123


def test_connect_is_idempotent() -> None:
    scope, card = make_driver()
    scope.connect()
    assert card.opened_device == "/dev/spcm0"


def test_close_is_idempotent_and_safe_before_connect() -> None:
    scope = SpectrumM5i3367(card=FakeCard())
    scope.close()
    scope.close()


def test_connect_rejects_non_analog_input_card() -> None:
    card = FakeCard(function_type=SPCM_TYPE_AI + 1)
    scope = SpectrumM5i3367(card=card)
    with pytest.raises(RuntimeError, match="not an analog-input card"):
        scope.connect()


def test_acquire_before_connect_raises() -> None:
    scope = SpectrumM5i3367(card=FakeCard())
    with pytest.raises(RuntimeError, match="not connected"):
        scope.acquire()


def test_context_manager_connects_and_closes() -> None:
    card = FakeCard()
    with SpectrumM5i3367(card=card) as scope:
        assert scope.product_name == "M5i.3367-x16"
    assert card.closed


def test_close_does_not_drop_an_injected_card() -> None:
    card = FakeCard()
    scope = SpectrumM5i3367(card=card)
    scope.connect()
    scope.close()
    assert card.closed
    # the driver does not own an injected card, so it keeps the reference
    # (mirrors SiglentSDS6204L's `_owns_transport` convention)
    assert scope._card is card


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------


def test_configure_rejects_unknown_key() -> None:
    scope, _card = make_driver()
    with pytest.raises(ValueError, match="unsupported setting"):
        scope.configure({"nonsense": 1})


def test_configure_performs_no_io() -> None:
    scope, card = make_driver()
    scope.configure(
        {
            "sample_rate": 1e9,
            "record_length": 4096,
            "range": 1.0,
            "trigger": {"source": "CH0", "level": 0.1},
        }
    )
    assert card.registers == {}
    assert card.commands == []


def test_configure_coupling_accepts_dc_rejects_other() -> None:
    scope, _card = make_driver()
    scope.configure({"coupling": "DC"})  # no-op, must not raise
    with pytest.raises(ValueError, match="fixed DC-coupled"):
        scope.configure({"coupling": "AC"})


def test_configure_impedance_accepts_50_rejects_other() -> None:
    scope, _card = make_driver()
    scope.configure({"impedance": 50})  # no-op, must not raise
    with pytest.raises(ValueError, match="fixed 50 ohm"):
        scope.configure({"impedance": 1_000_000})


def test_configure_segments_must_be_positive_or_none() -> None:
    scope, _card = make_driver()
    with pytest.raises(ValueError, match="segments"):
        scope.configure({"segments": 0})


def test_configure_channels_switches_active_channels() -> None:
    scope, _card = make_driver(channels=("CH0",))
    scope.configure({"channels": ["CH0", "CH1"]})
    assert scope.channels == ("CH0", "CH1")


def test_configure_channels_carries_over_existing_range_and_offset() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"range": 0.5, "record_length": 32})  # CH0 -> 500 mV
    scope.configure({"channels": ["CH0", "CH1"]})  # CH1 is new, gets the default
    card.fake_waveform = np.zeros(64, dtype=np.int16)

    scope.acquire()

    assert card.registers[SPC_AMP0] == 500
    assert card.registers[SPC_AMP0 + 100] == 1000


# ---------------------------------------------------------------------------
# acquire() -- Standard Single
# ---------------------------------------------------------------------------


def test_acquire_returns_capture_with_expected_shape_and_timing() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"sample_rate": 1e6, "record_length": 128, "pretrigger": 0.25})
    codes = np.arange(-64, 64, dtype=np.int16)
    card.fake_waveform = codes.copy()

    capture = scope.acquire()

    assert isinstance(capture, Capture)
    assert capture.channel_names == ("CH0",)
    assert capture.n_samples == 128
    assert capture.dt == pytest.approx(1e-6)
    assert capture.t0 == pytest.approx(-32 * 1e-6)  # pretrigger = 128*0.25 = 32
    np.testing.assert_array_equal(capture.raw[0], codes)


def test_acquire_deinterleaves_two_channels() -> None:
    scope, card = make_driver(channels=("CH0", "CH1"))
    scope.configure({"sample_rate": 1e6, "record_length": 32})
    ch0 = np.arange(32, dtype=np.int16)
    ch1 = -np.arange(32, dtype=np.int16)
    interleaved = np.empty(64, dtype=np.int16)
    interleaved[0::2] = ch0
    interleaved[1::2] = ch1
    card.fake_waveform = interleaved

    capture = scope.acquire()

    np.testing.assert_array_equal(capture["CH0"].raw, ch0)
    np.testing.assert_array_equal(capture["CH1"].raw, ch1)


def test_acquire_converts_codes_to_volts_with_configured_range() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"sample_rate": 1e6, "record_length": 32, "range": 1.0})
    card.fake_waveform = np.full(32, 2047, dtype=np.int16)

    capture = scope.acquire()

    np.testing.assert_allclose(capture["CH0"].volts, 1.0, atol=1e-6)


def test_acquire_writes_offset_as_percent_of_range() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"range": 1.0, "offset": 0.1, "record_length": 32})
    card.fake_waveform = np.zeros(32, dtype=np.int16)

    scope.acquire()

    assert card.registers[SPC_OFFS0] == 10  # 0.1 V / 1.0 V range = 10 %


def test_acquire_default_trigger_is_software() -> None:
    scope, card = make_driver()
    scope.configure({"record_length": 32})
    card.fake_waveform = np.zeros(32, dtype=np.int16)

    scope.acquire()

    assert card.registers[SPC_TRIG_ORMASK] == SPC_TMASK_SOFTWARE


def test_acquire_configures_channel_trigger() -> None:
    scope, card = make_driver(channels=("CH0", "CH1"))
    scope.configure(
        {
            "record_length": 32,
            "range": 1.0,
            "trigger": {"source": "CH1", "level": 0.5, "slope": "NEG"},
        }
    )
    card.fake_waveform = np.zeros(64, dtype=np.int16)

    scope.acquire()

    assert card.registers[SPC_TRIG_ORMASK] == SPC_TMASK_NONE
    assert card.registers[SPC_TRIG_ANDMASK] == 0
    assert card.registers[SPC_TRIG_CH_ANDMASK0] == 0
    assert card.registers[SPC_TRIG_CH_ORMASK0] == 0b10  # CH1 -> bit 1
    assert card.registers[SPC_TRIG_CH0_MODE + 1] == SPC_TM_NEG
    expected_code = volts_to_code(0.5, 1000, 2047)
    assert card.registers[SPC_TRIG_CH0_LEVEL0 + 1] == expected_code


def test_acquire_rejects_trigger_source_not_in_channels() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"record_length": 32, "trigger": {"source": "CH1"}})
    card.fake_waveform = np.zeros(32, dtype=np.int16)

    with pytest.raises(ValueError, match="trigger source"):
        scope.acquire()


def test_acquire_writes_registers_in_the_documented_order() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"sample_rate": 1e6, "record_length": 64, "range": 0.5})
    card.fake_waveform = np.zeros(64, dtype=np.int16)

    scope.acquire()

    assert card.commands == [
        M2CMD_CARD_RESET,
        M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_DATA_STARTDMA,
        M2CMD_CARD_WAITREADY | M2CMD_DATA_WAITDMA,
        M2CMD_DATA_STOPDMA,
    ]
    assert card.registers[SPC_CHENABLE] == 1
    assert card.registers[SPC_CARDMODE] == SPC_REC_STD_SINGLE
    assert card.registers[SPC_MEMSIZE] == 64
    assert card.registers[SPC_AMP0] == 500
    assert card.registers[SPC_OFFS0] == 0


def test_acquire_writes_posttrigger_from_record_length_minus_pretrigger() -> None:
    scope, card = make_driver()
    scope.configure({"record_length": 128, "pretrigger": 40})
    card.fake_waveform = np.zeros(128, dtype=np.int16)

    scope.acquire()

    assert card.registers[SPC_POSTTRIGGER] == 88


def test_acquire_timeout_stops_the_card_and_raises() -> None:
    card = FakeCard(timeout=True)
    scope = SpectrumM5i3367(card=card)
    scope.connect()
    scope.configure({"record_length": 32})
    card.fake_waveform = np.zeros(32, dtype=np.int16)

    with pytest.raises(TimeoutError, match="did not complete"):
        scope.acquire()

    # abort() stops the run and then releases the DMA transfer.
    assert card.commands[-2:] == [M2CMD_CARD_STOP, M2CMD_DATA_STOPDMA]


def test_acquire_raises_when_configured_for_multiple_recording() -> None:
    scope, card = make_driver()
    scope.configure({"segments": 4, "record_length": 32})
    card.fake_waveform = np.zeros(32 * 4, dtype=np.int16)

    with pytest.raises(RuntimeError, match="acquire_segments"):
        scope.acquire()


def test_metadata_is_json_serializable() -> None:
    scope, card = make_driver()
    scope.configure({"record_length": 32})
    card.fake_waveform = np.zeros(32, dtype=np.int16)

    capture = scope.acquire()

    json.dumps(capture.metadata)  # must not raise


# ---------------------------------------------------------------------------
# acquire_segments() -- Multiple Recording
# ---------------------------------------------------------------------------


def test_acquire_segments_raises_when_not_configured() -> None:
    scope, _card = make_driver()
    with pytest.raises(RuntimeError, match="set_segments"):
        scope.acquire_segments()


def test_acquire_segments_returns_one_capture_per_segment() -> None:
    scope, card = make_driver(channels=("CH0",))
    scope.configure({"segments": 3, "record_length": 32, "sample_rate": 1e6})
    # three distinguishable segments
    waveform = np.concatenate(
        [np.full(32, value, dtype=np.int16) for value in (1, 2, 3)]
    )
    card.fake_waveform = waveform

    captures = scope.acquire_segments()

    assert len(captures) == 3
    for index, capture in enumerate(captures):
        assert capture.n_samples == 32
        assert capture.metadata["segment_index"] == index
        np.testing.assert_array_equal(capture["CH0"].raw, np.full(32, index + 1))
        assert capture.dt == pytest.approx(1e-6)


def test_acquire_segments_writes_segment_registers() -> None:
    scope, card = make_driver()
    scope.configure({"segments": 5, "record_length": 32})
    card.fake_waveform = np.zeros(32 * 5, dtype=np.int16)

    scope.acquire_segments()

    assert card.registers[SPC_CARDMODE] == SPC_REC_STD_MULTI
    assert card.registers[SPC_SEGMENTSIZE] == 32
    assert card.registers[SPC_MEMSIZE] == 32 * 5


def test_acquire_segments_timeout_message() -> None:
    card = FakeCard(timeout=True)
    scope = SpectrumM5i3367(card=card)
    scope.connect()
    scope.configure({"segments": 2, "record_length": 32})
    card.fake_waveform = np.zeros(64, dtype=np.int16)

    with pytest.raises(TimeoutError, match="did not complete"):
        scope.acquire_segments()


# ---------------------------------------------------------------------------
# Hardware (real card), skipped by default
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_hardware_open_and_software_trigger_capture() -> None:
    with SpectrumM5i3367(channels=("CH0",)) as scope:
        assert "M5i" in scope.product_name
        assert scope.serial_number > 0
        scope.configure({"sample_rate": 1e6, "record_length": 1024, "range": 2.5})
        capture = scope.acquire()
        assert capture.n_samples == 1024
        assert np.all(np.isfinite(capture["CH0"].volts))
