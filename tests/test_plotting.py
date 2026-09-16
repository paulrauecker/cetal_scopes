import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest

from cetal_scopes import Capture, Channel
from cetal_scopes.analysis import fft
from cetal_scopes.plotting import decimate_envelope, plot_capture, plot_spectrum


def make_capture() -> Capture:
    return Capture(
        volts=np.array([[0.0, 1.0, 2.0], [2.0, 1.0, 0.0]]),
        t0=0.0,
        dt=1e-6,
        channel_names=("CH1", "CH2"),
    )


def make_tone() -> Channel:
    t = np.arange(1000) / 1000.0
    return Channel(name="CH1", volts=np.sin(2 * np.pi * 100.0 * t), t0=0.0, dt=1e-3)


def test_plot_capture_all_channels() -> None:
    ax = plot_capture(make_capture())
    assert len(ax.lines) == 2
    assert [line.get_label() for line in ax.lines] == ["CH1", "CH2"]
    assert ax.get_xlabel() == "time (s)"
    plt.close(ax.figure)


def test_plot_capture_time_unit_scales_axis() -> None:
    ax = plot_capture(make_capture(), channels=("CH1",), time_unit="us")
    assert ax.get_xlabel() == "time (us)"
    np.testing.assert_allclose(ax.lines[0].get_xdata(), [0.0, 1.0, 2.0])
    plt.close(ax.figure)


def test_plot_capture_bad_time_unit() -> None:
    with pytest.raises(ValueError, match="time_unit"):
        plot_capture(make_capture(), time_unit="fortnight")


def test_plot_spectrum_amplitude_uses_log_x() -> None:
    ax = plot_spectrum(fft(make_tone()))
    assert len(ax.lines) == 1
    assert ax.get_xscale() == "log"
    assert "amplitude (V)" == ax.get_ylabel()
    plt.close(ax.figure)


def test_plot_spectrum_psd_on_existing_axes() -> None:
    spectrum = fft(make_tone())
    _, ax = plt.subplots()
    returned = plot_spectrum(spectrum, ax=ax, psd=True, log_x=False, log_y=True)
    assert returned is ax
    assert ax.get_yscale() == "log"
    assert ax.get_ylabel() == "PSD (V^2/Hz)"
    plt.close(ax.figure)


# ---------------------------------------------------------------------------
# decimate_envelope
# ---------------------------------------------------------------------------


def test_decimate_envelope_returns_short_traces_unchanged() -> None:
    t = np.arange(10, dtype=np.float64)
    v = np.arange(10, dtype=np.float64)
    out_t, out_v = decimate_envelope(t, v, 100)

    assert out_t is t
    assert out_v is v


def test_decimate_envelope_respects_the_point_budget() -> None:
    t = np.arange(100_000, dtype=np.float64)
    v = np.sin(t * 0.01)
    out_t, out_v = decimate_envelope(t, v, 2000)

    assert out_t.size <= 2000
    assert out_t.size == out_v.size


def test_decimate_envelope_keeps_a_narrow_spike_that_striding_would_lose() -> None:
    n = 100_000
    t = np.arange(n, dtype=np.float64)
    v = np.zeros(n)
    v[54_321] = 1.0  # one sample wide, nowhere near a stride boundary

    strided_v = v[::100]
    envelope_t, envelope_v = decimate_envelope(t, v, 2000)

    assert strided_v.max() == 0.0  # plain striding drops it entirely
    assert envelope_v.max() == 1.0
    assert envelope_t[int(np.argmax(envelope_v))] == 54_321.0


def test_decimate_envelope_keeps_both_extremes_of_each_bin() -> None:
    n = 10_000
    t = np.arange(n, dtype=np.float64)
    v = np.sin(t * 0.1)
    _, out_v = decimate_envelope(t, v, 200)

    assert out_v.min() == pytest.approx(v.min(), abs=1e-6)
    assert out_v.max() == pytest.approx(v.max(), abs=1e-6)


def test_decimate_envelope_output_is_in_time_order() -> None:
    t = np.arange(50_000, dtype=np.float64)
    rng = np.random.default_rng(0)
    out_t, _ = decimate_envelope(t, rng.standard_normal(50_000), 1000)

    assert np.all(np.diff(out_t) > 0)


def test_decimate_envelope_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same shape"):
        decimate_envelope(np.arange(10.0), np.arange(5.0), 4)
