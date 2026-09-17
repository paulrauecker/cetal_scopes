"""The shot summary: what each instrument actually did, for the log and the shell."""

from __future__ import annotations

import numpy as np
from session import summarize_shot

from cetal_scopes import Capture, Shot


def make_shot(**metadata: object) -> Shot:
    shot = Shot()
    base: dict[str, object] = {"instrument": "Siglent SDS6204L"}
    base.update(metadata)
    shot.add(
        "siglent",
        Capture(
            volts=np.zeros((2, 1000)),
            t0=0.0,
            dt=1e-10,
            channel_names=("C1", "C2"),
            metadata=base,
        ),
    )
    return shot


def summary_of(**metadata: object) -> str:
    return "\n".join(summarize_shot(make_shot(**metadata)))


def test_summary_names_the_instrument_and_its_channels() -> None:
    text = summary_of()
    assert "siglent: Siglent SDS6204L" in text
    assert "2 ch (C1, C2)" in text
    assert "10 GS/s" in text
    assert "1000 pts" in text
    assert "window 100 ns" in text


def test_a_sample_rate_the_scope_did_not_honor_is_shown_against_the_request() -> None:
    """The Siglent derives its rate from timebase x depth, so it can overshoot."""
    text = summary_of(sample_rate_requested_hz=2e9)
    assert "10 GS/s (asked 2 GS/s)" in text


def test_a_sample_rate_that_matches_is_not_cluttered_with_the_request() -> None:
    text = summary_of(sample_rate_requested_hz=1e10)
    assert "10 GS/s" in text
    assert "asked" not in text


def test_a_record_length_the_scope_rounded_up_is_shown_against_the_request() -> None:
    text = summary_of(record_length_requested=100)
    assert "1000 pts (asked 100)" in text


def test_the_trigger_voltage_is_reported_when_the_driver_records_one() -> None:
    text = summary_of(trigger_source="C1", trigger_level_v=0.5, trigger_slope="RISing")
    assert "trigger C1 at 500 mV RISing" in text


def test_a_clamped_trigger_level_is_flagged_against_what_was_asked() -> None:
    """The level is silently clamped to about +/-4.5 * V/div of the source."""
    text = summary_of(
        trigger_source="C1", trigger_level_v=0.045, trigger_level_requested_v=0.5
    )
    assert "45 mV (asked 500 mV)" in text
    assert "clamped" in text


def test_a_trigger_level_that_stuck_is_not_flagged() -> None:
    text = summary_of(
        trigger_source="C1", trigger_level_v=0.5, trigger_level_requested_v=0.5
    )
    assert "clamped" not in text


def test_no_trigger_line_when_the_driver_records_no_trigger() -> None:
    assert "trigger" not in summary_of()


def test_verbose_adds_every_metadata_key() -> None:
    shot = make_shot(memory_depth="1M")
    assert "memory_depth = '1M'" not in "\n".join(summarize_shot(shot))
    assert "memory_depth = '1M'" in "\n".join(summarize_shot(shot, verbose=True))


# --- vertical resolution, measured rather than claimed ----------------------


def quantised_capture(span: float, codes: int, **metadata: object) -> Capture:
    t = np.arange(4096) * 2e-10
    signal = 0.0008 * np.sin(2 * np.pi * 3e7 * t)
    lsb = 2 * span / codes
    base: dict[str, object] = {"instrument": "test"}
    base.update(metadata)
    return Capture(
        volts=np.vstack([np.round(signal / lsb) * lsb] * 2),
        t0=0.0,
        dt=2e-10,
        channel_names=("A", "B"),
        metadata=base,
    )


def summary_of_capture(capture: Capture) -> str:
    shot = Shot()
    shot.add("x", capture)
    return "\n".join(summarize_shot(shot))


def test_the_measured_step_is_the_adc_code_size() -> None:
    """8 bits over +/-20 mV is 156 uV per code."""
    assert "step 156 uV" in summary_of_capture(quantised_capture(0.02, 256))


def test_more_bits_over_the_same_span_measure_finer() -> None:
    coarse = summary_of_capture(quantised_capture(0.2, 256))
    fine = summary_of_capture(quantised_capture(0.2, 4096))
    assert "step 1.56 mV" in coarse
    assert "step 97.7 uV" in fine


def test_the_code_count_is_reported_when_the_range_is_known() -> None:
    text = summary_of_capture(
        quantised_capture(0.2, 4096, channel_range_mv={"A": 200, "B": 200})
    )
    assert "over +/-200 mV" in text
    assert "= 4096 codes" in text


def test_a_constant_channel_reports_no_step_rather_than_guessing() -> None:
    capture = Capture(
        volts=np.zeros((1, 64)),
        t0=0.0,
        dt=1e-9,
        channel_names=("A",),
        metadata={"instrument": "test"},
    )
    assert "step" not in summary_of_capture(capture)
