import numpy as np

from cetal_scopes import Capture, Channel


def make_capture() -> Capture:
    return Capture(
        volts=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        t0=1.0,
        dt=0.5,
    )


def test_channel_metadata() -> None:
    channel = make_capture()["CH2"]
    assert isinstance(channel, Channel)
    assert channel.name == "CH2"
    assert channel.n_samples == 3
    assert len(channel) == 3


def test_channel_arrays_are_views() -> None:
    capture = make_capture()
    channel = capture["CH1"]
    assert np.shares_memory(channel.volts, capture.volts)
    np.testing.assert_array_equal(channel.volts, [1.0, 2.0, 3.0])


def test_channel_time_is_derived() -> None:
    channel = make_capture()["CH1"]
    np.testing.assert_allclose(channel.time, [1.0, 1.5, 2.0])


def test_channel_repr() -> None:
    assert repr(make_capture()["CH1"]) == "Channel(name='CH1', n_samples=3)"


def test_channel_raw_is_none_without_raw_capture() -> None:
    assert make_capture()["CH1"].raw is None


def test_channel_raw_is_row_view() -> None:
    capture = Capture(
        volts=np.zeros((2, 3)),
        t0=0.0,
        dt=1.0,
        raw=np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int16),
    )
    channel = capture["CH2"]
    assert channel.raw is not None
    np.testing.assert_array_equal(channel.raw, [4, 5, 6])
