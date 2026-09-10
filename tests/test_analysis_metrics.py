import numpy as np
import pytest

from cetal_scopes import Channel
from cetal_scopes.analysis import ChannelStats, stats


def test_stats_basic_values() -> None:
    channel = Channel(
        name="CH1",
        volts=np.array([1.0, -5.0, 3.0]),
        t0=0.0,
        dt=1.0,
    )
    result = stats(channel)
    assert isinstance(result, ChannelStats)
    assert result.name == "CH1"
    assert result.n_samples == 3
    assert result.mean == pytest.approx(-1.0 / 3.0)
    assert result.rms == pytest.approx(np.sqrt((1 + 25 + 9) / 3))
    assert result.minimum == pytest.approx(-5.0)
    assert result.maximum == pytest.approx(3.0)
    assert result.peak_to_peak == pytest.approx(8.0)
    assert result.peak_value == pytest.approx(-5.0)
    assert result.peak_time == pytest.approx(1.0)


def test_stats_empty_channel_is_nan() -> None:
    channel = Channel(name="CH1", volts=np.zeros(0), t0=0.0, dt=1.0)
    result = stats(channel)
    assert result.n_samples == 0
    assert np.isnan(result.mean)
    assert np.isnan(result.peak_time)
