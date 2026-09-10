import numpy as np
import pytest

from cetal_scopes import Capture, Channel


def test_default_channel_names_and_shape() -> None:
    capture = Capture(volts=np.zeros((2, 4)), t0=0.0, dt=0.1)
    assert capture.channel_names == ("CH1", "CH2")
    assert capture.n_channels == 2
    assert capture.n_samples == 4
    assert len(capture) == 2
    assert set(capture.channels) == {"CH1", "CH2"}


def test_custom_channel_names() -> None:
    capture = Capture(volts=np.zeros((2, 4)), t0=0.0, dt=0.1, channel_names=("A", "B"))
    assert tuple(capture.channels) == ("A", "B")
    assert capture["A"].name == "A"


def test_volts_is_cast_to_float64() -> None:
    capture = Capture(volts=np.zeros((1, 2), dtype=np.float32), t0=0.0, dt=1.0)
    assert capture.volts.dtype == np.float64


def test_time_axis() -> None:
    capture = Capture(volts=np.zeros((1, 3)), t0=2.0, dt=0.5)
    np.testing.assert_allclose(capture.time, [2.0, 2.5, 3.0])


def test_iteration_yields_channels() -> None:
    capture = Capture(volts=np.zeros((2, 2)), t0=0.0, dt=1.0)
    assert all(isinstance(channel, Channel) for channel in capture)
    assert [channel.name for channel in capture] == ["CH1", "CH2"]


def test_membership_and_lookup() -> None:
    capture = Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1.0)
    assert "CH1" in capture
    assert "nope" not in capture
    with pytest.raises(KeyError):
        capture["nope"]


def test_repr() -> None:
    capture = Capture(volts=np.zeros((2, 3)), t0=0.0, dt=1.0)
    assert repr(capture) == "Capture(n_channels=2, n_samples=3, t0=0.0, dt=1.0)"


@pytest.mark.parametrize("volts", [np.zeros(3), np.zeros((1, 2, 3))])
def test_volts_must_be_2d(volts: np.ndarray) -> None:
    with pytest.raises(ValueError, match="volts must be 2D"):
        Capture(volts=volts, t0=0.0, dt=1.0)


def test_raw_shape_must_match() -> None:
    with pytest.raises(ValueError, match="raw shape"):
        Capture(
            volts=np.zeros((2, 3)),
            t0=0.0,
            dt=1.0,
            raw=np.zeros((2, 2), dtype=np.int16),
        )


def test_raw_must_be_2d() -> None:
    with pytest.raises(ValueError, match="raw must be 2D"):
        Capture(volts=np.zeros((2, 3)), t0=0.0, dt=1.0, raw=np.zeros(3, dtype=np.int16))


def test_channel_name_count_must_match() -> None:
    with pytest.raises(ValueError, match="channel names"):
        Capture(volts=np.zeros((2, 3)), t0=0.0, dt=1.0, channel_names=("A",))


def test_channel_names_must_be_unique() -> None:
    with pytest.raises(ValueError, match="unique"):
        Capture(volts=np.zeros((2, 3)), t0=0.0, dt=1.0, channel_names=("A", "A"))


def test_channel_names_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Capture(volts=np.zeros((2, 3)), t0=0.0, dt=1.0, channel_names=("A", ""))


@pytest.mark.parametrize("dt", [0.0, -1.0, float("nan"), float("inf")])
def test_dt_must_be_positive_and_finite(dt: float) -> None:
    with pytest.raises(ValueError, match="dt must be positive"):
        Capture(volts=np.zeros((1, 2)), t0=0.0, dt=dt)


def test_t0_must_be_finite() -> None:
    with pytest.raises(ValueError, match="t0 must be finite"):
        Capture(volts=np.zeros((1, 2)), t0=float("nan"), dt=1.0)
