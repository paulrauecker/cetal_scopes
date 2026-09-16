"""CSV and NPZ export of a shot's traces."""

from __future__ import annotations

import io

import numpy as np
import pytest
from export import export_shot

from cetal_scopes import Capture, Shot
from cetal_scopes.analysis import lowpass

DT = 1e-9


def make_shot() -> Shot:
    shot = Shot(metadata={"shot_id": "s-1"})
    shot.add(
        "a",
        Capture(
            volts=np.vstack([np.arange(8.0), np.arange(8.0) * 2]),
            t0=0.0,
            dt=DT,
            channel_names=("C1", "C2"),
        ),
    )
    shot.add(
        "b",
        Capture(volts=np.ones((1, 4)), t0=0.0, dt=2 * DT, channel_names=("CH0",)),
        offset=1e-9,
    )
    return shot


def test_csv_has_one_time_column_and_one_column_per_channel() -> None:
    payload, media_type, filename = export_shot(make_shot(), fmt="csv")
    lines = payload.decode().strip().splitlines()

    assert media_type == "text/csv"
    assert filename == "s-1.csv"
    assert lines[0] == "time_s,a:C1,a:C2,b:CH0"
    assert len(lines) > 2


def test_csv_leaves_gaps_empty_rather_than_inventing_edge_values() -> None:
    shot = make_shot()
    shot.set_offset("b", 5e-9)  # pushed past the end of capture "a"
    payload, _, _ = export_shot(shot, fmt="csv")
    rows = payload.decode().strip().splitlines()[1:]

    # Somewhere a row must have an empty cell rather than a held-over value.
    assert any(",," in row or row.endswith(",") for row in rows)


def test_csv_reflects_the_offsets() -> None:
    shot = make_shot()
    first = export_shot(shot, fmt="csv")[0].decode()
    shot.set_offset("b", 4e-9)
    second = export_shot(shot, fmt="csv")[0].decode()

    assert first != second


def test_npz_keeps_every_channel_on_its_own_axis() -> None:
    payload, media_type, filename = export_shot(make_shot(), fmt="npz")
    loaded = np.load(io.BytesIO(payload), allow_pickle=False)

    assert media_type == "application/octet-stream"
    assert filename == "s-1.npz"
    assert set(loaded) == {
        "a__C1__t",
        "a__C1__v",
        "a__C2__t",
        "a__C2__v",
        "b__CH0__t",
        "b__CH0__v",
    }
    # Nothing is resampled: each keeps its own length.
    assert loaded["a__C1__v"].size == 8
    assert loaded["b__CH0__v"].size == 4


def test_npz_carries_the_recorded_samples_unchanged() -> None:
    payload, _, _ = export_shot(make_shot(), fmt="npz")
    loaded = np.load(io.BytesIO(payload), allow_pickle=False)

    np.testing.assert_array_equal(loaded["a__C1__v"], np.arange(8.0))


def test_a_channel_selection_is_honoured() -> None:
    payload, _, _ = export_shot(make_shot(), fmt="csv", channels=["a:C1"])
    assert payload.decode().splitlines()[0] == "time_s,a:C1"


def test_processed_channels_are_exported_when_given() -> None:
    n = 512
    t = np.arange(n) * 1e-6
    noisy = np.sin(2 * np.pi * 1e3 * t) + np.sin(2 * np.pi * 2e5 * t)
    shot = Shot()
    shot.add("a", Capture(volts=noisy[None, :], t0=0.0, dt=1e-6))

    processed = {
        key: lowpass(channel, cutoff=1e4)
        for key, channel in shot.aligned_channels().items()
    }
    plain = np.load(io.BytesIO(export_shot(shot, fmt="npz")[0]))["a__CH1__v"]
    filtered = np.load(
        io.BytesIO(export_shot(shot, fmt="npz", processed=processed)[0])
    )["a__CH1__v"]

    assert np.abs(filtered).max() < np.abs(plain).max()


def test_a_point_budget_decimates_the_npz() -> None:
    n = 10_000
    shot = Shot()
    shot.add("a", Capture(volts=np.sin(np.arange(n) * 0.01)[None, :], t0=0.0, dt=DT))
    payload, _, _ = export_shot(shot, fmt="npz", max_points=200)
    loaded = np.load(io.BytesIO(payload), allow_pickle=False)

    assert loaded["a__CH1__v"].size <= 200
    # Envelope decimation, so the extremes survive.
    assert loaded["a__CH1__v"].max() == pytest.approx(1.0, abs=0.01)


def test_an_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown export format"):
        export_shot(make_shot(), fmt="png")


def test_an_unknown_channel_is_rejected() -> None:
    with pytest.raises(KeyError, match="ghost"):
        export_shot(make_shot(), channels=["ghost"])


def test_an_empty_selection_is_rejected() -> None:
    with pytest.raises(ValueError, match="no channels selected"):
        export_shot(make_shot(), channels=[])
