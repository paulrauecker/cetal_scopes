"""The staged ``arm`` / ``wait`` / ``fetch`` acquisition lifecycle.

Covers the base-class emulation on :class:`~cetal_scopes.scopes.base.Scope` and
the native implementations in both drivers, driven through the same in-memory
doubles the per-driver test modules use.
"""

from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest
from test_siglent import make_driver as make_siglent
from test_spectrum import FakeCard
from test_spectrum import make_driver as make_spectrum

from cetal_scopes import Capture, Scope, SpectrumM5i3367
from cetal_scopes.scopes.spectrum import (
    EXT0_LEVEL_LIMIT_MV,
    M2CMD_CARD_FORCETRIGGER,
    M2CMD_CARD_STOP,
    SPC_TM_NEG,
    SPC_TM_POS,
    SPC_TMASK_EXT0,
    SPC_TRIG_EXT0_LEVEL0,
    SPC_TRIG_EXT0_MODE,
    SPC_TRIG_ORMASK,
)

# ---------------------------------------------------------------------------
# Base-class emulation
# ---------------------------------------------------------------------------


class CountingScope(Scope):
    """Minimal driver that only implements ``acquire()``, like ``FakeScope``."""

    def __init__(self) -> None:
        self.acquisitions = 0

    def connect(self) -> None: ...

    def configure(self, settings: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...

    def acquire(self) -> Capture:
        self.acquisitions += 1
        return Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1.0)


def test_base_class_advertises_no_staged_support() -> None:
    assert CountingScope.supports_staged_acquisition is False
    assert CountingScope.supports_force_trigger is False


def test_base_emulation_runs_the_acquisition_inside_wait() -> None:
    scope = CountingScope()
    scope.arm()
    assert scope.armed
    assert scope.acquisitions == 0  # nothing happens until wait()

    assert scope.wait() is True
    assert scope.acquisitions == 1

    capture = scope.fetch()
    assert isinstance(capture, Capture)
    assert not scope.armed


def test_base_emulation_rejects_double_arm() -> None:
    scope = CountingScope()
    scope.arm()
    with pytest.raises(RuntimeError, match="already armed"):
        scope.arm()


def test_base_emulation_rejects_wait_and_fetch_before_arm() -> None:
    scope = CountingScope()
    with pytest.raises(RuntimeError, match="before arm"):
        scope.wait()
    with pytest.raises(RuntimeError, match="before a completed wait"):
        scope.fetch()


def test_base_abort_is_safe_and_disarms() -> None:
    scope = CountingScope()
    scope.abort()  # never armed
    scope.arm()
    scope.abort()
    assert not scope.armed
    scope.arm()  # abort really released it


def test_base_fetch_all_wraps_fetch() -> None:
    scope = CountingScope()
    scope.arm()
    scope.wait()
    assert len(scope.fetch_all()) == 1


def test_base_force_trigger_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="cannot force a trigger"):
        CountingScope().force_trigger()


def test_base_trigger_status_is_none() -> None:
    assert CountingScope().trigger_status() is None


# ---------------------------------------------------------------------------
# Siglent
# ---------------------------------------------------------------------------


def test_siglent_staged_matches_acquire() -> None:
    staged_scope, _ = make_siglent()
    staged_scope.connect()
    staged_scope.arm()
    assert staged_scope.wait() is True
    staged = staged_scope.fetch()

    oneshot_scope, _ = make_siglent()
    oneshot_scope.connect()
    oneshot = oneshot_scope.acquire()

    np.testing.assert_array_equal(staged.volts, oneshot.volts)
    np.testing.assert_array_equal(staged.raw, oneshot.raw)
    assert staged.t0 == oneshot.t0
    assert staged.dt == oneshot.dt


def test_siglent_arm_does_not_wait_for_the_trigger() -> None:
    scope, fake = make_siglent()
    scope.connect()
    fake.advance_on_run = False  # the trigger never arrives
    scope.arm()

    assert scope.armed
    assert ":TRIGger:RUN" in fake.written
    assert scope.wait(timeout=0.0) is False
    assert scope.armed  # a False wait leaves it armed, so it can be repeated


def test_siglent_wait_returns_false_instead_of_raising() -> None:
    scope, fake = make_siglent()
    scope.connect()
    fake.advance_on_run = False
    scope.arm()

    assert scope.wait(timeout=0.01) is False
    assert scope.wait(timeout=0.01) is False  # re-enterable


def test_siglent_acquire_still_raises_on_timeout() -> None:
    scope, fake = make_siglent()
    scope.connect()
    fake.advance_on_run = False
    scope._acquire_timeout = 0.01

    with pytest.raises(TimeoutError, match="did not complete"):
        scope.acquire()
    assert not scope.armed


def test_siglent_fetch_before_wait_raises() -> None:
    scope, _ = make_siglent()
    scope.connect()
    with pytest.raises(RuntimeError, match="before a completed wait"):
        scope.fetch()


def test_siglent_abort_stops_the_scope() -> None:
    scope, fake = make_siglent()
    scope.connect()
    scope.arm()
    scope.abort()

    assert not scope.armed
    assert fake.written[-1] == ":TRIGger:STOP"


def test_siglent_abort_is_safe_when_disconnected() -> None:
    scope, _ = make_siglent()
    scope.abort()  # never connected


def test_siglent_force_trigger_uses_the_ftrig_sweep_mode() -> None:
    scope, fake = make_siglent()
    scope.connect()
    scope.arm()
    scope.force_trigger()

    assert fake.written[-1] == ":TRIGger:MODE FTRIG"


def test_siglent_force_trigger_requires_an_armed_scope() -> None:
    scope, _ = make_siglent()
    scope.connect()
    with pytest.raises(RuntimeError, match="requires an armed scope"):
        scope.force_trigger()


def test_siglent_next_arm_restores_the_sweep_mode_after_forcing() -> None:
    scope, fake = make_siglent()
    scope.connect()
    scope.arm()
    scope.force_trigger()
    scope.wait()
    scope.fetch()

    fake.written.clear()
    scope.arm()
    assert ":TRIGger:MODE SINGle" in fake.written


def test_siglent_streaming_arm_leaves_the_scope_running_on_abort() -> None:
    scope, fake = make_siglent(streaming=True)
    scope.connect()
    scope.arm()
    fake.written.clear()
    scope.abort()

    assert not scope.armed
    assert ":TRIGger:STOP" not in fake.written


def test_siglent_streaming_reasserts_the_sweep_mode_after_forcing() -> None:
    scope, fake = make_siglent(streaming=True)
    scope.connect()
    # Streaming latches the counter after RUN, so the counter has to keep
    # advancing for the wait to complete.
    fake.advance_on_query = True
    scope.arm()
    scope.force_trigger()
    scope.wait()
    scope.fetch()

    fake.status = "Trig'd"
    fake.written.clear()
    scope.arm()
    assert ":TRIGger:MODE SINGle" in fake.written


def test_siglent_trigger_status_is_reported() -> None:
    scope, fake = make_siglent()
    scope.connect()
    fake.status = "Ready"
    assert scope.trigger_status() == "Ready"


def test_siglent_capture_carries_provenance_metadata() -> None:
    scope, _ = make_siglent()
    scope.connect()
    capture = scope.acquire()

    assert capture.metadata["instrument"] == "Siglent SDS6204L"
    assert capture.metadata["channels"] == ["C1", "C2"]
    assert capture.metadata["trigger_mode"] == "SINGle"
    assert "timestamp" in capture.metadata


# ---------------------------------------------------------------------------
# Spectrum M5i
# ---------------------------------------------------------------------------


def _waveform(card: FakeCard, n: int) -> None:
    card.fake_waveform = np.zeros(n, dtype=np.int16)


def test_spectrum_staged_matches_acquire() -> None:
    staged_scope, staged_card = make_spectrum()
    staged_scope.configure({"record_length": 32})
    _waveform(staged_card, 32)
    staged_scope.arm()
    assert staged_scope.wait() is True
    staged = staged_scope.fetch()

    oneshot_scope, oneshot_card = make_spectrum()
    oneshot_scope.configure({"record_length": 32})
    _waveform(oneshot_card, 32)
    oneshot = oneshot_scope.acquire()

    np.testing.assert_array_equal(staged.volts, oneshot.volts)
    assert staged.t0 == oneshot.t0
    assert staged.dt == oneshot.dt


def test_spectrum_arm_starts_the_card_without_waiting() -> None:
    scope, card = make_spectrum()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()

    assert scope.armed
    # START|ENABLETRIGGER|STARTDMA is issued, WAITREADY is not.
    assert not any(command & 16384 for command in card.commands)


def test_spectrum_wait_returns_false_on_timeout() -> None:
    card = FakeCard(timeout=True)
    scope = SpectrumM5i3367(card=card)
    scope.connect()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()

    assert scope.wait(timeout=0.01) is False
    assert scope.armed  # still running, per the card's own semantics


def test_spectrum_fetch_before_wait_raises() -> None:
    scope, _ = make_spectrum()
    with pytest.raises(RuntimeError, match="before a completed wait"):
        scope.fetch()


def test_spectrum_abort_is_safe_when_unarmed_and_disconnected() -> None:
    scope, _ = make_spectrum()
    scope.abort()
    scope.close()
    scope.abort()


def test_spectrum_force_trigger_issues_the_command() -> None:
    scope, card = make_spectrum()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()
    scope.force_trigger()

    assert card.commands[-1] == M2CMD_CARD_FORCETRIGGER


def test_spectrum_force_trigger_requires_an_armed_card() -> None:
    scope, _ = make_spectrum()
    with pytest.raises(RuntimeError, match="requires an armed card"):
        scope.force_trigger()


def test_spectrum_abort_stops_the_card() -> None:
    scope, card = make_spectrum()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()
    scope.abort()

    assert not scope.armed
    assert M2CMD_CARD_STOP in card.commands


def test_spectrum_fetch_all_returns_every_segment() -> None:
    scope, card = make_spectrum()
    scope.configure({"segments": 4, "record_length": 32})
    _waveform(card, 32 * 4)
    scope.arm()
    scope.wait()

    captures = scope.fetch_all()
    assert len(captures) == 4
    assert [c.metadata["segment_index"] for c in captures] == [0, 1, 2, 3]


def test_spectrum_fetch_rejects_the_wrong_card_mode() -> None:
    scope, card = make_spectrum()
    scope.configure({"segments": 2, "record_length": 32})
    _waveform(card, 32 * 2)
    scope.arm()
    scope.wait()

    with pytest.raises(RuntimeError, match="fetch_segments"):
        scope.fetch()


def test_spectrum_fetch_segments_rejects_standard_single() -> None:
    scope, card = make_spectrum()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()
    scope.wait()

    with pytest.raises(RuntimeError, match="not configured for Multiple Recording"):
        scope.fetch_segments()


def test_spectrum_double_arm_raises() -> None:
    scope, card = make_spectrum()
    scope.configure({"record_length": 32})
    _waveform(card, 32)
    scope.arm()
    with pytest.raises(RuntimeError, match="already armed"):
        scope.arm()


# ---------------------------------------------------------------------------
# Spectrum M5i external trigger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["EXT", "EXT0", "ext", "Ex"])
def test_spectrum_external_trigger_sets_the_ext0_mask(source: str) -> None:
    scope, card = make_spectrum()
    scope.configure(
        {
            "record_length": 32,
            "trigger": {"source": source, "level": 1.5, "slope": "RISing"},
        }
    )
    _waveform(card, 32)
    scope.acquire()

    assert card.registers[SPC_TRIG_ORMASK] == SPC_TMASK_EXT0
    assert card.registers[SPC_TRIG_EXT0_MODE] == SPC_TM_POS
    assert card.registers[SPC_TRIG_EXT0_LEVEL0] == 1500  # millivolts


def test_spectrum_external_trigger_honours_the_slope() -> None:
    scope, card = make_spectrum()
    scope.configure(
        {
            "record_length": 32,
            "trigger": {"source": "EXT", "level": -0.25, "slope": "FALLing"},
        }
    )
    _waveform(card, 32)
    scope.acquire()

    assert card.registers[SPC_TRIG_EXT0_MODE] == SPC_TM_NEG
    assert card.registers[SPC_TRIG_EXT0_LEVEL0] == -250


def test_spectrum_external_trigger_rejects_an_out_of_range_level() -> None:
    scope, card = make_spectrum()
    scope.configure(
        {
            "record_length": 32,
            "trigger": {"source": "EXT", "level": EXT0_LEVEL_LIMIT_MV / 1000 + 1},
        }
    )
    _waveform(card, 32)

    with pytest.raises(ValueError, match="outside the card"):
        scope.acquire()


def test_spectrum_unknown_trigger_source_names_the_external_option() -> None:
    # Reported by configure(), not by acquire(): one instrument failing to
    # arm takes a whole multi-instrument shot down with it, so a trigger that
    # cannot be realised must be caught where the setting was made.
    scope, _ = make_spectrum(channels=("CH0",))

    with pytest.raises(ValueError, match="external source"):
        scope.configure({"record_length": 32, "trigger": {"source": "CH1"}})
