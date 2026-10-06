"""Tests for the B-dot vector view's signal processing (no hardware)."""

from __future__ import annotations

import bdot_vector
import numpy as np
import pytest


def _tones(p: np.ndarray, freq: float, dt: float, n: int) -> np.ndarray:
    t = np.arange(n) * dt
    return np.real(p[:, None] * np.exp(2j * np.pi * freq * t))


def test_find_tone_and_phasors_recover_the_signal() -> None:
    dt, n, freq = 1e-10, 100_000, 23.4e6
    p = np.array([1e-3, 0.5e-3j, 0.2e-3])
    volts = _tones(p, freq, dt, n)
    found = bdot_vector.find_tone(volts, dt)
    assert found == pytest.approx(freq, rel=1e-4)
    t = np.arange(n, dtype=np.float64) * dt
    assert bdot_vector.phasors(volts, t, freq) == pytest.approx(p, abs=1e-6)


def test_find_tone_skips_the_adc_comb() -> None:
    dt, n = 1e-10, 51_200
    comb = 1.0 / (dt * bdot_vector.COMB_PERIOD) * 8  # fs / 32
    volts = _tones(np.array([1.0, 1.0, 1.0]), comb, dt, n)
    volts += _tones(np.array([0.1, 0, 0]), 50e6, dt, n)
    assert bdot_vector.find_tone(volts, dt) == pytest.approx(50e6, rel=1e-3)


def test_major_axis_is_the_peak_of_the_oscillation() -> None:
    b = np.array([3.0, 1.5j, 1.0]) * np.exp(0.7j)
    u = bdot_vector.major_axis(b)
    phase = np.linspace(0, 2 * np.pi, 3601)
    trace = np.real(b[:, None] * np.exp(1j * phase))
    assert np.linalg.norm(u) == pytest.approx(np.linalg.norm(trace, axis=0).max())
