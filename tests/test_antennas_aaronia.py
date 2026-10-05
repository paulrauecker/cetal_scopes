import math

import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import b_field
from cetal_scopes.antennas import PBS1_PROBES, pbs1

# Values printed by Aaronia's PBS1 converter workbooks (not in the repo): the
# Voltage converter's example
# (2e-5 V at 0.1 MHz) and the Power converter's (-40 dBm at 100 MHz).
VOLTAGE_SHEET = {
    "H1": 6.942965280479832e-06,
    "H2": 4.915243001441141e-07,
    "H3": 4.9721588329526106e-08,
    "H4": 1.2663244536421584e-08,
    "E1": 408.8321054764699,
}
POWER_SHEET = {
    "H1": 7.762471166286902e-07,
    "H2": 5.495408738576236e-08,
    "H3": 5.55904257270404e-09,
    "H4": 1.4157937799570766e-09,
    "E1": 45.70881896148753,
}


def field_from_reading(model: str, volts: float, freq: float) -> float:
    """Invert ``V = gain * 2 pi f * field`` for a sinusoid."""
    return volts / (PBS1_PROBES[model].gain * 2.0 * math.pi * freq)


@pytest.mark.parametrize("model", sorted(VOLTAGE_SHEET))
def test_matches_voltage_converter_sheet(model: str) -> None:
    field = field_from_reading(model, 2.0e-5, 0.1e6)
    assert field == pytest.approx(VOLTAGE_SHEET[model], rel=1e-12)


@pytest.mark.parametrize("model", sorted(POWER_SHEET))
def test_matches_power_converter_sheet(model: str) -> None:
    volts = 10.0 ** (-40.0 / 20.0) * math.sqrt(1.0e-3 * 50.0)
    field = field_from_reading(model, volts, 100.0e6)
    assert field == pytest.approx(POWER_SHEET[model], rel=1e-12)


def test_factory_builds_calibrated_antenna() -> None:
    antenna = pbs1("H2", axis=(0.0, 0.0, 1.0), delay=2.0e-9)
    assert antenna.name == "PBS-H2"
    assert antenna.kind == "b-dot"
    assert antenna.delay == 2.0e-9
    transfer = antenna.transfer_function
    assert transfer is not None
    assert transfer.unit == "V/(T/s)"
    assert (transfer.f_min, transfer.f_max) == (0.0, 1.0e9)
    assert antenna.gain_at(5.0e8) == pytest.approx(PBS1_PROBES["H2"].gain)
    assert np.isnan(antenna.gain_at(2.0e9))


def test_e1_is_a_d_dot() -> None:
    antenna = pbs1("E1", name="E-ref")
    assert antenna.name == "E-ref"
    assert antenna.kind == "d-dot"
    assert antenna.transfer_function is not None
    assert antenna.transfer_function.unit == "V/(V/m/s)"


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="unknown PBS1 probe"):
        pbs1("H9")  # pyright: ignore[reportArgumentType]


def test_b_field_reproduces_sheet_end_to_end() -> None:
    # A 1 MHz sine of 2e-5 V peak on an H1 must integrate to the field the
    # workbook gives for that reading (peak in, peak out; the model is linear).
    dt = 1.0e-9
    n = 10_000
    freq = 1.0e6
    time = np.arange(n) * dt
    channel = Channel(
        name="C1",
        volts=2.0e-5 * np.cos(2 * np.pi * freq * time),
        t0=0.0,
        dt=dt,
        antenna=pbs1("H1"),
        unit="V",
    )
    field = b_field(channel)
    expected = VOLTAGE_SHEET["H1"] / 10.0  # sheet is at 0.1 MHz, field ~ 1/f
    assert np.max(np.abs(field.volts)) == pytest.approx(expected, rel=1e-6)
