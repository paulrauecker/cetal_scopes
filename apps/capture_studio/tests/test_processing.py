"""The serialisable processing pipeline."""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest
from processing import (
    STEPS,
    ProcessingGroup,
    ProcessingStep,
    apply_pipeline,
    group_of,
    groups_to_metadata,
    pipeline_to_metadata,
    step_catalog,
    validate_groups,
)

from cetal_scopes import Antenna, Channel, TransferFunction

DT = 1e-6
N = 4096


def make_channel(*, antenna: Antenna | None = None) -> Channel:
    t = np.arange(N, dtype=np.float64) * DT
    values = 2.0 + np.sin(2.0 * np.pi * 1e3 * t) + 0.5 * np.sin(2.0 * np.pi * 2e5 * t)
    return Channel(name="C1", volts=values, t0=0.0, dt=DT, antenna=antenna)


def test_the_catalog_covers_every_step() -> None:
    catalog = step_catalog()
    assert {info.name for info in catalog} == set(STEPS)
    assert all(info.summary for info in catalog)


def test_a_single_step_runs() -> None:
    step = ProcessingStep(name="lowpass", params={"cutoff": 1e4})
    filtered = step.apply(make_channel())

    assert filtered.n_samples == N
    assert float(np.std(filtered.volts)) < float(np.std(make_channel().volts))


def test_steps_run_in_order() -> None:
    steps = [
        ProcessingStep(name="subtract_baseline", params={}),
        ProcessingStep(name="lowpass", params={"cutoff": 1e4}),
    ]
    result, warnings = apply_pipeline(make_channel(), steps)

    assert warnings == []
    assert float(np.mean(result.volts)) == pytest.approx(0.0, abs=0.05)


def test_none_parameters_fall_back_to_the_function_default() -> None:
    # The UI sends every field; a blank one must not be passed as None.
    step = ProcessingStep(name="detrend", params={"type": None})
    assert step.apply(make_channel()).n_samples == N


def test_a_disabled_step_is_skipped() -> None:
    steps = [ProcessingStep(name="lowpass", params={"cutoff": 1e4}, enabled=False)]
    result, warnings = apply_pipeline(make_channel(), steps)

    assert warnings == []
    np.testing.assert_array_equal(result.volts, make_channel().volts)


def test_an_unknown_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown processing step"):
        ProcessingStep(name="teleport").apply(make_channel())


def test_a_step_that_cannot_run_here_is_reported_not_fatal() -> None:
    # One pipeline serves a whole shot, so b_field on a channel with no
    # antenna is normal rather than an error.
    steps = [
        ProcessingStep(name="b_field", params={}),
        ProcessingStep(name="lowpass", params={"cutoff": 1e4}),
    ]
    result, warnings = apply_pipeline(make_channel(), steps)

    assert len(warnings) == 1
    assert "b_field skipped" in warnings[0]
    assert result.n_samples == N  # the rest of the pipeline still ran


def test_errors_can_be_made_fatal() -> None:
    steps = [ProcessingStep(name="b_field", params={})]
    with pytest.raises(ValueError, match="no antenna"):
        apply_pipeline(make_channel(), steps, skip_errors=False)


def test_bad_parameters_are_reported_with_the_step_name() -> None:
    step = ProcessingStep(name="lowpass", params={"nonsense": 1.0})
    with pytest.raises(ValueError, match="step 'lowpass' rejected"):
        step.apply(make_channel())


def test_out_of_band_parameters_surface_as_a_warning() -> None:
    steps = [ProcessingStep(name="lowpass", params={"cutoff": 1e9})]
    _, warnings = apply_pipeline(make_channel(), steps)

    assert len(warnings) == 1
    assert "cutoff must be in" in warnings[0]


def test_the_pipeline_round_trips_through_json() -> None:
    steps = [
        ProcessingStep(name="gate", params={"t_start": 0.0, "t_end": 1e-3}),
        ProcessingStep(name="bandpass", params={"low": 1e3, "high": 1e5}),
    ]
    restored = [
        ProcessingStep.model_validate(item) for item in pipeline_to_metadata(steps)
    ]

    assert restored == steps


def test_metadata_omits_disabled_steps() -> None:
    steps = [
        ProcessingStep(name="detrend"),
        ProcessingStep(name="envelope", enabled=False),
    ]
    assert [item["name"] for item in pipeline_to_metadata(steps)] == ["detrend"]


def test_groups_may_span_instruments() -> None:
    groups = [
        ProcessingGroup(name="x", channels=["siglent:C1", "m5i:CH0"]),
        ProcessingGroup(name="y", channels=["siglent:C2"]),
    ]
    validate_groups(groups)

    owners = group_of(groups)
    assert owners["m5i:CH0"].name == "x"
    assert owners["siglent:C2"].name == "y"
    assert "m5i:CH1" not in owners


def test_a_channel_in_two_groups_is_rejected() -> None:
    groups = [
        ProcessingGroup(name="x", channels=["siglent:C1"]),
        ProcessingGroup(name="y", channels=["siglent:C1"]),
    ]
    with pytest.raises(ValueError, match="at most one group"):
        validate_groups(groups)


def test_group_metadata_keeps_only_enabled_steps() -> None:
    group = ProcessingGroup(
        name="x",
        channels=["a:b"],
        steps=[
            ProcessingStep(name="detrend"),
            ProcessingStep(name="lowpass", enabled=False),
        ],
    )
    (stored,) = groups_to_metadata([group])

    assert stored["channels"] == ["a:b"]
    assert [step["name"] for step in stored["steps"]] == ["detrend"]


#: Parameters that make each catalogue step actually run on make_channel().
RUNNABLE: dict[str, dict[str, Any]] = {
    "gate": {"t_start": 1e-3, "t_end": 3e-3},
    "subtract_baseline": {"t_start": 0.0, "t_end": 1e-3},
    "detrend": {},
    "remove_adc_comb": {"period": 16},
    "lowpass": {"cutoff": 1e4},
    "highpass": {"cutoff": 1e4},
    "bandpass": {"low": 1e3, "high": 1e5},
    "bandstop": {"low": 1e3, "high": 1e5},
    "moving_average": {"window_s": 1e-5},
    "savgol": {"window_s": 1e-5},
    "wiener": {"noise_start": 0.0, "noise_end": 1e-3, "signal_end": 4e-3},
    "resample": {"dt": 2e-6},
    "envelope": {},
    "b_field_rate": {"outside": "zero"},
    "b_field": {"outside": "zero"},
}


def test_every_step_has_runnable_parameters_here() -> None:
    assert set(RUNNABLE) == set(STEPS)


@pytest.mark.parametrize("name", sorted(STEPS))
def test_a_step_never_modifies_its_input(name: str) -> None:
    # Processed channels share their arrays with the recorded capture (the
    # aligned view is a dataclass replace, not a copy), so processing is
    # non-destructive only as long as no step writes into its input.
    antenna = Antenna(
        name="probe",
        kind="b-dot",
        transfer_function=TransferFunction(
            freq=[1e2, 1e6], gain=[1e-9 + 0j, 1e-9 + 0j], unit="V/(T/s)"
        ),
    )
    channel = make_channel(antenna=antenna)
    codes = np.arange(N, dtype=np.int16)
    channel = dataclasses.replace(channel, raw=codes)
    volts = channel.volts.copy()
    raw = codes.copy()

    step = ProcessingStep(name=name, params=RUNNABLE[name])
    _, warnings = apply_pipeline(channel, [step], skip_errors=False)

    assert warnings == []
    np.testing.assert_array_equal(channel.volts, volts)
    np.testing.assert_array_equal(codes, raw)
