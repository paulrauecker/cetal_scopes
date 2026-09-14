"""Tests for the B-dot web app engine (no Dash server, no hardware)."""

from __future__ import annotations

import engine
import numpy as np
import plotly.graph_objects as go
import pytest

from cetal_scopes import Channel


def test_nominal_transfer_function_band() -> None:
    transfer = engine.nominal_transfer_function(2.0, f_min=1.0e5, f_max=1.0e8)
    assert transfer.f_min == 1.0e5
    assert transfer.f_max == 1.0e8
    assert transfer.gain_at(1.0e6) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="sensitivity"):
        engine.nominal_transfer_function(0.0, f_min=1.0e5, f_max=1.0e8)
    with pytest.raises(ValueError, match="f_min"):
        engine.nominal_transfer_function(1.0, f_min=1.0e8, f_max=1.0e5)


def test_band_for_channel_defaults_to_ten_bins() -> None:
    channel = Channel(name="C1", volts=np.zeros(1000), t0=0.0, dt=1.0e-9)
    low, high = engine.band_for_channel(channel)
    assert low == pytest.approx(10.0 / (1000 * 1.0e-9))
    assert high == pytest.approx(0.5 / 1.0e-9)


def test_trigger_level_warning_detects_clamp() -> None:
    assert engine.trigger_level_warning(0.05, 0.05) is None
    note = engine.trigger_level_warning(0.05, 0.00225)
    assert note is not None
    assert "clamped" in note


def test_decimate_strides_large_traces() -> None:
    time = np.arange(1000.0)
    values = time.copy()
    time_out, values_out = engine.decimate(time, values, 100)
    assert values_out.size <= 100
    assert time_out.size == values_out.size
    same_time, same_values = engine.decimate(time[:10], values[:10], 100)
    assert same_time.size == 10
    assert same_values.size == 10


def test_analyse_capture_builds_two_panes() -> None:
    scope = engine.DemoScope(channels=("C1", "C2", "C3"), n_samples=4096)
    capture = scope.acquire()

    figure, result = engine.analyse_capture(capture, ("C1", "C2", "C3"))

    assert isinstance(figure, go.Figure)
    data = figure.to_dict()["data"]
    assert len(data) == 7  # 2 per channel + |B|
    assert [reading.label for reading in result.axis] == ["X", "Y", "Z"]
    assert result.magnitude_peak > 0.0
    assert result.band[0] < result.band[1]
    assert any(trace["name"] == "|B|" for trace in data)


def test_analyse_capture_requires_a_channel() -> None:
    scope = engine.DemoScope(channels=("C1",), n_samples=256)
    capture = scope.acquire()
    with pytest.raises(ValueError, match="none of the requested channels"):
        engine.analyse_capture(capture, ("C9",))


def test_trigger_message_includes_peaks() -> None:
    scope = engine.DemoScope(channels=("C1",), n_samples=256)
    capture = scope.acquire()
    _, result = engine.analyse_capture(capture, ("C1",))

    message = engine.trigger_message(3, capture, result, "C1")

    assert "captured shot 3" in message
    assert "C1 peak=" in message
    assert "|B|peak=" in message


def test_empty_figure_is_two_pane() -> None:
    figure = engine.empty_figure("hello")
    assert isinstance(figure, go.Figure)
    assert "hello" in figure.layout.title.text


def test_probe_session_demo_capture() -> None:
    config = engine.ScopeConfig(address="192.0.2.1", channels=("C1", "C2"), demo=True)
    session = engine.ProbeSession(config)

    status = session.arm()
    captured = session.capture_once()

    assert "armed" in status
    assert captured is not None
    _, result = captured
    assert session.shot == 1
    assert result.magnitude_peak > 0.0
    assert session.log and "captured shot 1" in session.log[0]
    session.disconnect()


def test_parse_channels() -> None:
    import bdot_web

    assert bdot_web._parse_channels("C1 C2") == ("C1", "C2")
    assert bdot_web._parse_channels("C1,C2,C3") == ("C1", "C2", "C3")
    assert bdot_web._parse_channels("") == ("C1",)
