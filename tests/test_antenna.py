import numpy as np
import pytest

from cetal_scopes import Antenna, TransferFunction


def make_transfer_function() -> TransferFunction:
    return TransferFunction(
        freq=[1.0e6, 1.0e7, 1.0e8],
        gain=[1.0 + 0j, 2.0 + 1j, 3.0 - 1j],
    )


def test_transfer_function_bounds_and_repr() -> None:
    transfer = make_transfer_function()
    assert transfer.f_min == 1.0e6
    assert transfer.f_max == 1.0e8
    assert transfer.unit == "V/(T/s)"
    assert "n_points=3" in repr(transfer)


def test_transfer_function_interpolates_real_and_imag() -> None:
    transfer = make_transfer_function()
    value = transfer.gain_at(1.0e7)
    assert value == pytest.approx(2.0 + 1.0j)


def test_transfer_function_returns_nan_outside_calibration() -> None:
    transfer = make_transfer_function()
    outside = transfer.gain_at([1.0e5, 1.0e9])
    assert np.all(np.isnan(outside))


def test_transfer_function_accepts_arrays() -> None:
    transfer = make_transfer_function()
    values = transfer.gain_at(np.array([1.0e6, 1.0e8]))
    np.testing.assert_allclose(values, [1.0 + 0j, 3.0 - 1j])


def test_transfer_function_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        TransferFunction(freq=[1.0, 2.0], gain=[1.0 + 0j])


def test_transfer_function_needs_two_points() -> None:
    with pytest.raises(ValueError, match="at least two"):
        TransferFunction(freq=[1.0], gain=[1.0 + 0j])


def test_transfer_function_requires_increasing_freq() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        TransferFunction(freq=[2.0, 1.0], gain=[1.0 + 0j, 2.0 + 0j])


def test_transfer_function_requires_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        TransferFunction(freq=[1.0, np.nan], gain=[1.0 + 0j, 2.0 + 0j])


def test_transfer_function_requires_1d_freq() -> None:
    with pytest.raises(ValueError, match="freq must be 1D"):
        TransferFunction(freq=[[1.0, 2.0]], gain=[1.0 + 0j, 2.0 + 0j])


def test_transfer_function_requires_unit() -> None:
    with pytest.raises(ValueError, match="unit"):
        TransferFunction(freq=[1.0, 2.0], gain=[1.0 + 0j, 2.0 + 0j], unit="")


def test_antenna_metadata_and_normalized_axis() -> None:
    antenna = Antenna(name="Bdot-X", kind="b-dot", axis=(2.0, 0.0, 0.0), delay=1.0e-9)
    assert antenna.unit_axis is not None
    np.testing.assert_allclose(antenna.unit_axis, [1.0, 0.0, 0.0])
    assert antenna.kind == "b-dot"
    assert antenna.delay == 1.0e-9


def test_antenna_without_axis_has_no_unit_axis() -> None:
    assert Antenna(name="probe").unit_axis is None


def test_antenna_gain_delegates_to_transfer_function() -> None:
    antenna = Antenna(name="Bdot-X", transfer_function=make_transfer_function())
    assert antenna.gain_at(1.0e7) == pytest.approx(2.0 + 1.0j)


def test_antenna_without_transfer_function_raises() -> None:
    with pytest.raises(ValueError, match="no transfer function"):
        Antenna(name="probe").gain_at(1.0e6)


def test_antenna_requires_name_and_kind() -> None:
    with pytest.raises(ValueError, match="name"):
        Antenna(name="")
    with pytest.raises(ValueError, match="kind"):
        Antenna(name="probe", kind="")


def test_antenna_rejects_bad_axis() -> None:
    with pytest.raises(ValueError, match="3 components"):
        Antenna(name="probe", axis=(1.0, 0.0))
    with pytest.raises(ValueError, match="non-zero"):
        Antenna(name="probe", axis=(0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="finite"):
        Antenna(name="probe", axis=(1.0, np.inf, 0.0))


def test_antenna_rejects_non_finite_delay() -> None:
    with pytest.raises(ValueError, match="delay"):
        Antenna(name="probe", delay=float("inf"))


def test_antenna_rejects_bad_position() -> None:
    with pytest.raises(ValueError, match="position"):
        Antenna(name="probe", position=(0.0, 0.0))


def test_antenna_repr() -> None:
    antenna = Antenna(
        name="Bdot-X", kind="b-dot", transfer_function=make_transfer_function()
    )
    assert repr(antenna) == "Antenna(name='Bdot-X', kind='b-dot', calibrated=True)"
