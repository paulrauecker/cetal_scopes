"""Tests for the B-dot transient app's signal processing (no hardware)."""

from __future__ import annotations

import bdot_transient
import numpy as np
import pytest

from cetal_scopes import Capture

DT = 1e-10


def _capture(volts: np.ndarray) -> Capture:
    n = volts.shape[1]
    return Capture(
        volts=volts, t0=-n // 2 * DT, dt=DT, channel_names=bdot_transient.CHANNELS
    )


def test_running_integral_recovers_a_field_step() -> None:
    n = 2000
    volts = np.full((3, n), 0.003)  # offset: must not integrate into a ramp
    volts[1, 1200:1210] += 0.01  # 1 ns pulse on C2
    trace = bdot_transient.field_trace(_capture(volts), [2e-4, 1e-4, 3e-4])
    assert trace.unit == "T"
    # Integral of the pulse: 0.01 V * 1 ns / 1e-4 m^2 = 1e-7 T, held after it.
    assert trace.values[1, -1] == pytest.approx(1e-7)
    assert trace.values[1, :1000] == pytest.approx(0.0, abs=1e-15)
    assert trace.values[0] == pytest.approx(0.0, abs=1e-15)


def test_raw_mode_only_removes_the_baseline() -> None:
    volts = np.full((3, 1000), 0.5)
    volts[2, 700] = 1.5
    trace = bdot_transient.field_trace(_capture(volts), [1.0], raw=True)
    assert trace.unit == "V"
    assert trace.values[2, 700] == pytest.approx(1.0)
    assert trace.values[2, 699] == pytest.approx(0.0)


def test_baseline_needs_pretrigger_samples() -> None:
    with pytest.raises(ValueError, match="pretrigger"):
        bdot_transient.pretrigger_mask(np.arange(10, dtype=np.float64) * DT, 0.0)


def test_event_window_pads_around_the_event() -> None:
    values = np.zeros((3, 1000))
    values[0, 500:520] = 1.0
    trace = bdot_transient.FieldTrace(
        t=np.arange(1000, dtype=np.float64) * DT, values=values, unit="V"
    )
    assert bdot_transient.event_window(trace, pad=1e-9) == (490, 530)


def test_clipped_channels_flags_full_scale() -> None:
    volts = np.zeros((3, 100))
    volts[2, 50] = 0.199  # 50 mV/div spans +/-0.2 V
    assert bdot_transient.clipped_channels(_capture(volts), 0.05) == ["C3"]


def test_playback_steps_whole_samples_and_never_stalls() -> None:
    assert bdot_transient.playback_step(1.0, DT) == 1  # 0.5 samples -> 1
    assert bdot_transient.playback_step(1000.0, DT) == 500
