import numpy as np
import pytest

from cetal_scopes import Antenna, Channel, TransferFunction
from cetal_scopes.analysis import b_field, b_field_rate, b_magnitude

DT = 1.0e-9
N = 4096
F0 = 200 * (1.0 / DT) / N
T = np.arange(N) * DT
AMPLITUDE = 3.0e-3
GAIN = 2.5


def flat_transfer(f_min: float = 1.0e6, f_max: float = 4.0e8) -> TransferFunction:
    return TransferFunction(
        freq=[f_min, f_max], gain=[GAIN + 0j, GAIN + 0j], unit="V/(T/s)"
    )


def make_bdot_channel(
    *, delay: float = 0.0, transfer: TransferFunction | None = None
) -> Channel:
    antenna = Antenna(
        name="Bdot-X",
        kind="b-dot",
        axis=(1.0, 0.0, 0.0),
        transfer_function=flat_transfer() if transfer is None else transfer,
        delay=delay,
    )
    db_dt = AMPLITUDE * 2 * np.pi * F0 * np.cos(2 * np.pi * F0 * T)
    volts = GAIN * db_dt
    return Channel(name="C1", volts=volts, t0=0.0, dt=DT, antenna=antenna, unit="V")


def test_b_field_rate_recovers_derivative() -> None:
    channel = make_bdot_channel()
    expected = AMPLITUDE * 2 * np.pi * F0 * np.cos(2 * np.pi * F0 * T)
    result = b_field_rate(channel)
    assert result.unit == "T/s"
    assert result.antenna is channel.antenna
    np.testing.assert_allclose(result.volts, expected, rtol=1e-9, atol=1e-6)


def test_b_field_recovers_field() -> None:
    channel = make_bdot_channel()
    expected = AMPLITUDE * np.sin(2 * np.pi * F0 * T)
    result = b_field(channel)
    assert result.unit == "T"
    np.testing.assert_allclose(result.volts, expected, atol=1e-12)
    assert result.raw is None


def test_b_field_applies_delay_correction() -> None:
    delay_samples = 8
    channel = make_bdot_channel()
    delayed = Channel(
        name=channel.name,
        volts=np.roll(channel.volts, delay_samples),
        t0=channel.t0,
        dt=channel.dt,
        antenna=Antenna(
            name="Bdot-X",
            kind="b-dot",
            transfer_function=flat_transfer(),
            delay=delay_samples * DT,
        ),
        unit="V",
    )
    expected = AMPLITUDE * np.sin(2 * np.pi * F0 * T)
    result = b_field(delayed)
    np.testing.assert_allclose(result.volts, expected, atol=1e-12)


def test_missing_transfer_function_raises() -> None:
    plain = Channel(name="C1", volts=np.zeros(16), t0=0.0, dt=DT)
    with pytest.raises(ValueError, match="no antenna with a transfer function"):
        b_field(plain)
    uncalibrated = Channel(
        name="C1", volts=np.zeros(16), t0=0.0, dt=DT, antenna=Antenna(name="Bdot")
    )
    with pytest.raises(ValueError, match="no antenna with a transfer function"):
        b_field_rate(uncalibrated)


def test_raise_outside_band() -> None:
    transfer = TransferFunction(
        freq=[1.0e6, 1.0e7], gain=[GAIN + 0j, GAIN + 0j], unit="V/(T/s)"
    )
    tone = AMPLITUDE * np.cos(2 * np.pi * F0 * T)
    channel = Channel(
        name="C1",
        volts=tone,
        t0=0.0,
        dt=DT,
        antenna=Antenna(name="Bdot-X", kind="b-dot", transfer_function=transfer),
    )
    with pytest.raises(ValueError, match="outside the calibrated band"):
        b_field_rate(channel)


def test_zero_and_clamp_outside_band() -> None:
    transfer = TransferFunction(
        freq=[1.0e6, 1.0e7], gain=[GAIN + 0j, GAIN + 0j], unit="V/(T/s)"
    )
    tone = AMPLITUDE * np.cos(2 * np.pi * F0 * T)
    channel = Channel(
        name="C1",
        volts=tone,
        t0=0.0,
        dt=DT,
        antenna=Antenna(name="Bdot-X", kind="b-dot", transfer_function=transfer),
    )
    zeroed = b_field_rate(channel, outside="zero")
    assert np.max(np.abs(zeroed.volts)) < 1e-15
    clamped = b_field_rate(channel, outside="clamp")
    assert np.all(np.isfinite(clamped.volts))
    assert np.max(np.abs(clamped.volts)) > 0.0


def test_b_magnitude_of_components() -> None:
    base = b_field(make_bdot_channel())
    triad = b_magnitude(base, base, base)
    assert triad.unit == "T"
    assert triad.name == "|B|"
    assert triad.antenna is None
    np.testing.assert_allclose(triad.volts, np.sqrt(3.0) * np.abs(base.volts))


def test_b_magnitude_validates_inputs() -> None:
    with pytest.raises(ValueError, match="at least one"):
        b_magnitude()
    a = Channel(name="A", volts=np.zeros(8), t0=0.0, dt=DT, unit="T")
    b = Channel(name="B", volts=np.zeros(8), t0=0.0, dt=DT, unit="T/s")
    with pytest.raises(ValueError, match="mixed units"):
        b_magnitude(a, b)
    short = Channel(name="C", volts=np.zeros(4), t0=0.0, dt=DT, unit="T")
    with pytest.raises(ValueError, match="share a timebase"):
        b_magnitude(a, short)
