"""Tests for the B-dot probe app (no hardware required)."""

from __future__ import annotations

from collections.abc import Iterator

import matplotlib

matplotlib.use("Agg")

import bdot_probe
import matplotlib.pyplot as plt
import numpy as np
import pytest

from cetal_scopes import Capture, Channel

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    yield
    plt.close("all")


def test_probe_requires_channel() -> None:
    with pytest.raises(ValueError, match="at least one channel"):
        bdot_probe.BdotProbe(())


def test_probe_labels_channels() -> None:
    probe = bdot_probe.BdotProbe(("C1", "C2"))
    assert [line.get_label() for line in probe.ax_rate.lines] == ["X", "Y"]
    assert [line.get_label() for line in probe.ax_field.lines] == ["X", "Y", "|B|"]
    probe.close()


def test_labels_must_match_channels() -> None:
    with pytest.raises(ValueError, match="labels must match"):
        bdot_probe.BdotProbe(("C1", "C2"), labels=("X",))


def test_nominal_transfer_function_band() -> None:
    transfer = bdot_probe.nominal_transfer_function(2.0, f_min=1.0e5, f_max=1.0e8)
    assert transfer.f_min == 1.0e5
    assert transfer.f_max == 1.0e8
    assert transfer.gain_at(1.0e6) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="sensitivity"):
        bdot_probe.nominal_transfer_function(0.0, f_min=1.0e5, f_max=1.0e8)
    with pytest.raises(ValueError, match="f_min"):
        bdot_probe.nominal_transfer_function(1.0, f_min=1.0e8, f_max=1.0e5)


def test_band_for_channel_defaults_to_ten_bins() -> None:
    channel = Channel(name="C1", volts=np.zeros(1000), t0=0.0, dt=1.0e-9)
    low, high = bdot_probe.band_for_channel(channel)
    assert low == pytest.approx(10.0 / (1000 * 1.0e-9))
    assert high == pytest.approx(0.5 / 1.0e-9)


def test_trigger_level_warning_detects_clamp() -> None:
    assert bdot_probe.trigger_level_warning(0.05, 0.05) is None
    note = bdot_probe.trigger_level_warning(0.05, 0.00225)
    assert note is not None
    assert "clamped" in note


def test_update_recovers_and_latches() -> None:
    scope = bdot_probe.DemoScope(channels=("C1", "C2", "C3"), n_samples=4096)
    probe = bdot_probe.BdotProbe(("C1", "C2", "C3"))

    result = probe.update(scope.acquire())

    assert len(result.axis) == 3
    assert result.magnitude_peak > 0.0
    assert probe.peak_magnitude >= result.magnitude_peak
    latched = probe.peak_magnitude
    probe.update(scope.acquire())
    assert probe.peak_magnitude >= latched
    probe.close()


def test_reset_clears_latch() -> None:
    scope = bdot_probe.DemoScope(channels=("C1",))
    probe = bdot_probe.BdotProbe(("C1",))
    probe.update(scope.acquire())
    assert probe.peak_magnitude > 0.0

    probe.reset()

    assert probe.peak_magnitude == 0.0
    assert all(value == 0.0 for value in probe.peak_rate.values())
    probe.close()


def test_run_draws_requested_frames() -> None:
    scope = bdot_probe.DemoScope(channels=("C1",), n_samples=512)
    probe = bdot_probe.BdotProbe(("C1",))

    drawn = bdot_probe.run(
        scope,
        probe,
        interval=0.0,
        frames=3,
        hold=False,
        show=False,
        pause=lambda _: None,
    )

    assert drawn == 3
    probe.close()


def test_run_stops_when_window_closed() -> None:
    scope = bdot_probe.DemoScope(channels=("C1",), n_samples=256)
    probe = bdot_probe.BdotProbe(("C1",))
    probe.close()

    drawn = bdot_probe.run(
        scope, probe, interval=0.0, frames=None, show=False, pause=lambda _: None
    )

    assert drawn == 0


def test_parser_defaults() -> None:
    args = bdot_probe.build_parser().parse_args([])
    assert args.channels == ["C1", "C2", "C3"]
    assert args.sensitivity == bdot_probe.DEFAULT_SENSITIVITY
    assert args.fmin is None
    assert args.fmax is None
    assert args.demo is False
    assert args.trigger_mode == "SINGle"
    assert args.trigger_level == 0.0
    assert args.trigger_slope == "RISing"
    assert args.trigger_timeout == 5.0
    assert args.max_points == 20000
    assert args.impedance == "1M"
    assert args.continuous is False


def test_make_scope_is_single_shot() -> None:
    args = bdot_probe.build_parser().parse_args(["--address", "192.0.2.1"])
    scope = bdot_probe._make_scope(args)
    assert isinstance(scope, bdot_probe.SiglentSDS6204L)
    assert scope.trigger_mode == "SINGle"


def test_decimate_strides_large_traces() -> None:
    time = np.arange(1000.0)
    values = time.copy()
    time_out, values_out = bdot_probe.decimate(time, values, 100)
    assert values_out.size <= 100
    assert time_out.size == values_out.size
    same_time, same_values = bdot_probe.decimate(time[:10], values[:10], 100)
    assert same_time.size == 10
    assert same_values.size == 10


def test_run_retries_while_waiting_for_trigger() -> None:
    class Flaky(bdot_probe.DemoScope):
        def __init__(self, *, timeouts: int) -> None:
            super().__init__(channels=("C1",), n_samples=256)
            self._timeouts = timeouts

        def acquire(self) -> Capture:
            if self._timeouts > 0:
                self._timeouts -= 1
                raise TimeoutError("acquisition did not complete within 5s")
            return super().acquire()

    scope = Flaky(timeouts=2)
    probe = bdot_probe.BdotProbe(("C1",))

    drawn = bdot_probe.run(
        scope,
        probe,
        interval=0.0,
        frames=1,
        hold=False,
        show=False,
        pause=lambda _: None,
    )

    assert drawn == 1
    probe.close()


def test_run_propagates_unexpected_timeout() -> None:
    class Broken(bdot_probe.DemoScope):
        def acquire(self) -> Capture:
            raise TimeoutError("timed out")

    probe = bdot_probe.BdotProbe(("C1",))
    with pytest.raises(TimeoutError, match="timed out"):
        bdot_probe.run(
            Broken(channels=("C1",)), probe, frames=1, show=False, pause=lambda _: None
        )
    probe.close()


def test_run_holds_single_shot_until_rearm() -> None:
    class Counting(bdot_probe.DemoScope):
        def __init__(self) -> None:
            super().__init__(channels=("C1",), n_samples=256)
            self.acquires = 0

        def acquire(self) -> Capture:
            self.acquires += 1
            return super().acquire()

    scope = Counting()
    probe = bdot_probe.BdotProbe(("C1",))
    calls = {"n": 0}

    def pause(_: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 4:
            plt.close(probe.figure)

    drawn = bdot_probe.run(
        scope, probe, interval=0.0, hold=True, show=False, pause=pause
    )

    assert drawn == 1
    assert scope.acquires == 1
    probe.close()


def test_trigger_is_printed_with_amplitude(
    capsys: pytest.CaptureFixture[str],
) -> None:
    scope = bdot_probe.DemoScope(channels=("C1",), n_samples=256)
    probe = bdot_probe.BdotProbe(("C1",))

    bdot_probe.run(
        scope,
        probe,
        interval=0.0,
        frames=1,
        hold=False,
        trigger_channel="C1",
        show=False,
        pause=lambda _: None,
    )

    out = capsys.readouterr().out
    assert "trigger: captured shot 1" in out
    assert "C1 peak=" in out
    assert "|B|peak=" in out
    probe.close()


def test_trigger_message_includes_peaks() -> None:
    scope = bdot_probe.DemoScope(channels=("C1",), n_samples=256)
    probe = bdot_probe.BdotProbe(("C1",))
    capture = scope.acquire()
    result = probe.update(capture)

    message = bdot_probe.trigger_message(3, capture, result, "C1")

    assert "captured shot 3" in message
    assert "C1 peak=" in message
    assert "|B|peak=" in message
    probe.close()


def test_snap_vdiv() -> None:
    assert bdot_probe.snap_vdiv(0.0001) == 0.0005
    assert bdot_probe.snap_vdiv(1.5) == 2.0


def test_main_demo_runs() -> None:
    assert (
        bdot_probe.main(["--demo", "--continuous", "--frames", "2", "--interval", "0"])
        == 0
    )
