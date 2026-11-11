"""Tests for the cable-swap solver (no hardware required)."""

from __future__ import annotations

import json
from pathlib import Path

import cable_swap
import numpy as np
import pytest

CABLES = {"1": 0.0, "2": 12e-12, "3": -7e-12, "4": 30e-12, "5": 4e-12, "6": -20e-12}
PORTS = {"P1": 0.0, "P2": 3e-12, "P3": -5e-12}
INPUTS = {"C1": 0.0, "C2": 40e-12, "C3": -15e-12, "C4": 8e-12}

#: The bench plan: scope ends rotate (runs 1-4), then whole cables (5-8).
PLAN = [
    "C1=1@P1,C2=2@P2,C3=3@P3",
    "C2=1@P1,C3=2@P2,C4=3@P3",
    "C3=1@P1,C4=2@P2,C1=3@P3",
    "C4=1@P1,C1=2@P2,C2=3@P3",
    "C1=4@P1,C2=5@P2,C3=6@P3",
    "C1=6@P1,C2=4@P2,C3=5@P3",
    "C1=6@P1,C2=4@P2,C3=1@P3",
    "C1=1@P1,C2=6@P2,C3=4@P3",
]


def write_run(tmp: Path, name: str, mapping: str, noise: float = 0.0) -> Path:
    rng = np.random.default_rng(abs(hash(name)) % 2**32)
    parsed = cable_swap.parse_mapping(mapping)
    tau = {}
    for ch, (cable, port) in parsed.items():
        tau[ch] = INPUTS[ch] + CABLES[cable] + (PORTS[port] if port else 0.0)
    ref = next(iter(parsed))
    channels = {
        ch: {
            "delay_ps": (tau[ch] - tau[ref] + rng.normal(0, noise)) * 1e12,
            "delay_stderr_ps": 0.1,
        }
        for ch in parsed
        if ch != ref
    }
    d = tmp / name
    d.mkdir()
    (d / "result.json").write_text(json.dumps({"reference": ref, "channels": channels}))
    return d


def solve_plan(tmp: Path, plan: list[str], noise: float = 0.0) -> cable_swap.Solution:
    obs = []
    for i, mapping in enumerate(plan):
        d = write_run(tmp, f"r{i}", mapping, noise)
        obs += cable_swap.load_run(i, d, cable_swap.parse_mapping(mapping))
    return cable_swap.solve(obs)


def test_bench_plan_recovers_every_factor(tmp_path: Path) -> None:
    s = solve_plan(tmp_path, PLAN)
    for truth, factor in ((CABLES, "cable"), (INPUTS, "input"), (PORTS, "port")):
        for k, v in truth.items():
            assert s.values[factor][k] == pytest.approx(v, abs=1e-15), (factor, k)
    assert s.dof == 3 * len(PLAN) - (5 + 3 + 2 + len(PLAN))


def test_without_the_linking_runs_cables_4_6_float(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="undetermined"):
        solve_plan(tmp_path, PLAN[:6])


def test_noise_shows_in_errors(tmp_path: Path) -> None:
    s = solve_plan(tmp_path, PLAN, noise=0.5e-12)
    assert s.chi2_reduced > 1
    assert 0 < s.errors["cable"]["2"] < 2e-12
    assert s.values["cable"]["2"] == pytest.approx(12e-12, abs=2e-12)


def test_position_model_without_ports(tmp_path: Path) -> None:
    plan = ["C1=A,C2=B,C3=C", "C1=C,C2=A,C3=B"]
    cables = {"A": 0.0, "B": 5e-12, "C": -3e-12}
    saved = dict(CABLES)
    CABLES.update(cables)
    try:
        s = solve_plan(tmp_path, plan)
    finally:
        CABLES.clear()
        CABLES.update(saved)
    assert "port" not in s.values
    assert s.values["cable"]["B"] == pytest.approx(5e-12, abs=1e-15)
    assert s.values["input"]["C2"] == pytest.approx(40e-12, abs=1e-15)


def test_mapping_must_cover_the_run(tmp_path: Path) -> None:
    d = write_run(tmp_path, "r", PLAN[0])
    with pytest.raises(ValueError, match="mapping"):
        cable_swap.load_run(0, d, cable_swap.parse_mapping("C1=1@P1,C2=2@P2"))


@pytest.mark.parametrize("bad", ["C1=A,C2=A", "C1=A@P1,C2=B@P1", "C1=A@P1,C2=B", "C1"])
def test_parse_mapping_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        cable_swap.parse_mapping(bad)
