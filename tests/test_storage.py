import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from cetal_scopes import Antenna, Capture, TransferFunction, load_capture, save_capture


def make_antenna() -> Antenna:
    transfer = TransferFunction(
        freq=[1.0e6, 1.0e7, 1.0e8],
        gain=[1.0 + 0j, 2.0 + 1j, 3.0 - 1j],
    )
    return Antenna(
        name="Bdot-X",
        kind="b-dot",
        axis=(1.0, 0.0, 0.0),
        transfer_function=transfer,
        delay=1.0e-9,
        position=(0.0, 0.0, 0.0),
    )


def make_capture() -> Capture:
    return Capture(
        volts=np.arange(12, dtype=np.float64).reshape(2, 6),
        t0=2.0,
        dt=0.5,
        channel_names=("C1", "C2"),
        raw=np.arange(12, dtype=np.int16).reshape(2, 6),
        antennas={"C1": make_antenna()},
        metadata={"instrument": "siglent", "notes": "triad"},
    )


def test_round_trip_preserves_everything(tmp_path: Path) -> None:
    capture = make_capture()
    json_path = save_capture(capture, tmp_path / "cap.json")

    assert json_path == tmp_path / "cap.json"
    assert {p.name for p in tmp_path.iterdir()} == {
        "cap.json",
        "cap.volts.npy",
        "cap.raw.npy",
    }

    loaded = load_capture(json_path)
    assert loaded.channel_names == ("C1", "C2")
    assert loaded.metadata == {"instrument": "siglent", "notes": "triad"}
    assert loaded.t0 == 2.0
    assert loaded.dt == 0.5
    np.testing.assert_array_equal(loaded.volts, capture.volts)
    assert loaded.raw is not None
    np.testing.assert_array_equal(loaded.raw, capture.raw)
    assert loaded["C2"].antenna is None

    antenna = loaded["C1"].antenna
    assert antenna is not None
    assert antenna.name == "Bdot-X"
    assert antenna.kind == "b-dot"
    assert antenna.delay == 1.0e-9
    assert antenna.unit_axis is not None
    np.testing.assert_allclose(antenna.unit_axis, [1.0, 0.0, 0.0])
    assert antenna.transfer_function is not None
    assert antenna.transfer_function.gain_at(1.0e7) == pytest.approx(2.0 + 1.0j)


def test_round_trip_without_raw(tmp_path: Path) -> None:
    capture = Capture(volts=np.zeros((1, 4)), t0=0.0, dt=1.0)
    save_capture(capture, tmp_path / "cap.json")
    assert not (tmp_path / "cap.raw.npy").exists()
    assert load_capture(tmp_path / "cap.json").raw is None


def test_missing_json_suffix_is_appended(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap")
    assert json_path.name == "cap.json"
    assert (tmp_path / "cap.volts.npy").exists()


def test_sidecars_resolve_relative_to_json(tmp_path: Path) -> None:
    save_capture(make_capture(), tmp_path / "cap.json")
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    for name in ("cap.json", "cap.volts.npy", "cap.raw.npy"):
        (tmp_path / name).rename(moved / name)
    loaded = load_capture(moved / "cap.json")
    assert loaded["C1"].antenna is not None


def test_unknown_format_version_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    data = json.loads(json_path.read_text())
    data["format_version"] = 99
    json_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="format_version"):
        load_capture(json_path)


def test_missing_sidecar_is_reported(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    (tmp_path / "cap.volts.npy").unlink()
    with pytest.raises(FileNotFoundError, match="cap.volts.npy"):
        load_capture(json_path)


def test_shape_mismatch_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    np.save(tmp_path / "cap.volts.npy", np.zeros((2, 3)))
    with pytest.raises(ValueError, match="does not match metadata"):
        load_capture(json_path)


def test_non_float64_volts_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    np.save(tmp_path / "cap.volts.npy", np.zeros((2, 6), dtype=np.float32))
    with pytest.raises(ValueError, match="float64"):
        load_capture(json_path)


def test_channel_metadata_mismatch_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    data = json.loads(json_path.read_text())
    data["channels"][0]["name"] = "WRONG"
    json_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match channel_names"):
        load_capture(json_path)


def test_sidecar_with_directory_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    data = json.loads(json_path.read_text())
    data["volts_sidecar"] = "../cap.volts.npy"
    json_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="bare filename"):
        load_capture(json_path)


def test_unknown_json_field_is_rejected(tmp_path: Path) -> None:
    json_path = save_capture(make_capture(), tmp_path / "cap.json")
    data = json.loads(json_path.read_text())
    data["unexpected"] = 1
    json_path.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        load_capture(json_path)


def test_load_missing_json_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_capture(tmp_path / "nope.json")
