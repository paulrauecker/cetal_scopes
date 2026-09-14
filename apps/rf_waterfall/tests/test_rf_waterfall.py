"""Tests for the RF waterfall app (no hardware required)."""

from __future__ import annotations

import csv
from collections.abc import Iterator

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest
import rf_waterfall

from cetal_scopes import Capture, Channel
from cetal_scopes.analysis import Spectrogram, fft
from cetal_scopes.scopes.base import Scope

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    yield
    plt.close("all")


def test_demo_scope_shape_and_dt() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=1024, sample_rate=1e7)
    capture = scope.acquire()
    assert isinstance(capture, Capture)
    assert capture.n_channels == 1
    assert capture.n_samples == 1024
    assert capture.dt == pytest.approx(1e-7)
    assert "C1" in capture


def test_demo_scope_chirp_moves_peak_bin() -> None:
    scope = rf_waterfall.DemoScope(
        channel="C1", f_start=1e6, f_end=5e6, sweep_period_frames=10, noise=0.0
    )
    first = scope.acquire()["C1"]
    later = scope.acquire()["C1"]
    for _ in range(3):
        later = scope.acquire()["C1"]
    first_spectrum = _fft_peak(first)
    later_spectrum = _fft_peak(later)
    assert later_spectrum != pytest.approx(first_spectrum, rel=1e-3)


def _fft_peak(channel: Channel) -> float:
    spectrum = fft(channel, window="boxcar", detrend=None)
    freq, _ = spectrum.peak()
    return float(freq)


def test_waterfall_viewer_update_fills_buffer_headlessly() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=512)
    viewer = rf_waterfall.WaterfallViewer(depth=5, window="boxcar", detrend=None)
    drawn = rf_waterfall.run(
        scope, viewer, channel="C1", show=False, pause=lambda s: None, frames=5
    )
    assert drawn == 5
    spectrogram = viewer.to_spectrogram()
    assert spectrogram.n_rows == 5
    assert not np.isnan(spectrogram.amplitude).any()
    viewer.close()


def test_record_waterfall_returns_expected_shape() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=512)
    rows: list[int] = []
    spectrogram = rf_waterfall.record_waterfall(
        scope,
        channel="C1",
        n_sweeps=7,
        window="boxcar",
        detrend=None,
        on_row=lambda row, spectrum: rows.append(row),
    )
    assert spectrogram.n_rows == 7
    assert spectrogram.n_bins == 512 // 2 + 1
    assert rows == list(range(1, 8))


def test_record_waterfall_rejects_bad_n_sweeps() -> None:
    scope = rf_waterfall.DemoScope(channel="C1")
    with pytest.raises(ValueError, match="n_sweeps"):
        rf_waterfall.record_waterfall(scope, channel="C1", n_sweeps=0)


class _RetryOnceScope(Scope):
    """Stub whose first acquire() times out, matching the Siglent driver's idiom."""

    def __init__(self) -> None:
        self._tries = 0

    def connect(self) -> None: ...
    def configure(self, settings: object) -> None: ...
    def close(self) -> None: ...

    def acquire(self) -> Capture:
        self._tries += 1
        if self._tries == 1:
            raise TimeoutError("acquisition did not complete within 5s")
        t = np.arange(256, dtype=np.float64) * 1e-7
        volts = 0.1 * np.sin(2 * np.pi * 1e6 * t)
        return Capture(
            volts=volts[np.newaxis, :], t0=0.0, dt=1e-7, channel_names=("C1",)
        )


def test_record_waterfall_retries_on_trigger_timeout() -> None:
    scope = _RetryOnceScope()
    spectrogram = rf_waterfall.record_waterfall(scope, channel="C1", n_sweeps=1)
    assert spectrogram.n_rows == 1
    assert scope._tries == 2


def test_record_waterfall_reraises_other_timeout_errors() -> None:
    class _OtherTimeout(Scope):
        def connect(self) -> None: ...
        def configure(self, settings: object) -> None: ...
        def close(self) -> None: ...

        def acquire(self) -> Capture:
            raise TimeoutError("some other failure")

    with pytest.raises(TimeoutError, match="some other failure"):
        rf_waterfall.record_waterfall(_OtherTimeout(), channel="C1", n_sweeps=1)


def test_plot_spectrogram_returns_figure_with_image() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=256)
    spectrogram = rf_waterfall.record_waterfall(scope, channel="C1", n_sweeps=3)
    fig = rf_waterfall.plot_spectrogram(spectrogram)
    assert len(fig.axes) == 2  # main axes + colorbar axes
    assert len(fig.axes[0].images) == 1


def test_save_csv_round_trip(tmp_path) -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=128)
    spectrogram = rf_waterfall.record_waterfall(scope, channel="C1", n_sweeps=3)
    path = tmp_path / "waterfall.csv"
    rf_waterfall.save_csv(spectrogram, str(path))
    with open(path, newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["sweep_index", "time_s", "freq_hz", "amplitude"]
    assert len(rows) - 1 == spectrogram.n_rows * spectrogram.n_bins


def test_trigger_level_warning_flags_clamped_level() -> None:
    assert rf_waterfall.trigger_level_warning(1.0, 0.1) is not None
    assert rf_waterfall.trigger_level_warning(1.0, 1.0) is None


def test_build_parser_defaults() -> None:
    args = rf_waterfall.build_parser().parse_args([])
    assert args.mode == "dynamic"
    assert args.channel == rf_waterfall.DEFAULT_CHANNEL
    assert args.depth == rf_waterfall.DEFAULT_DEPTH
    assert args.db is True
    # --trigger-mode has no static default; main() resolves it per mode.
    assert args.trigger_mode is None


def test_main_resolves_trigger_mode_per_mode(monkeypatch) -> None:
    """Regression test: dynamic mode must default to AUTO, not SINGle.

    A SINGle re-arm blocks on the scope's leftover/unconfigured trigger
    (dynamic mode never sends trigger source/level/slope -- only record mode
    does), so every live-view frame could stall for the full
    --trigger-timeout. Live viewing has no gating requirement, so it must
    default to AUTO (free-running), matching apps/realtime_viewer; record
    mode keeps defaulting to SINGle.
    """
    captured: list[str] = []

    def fake_siglent(**kwargs: object) -> object:
        captured.append(str(kwargs["trigger_mode"]))
        return rf_waterfall.DemoScope(channel="C1")

    monkeypatch.setattr(rf_waterfall, "SiglentSDS6204L", fake_siglent)
    rf_waterfall.main(["--frames", "1", "--no-show"])
    assert captured == ["AUTO"]

    captured.clear()
    rf_waterfall.main(["--mode", "record", "--sweeps", "1", "--no-show"])
    assert captured == ["SINGle"]


def test_build_parser_mode_choices() -> None:
    with pytest.raises(SystemExit):
        rf_waterfall.build_parser().parse_args(["--mode", "bogus"])


def test_run_retries_on_trigger_timeout_instead_of_crashing() -> None:
    """Regression test: a triggered re-arm miss must not crash the live loop.

    scope.acquire() raising TimeoutError("... did not complete ...") on a
    SINGle/NORMal re-arm is normal (no trigger yet), matching
    SiglentSDS6204L's convention -- run() must swallow it and keep polling,
    the same idiom apps/bdot_probe uses, instead of propagating the
    exception out of main().
    """
    scope = _RetryOnceScope()
    viewer = rf_waterfall.WaterfallViewer(depth=3, window="boxcar", detrend=None)
    drawn = rf_waterfall.run(
        scope, viewer, channel="C1", show=False, pause=lambda s: None, frames=1
    )
    assert drawn == 1
    assert scope._tries == 2
    viewer.close()


def test_main_record_mode_smoke(tmp_path) -> None:
    csv_path = tmp_path / "out.csv"
    plot_path = tmp_path / "out.png"
    exit_code = rf_waterfall.main(
        [
            "--demo",
            "--mode",
            "record",
            "--sweeps",
            "5",
            "--no-show",
            "--output-csv",
            str(csv_path),
            "--output-plot",
            str(plot_path),
        ]
    )
    assert exit_code == 0
    assert csv_path.exists()
    assert plot_path.exists()


def test_main_requires_sweeps_in_record_mode() -> None:
    with pytest.raises(SystemExit):
        rf_waterfall.main(["--demo", "--mode", "record", "--no-show"])


def test_main_dynamic_mode_smoke() -> None:
    exit_code = rf_waterfall.main(["--demo", "--frames", "3", "--no-show"])
    assert exit_code == 0


def test_acquire_single_retries_on_trigger_timeout() -> None:
    scope = _RetryOnceScope()
    channel = rf_waterfall.acquire_single(scope, "C1")
    assert channel.name == "C1"
    assert scope._tries == 2


def test_transient_waterfall_returns_expected_shape() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=2000, sample_rate=1e6)
    spectrogram = rf_waterfall.transient_waterfall(
        scope, channel="C1", stft_window=200e-6, window="boxcar", detrend=None
    )
    # dt = 1e-6, segment_samples = round(200e-6/1e-6) = 200, default hop 100 ->
    # starts 0, 100, ..., 1800 -> 19 rows.
    assert spectrogram.n_rows == 19
    assert spectrogram.n_bins == 101


def test_transient_waterfall_on_capture_reports_raw_and_spectral_peaks() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=2000, sample_rate=1e6)
    calls: list[tuple[Channel, Spectrogram]] = []
    rf_waterfall.transient_waterfall(
        scope,
        channel="C1",
        stft_window=200e-6,
        window="boxcar",
        detrend=None,
        on_capture=lambda source, spectrogram: calls.append((source, spectrogram)),
    )
    assert len(calls) == 1
    source, spectrogram = calls[0]
    assert isinstance(source, Channel)
    assert source.n_samples == 2000
    peak_time, peak_freq, peak_amplitude = spectrogram.peak()
    assert peak_freq >= 0.0
    assert peak_amplitude > 0.0
    assert 0.0 <= peak_time <= 2000 * 1e-6


def test_transient_waterfall_times_are_physical_not_wall_clock() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=2000, sample_rate=1e6)
    spectrogram = rf_waterfall.transient_waterfall(
        scope,
        channel="C1",
        stft_window=200e-6,
        stft_hop=200e-6,
        window="boxcar",
        detrend=None,
    )
    # 10 non-overlapping 200-sample segments; centers at 100us, 300us, ..., 1900us.
    expected = [(i * 200 + 100) * 1e-6 for i in range(10)]
    assert spectrogram.times.tolist() == pytest.approx(expected)


def test_plot_spectrogram_time_axis_uses_spectrogram_times() -> None:
    scope = rf_waterfall.DemoScope(channel="C1", n_samples=2000, sample_rate=1e6)
    spectrogram = rf_waterfall.transient_waterfall(
        scope, channel="C1", stft_window=200e-6, window="boxcar", detrend=None
    )
    fig = rf_waterfall.plot_spectrogram(
        spectrogram, time_axis=True, title="custom title"
    )
    ax = fig.axes[0]
    assert ax.get_ylabel() == "time (s)"
    assert ax.get_title() == "custom title"
    _, _, y0, y1 = ax.images[0].get_extent()
    assert y0 == pytest.approx(float(spectrogram.times[0]))
    assert y1 == pytest.approx(float(spectrogram.times[-1]))


def test_main_transient_mode_smoke(tmp_path) -> None:
    csv_path = tmp_path / "out.csv"
    plot_path = tmp_path / "out.png"
    exit_code = rf_waterfall.main(
        [
            "--demo",
            "--mode",
            "transient",
            "--stft-window",
            "1e-6",
            "--no-show",
            "--output-csv",
            str(csv_path),
            "--output-plot",
            str(plot_path),
        ]
    )
    assert exit_code == 0
    assert csv_path.exists()
    assert plot_path.exists()


def test_main_requires_stft_window_in_transient_mode() -> None:
    with pytest.raises(SystemExit):
        rf_waterfall.main(["--demo", "--mode", "transient", "--no-show"])
