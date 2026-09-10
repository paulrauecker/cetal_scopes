import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest

from cetal_scopes import Capture, Channel
from cetal_scopes.analysis import fft
from cetal_scopes.plotting import plot_capture, plot_spectrum


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
