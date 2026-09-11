import numpy as np
import pytest

from cetal_scopes import Capture, Shot


def make_capture(volts: list[float], *, t0: float = 0.0, dt: float = 1.0) -> Capture:
    return Capture(
        volts=np.asarray([volts], dtype=np.float64),
        t0=t0,
        dt=dt,
        channel_names=("C1",),
    )


def test_from_captures_defaults_reference_to_first() -> None:
    shot = Shot.from_captures({"siglent": make_capture([1, 2, 3])})
    assert shot.reference == "siglent"
    assert shot.n_captures == 1


def test_add_keeps_labels_and_sets_reference() -> None:
    shot = Shot()
    shot.add("b", make_capture([3, 4, 5]))
    shot.add("a", make_capture([1, 2, 3]))
    assert list(shot) == ["b", "a"]
    assert shot.reference == "b"
    assert "a" in shot
    assert shot["a"].n_samples == 3


def test_add_rejects_duplicate_label() -> None:
    shot = Shot.from_captures({"a": make_capture([1, 2])})
    with pytest.raises(ValueError, match="already added"):
        shot.add("a", make_capture([1, 2]))


def test_add_rejects_empty_label_and_bad_offset() -> None:
    shot = Shot()
    with pytest.raises(ValueError, match="non-empty"):
        shot.add("", make_capture([1, 2]))
    with pytest.raises(ValueError, match="finite"):
        shot.add("a", make_capture([1, 2]), offset=float("nan"))


def test_constructor_rejects_unknown_offset_and_reference() -> None:
    with pytest.raises(ValueError, match="unknown captures"):
        Shot(captures={"a": make_capture([1, 2])}, offsets={"b": 1.0})
    with pytest.raises(ValueError, match="reference"):
        Shot(captures={"a": make_capture([1, 2])}, reference="b")


def test_set_offset_and_reference() -> None:
    shot = Shot.from_captures({"a": make_capture([1, 2]), "b": make_capture([3, 4])})
    shot.set_offset("b", 2.5)
    shot.set_reference("b")
    assert shot.time_offset("b") == 2.5
    assert shot.reference == "b"
    with pytest.raises(KeyError):
        shot.set_offset("nope", 1.0)
    with pytest.raises(ValueError, match="finite"):
        shot.set_offset("a", float("inf"))


def test_channels_are_namespaced() -> None:
    shot = Shot.from_captures({"a": make_capture([1, 2]), "b": make_capture([3, 4])})
    assert list(shot.channels) == ["a:C1", "b:C1"]
    assert shot.channels["a:C1"].volts[0] == 1.0


def test_aligned_channel_applies_offset_and_shares_data() -> None:
    capture = make_capture([1, 2, 3], t0=0.0, dt=1.0)
    shot = Shot.from_captures({"a": capture})
    shot.set_offset("a", -2.0)
    aligned = shot.aligned_channel("a", "C1")
    assert aligned.t0 == -2.0
    assert np.shares_memory(aligned.volts, capture["C1"].volts)
    assert list(shot.aligned_channels()) == ["a:C1"]


def test_time_bounds_and_common_time() -> None:
    shot = Shot.from_captures(
        {
            "a": make_capture([0, 0, 0, 0], t0=0.0, dt=1.0),
            "b": make_capture([0, 0, 0, 0], t0=10.0, dt=0.5),
        }
    )
    shot.set_offset("b", -10.0)
    assert shot.time_bounds() == (0.0, 3.0)
    grid = shot.common_time()
    assert grid[0] == 0.0
    assert grid[1] - grid[0] == 0.5
    assert grid[-1] == pytest.approx(3.0)
    with pytest.raises(ValueError, match="dt must be positive"):
        shot.common_time(dt=0.0)


def test_empty_shot_time_bounds_raises() -> None:
    with pytest.raises(ValueError, match="no captures"):
        Shot().time_bounds()


def test_shot_repr() -> None:
    shot = Shot.from_captures({"a": make_capture([1, 2])})
    assert repr(shot) == "Shot(n_captures=1, labels=['a'], reference='a')"
