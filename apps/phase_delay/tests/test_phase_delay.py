"""Tests for the phase-delay app (no hardware required)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import phase_delay
import pytest


def test_frequency_plan_inclusive_and_capped() -> None:
    freqs = phase_delay.frequency_plan(100e6, 2e9, 25e6)
    assert freqs[0] == 100e6
    assert freqs[-1] == 2e9
    assert len(freqs) == 77
    with pytest.raises(ValueError):
        phase_delay.frequency_plan(100e6, 3e9, 25e6)


def test_comb_points_on_the_default_grid() -> None:
    freqs = phase_delay.frequency_plan(100e6, 2e9, 25e6)
    hits = freqs[phase_delay.near_adc_comb(freqs, 100e3)]
    assert hits.tolist() == [625e6, 1250e6, 1875e6]


def test_power_cap(tmp_path: Path) -> None:
    assert phase_delay.main(["--demo", "--power", "6", "--out", str(tmp_path)]) == 2


def test_demo_recovers_known_delays(tmp_path: Path) -> None:
    argv = ["--demo", "--repeats", "2", "--step", "50e6", "--out", str(tmp_path)]
    assert phase_delay.main(argv) == 0
    (run,) = tmp_path.iterdir()
    result = json.loads((run / "result.json").read_text())
    for ch, expected in phase_delay.DemoScope.DELAYS.items():
        if ch == "C1":
            continue
        got = result["channels"][ch]["delay_ps"]
        assert got == pytest.approx(expected * 1e12, abs=0.3)
    assert (run / "points.csv").exists()
    assert (run / "phase_delay.png").exists()
    sweep = np.load(run / "sweep.npz")
    assert sweep["dphi"].shape == (39, 2, 3)
