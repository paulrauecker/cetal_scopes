import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from cetal_scopes import (
    Antenna,
    Capture,
    Shot,
    TransferFunction,
    load_capture,
    load_shot,
    save_capture,
    save_shot,
)
from cetal_scopes.metadata import SHOT_FORMAT_VERSION


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
        units={"C1": "T/s"},
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
    assert loaded["C1"].unit == "T/s"
    assert loaded["C2"].unit == "V"

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


# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------


def make_shot() -> Shot:
    siglent = Capture(
        volts=np.arange(6, dtype=np.float64).reshape(2, 3),
        t0=-1e-9,
        dt=1e-9,
        channel_names=("C1", "C2"),
        raw=np.arange(6, dtype=np.int16).reshape(2, 3),
        metadata={"instrument": "Siglent SDS6204L"},
    )
    m5i = Capture(
        volts=np.ones((1, 4), dtype=np.float64),
        t0=0.0,
        dt=2e-10,
        channel_names=("CH0",),
    )
    shot = Shot(metadata={"shot_id": "s-1", "arm_spread_s": 0.002})
    shot.add("siglent", siglent)
    shot.add("m5i", m5i, offset=3.5e-9)
    return shot


def test_shot_round_trip(tmp_path: Path) -> None:
    shot = make_shot()
    index = save_shot(shot, tmp_path / "shot001")
    loaded = load_shot(index.parent)

    assert list(loaded) == ["siglent", "m5i"]
    assert loaded.reference == "siglent"
    assert loaded.offsets == {"siglent": 0.0, "m5i": 3.5e-9}
    assert loaded.metadata == {"shot_id": "s-1", "arm_spread_s": 0.002}

    np.testing.assert_array_equal(loaded["siglent"].volts, shot["siglent"].volts)
    np.testing.assert_array_equal(loaded["siglent"].raw, shot["siglent"].raw)
    assert loaded["siglent"].metadata["instrument"] == "Siglent SDS6204L"


def test_shot_round_trip_without_raw(tmp_path: Path) -> None:
    shot = Shot()
    shot.add("only", Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1e-9))
    loaded = load_shot(save_shot(shot, tmp_path / "s").parent)

    assert loaded["only"].raw is None


def test_shot_can_be_loaded_from_the_index_path(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "shot001")
    assert list(load_shot(index)) == ["siglent", "m5i"]


def test_shot_preserves_a_non_default_reference(tmp_path: Path) -> None:
    shot = make_shot()
    shot.set_reference("m5i")
    assert load_shot(save_shot(shot, tmp_path / "s").parent).reference == "m5i"


def test_shot_directory_is_movable(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "here")
    moved = tmp_path / "there"
    index.parent.rename(moved)

    assert list(load_shot(moved)) == ["siglent", "m5i"]


def test_shot_index_is_written_last(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "s")
    index_mtime = index.stat().st_mtime_ns
    captures = [p for p in index.parent.glob("*.json") if p != index]

    assert captures
    assert all(p.stat().st_mtime_ns <= index_mtime for p in captures)


def test_segment_labels_are_made_filename_safe(tmp_path: Path) -> None:
    shot = Shot()
    shot.add("m5i[0]", Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1e-9))
    shot.add("m5i[1]", Capture(volts=np.ones((1, 2)), t0=0.0, dt=1e-9))
    index = save_shot(shot, tmp_path / "s")

    assert {p.name for p in index.parent.glob("*.json")} == {
        "shot.json",
        "m5i_0.json",
        "m5i_1.json",
    }
    assert list(load_shot(index.parent)) == ["m5i[0]", "m5i[1]"]


def test_saving_an_empty_shot_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no captures"):
        save_shot(Shot(), tmp_path / "s")


def test_colliding_labels_are_rejected(tmp_path: Path) -> None:
    shot = Shot()
    shot.add("a b", Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1e-9))
    shot.add("a/b", Capture(volts=np.zeros((1, 2)), t0=0.0, dt=1e-9))

    with pytest.raises(ValueError, match="both map to the filename"):
        save_shot(shot, tmp_path / "s")


def test_loading_a_missing_shot_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no shot index"):
        load_shot(tmp_path / "nope")


def test_unknown_shot_format_version_is_rejected(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "s")
    payload = json.loads(index.read_text())
    payload["format_version"] = SHOT_FORMAT_VERSION + 1
    index.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="unsupported shot format_version"):
        load_shot(index)


def test_an_unknown_shot_reference_is_rejected(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "s")
    payload = json.loads(index.read_text())
    payload["reference"] = "ghost"
    index.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="is not one of the saved captures"):
        load_shot(index)


def test_a_missing_capture_file_is_reported(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "s")
    (index.parent / "m5i.json").unlink()

    with pytest.raises(FileNotFoundError):
        load_shot(index)


def test_a_traversing_capture_reference_is_rejected(tmp_path: Path) -> None:
    index = save_shot(make_shot(), tmp_path / "s")
    payload = json.loads(index.read_text())
    payload["captures"][0]["capture_json"] = "../elsewhere.json"
    index.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="bare filename"):
        load_shot(index)
