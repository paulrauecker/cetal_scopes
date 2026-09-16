"""Phase-correct three-axis vector reconstruction."""

from __future__ import annotations

import numpy as np
import pytest

from cetal_scopes import Antenna, Capture
from cetal_scopes.analysis import vector_at

DT = 1e-10
N = 4096
FREQ = 5e8


def make_capture(
    components: tuple[float, float, float],
    *,
    antennas: dict[str, Antenna] | None = None,
    phases: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Capture:
    t = np.arange(N, dtype=np.float64) * DT
    rows = [
        amplitude * np.sin(2.0 * np.pi * FREQ * t + phase)
        for amplitude, phase in zip(components, phases, strict=True)
    ]
    return Capture(
        volts=np.vstack(rows),
        t0=0.0,
        dt=DT,
        channel_names=("C1", "C2", "C3"),
        antennas=antennas,
    )


def test_a_negative_component_comes_back_negative() -> None:
    # The whole point: FFT magnitudes would report +0.5 here and put the
    # vector in the wrong octant.
    capture = make_capture((1.0, 0.5, 0.25), phases=(0.0, np.pi, 0.0))
    vector = vector_at(capture, FREQ)

    assert vector.real[0] == pytest.approx(1.0, rel=0.02)
    assert vector.real[1] == pytest.approx(-0.5, rel=0.02)
    assert vector.real[2] == pytest.approx(0.25, rel=0.02)


def test_components_are_recovered_in_channel_order() -> None:
    vector = vector_at(make_capture((1.0, 0.5, 0.25)), FREQ)

    assert vector.channels == ("C1", "C2", "C3")
    np.testing.assert_allclose(vector.real, [1.0, 0.5, 0.25], rtol=0.02)


def test_magnitude_is_the_root_sum_square() -> None:
    vector = vector_at(make_capture((3.0, 4.0, 0.0)), FREQ)
    assert vector.magnitude == pytest.approx(5.0, rel=0.02)


def test_a_linear_field_has_near_zero_ellipticity() -> None:
    vector = vector_at(make_capture((1.0, 0.5, 0.25)), FREQ)

    assert vector.ellipticity < 0.02
    np.testing.assert_allclose(vector.quadrature, 0.0, atol=0.03)


def test_a_quadrature_component_shows_up_as_ellipticity() -> None:
    capture = make_capture((1.0, 1.0, 0.0), phases=(0.0, np.pi / 2, 0.0))
    vector = vector_at(capture, FREQ)

    assert vector.ellipticity == pytest.approx(1 / np.sqrt(2), rel=0.05)
    assert abs(vector.quadrature[1]) == pytest.approx(1.0, rel=0.02)


def test_the_reference_channel_is_purely_real() -> None:
    capture = make_capture((1.0, 0.5, 0.25), phases=(0.7, 0.7, 0.7))
    vector = vector_at(capture, FREQ)

    assert vector.quadrature[0] == pytest.approx(0.0, abs=1e-6)
    assert vector.reference_phase != 0.0


def test_a_different_reference_can_be_chosen() -> None:
    capture = make_capture((1.0, 0.5, 0.25))
    vector = vector_at(capture, FREQ, reference="C2")

    assert vector.quadrature[1] == pytest.approx(0.0, abs=1e-6)


def test_an_antenna_delay_is_removed_as_a_phase_rotation() -> None:
    # C2 lags by exactly half a period, which without delay correction reads
    # as an inverted component.
    delay = 0.5 / FREQ
    antennas = {
        "C1": Antenna(name="x", axis=(1.0, 0.0, 0.0)),
        "C2": Antenna(name="y", axis=(0.0, 1.0, 0.0), delay=delay),
        "C3": Antenna(name="z", axis=(0.0, 0.0, 1.0)),
    }
    capture = make_capture(
        (1.0, 1.0, 0.0), antennas=antennas, phases=(0.0, -np.pi, 0.0)
    )
    vector = vector_at(capture, FREQ)

    assert vector.real[1] == pytest.approx(1.0, rel=0.02)


def test_explicit_delays_override_the_antennas() -> None:
    antennas = {"C2": Antenna(name="y", delay=1e-9)}
    capture = make_capture((1.0, 1.0, 0.0), antennas=antennas)
    vector = vector_at(capture, FREQ, delays={"C1": 0.0, "C2": 0.0, "C3": 0.0})

    assert vector.real[1] == pytest.approx(1.0, rel=0.02)


def test_lab_vector_uses_the_probe_orientations() -> None:
    antennas = {
        "C1": Antenna(name="x", axis=(1.0, 0.0, 0.0)),
        "C2": Antenna(name="y", axis=(0.0, 1.0, 0.0)),
        "C3": Antenna(name="z", axis=(0.0, 0.0, 1.0)),
    }
    vector = vector_at(make_capture((1.0, 0.5, 0.25), antennas=antennas), FREQ)

    assert vector.axes is not None
    np.testing.assert_allclose(vector.lab_vector, vector.real, atol=1e-9)


def test_lab_vector_falls_back_to_the_components_without_antennas() -> None:
    vector = vector_at(make_capture((1.0, 0.5, 0.25)), FREQ)

    assert vector.axes is None
    np.testing.assert_allclose(vector.lab_vector, vector.real)


def test_a_missing_channel_is_reported() -> None:
    with pytest.raises(KeyError, match="C9"):
        vector_at(make_capture((1.0, 1.0, 1.0)), FREQ, channels=("C1", "C9"))


def test_too_few_channels_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least two channels"):
        vector_at(make_capture((1.0, 1.0, 1.0)), FREQ, channels=("C1",))


def test_a_negative_frequency_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        vector_at(make_capture((1.0, 1.0, 1.0)), -1.0)


def test_a_reference_outside_the_channels_is_rejected() -> None:
    with pytest.raises(ValueError, match="is not one of the channels"):
        vector_at(make_capture((1.0, 1.0, 1.0)), FREQ, reference="C9")
