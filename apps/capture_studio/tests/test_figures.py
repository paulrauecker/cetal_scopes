"""Plotly figure specifications built from a shot."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from figures import (
    LAYOUTS,
    LayoutMode,
    coherence_figure,
    decimate_spectrum,
    empty_figure,
    fft_figure,
    measurement_table,
    spectrogram_figure,
    time_figure,
    time_scale,
    transfer_figure,
    vector_figure,
    window_samples,
    xy_figure,
)

from cetal_scopes import Capture, Shot
from cetal_scopes.analysis import lowpass

DT = 1e-9
N = 4096


def make_shot() -> Shot:
    t = np.arange(N, dtype=np.float64) * DT
    burst = np.exp(-0.5 * ((t - 2e-6) / 2e-7) ** 2)
    a = Capture(
        volts=np.vstack([np.sin(2e8 * np.pi * t) * burst, burst]),
        t0=0.0,
        dt=DT,
        channel_names=("C1", "C2"),
    )
    b = Capture(
        volts=(np.sin(2e8 * np.pi * t) * burst)[None, :],
        t0=0.0,
        dt=2 * DT,
        channel_names=("CH0",),
    )
    shot = Shot()
    shot.add("siglent", a)
    shot.add("m5i", b, offset=3e-9)
    return shot


def test_empty_figure_carries_the_reason() -> None:
    figure = empty_figure("No shot yet.")
    assert figure["data"] == []
    assert figure["layout"]["annotations"][0]["text"] == "No shot yet."


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_draws_every_channel(layout: LayoutMode) -> None:
    figure = time_figure(make_shot(), layout=layout)
    names = {trace["name"] for trace in figure["data"]}

    assert names == {"siglent:C1", "siglent:C2", "m5i:CH0"}


def test_overlay_puts_everything_on_one_axes() -> None:
    figure = time_figure(make_shot(), layout="overlay")
    assert {trace["yaxis"] for trace in figure["data"]} == {"y"}
    assert "grid" not in figure["layout"]


def test_per_capture_gives_each_instrument_a_row() -> None:
    figure = time_figure(make_shot(), layout="per-capture")
    assert figure["layout"]["grid"]["rows"] == 2
    assert len({trace["yaxis"] for trace in figure["data"]}) == 2


def test_per_channel_gives_each_channel_a_row() -> None:
    figure = time_figure(make_shot(), layout="per-channel")
    assert figure["layout"]["grid"]["rows"] == 3
    assert len({trace["yaxis"] for trace in figure["data"]}) == 3


def test_stacked_rows_share_the_x_axis() -> None:
    # Comparing the same instant across instruments is the point of stacking.
    figure = time_figure(make_shot(), layout="per-capture")
    assert figure["layout"]["xaxis2"]["matches"] == "x"


def test_the_offset_moves_the_traces() -> None:
    shot = make_shot()
    before = time_figure(shot)["data"]
    shot.set_offset("m5i", 1e-6)
    after = time_figure(shot)["data"]

    index = [trace["name"] for trace in before].index("m5i:CH0")
    assert after[index]["x"][0] > before[index]["x"][0]


def test_a_channel_selection_is_honoured() -> None:
    figure = time_figure(make_shot(), channels=["siglent:C1"])
    assert [trace["name"] for trace in figure["data"]] == ["siglent:C1"]


def test_an_empty_selection_explains_itself() -> None:
    figure = time_figure(make_shot(), channels=[])
    assert "No channels" in figure["layout"]["annotations"][0]["text"]


def test_processed_channels_replace_the_recorded_ones() -> None:
    shot = make_shot()
    processed = {
        key: lowpass(channel, cutoff=1e7)
        for key, channel in shot.aligned_channels().items()
    }
    plain = time_figure(shot)["data"][0]["y"]
    filtered = time_figure(shot, processed=processed)["data"][0]["y"]

    assert max(map(abs, filtered)) < max(map(abs, plain))


def test_long_records_are_decimated_to_the_budget() -> None:
    figure = time_figure(make_shot(), max_points=200)
    assert all(len(trace["x"]) <= 200 for trace in figure["data"])


@pytest.mark.parametrize(
    ("span", "unit"), [(2.0, "s"), (2e-3, "ms"), (2e-6, "µs"), (2e-9, "ns")]
)
def test_the_time_unit_follows_the_span(span: float, unit: str) -> None:
    assert time_scale(span)[1] == unit


def test_fft_draws_every_channel_and_annotates_the_peak() -> None:
    figure = fft_figure(make_shot())

    assert len(figure["data"]) == 3
    assert figure["layout"]["xaxis"]["type"] == "log"
    assert len(figure["layout"]["annotations"]) == 3


def test_fft_axes_can_be_made_linear() -> None:
    figure = fft_figure(make_shot(), log_x=False, log_y=False)
    assert figure["layout"]["xaxis"]["type"] == "linear"
    assert figure["layout"]["yaxis"]["type"] == "linear"


def test_fft_can_show_psd() -> None:
    figure = fft_figure(make_shot(), psd=True)
    assert figure["layout"]["yaxis"]["title"]["text"] == "PSD (V^2/Hz)"


def test_fft_respects_a_frequency_window() -> None:
    figure = fft_figure(make_shot(), f_min=1e7, f_max=2e8)
    assert all(min(trace["x"]) >= 1e7 for trace in figure["data"])
    assert all(max(trace["x"]) <= 2e8 for trace in figure["data"])


def test_an_empty_frequency_window_explains_itself() -> None:
    figure = fft_figure(make_shot(), f_min=1e15)
    assert "frequency range" in figure["layout"]["annotations"][0]["text"]


def test_spectrogram_is_a_heatmap() -> None:
    figure = spectrogram_figure(make_shot(), "siglent:C1")
    assert figure["data"][0]["type"] == "heatmap"
    assert len(figure["data"][0]["z"]) > 1


def test_spectrogram_rejects_an_unknown_channel() -> None:
    with pytest.raises(KeyError):
        spectrogram_figure(make_shot(), "ghost:C1")


def test_coherence_is_bounded_and_labelled() -> None:
    figure = coherence_figure(make_shot(), "siglent:C1", "m5i:CH0")
    assert figure["layout"]["yaxis"]["range"] == [0, 1.05]
    assert all(0.0 <= value <= 1.0 for value in figure["data"][0]["y"])


def test_transfer_shows_gain_phase_and_coherence() -> None:
    figure = transfer_figure(make_shot(), "siglent:C1", "m5i:CH0")
    assert [trace["name"] for trace in figure["data"]] == [
        "|H|",
        "phase (deg)",
        "coherence",
    ]
    # nan must not reach the JSON; Plotly wants null for a gap.
    assert all(
        value is None or isinstance(value, float) for value in figure["data"][0]["y"]
    )


def test_xy_works_across_instruments() -> None:
    figure = xy_figure(make_shot(), "siglent:C1", "m5i:CH0")
    assert len(figure["data"][0]["x"]) == len(figure["data"][0]["y"])
    assert figure["layout"]["yaxis"]["scaleanchor"] == "x"


def test_measurements_cover_every_channel() -> None:
    rows = measurement_table(make_shot())
    assert [row["channel"] for row in rows] == [
        "siglent:C1",
        "siglent:C2",
        "m5i:CH0",
    ]
    assert all(row["n_samples"] > 0 for row in rows)


def test_measurements_report_none_for_unsupported_timings() -> None:
    shot = Shot()
    shot.add("flat", Capture(volts=np.ones((1, 256)), t0=0.0, dt=DT))
    row = measurement_table(shot)[0]

    # A flat trace has no edge; JSON gets null rather than nan.
    assert row["rise_time"] is None
    assert row["fwhm"] is None


# ---------------------------------------------------------------------------
# Field vector
# ---------------------------------------------------------------------------


def make_triad_shot(components: tuple[float, float, float]) -> Shot:
    t = np.arange(N, dtype=np.float64) * DT
    rows = [
        amplitude * np.sin(2.0 * np.pi * 5e7 * t) for amplitude in map(abs, components)
    ]
    rows = [
        row if amplitude >= 0 else -row
        for row, amplitude in zip(rows, components, strict=True)
    ]
    shot = Shot()
    shot.add(
        "probe",
        Capture(
            volts=np.vstack(rows),
            t0=0.0,
            dt=DT,
            channel_names=("X", "Y", "Z"),
        ),
    )
    return shot


def test_the_vector_panel_draws_a_3d_arrow() -> None:
    keys = ["probe:X", "probe:Y", "probe:Z"]
    figure = vector_figure(make_triad_shot((1.0, 0.5, 0.25)), keys, 5e7)

    assert figure["data"][0]["type"] == "scatter3d"
    assert figure["data"][0]["x"][0] == 0.0  # drawn from the origin
    assert figure["data"][0]["x"][1] == pytest.approx(1.0, rel=0.05)
    assert figure["data"][0]["z"][1] == pytest.approx(0.25, rel=0.05)


def test_the_vector_panel_shows_a_negative_component_pointing_the_other_way() -> None:
    # FFT magnitudes would force this into the +++ octant.
    keys = ["probe:X", "probe:Y", "probe:Z"]
    figure = vector_figure(make_triad_shot((1.0, -0.5, 0.25)), keys, 5e7)

    assert figure["data"][0]["y"][1] < 0


def test_the_vector_panel_needs_exactly_three_channels() -> None:
    with pytest.raises(ValueError, match="exactly three channels"):
        vector_figure(make_triad_shot((1.0, 1.0, 1.0)), ["probe:X"], 5e7)


def test_the_vector_panel_refuses_channels_from_different_captures() -> None:
    shot = make_shot()
    with pytest.raises(ValueError, match="must come from one capture"):
        vector_figure(shot, ["siglent:C1", "siglent:C2", "m5i:CH0"], 1e8)


def test_spectra_are_decimated_to_the_budget() -> None:
    # A full-rate spectrum is half the record per channel, which is a
    # multi-megabyte response the browser cannot draw. The figure is binned
    # instead; the analysis behind it is not.
    figure = fft_figure(make_shot(), max_points=200)
    assert all(len(trace["x"]) <= 200 for trace in figure["data"])
    assert all(len(trace["x"]) == len(trace["y"]) for trace in figure["data"])


def test_decimation_keeps_the_peak() -> None:
    freq = np.linspace(1.0, 1e8, 50_000)
    values = np.full(freq.shape, 0.01)
    values[31_415] = 7.0

    for log_x in (False, True):
        drawn_freq, drawn_values = decimate_spectrum(freq, values, 400, log_x=log_x)
        assert drawn_freq.size <= 400
        assert drawn_values.max() == pytest.approx(7.0)
        peak = drawn_freq[int(np.argmax(drawn_values))]
        assert peak == pytest.approx(freq[31_415])


def test_decimation_leaves_a_short_spectrum_alone() -> None:
    freq = np.linspace(1.0, 10.0, 50)
    values = np.arange(50, dtype=np.float64)
    drawn_freq, drawn_values = decimate_spectrum(freq, values, 4000)
    assert drawn_freq is freq or np.array_equal(drawn_freq, freq)
    assert np.array_equal(drawn_values, values)


def test_the_peak_annotation_is_measured_before_decimation() -> None:
    # The label must report the real peak frequency, not a binned one.
    full = fft_figure(make_shot(), max_points=1_000_000)["layout"]["annotations"]
    binned = fft_figure(make_shot(), max_points=100)["layout"]["annotations"]
    assert [item["text"] for item in binned] == [item["text"] for item in full]


def test_peak_labels_do_not_stack_on_one_another() -> None:
    offsets = [item["ay"] for item in fft_figure(make_shot())["layout"]["annotations"]]
    assert len(set(offsets)) == len(offsets)


def test_a_spectrogram_caps_its_frequency_bins() -> None:
    figure = spectrogram_figure(make_shot(), "siglent:C1", max_freq_bins=32)
    assert len(figure["data"][0]["y"]) <= 32
    assert all(len(row) <= 32 for row in figure["data"][0]["z"])


def test_stacked_rows_label_only_the_bottom_axis() -> None:
    # A time title on every row lands on the row beneath it.
    layout = time_figure(make_shot(), layout="per-channel")["layout"]
    titled = [
        key
        for key, value in layout.items()
        if key.startswith("xaxis") and isinstance(value, dict) and "title" in value
    ]
    assert len(titled) == 1


def test_db_reports_the_right_level_for_a_known_tone() -> None:
    dt, n, amplitude = 1e-9, 65536, 0.5
    t = np.arange(n) * dt
    shot = Shot()
    shot.add(
        "gen",
        Capture(
            volts=(amplitude * np.sin(2e7 * 2 * np.pi * t))[None, :], t0=0.0, dt=dt
        ),
    )

    figure = fft_figure(shot, window="flattop", db=True)

    # 20 log10(0.5) = -6.02 dB re 1 V; the slack is the window's own
    # amplitude accuracy, not the conversion's.
    assert max(figure["data"][0]["y"]) == pytest.approx(-6.02, abs=0.05)
    assert figure["layout"]["yaxis"]["title"]["text"] == "dB re 1 V"


def test_a_psd_in_db_uses_the_power_factor() -> None:
    # A PSD is already a power quantity: 10 log10, not 20. Getting this wrong
    # doubles every number on the axis.
    shot = make_shot()
    amplitude = fft_figure(shot, psd=True)["data"][0]["y"]
    decibels = fft_figure(shot, psd=True, db=True)["data"][0]["y"]

    peak = max(amplitude)
    assert max(decibels) == pytest.approx(10.0 * np.log10(peak), abs=1e-3)


def test_db_overrides_a_logarithmic_y_axis() -> None:
    # dB is already logarithmic; a log axis on top of it is a log of a log.
    figure = fft_figure(make_shot(), db=True, log_y=True)
    assert figure["layout"]["yaxis"]["type"] == "linear"


def test_a_dead_channel_cannot_drag_the_db_axis_down() -> None:
    shot = Shot()
    shot.add("dead", Capture(volts=np.zeros((1, 1024)), t0=0.0, dt=1e-9))

    values = fft_figure(shot, db=True)["data"][0]["y"]

    assert all(np.isfinite(value) for value in values)
    assert min(values) == pytest.approx(-400.0)


def test_the_axis_title_follows_the_channel_unit() -> None:
    shot = make_shot()
    # Channels of different units share no unit, so none is claimed.
    processed = {"siglent:C1": replace(shot.aligned_channels()["siglent:C1"], unit="T")}
    mixed = fft_figure(shot, processed=processed)
    assert mixed["layout"]["yaxis"]["title"]["text"] == "amplitude"


def test_a_frequency_band_spends_the_whole_budget_on_the_band() -> None:
    # Narrowing the band must buy resolution, not lose it: the limits are
    # applied before the drawing budget, not after. Only a record with far
    # more bins than the budget can show this -- on a short one there is no
    # spare resolution to redistribute.
    n = 65536
    t = np.arange(n, dtype=np.float64) * DT
    shot = Shot()
    shot.add("long", Capture(volts=np.sin(2e7 * 2 * np.pi * t)[None, :], t0=0.0, dt=DT))

    full = fft_figure(shot, max_points=400)["data"][0]["x"]
    band = fft_figure(shot, f_min=1.9e7, f_max=2.1e7, max_points=400)["data"][0]["x"]

    assert min(band) >= 1.9e7
    assert max(band) <= 2.1e7

    def hz_per_point(x: list[float]) -> float:
        return (max(x) - min(x)) / len(x)

    assert hz_per_point(band) < hz_per_point(full) / 10


def long_shot(n: int = 65536, dt: float = DT) -> Shot:
    t = np.arange(n, dtype=np.float64) * dt
    shot = Shot()
    shot.add("m5i", Capture(volts=np.sin(2e8 * 2 * np.pi * t)[None, :], t0=0.0, dt=dt))
    return shot


def test_a_narrow_window_draws_every_sample_in_it() -> None:
    # The whole point: zooming in has to recover real samples, not magnify
    # the ones already sent.
    shot = long_shot()
    span = 500 * DT  # 500 samples, well inside a 4000-point budget

    figure = time_figure(shot, max_points=4000, t_min=1e-6, t_max=1e-6 + span)
    meta = figure["layout"]["meta"]

    assert meta["decimated"] is False
    assert meta["drawn_points"] == meta["samples_in_window"]
    x = np.asarray(figure["data"][0]["x"])
    # Drawn at the native sample interval, in the figure's own time unit.
    assert np.median(np.diff(x)) == pytest.approx(DT / meta["time_scale"], rel=1e-6)


def test_a_window_spends_the_whole_budget_on_itself() -> None:
    shot = long_shot()
    full = time_figure(shot, max_points=1000)
    zoomed = time_figure(shot, max_points=1000, t_min=1e-6, t_max=6e-6)

    def step(figure: dict[str, Any]) -> float:
        x = np.asarray(figure["data"][0]["x"])
        return float(np.median(np.diff(x)))

    assert step(zoomed) < step(full) / 5
    assert full["layout"]["meta"]["decimated"] is True


def test_the_time_unit_does_not_change_as_you_zoom() -> None:
    # The browser converts axis coordinates back to seconds with this scale
    # to ask for the next window; a scale that moved under it would redefine
    # the numbers mid-gesture.
    shot = long_shot()
    whole = time_figure(shot)["layout"]["meta"]
    tiny = time_figure(shot, t_min=1e-6, t_max=1e-6 + 20 * DT)["layout"]["meta"]

    assert tiny["time_scale"] == whole["time_scale"]
    assert tiny["time_unit"] == whole["time_unit"]


def test_a_window_outside_a_capture_leaves_it_empty_not_missing() -> None:
    # Captures with different pretriggers do not all reach into every window.
    # The trace stays, so its colour and legend entry do not shuffle.
    shot = make_shot()
    figure = time_figure(shot, t_min=1.0, t_max=2.0)

    assert len(figure["data"]) == 3
    assert all(len(trace["x"]) == 0 for trace in figure["data"])


def test_window_samples_is_inclusive_of_its_bounds() -> None:
    channel = long_shot().aligned_channels()["m5i:CH1"]
    times, values = window_samples(channel, 10 * DT, 20 * DT)

    assert times[0] == pytest.approx(10 * DT)
    assert times[-1] == pytest.approx(20 * DT)
    assert times.size == values.size == 11


def test_the_revision_follows_the_shot_so_a_zoom_survives_a_redraw() -> None:
    shot = long_shot()
    assert time_figure(shot, revision="shot-1")["layout"]["uirevision"] == "shot-1"
    # Without one, redraws share the panel-wide revision.
    assert time_figure(shot)["layout"]["uirevision"] == "studio"
