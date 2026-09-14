"""Tests for the SG8 sweep app (no hardware required)."""

from __future__ import annotations

from collections.abc import Iterator

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
import sg8_sweep

from cetal_scopes import Capture

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    yield
    plt.close("all")


def test_log_sweep_endpoints_and_count() -> None:
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 5)
    assert freqs.size == 5
    assert freqs[0] == pytest.approx(10e6)
    assert freqs[-1] == pytest.approx(2e9)
    assert np.all(np.diff(freqs) > 0)


def test_log_sweep_rejects_bad_range() -> None:
    with pytest.raises(ValueError, match="points"):
        sg8_sweep.log_sweep(10e6, 2e9, 1)
    with pytest.raises(ValueError, match="0 < start < stop"):
        sg8_sweep.log_sweep(2e9, 10e6, 5)


def test_linear_sweep_endpoints_and_spacing() -> None:
    freqs = sg8_sweep.linear_sweep(10e6, 2e9, 5)
    assert freqs.size == 5
    assert freqs[0] == pytest.approx(10e6)
    assert freqs[-1] == pytest.approx(2e9)
    diffs = np.diff(freqs)
    assert np.allclose(diffs, diffs[0])


def test_linear_sweep_allows_zero_start() -> None:
    freqs = sg8_sweep.linear_sweep(0.0, 2e9, 3)
    assert freqs[0] == pytest.approx(0.0)


def test_linear_sweep_rejects_bad_range() -> None:
    with pytest.raises(ValueError, match="points"):
        sg8_sweep.linear_sweep(10e6, 2e9, 1)
    with pytest.raises(ValueError, match="0 <= start < stop"):
        sg8_sweep.linear_sweep(2e9, 10e6, 5)


def test_frequency_sweep_dispatches_on_log_flag() -> None:
    log_freqs = sg8_sweep.frequency_sweep(10e6, 2e9, 5, log=True)
    linear_freqs = sg8_sweep.frequency_sweep(10e6, 2e9, 5, log=False)
    assert np.array_equal(log_freqs, sg8_sweep.log_sweep(10e6, 2e9, 5))
    assert np.array_equal(linear_freqs, sg8_sweep.linear_sweep(10e6, 2e9, 5))
    assert not np.array_equal(log_freqs, linear_freqs)


def test_timebase_for_frequency_fits_cycles_across_grid() -> None:
    scale = sg8_sweep.timebase_for_frequency(10e6, cycles=20.0, grid_num=10)
    period = 1.0 / 10e6
    assert scale * 10 == pytest.approx(20.0 * period)


def test_timebase_rejects_nonpositive_frequency() -> None:
    with pytest.raises(ValueError, match="freq must be positive"):
        sg8_sweep.timebase_for_frequency(0.0)


def test_expected_vpp_matches_power_formula() -> None:
    # 0 dBm into 50 ohm is 1 mW: V_rms = sqrt(1e-3 * 50), Vpp = 2*sqrt(2)*V_rms
    vpp = sg8_sweep.expected_vpp(0.0, 50.0)
    assert vpp == pytest.approx(2.0 * np.sqrt(2.0) * np.sqrt(50.0e-3))


def test_coherent_amplitude_recovers_known_sine() -> None:
    freq = 12.5e6
    dt = 1e-10
    n = 4000
    t = (np.arange(n) * dt).astype(np.float64)
    peak = 0.3
    volts = peak * np.sin(2.0 * np.pi * freq * t)
    measured = sg8_sweep.coherent_amplitude(volts, t, freq)
    assert measured == pytest.approx(2.0 * peak, rel=1e-3)


def test_coherent_amplitude_ignores_off_frequency_content() -> None:
    freq = 12.5e6
    dt = 1e-10
    n = 4000
    t = (np.arange(n) * dt).astype(np.float64)
    volts = 0.3 * np.sin(2.0 * np.pi * freq * t) + 0.5 * np.sin(2.0 * np.pi * 900e6 * t)
    measured = sg8_sweep.coherent_amplitude(volts, t, freq)
    assert measured == pytest.approx(0.6, rel=1e-2)


def test_split_half_consistency_db_near_zero_for_stable_tone() -> None:
    freq = 12.5e6
    dt = 1e-10
    n = 4000
    t = (np.arange(n) * dt).astype(np.float64)
    volts = 0.3 * np.sin(2.0 * np.pi * freq * t)
    gap = sg8_sweep.split_half_consistency_db(volts, t, freq)
    assert gap == pytest.approx(0.0, abs=0.5)


def test_split_half_consistency_db_large_when_amplitude_jumps_mid_record() -> None:
    freq = 12.5e6
    dt = 1e-10
    n = 4000
    t = (np.arange(n) * dt).astype(np.float64)
    mid = n // 2
    volts = 0.3 * np.sin(2.0 * np.pi * freq * t)
    # Simulate the RF still ramping up during the first half of the capture.
    volts[:mid] *= 0.1
    gap = sg8_sweep.split_half_consistency_db(volts, t, freq)
    assert gap > 15.0


def test_split_half_consistency_db_handles_tiny_records() -> None:
    t = np.array([0.0, 1e-9])
    volts = np.array([0.0, 0.0])
    assert sg8_sweep.split_half_consistency_db(volts, t, 1e6) == 0.0


def test_demo_generator_reset_and_output_state() -> None:
    gen = sg8_sweep.DemoGenerator()
    gen.set_frequency(2.5e9)
    gen.set_power(-3.0)
    gen.set_output("ON")
    assert gen.output is True
    gen.reset()
    assert gen.frequency() == pytest.approx(1e9)
    assert gen.power == pytest.approx(0.0)
    assert gen.output is False
    gen.set_output(True)
    assert gen.output is True
    gen.set_output("off")
    assert gen.output is False


def test_demo_generator_operation_complete_is_synchronous() -> None:
    assert sg8_sweep.DemoGenerator().operation_complete() is True


def test_run_sweep_against_demo_produces_rolloff() -> None:
    generator = sg8_sweep.DemoGenerator()
    scope = sg8_sweep.DemoScope(generator, channels=("C1", "C2", "C3"), noise=0.0)
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 6)

    result = sg8_sweep.run_sweep(
        generator,
        scope,
        channels=("C1", "C2", "C3"),
        frequencies=freqs,
        power_dbm=-10.0,
        settle=0.0,
        reset_settle=0.0,
    )

    assert result.frequencies.size == 6
    assert set(result.amplitudes) == {"C1", "C2", "C3"}
    assert np.all(np.isfinite(result.amplitudes["C1"]))

    # C1 is flat in the demo model; C3 has the lowest cutoff, so it should
    # roll off the most between the first and last swept frequency.
    c1_ratio = result.amplitudes["C1"][-1] / result.amplitudes["C1"][0]
    c3_ratio = result.amplitudes["C3"][-1] / result.amplitudes["C3"][0]
    assert c3_ratio < c1_ratio

    # RF Out must be left off after the sweep.
    assert generator.output is False

    # A clean, stable demo tone shouldn't trip the settle-gap diagnostic.
    assert set(result.settle_gap_db) == {"C1", "C2", "C3"}
    assert np.all(result.settle_gap_db["C1"] < sg8_sweep.DEFAULT_SETTLE_WARN_DB)


def test_run_sweep_calls_generator_operation_complete_every_step() -> None:
    calls = {"n": 0}

    class CountingGenerator(sg8_sweep.DemoGenerator):
        def operation_complete(self) -> bool:
            calls["n"] += 1
            return super().operation_complete()

    generator = CountingGenerator()
    scope = sg8_sweep.DemoScope(generator, channels=("C1",), noise=0.0)
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 5)

    sg8_sweep.run_sweep(
        generator,
        scope,
        channels=("C1",),
        frequencies=freqs,
        power_dbm=-10.0,
        settle=0.0,
        reset_settle=0.0,
    )

    assert calls["n"] == 5


def test_run_sweep_warns_when_capture_looks_unsettled() -> None:
    generator = sg8_sweep.DemoGenerator()

    class JumpyScope(sg8_sweep.DemoScope):
        def acquire(self) -> Capture:
            capture = super().acquire()
            channel = capture["C1"]
            mid = channel.volts.size // 2
            # Simulate the RF still ramping up during the first half.
            channel.volts[:mid] *= 0.05
            return capture

    scope = JumpyScope(generator, channels=("C1",), noise=0.0)
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 3)
    warnings: list[str] = []

    sg8_sweep.run_sweep(
        generator,
        scope,
        channels=("C1",),
        frequencies=freqs,
        power_dbm=-10.0,
        settle=0.0,
        reset_settle=0.0,
        on_warning=warnings.append,
    )

    assert len(warnings) == 3
    assert "possibly not yet settled" in warnings[0]


def test_run_sweep_does_not_warn_below_min_amplitude() -> None:
    generator = sg8_sweep.DemoGenerator()

    class JumpyScope(sg8_sweep.DemoScope):
        def acquire(self) -> Capture:
            capture = super().acquire()
            channel = capture["C1"]
            mid = channel.volts.size // 2
            channel.volts[:mid] *= 0.05
            return capture

    scope = JumpyScope(generator, channels=("C1",), noise=0.0)
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 3)
    warnings: list[str] = []

    sg8_sweep.run_sweep(
        generator,
        scope,
        channels=("C1",),
        frequencies=freqs,
        power_dbm=-80.0,  # tiny amplitude, well under the default min
        settle=0.0,
        reset_settle=0.0,
        on_warning=warnings.append,
    )

    assert warnings == []


def test_run_sweep_calls_on_point_for_every_frequency() -> None:
    generator = sg8_sweep.DemoGenerator()
    scope = sg8_sweep.DemoScope(generator, channels=("C1",), noise=0.0)
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 4)
    seen: list[int] = []

    sg8_sweep.run_sweep(
        generator,
        scope,
        channels=("C1",),
        frequencies=freqs,
        power_dbm=-10.0,
        settle=0.0,
        reset_settle=0.0,
        on_point=lambda index, freq, point: seen.append(index),
    )

    assert seen == [0, 1, 2, 3]


def test_run_sweep_turns_output_off_on_error() -> None:
    generator = sg8_sweep.DemoGenerator()

    class BrokenScope(sg8_sweep.DemoScope):
        def acquire(self):  # type: ignore[override]
            raise RuntimeError("boom")

    scope = BrokenScope(generator, channels=("C1",))
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 3)

    with pytest.raises(RuntimeError, match="boom"):
        sg8_sweep.run_sweep(
            generator,
            scope,
            channels=("C1",),
            frequencies=freqs,
            power_dbm=-10.0,
            settle=0.0,
            reset_settle=0.0,
        )
    assert generator.output is False


def test_run_sweep_without_scope_only_drives_generator() -> None:
    generator = sg8_sweep.DemoGenerator()
    freqs = sg8_sweep.log_sweep(10e6, 2e9, 5)
    seen_freqs: list[float] = []

    result = sg8_sweep.run_sweep(
        generator,
        None,
        channels=(),
        frequencies=freqs,
        power_dbm=-10.0,
        settle=0.0,
        reset_settle=0.0,
        on_point=lambda index, freq, point: seen_freqs.append(freq),
    )

    assert result.amplitudes == {}
    assert result.frequencies.size == 5
    assert seen_freqs == pytest.approx(list(freqs))
    # The generator itself was actually driven through the whole sweep.
    assert generator.frequency() == pytest.approx(freqs[-1])
    assert generator.output is False


def test_plot_sweep_draws_one_axis_per_channel() -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6, 1e7, 1e8]),
        amplitudes={
            "C1": np.array([0.1, 0.1, 0.1]),
            "C2": np.array([0.1, 0.05, 0.01]),
        },
        power_dbm=-10.0,
    )
    fig = sg8_sweep.plot_sweep(result)
    assert len(fig.axes) == 2


def test_plot_sweep_with_reference_uses_db_ylabel() -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6, 1e7]),
        amplitudes={"C1": np.array([0.1, 0.05])},
        power_dbm=-10.0,
    )
    fig = sg8_sweep.plot_sweep(result, reference_vpp=0.1)
    assert fig.axes[0].get_ylabel() == "gain (dB)"


def test_plot_sweep_without_reference_uses_vpp_ylabel() -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6, 1e7]),
        amplitudes={"C1": np.array([0.1, 0.05])},
        power_dbm=-10.0,
    )
    fig = sg8_sweep.plot_sweep(result, reference_vpp=None)
    assert fig.axes[0].get_ylabel() == "Vpp (V)"


def test_plot_sweep_log_x_toggle() -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6, 1e7]),
        amplitudes={"C1": np.array([0.1, 0.05])},
        power_dbm=-10.0,
    )
    log_fig = sg8_sweep.plot_sweep(result, log_x=True)
    linear_fig = sg8_sweep.plot_sweep(result, log_x=False)
    assert log_fig.axes[0].get_xscale() == "log"
    assert linear_fig.axes[0].get_xscale() == "linear"


def test_save_csv_round_trips(tmp_path) -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6, 2e6]),
        amplitudes={"C1": np.array([0.1, 0.2]), "C2": np.array([0.3, 0.4])},
        power_dbm=-5.0,
        settle_gap_db={"C1": np.array([0.5, 1.5]), "C2": np.array([2.5, 3.5])},
    )
    path = tmp_path / "sweep.csv"
    sg8_sweep.save_csv(result, str(path))

    lines = path.read_text().strip().splitlines()
    assert lines[0] == "frequency_hz,C1_vpp,C2_vpp,C1_settle_gap_db,C2_settle_gap_db"
    assert lines[1] == "1000000.0,0.1,0.3,0.5,2.5"
    assert lines[2] == "2000000.0,0.2,0.4,1.5,3.5"


def test_save_csv_without_settle_gap_data_writes_nan(tmp_path) -> None:
    result = sg8_sweep.SweepResult(
        frequencies=np.array([1e6]),
        amplitudes={"C1": np.array([0.1])},
        power_dbm=-5.0,
    )
    path = tmp_path / "sweep.csv"
    sg8_sweep.save_csv(result, str(path))

    lines = path.read_text().strip().splitlines()
    assert lines[0] == "frequency_hz,C1_vpp,C1_settle_gap_db"
    assert lines[1] == "1000000.0,0.1,nan"


def test_build_parser_defaults() -> None:
    args = sg8_sweep.build_parser().parse_args([])
    assert args.channels == ["C1", "C2", "C3"]
    assert args.start == sg8_sweep.DEFAULT_START_HZ
    assert args.stop == sg8_sweep.DEFAULT_STOP_HZ
    assert args.power == sg8_sweep.DEFAULT_POWER_DBM
    assert args.demo is False
    assert args.log is True
    assert args.raw_volts is False


def test_build_parser_log_and_raw_volts_toggles() -> None:
    args = sg8_sweep.build_parser().parse_args(["--no-log", "--raw-volts"])
    assert args.log is False
    assert args.raw_volts is True


def test_build_parser_no_scope_default_and_flag() -> None:
    assert sg8_sweep.build_parser().parse_args([]).no_scope is False
    assert sg8_sweep.build_parser().parse_args(["--no-scope"]).no_scope is True


def test_build_parser_settle_warn_defaults_and_override() -> None:
    args = sg8_sweep.build_parser().parse_args([])
    assert args.settle_warn_db == sg8_sweep.DEFAULT_SETTLE_WARN_DB
    assert args.settle_warn_min_vpp == sg8_sweep.DEFAULT_SETTLE_WARN_MIN_VPP

    args = sg8_sweep.build_parser().parse_args(
        ["--settle-warn-db", "3", "--settle-warn-min-vpp", "0.001"]
    )
    assert args.settle_warn_db == pytest.approx(3.0)
    assert args.settle_warn_min_vpp == pytest.approx(0.001)


def test_make_scope_returns_none_when_no_scope() -> None:
    args = sg8_sweep.build_parser().parse_args(["--demo", "--no-scope"])
    generator = sg8_sweep._make_generator(args)
    assert sg8_sweep._make_scope(args, generator) is None


def test_main_demo_end_to_end(tmp_path) -> None:
    csv_path = tmp_path / "out.csv"
    png_path = tmp_path / "out.png"
    code = sg8_sweep.main(
        [
            "--demo",
            "--points",
            "4",
            "--no-show",
            "--output-csv",
            str(csv_path),
            "--output-plot",
            str(png_path),
        ]
    )
    assert code == 0
    assert csv_path.exists()
    assert png_path.exists()


def test_main_demo_linear_raw_volts_end_to_end(tmp_path) -> None:
    png_path = tmp_path / "out.png"
    code = sg8_sweep.main(
        [
            "--demo",
            "--points",
            "4",
            "--no-log",
            "--raw-volts",
            "--no-show",
            "--output-plot",
            str(png_path),
        ]
    )
    assert code == 0
    assert png_path.exists()


def test_main_demo_no_scope_end_to_end(tmp_path) -> None:
    png_path = tmp_path / "out.png"
    code = sg8_sweep.main(
        [
            "--demo",
            "--no-scope",
            "--points",
            "4",
            "--output-plot",
            str(png_path),
        ]
    )
    assert code == 0
    # --no-scope skips capture entirely, so no plot/CSV is ever written.
    assert not png_path.exists()
