"""Plotly figure specifications built from a shot."""

from __future__ import annotations

import numpy as np
import pytest
from figures import (
    LAYOUTS,
    LayoutMode,
    coherence_figure,
    empty_figure,
    fft_figure,
    measurement_table,
    spectrogram_figure,
    time_figure,
    time_scale,
    transfer_figure,
    vector_figure,
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
    assert figure["layout"]["yaxis"]["title"]["text"] == "PSD"


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
