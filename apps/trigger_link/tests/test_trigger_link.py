"""Tests for the trigger-link app (no hardware required)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trigger_link


def make_shots(f: float, jitter: float, delay: float, n: int = 400) -> list:
    rng = np.random.default_rng(0)
    w = 2 * np.pi * f
    shots = []
    for _ in range(n):
        t_sig = rng.normal(0, jitter)
        t_m5i = t_sig + delay + rng.normal(0, jitter)
        shots.append(
            trigger_link.Shot(
                siglent=w * t_sig,
                siglent_other=w * t_sig + 0.01,
                m5i=w * t_m5i,
                m5i_amplitude=0.1,
                m5i_phase_std=1e-4,
            )
        )
    return shots


def test_circular_spread_handles_the_wrap() -> None:
    p = np.array([np.pi - 0.01, -np.pi + 0.01])
    assert trigger_link.circular_spread(p) == pytest.approx(0.01)


def test_summary_recovers_jitter_and_flags_resolution() -> None:
    r = trigger_link.summarize(300e6, make_shots(300e6, 30e-12, 5e-9), 0, 0)
    assert r.siglent_jitter == pytest.approx(30e-12, rel=0.1)
    assert r.link_jitter == pytest.approx(30e-12, rel=0.1)  # only M5i's own part
    assert r.siglent_pair_jitter == pytest.approx(0.0, abs=1e-15)
    assert r.link_resolved


def test_link_delay_from_the_phase_slope() -> None:
    delay = 7.3e-9
    freqs = [100e6, 110e6, 120e6, 130e6]
    results = [
        trigger_link.summarize(f, make_shots(f, 20e-12, delay), 0, 0) for f in freqs
    ]
    assert trigger_link.link_delay(results) == pytest.approx(delay, rel=1e-3)


def test_link_delay_needs_resolved_points() -> None:
    shots = make_shots(100e6, 20e-12, 1e-9)
    for s in shots:
        s.m5i_phase_std = 10.0  # fit noise swamps the jitter
    r = trigger_link.summarize(100e6, shots, 0, 0)
    assert not r.link_resolved
    assert trigger_link.link_delay([r, r]) is None


def test_power_cap(tmp_path: Path) -> None:
    assert trigger_link.main(["--power", "6", "--out", str(tmp_path)]) == 2
