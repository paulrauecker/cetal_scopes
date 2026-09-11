"""Tests for the real-time viewer app (no hardware required)."""

from __future__ import annotations

import argparse
from collections.abc import Iterator

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
import viewer

from cetal_scopes import Capture

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    yield
    plt.close("all")


def make_tone(frequency: float, sample_rate: float, n: int) -> np.ndarray:
    time = np.arange(n) / sample_rate
    return 0.1 * np.sin(2.0 * np.pi * frequency * time)


@pytest.mark.parametrize(
    ("span", "unit"),
    [(2.0, "s"), (0.5, "ms"), (1e-5, "\u00b5s"), (1e-10, "ps")],
)
def test_time_unit_for_span(span: float, unit: str) -> None:
    assert viewer.time_unit_for_span(span)[0] == unit


def test_viewer_labels_channels() -> None:
    view = viewer.RealtimeViewer(("C1", "C2"))
    assert [line.get_label() for line in view.ax_time.lines] == ["C1", "C2"]
    assert [line.get_label() for line in view.ax_fft.lines] == ["C1", "C2"]
    assert view.ax_fft.get_xscale() == "log"
    view.close()


def test_viewer_requires_channel() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        viewer.RealtimeViewer(())


def test_update_draws_time_and_fft() -> None:
    volts = make_tone(100e3, 1e6, 800)
    capture = Capture(volts=volts[None, :], t0=0.0, dt=1e-6, channel_names=("CH1",))
    view = viewer.RealtimeViewer(("CH1",), log_frequency=False)

    results = view.update(capture)

    assert len(results) == 1
    assert results[0].channel == "CH1"
    assert results[0].peak_frequency == pytest.approx(100e3, rel=0.02)
    np.testing.assert_allclose(np.asarray(view.time_lines["CH1"].get_ydata()), volts)
    assert np.asarray(view.fft_lines["CH1"].get_xdata()).size > 0
    view.close()


def test_update_suppresses_comb_when_enabled() -> None:
    period = 8
    pattern = np.array([0.05, -0.03, 0.02, -0.01, 0.04, -0.02, 0.01, -0.04])
    index = np.arange(800)
    capture = Capture(
        volts=pattern[index % period][None, :],
        t0=0.0,
        dt=1e-6,
        channel_names=("CH1",),
    )

    raw = viewer.RealtimeViewer(("CH1",), log_frequency=False)
    assert raw.update(capture)[0].peak_amplitude > 1e-3

    cleaned = viewer.RealtimeViewer(
        ("CH1",), log_frequency=False, remove_comb=True, comb_period=period
    )
    assert cleaned.update(capture)[0].peak_amplitude < 1e-9

    raw.close()
    cleaned.close()


def test_demo_scope_acquire() -> None:
    scope = viewer.DemoScope(channels=("C1", "C2"), n_samples=256)
    capture = scope.acquire()
    assert capture.n_channels == 2
    assert capture.n_samples == 256
    assert np.isfinite(capture.volts).all()


def test_run_draws_requested_frames() -> None:
    scope = viewer.DemoScope(channels=("C1",), n_samples=512)
    view = viewer.RealtimeViewer(("C1",), log_frequency=False)

    drawn = viewer.run(
        scope, view, interval=0.0, frames=3, show=False, pause=lambda _: None
    )

    assert drawn == 3
    view.close()


def test_parser_defaults() -> None:
    args = viewer.build_parser().parse_args([])
    assert args.channels == ["C1"]
    assert args.demo is False
    assert args.sample_width == "WORD"
    assert args.trigger_mode == "AUTO"
    assert args.stream is False
    assert args.linear_y is False
    assert args.vdiv is None
    assert args.impedance == "1M"


def test_parser_impedance() -> None:
    assert viewer.build_parser().parse_args(["--impedance", "50"]).impedance == "50"
    with pytest.raises(SystemExit):
        viewer.build_parser().parse_args(["--impedance", "75"])


def test_make_scope_stops_by_default() -> None:
    args = viewer.build_parser().parse_args(["--address", "192.0.2.1"])
    scope = viewer._make_scope(args)
    assert isinstance(scope, viewer.SiglentSDS6204L)
    assert scope.streaming is False

    args = viewer.build_parser().parse_args(["--address", "192.0.2.1", "--stream"])
    streaming = viewer._make_scope(args)
    assert isinstance(streaming, viewer.SiglentSDS6204L)
    assert streaming.streaming is True


def test_linear_amplitude_axis() -> None:
    view = viewer.RealtimeViewer(("C1",), log_amplitude=False)
    assert view.ax_fft.get_yscale() == "linear"
    view.close()


def test_run_stops_when_window_is_closed() -> None:
    scope = viewer.DemoScope(channels=("C1",), n_samples=256)
    view = viewer.RealtimeViewer(("C1",), log_frequency=False)
    view.close()

    drawn = viewer.run(
        scope, view, interval=0.0, frames=None, show=False, pause=lambda _: None
    )

    assert drawn == 0


def test_main_demo_runs() -> None:
    assert viewer.main(["--demo", "--frames", "2", "--interval", "0"]) == 0


def test_snap_vdiv_rounds_up_the_ladder() -> None:
    assert viewer.snap_vdiv(0.0001) == 0.0005
    assert viewer.snap_vdiv(0.03) == 0.05
    assert viewer.snap_vdiv(1.5) == 2.0
    assert viewer.snap_vdiv(100.0) == 10.0


def test_vdiv_arg_accepts_auto_and_numbers() -> None:
    assert viewer._vdiv_arg("auto") == "auto"
    assert viewer._vdiv_arg("1.5") == 1.5
    with pytest.raises(argparse.ArgumentTypeError):
        viewer._vdiv_arg("nonsense")


def test_window_choices() -> None:
    parser = viewer.build_parser()
    args = parser.parse_args(["--demo", "--window", "blackmanharris"])
    assert args.window == "blackmanharris"
    with pytest.raises(SystemExit):
        parser.parse_args(["--window", "not-a-window"])


def test_robust_peak_uses_peak_not_baseline() -> None:
    channel = viewer.Channel(
        name="C1", volts=np.array([5.0, -5.0, 1.0, -1.0]), t0=0.0, dt=1e-6
    )
    assert viewer._robust_peak(channel) > 4.0


def test_auto_range_vdiv_sets_a_ladder_scale() -> None:
    seen: list[object] = []

    class Recording(viewer.DemoScope):
        def configure(self, settings: object) -> None:
            seen.append(settings)

    scope = Recording(channels=("C1",), n_samples=1024)
    capture = viewer.auto_range_vdiv(scope, ("C1",))

    assert isinstance(capture, Capture)
    assert seen  # at least the final per-channel scales were applied
