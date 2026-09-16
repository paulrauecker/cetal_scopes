"""The serialisable processing pipeline."""

from __future__ import annotations

import numpy as np
import pytest
from processing import (
    STEPS,
    ProcessingStep,
    apply_pipeline,
    pipeline_to_metadata,
    step_catalog,
)

from cetal_scopes import Antenna, Channel

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
