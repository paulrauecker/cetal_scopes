import numpy as np
import pytest

from cetal_scopes import Antenna, Capture, Channel, Shot
from cetal_scopes.analysis import (
    detrend,
    gate,
    remove_adc_comb,
    resample,
    resample_onto,
    subtract_baseline,
)


def make_channel(volts: list[float], *, t0: float = 0.0, dt: float = 1.0) -> Channel:
    return Channel(name="CH1", volts=np.asarray(volts, dtype=np.float64), t0=t0, dt=dt)


def test_gate_selects_inclusive_window() -> None:
    result = gate(make_channel([0, 1, 2, 3, 4, 5]), t_start=1.0, t_end=3.0)
    np.testing.assert_array_equal(result.volts, [1, 2, 3])
    assert result.t0 == pytest.approx(1.0)
    assert result.dt == pytest.approx(1.0)


def test_gate_defaults_to_whole_channel() -> None:
    channel = make_channel([1, 2, 3])
    result = gate(channel)
    np.testing.assert_array_equal(result.volts, channel.volts)
    assert result.t0 == pytest.approx(channel.t0)


def test_gate_preserves_raw_slice() -> None:
    channel = Channel(
        name="CH1",
        volts=np.arange(5.0),
        t0=0.0,
        dt=1.0,
        raw=np.arange(5, dtype=np.int16),
    )
    result = gate(channel, t_start=2.0, t_end=4.0)
    assert result.raw is not None
    np.testing.assert_array_equal(result.raw, [2, 3, 4])


def test_gate_empty_window_raises() -> None:
    with pytest.raises(ValueError, match="no samples"):
        gate(make_channel([0, 1, 2, 3]), t_start=10.0, t_end=11.0)


def test_subtract_baseline_mean_over_window() -> None:
    result = subtract_baseline(make_channel([10, 10, 10, 20]), t_start=0.0, t_end=2.0)
    np.testing.assert_allclose(result.volts, [0, 0, 0, 10])


def test_subtract_baseline_median_whole_channel() -> None:
    result = subtract_baseline(make_channel([1, 2, 3, 100]), mode="median")
    np.testing.assert_allclose(result.volts, [-1.5, -0.5, 0.5, 97.5])


def test_subtract_baseline_bad_mode() -> None:
    with pytest.raises(ValueError, match="mean"):
        subtract_baseline(make_channel([1, 2, 3]), mode="max")


def test_detrend_linear_removes_ramp() -> None:
    result = detrend(make_channel([0, 1, 2, 3, 4]))
    np.testing.assert_allclose(result.volts, 0.0, atol=1e-12)


def test_detrend_constant_removes_mean() -> None:
    result = detrend(make_channel([1, 2, 3]), type="constant")
    np.testing.assert_allclose(result.volts, [-1, 0, 1])


def test_detrend_bad_type() -> None:
    with pytest.raises(ValueError, match="type"):
        detrend(make_channel([1, 2, 3]), type="quadratic")  # type: ignore[arg-type]


def test_resample_by_n_preserves_endpoints() -> None:
    result = resample(make_channel([0, 1, 2, 3]), n=7)
    assert result.n_samples == 7
    assert result.dt == pytest.approx(0.5)
    np.testing.assert_allclose(result.volts, [0, 0.5, 1, 1.5, 2, 2.5, 3])


def test_resample_by_dt() -> None:
    result = resample(make_channel([0, 1, 2, 3]), dt=0.5)
    assert result.n_samples == 7
    assert result.dt == pytest.approx(0.5)


def test_resample_requires_exactly_one_parameter() -> None:
    channel = make_channel([0, 1, 2, 3])
    with pytest.raises(ValueError, match="exactly one"):
        resample(channel)
    with pytest.raises(ValueError, match="exactly one"):
        resample(channel, dt=0.5, n=5)


def test_resample_bad_dt() -> None:
    with pytest.raises(ValueError, match="dt must be positive"):
        resample(make_channel([0, 1, 2, 3]), dt=0.0)


def test_resample_bad_n() -> None:
    with pytest.raises(ValueError, match="n must be positive"):
        resample(make_channel([0, 1, 2, 3]), n=0)


def _coherent_amplitude(volts: np.ndarray, frequency: float) -> float:
    index = np.arange(volts.size)
    kernel = np.exp(-2j * np.pi * frequency * index)
    return float(2.0 * np.abs(np.dot(volts - volts.mean(), kernel)) / volts.size)


def test_remove_adc_comb_removes_pattern_and_keeps_tone() -> None:
    period = 8
    n = 2048
    pattern = np.array([0.5, -0.3, 0.2, -0.1, 0.4, -0.2, 0.1, -0.4])
    tone_bin = 100  # an exact DFT bin, not a multiple of n / period
    index = np.arange(n)
    tone = 0.25 * np.sin(2 * np.pi * tone_bin * index / n)
    channel = make_channel(list(pattern[index % period] + tone))

    result = remove_adc_comb(channel, period=period)

    before = _coherent_amplitude(channel.volts, tone_bin / n)
    after = _coherent_amplitude(result.volts, tone_bin / n)
    assert after == pytest.approx(before, rel=1e-9)
    for k in range(1, period):
        assert _coherent_amplitude(result.volts, k / period) < 1e-9


def test_remove_adc_comb_preserves_dc() -> None:
    values = np.tile([1.0, 2.0, 3.0, 4.0], 4)
    result = remove_adc_comb(make_channel(list(values)), period=4)
    assert result.volts.mean() == pytest.approx(values.mean())
    np.testing.assert_allclose(result.volts, values.mean())


def test_remove_adc_comb_rejects_bad_period() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        remove_adc_comb(make_channel([1, 2, 3, 4]), period=1)


def test_remove_adc_comb_rejects_short_channel() -> None:
    with pytest.raises(ValueError, match="shorter than one comb period"):
        remove_adc_comb(make_channel([1, 2, 3]), period=8)


def test_processing_preserves_antenna_and_unit() -> None:
    antenna = Antenna(name="Bdot-X", kind="b-dot")
    channel = Channel(
        name="CH1", volts=np.arange(5.0), t0=0.0, dt=1.0, antenna=antenna, unit="T"
    )
    results = (
        gate(channel, t_start=1.0, t_end=3.0),
        subtract_baseline(channel),
        detrend(channel),
        resample(channel, dt=0.5),
        remove_adc_comb(channel, period=2),
    )
    assert all(result.antenna is antenna for result in results)
    assert all(result.unit == "T" for result in results)


# ---------------------------------------------------------------------------
# resample_onto
# ---------------------------------------------------------------------------


def test_resample_onto_places_a_channel_on_a_shared_grid() -> None:
    channel = Channel(name="C1", volts=np.arange(5, dtype=np.float64), t0=0.0, dt=1.0)
    grid = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    moved = resample_onto(channel, grid)

    assert moved.t0 == 0.0
    assert moved.dt == pytest.approx(0.5)
    np.testing.assert_allclose(moved.volts, [0.0, 0.5, 1.0, 1.5, 2.0])


def test_resample_onto_fills_outside_the_span_with_nan_by_default() -> None:
    channel = Channel(name="C1", volts=np.ones(3), t0=0.0, dt=1.0)
    moved = resample_onto(channel, np.array([-2.0, -1.0, 0.0, 1.0, 2.0, 3.0]))

    # np.interp would hold the edge values here, quietly inventing data at
    # the edges of a shot; a gap must stay visible.
    assert np.isnan(moved.volts[0])
    assert np.isnan(moved.volts[1])
    assert np.isnan(moved.volts[-1])
    np.testing.assert_allclose(moved.volts[2:5], 1.0)


def test_resample_onto_accepts_an_explicit_fill() -> None:
    channel = Channel(name="C1", volts=np.ones(3), t0=0.0, dt=1.0)
    moved = resample_onto(channel, np.array([-1.0, 0.0, 1.0, 2.0, 3.0]), fill=0.0)

    assert moved.volts[0] == 0.0
    assert moved.volts[-1] == 0.0


def test_resample_onto_carries_the_antenna_and_unit() -> None:
    channel = Channel(
        name="C1",
        volts=np.ones(4),
        t0=0.0,
        dt=1.0,
        antenna=Antenna(name="probe"),
        unit="T/s",
        raw=np.arange(4, dtype=np.int16),
    )
    moved = resample_onto(channel, np.arange(4, dtype=np.float64))

    assert moved.antenna is not None
    assert moved.unit == "T/s"
    assert moved.raw is None


def test_resample_onto_puts_two_captures_on_one_axis() -> None:
    shot = Shot()
    shot.add("a", Capture(volts=np.ones((1, 8)), t0=0.0, dt=1e-9))
    shot.add("b", Capture(volts=np.full((1, 4), 2.0), t0=0.0, dt=2e-9), offset=1e-9)

    grid = shot.common_time()
    first = resample_onto(shot.aligned_channel("a", "CH1"), grid)
    second = resample_onto(shot.aligned_channel("b", "CH1"), grid)

    assert first.n_samples == second.n_samples == grid.size
    assert first.dt == second.dt


@pytest.mark.parametrize(
    "grid",
    [
        np.array([0.0]),
        np.array([1.0, 0.0, 2.0]),
        np.array([0.0, 1.0, 5.0]),
    ],
)
def test_resample_onto_rejects_a_bad_grid(grid: np.ndarray) -> None:
    channel = Channel(name="C1", volts=np.ones(4), t0=0.0, dt=1.0)
    with pytest.raises(ValueError):
        resample_onto(channel, grid)
